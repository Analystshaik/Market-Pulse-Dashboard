"""TBT research-only extraction. Uses the existing repaired master SQLite inputs.

No trade status is written or inferred by this module.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import re
import sqlite3

SECTORS = {
    "XLC": "Communication Services", "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples", "XLE": "Energy", "XLF": "Financials",
    "XLV": "Health Care", "XLI": "Industrials", "XLB": "Materials",
    "XLRE": "Real Estate", "XLK": "Technology", "XLU": "Utilities",
}

DEFAULTS = {"min_score":45.0, "min_hit_rate":0.40, "min_samples":5,
            "require_positive_correlation": True, "max_lag_days":7}


def qident(name):
    """Identifiers only come from PRAGMA, never user input."""
    return '"' + str(name).replace('"', '""') + '"'


def exists(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)).fetchone() is not None


def cols(con, table):
    return [r[1] for r in con.execute("PRAGMA table_info(%s)" % qident(table))]


def rows(con, table, where="", params=()):
    # All table names below are module constants.
    cur=con.execute("SELECT * FROM %s %s" % (qident(table), where), tuple(params))
    names=[d[0] for d in cur.description]
    return [dict(zip(names,row)) for row in cur.fetchall()]


def key(row, variants, default=None):
    lower={str(k).lower(): k for k in row}
    for v in variants:
        if v.lower() in lower:
            value=row[lower[v.lower()]]
            if value is not None and str(value).strip() != "":
                return value
    return default


def numerical(v):
    try:
        n=float(v)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def normalized_hit(v):
    h=numerical(v)
    if h is None: return None
    if 1 < h <= 100: h=h/100
    if 0 <= h <= 1: return h
    return None


def normalized_corr(v):
    c=numerical(v)
    return c if c is not None and -1 <= c <= 1 else None


def as_date(x):
    if x is None: return None
    s=str(x).strip()
    if s.lower() in ('none','nan','nat',''): return None
    # Handles 20261106, ISO date and datetime without normalizing time zone.
    if len(s)==8 and s.isdigit(): return s[:4]+'-'+s[4:6]+'-'+s[6:]
    return s[:10]


# Names are helpful but values are authoritative: several MarketVector builds
# store a constituent as stock_ticker / etf_source, not symbol / sector_etf.
SYMBOL_ALIASES = (
    'symbol', 'ticker', 'stock_symbol', 'stock_ticker', 'ticker_symbol',
    'stock', 'constituent_symbol', 'constituent_ticker', 'constituent',
    'stock_code', 'security_symbol', 'equity_symbol', 'equity_ticker',
    'member_symbol', 'member_ticker', 'underlying_symbol', 'asset_symbol',
)
ETF_ALIASES = (
    'etf', 'sector_etf', 'etf_symbol', 'sector', 'sector_etfs',
    'sector_ticker', 'etf_ticker', 'sector_etf_symbol', 'spdr_etf',
    'source_etf', 'etf_source', 'etf_sources', 'source_etfs',
    'etf_symbols', 'etf_tickers', 'sector_etf_ticker', 'spdr_sector',
    'source_sector', 'source_sector_etf', 'source_sector_symbol',
    'qualifying_etf', 'source_etf_symbol', 'sector_etf_code',
    'sector_name', 'sector_names', 'etf_membership', 'sector_membership',
)
ETF_TOKEN_RE = re.compile(r'(?<![A-Z0-9])(?:' + '|'.join(SECTORS) + r')(?![A-Z0-9])')
TICKER_RE = re.compile(r'^[A-Z][A-Z0-9.\^-]{0,14}$')


def _ticker(v):
    if v is None: return ''
    x = str(v).strip().upper()
    return x if TICKER_RE.fullmatch(x) and x not in {'NAN','NONE','NULL','N/A','.', 'TBT','TLT'} and x not in SECTORS else ''


def _pick_symbol_column(records, reference_symbols=None):
    if not records: return None
    names = list(records[0])
    known = {name.lower(): name for name in names}
    for alias in SYMBOL_ALIASES:
        if alias in known:
            return known[alias]
    # Unknown column name: compare actual data with the independently stored
    # stock symbols from the TBT lead-lag table, not the column's position.
    if reference_symbols:
        candidate_scores = []
        for name in names:
            values = [_ticker(r.get(name)) for r in records]
            valid = [x for x in values if x]
            overlap = sum(x in reference_symbols for x in valid)
            if overlap >= max(1, min(len(reference_symbols), int(len(reference_symbols)*0.20))):
                candidate_scores.append((overlap, len(set(valid)), name))
        if candidate_scores:
            return max(candidate_scores)[2]
    return None


def _etfs(value):
    if value is None: return set()
    raw = str(value).strip().upper()
    if not raw or raw in ('NONE','NAN','NULL'): return set()
    result = set(ETF_TOKEN_RE.findall(raw))
    # A few source views expose sector descriptions rather than ETF symbols.
    # Convert only exact matches, never fuzzy or guessed industry labels.
    phrases = [re.sub(r'\s+', ' ', part).strip() for part in re.split(r'[,;|/]+', raw)]
    for etf, desc in SECTORS.items():
        if desc.upper() in phrases:
            result.add(etf)
    return result


def _row_etfs(r, symbol_column):
    keyset = {c.lower(): c for c in r}
    matched = set()
    for alias in ETF_ALIASES:
        if alias in keyset and keyset[alias] != symbol_column:
            matched |= _etfs(r[keyset[alias]])
    if matched:
        return matched
    # Unknown field name (e.g. qualifying_sector): look for actual ETF tokens
    # in *non-symbol* columns, allowing comma/pipe/JSON lists.
    for col, value in r.items():
        if col != symbol_column:
            matched |= _etfs(value)
    return matched


def _fallback_etf_map(con, symbols):
    """Join to a separate explicit ETF/holdings table only when needed.

    This never adds symbols that are absent from the TBT-authoritative universe.
    Tables of market bars, relationships, or trading snapshots are not eligible.
    """
    out = defaultdict(set)
    possible = con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'").fetchall()
    inspected = []
    for (table,) in possible:
        lower = table.lower()
        if lower in ('mv_tbt_stock_universe_current',) or not any(t in lower for t in ('holding','constituent','sector_stock','sector_map','etf_stock','etf_membership')):
            continue
        if any(t in lower for t in ('bar','price','snapshot','reversal','leadlag','event','trend','history','relationship')):
            continue
        columns = cols(con,table)
        if not columns or not any(re.search(r'(stock|ticker|symbol|constituent|member)',c,re.I) for c in columns):
            continue
        if not any(re.search(r'(etf|sector)',c,re.I) for c in columns):
            continue
        inspected.append(table)
        cur=con.execute('SELECT * FROM '+qident(table)+' LIMIT 15000')
        names=[desc[0] for desc in cur.description]
        subset=[dict(zip(names, vals)) for vals in cur.fetchall()]
        symbol_col=_pick_symbol_column(subset,symbols)
        if not symbol_col: continue
        for r in subset:
            sym=_ticker(r.get(symbol_col))
            if sym in symbols:
                out[sym].update(_row_etfs(r,symbol_col))
    return out, inspected


def source_universe(con):
    table='mv_tbt_stock_universe_current'
    if not exists(con,table):
        raise ValueError('Missing mv_tbt_stock_universe_current; run the TBT research pipeline first')
    records=rows(con,table)
    if not records:
        raise ValueError('TBT stock universe is empty; research export stopped')
    lag_table='mv_tbt_stock_reversal_leadlag_day'
    ref_symbols=set()
    if exists(con,lag_table):
        # Metadata-guided selection avoids assuming the lead-lag is keyed by
        # a particular spelling of the stock field.
        lag_columns=cols(con,lag_table)
        lc=next((c for name in SYMBOL_ALIASES for c in lag_columns if c.lower()==name),None)
        if lc:
            ref_symbols={_ticker(r[0]) for r in con.execute('SELECT DISTINCT '+qident(lc)+' FROM '+qident(lag_table))}
            ref_symbols.discard('')
    symbol_col=_pick_symbol_column(records,ref_symbols)
    if not symbol_col:
        raise ValueError('Cannot identify stock symbol field in TBT universe. Columns: '+', '.join(cols(con,table))+'. Run python DIAGNOSE_TBT_RESEARCH_SOURCE.py for safe examples.')
    mapped=defaultdict(set)
    symbols=set()
    for r in records:
        sym=_ticker(r.get(symbol_col))
        if not sym: continue
        symbols.add(sym)
        mapped[sym].update(_row_etfs(r,symbol_col))
    if not symbols:
        raise ValueError('No valid stock tickers found in TBT universe using field '+symbol_col)
    unresolved={sym for sym in symbols if not mapped[sym]}
    fallback_tables=[]
    if unresolved:
        extra,fallback_tables=_fallback_etf_map(con,unresolved)
        for sym in unresolved:
            mapped[sym].update(extra.get(sym,set()))
    results={sym: etfs for sym,etfs in mapped.items() if etfs}
    if not results:
        preview=[]
        for r in records[:2]:
            preview.append({str(k):str(v)[:75] for k,v in r.items()})
        raise ValueError(
            'TBT universe has stock symbols but no recognizable SPDR ETF membership. '
            'Columns='+str(cols(con,table))+'; symbol_field='+symbol_col+
            '; first_rows='+str(preview)+'; sector_join_tables_checked='+str(fallback_tables)+
            '. Run python DIAGNOSE_TBT_RESEARCH_SOURCE.py. No data modified.'
        )
    missing=sorted(symbols-set(results))
    if missing:
        # Do not silently publish a partial universe as though all candidates
        # had sector attribution. A missing ETF is a data-source issue.
        raise ValueError(
            f'TBT universe maps {len(results)}/{len(symbols)} symbols to an SPDR ETF; '
            f'{len(missing)} are unmapped (examples: {missing[:10]}). '
            f'Columns={cols(con,table)}. No cloud data was changed.'
        )
    return results


def best_records(con, universe, cfg):
    table="mv_tbt_stock_reversal_leadlag_day"
    if not exists(con,table):
        raise ValueError("Missing mv_tbt_stock_reversal_leadlag_day; run the repaired TBT analysis first")
    raw=rows(con,table)
    if not raw:
        raise ValueError("TBT daily lead-lag table is empty")
    grouped=defaultdict(list)
    valid_stats=0
    for r in raw:
        sym=str(key(r,['symbol','ticker','stock_symbol','stock'],'')).strip().upper()
        if sym not in universe: continue
        lag=numerical(key(r,['lag_days','lag','lag_bars','response_lag','lag_day','lag_sessions','best_lag']))
        hit=normalized_hit(key(r,['hit_rate','hit_ratio','hit_pct','response_hit_rate','follower_hit_rate','success_rate']))
        corr=normalized_corr(key(r,['correlation','corr','lag_corr','response_corr','event_corr','best_lag_corr']))
        score=numerical(key(r,['score','event_score','relationship_score','lag_score','follower_score','confidence_score']))
        n=numerical(key(r,['sample_size','samples','n_samples','n_events','sample_count','event_count','event_samples','number_of_samples','positive_events','n_obs','observations']))
        if None in (lag,hit,corr,score,n): continue
        if not lag.is_integer() or lag<0 or lag>cfg['max_lag_days']: continue
        valid_stats+=1
        best_flag=key(r,['is_best_lag','best_lag_flag','is_best','best_flag'])
        selected = str(best_flag).strip().lower() in ('1','true','yes','y')
        grouped[sym].append(dict(symbol=sym,lag_days=int(lag),hit_rate=hit,correlation=corr,
                                 score=score,samples=int(n),is_best=selected))
    if not valid_stats:
        raise ValueError("No complete lag/score/hit/correlation/sample rows. Inspect lead-lag source column names and repaired calculations")
    best=[]
    for sym, candidates in grouped.items():
        # Engine-selected best rows take precedence; otherwise rank by published score, then hit rate.
        chosen=max(candidates,key=lambda x:(int(x['is_best']),x['score'],x['hit_rate'],x['correlation'],x['samples'],-x['lag_days']))
        if chosen['score'] < cfg['min_score'] or chosen['hit_rate'] < cfg['min_hit_rate'] or chosen['samples'] < cfg['min_samples']:
            continue
        if cfg['require_positive_correlation'] and chosen['correlation'] <= 0:
            continue
        if chosen['score']>=65 and chosen['hit_rate']>=0.55 and chosen['correlation']>=0.25 and chosen['samples']>=15:
            tier='STRONG'
        elif chosen['score']>=55 and chosen['hit_rate']>=0.45 and chosen['correlation']>=0.10 and chosen['samples']>=10:
            tier='GOOD'
        else:
            tier='WATCH'
        etfs=sorted(universe[sym])
        chosen.update(etfs=', '.join(etfs),sectors=' / '.join(SECTORS[e] for e in etfs),research_confidence=tier)
        best.append(chosen)
    best.sort(key=lambda x:({'STRONG':0,'GOOD':1,'WATCH':2}[x['research_confidence']],-x['score'],-x['hit_rate'],-x['correlation'],x['symbol']))
    return best, {'raw_lag_rows':len(raw),'complete_lag_rows':valid_stats,'stocks_with_complete_lags':len(grouped),'universe_size':len(universe)}


def weekly_gate(con):
    """Use existing client STATUS rows, but require unambiguous positive confirmation to say OPEN.

    This is display information only; the real trading engine owns Buy eligibility.
    """
    table="mv_actionable_dashboard_weekly"
    if not exists(con,table):
        return {'gate':'UNKNOWN','weekly_status':'MISSING','master_direction':'UNKNOWN','master_date':None,
                'reason':'Weekly snapshot missing; cannot establish whether Buy gate is open'}
    columns=cols(con,table)
    where="WHERE UPPER(TRIM(reference))='TBT' AND UPPER(TRIM(row_type))='STATUS'" if all(c in columns for c in ('reference','row_type')) else ''
    snap=rows(con,table,where)
    if not snap:
        return {'gate':'UNKNOWN','weekly_status':'MISSING','master_direction':'UNKNOWN','master_date':None,
                'reason':'TBT weekly STATUS missing; cannot establish whether Buy gate is open'}
    snap.sort(key=lambda x:str(key(x,['snapshot_ts','reference_asof','asof','updated_at'],'') or ''))
    s=snap[-1]
    status=str(key(s,['weekly_status'],'UNKNOWN')).strip().upper()
    direction=str(key(s,['reference_weekly_master_direction','weekly_master_direction','master_weekly_direction','master_direction','latest_master_weekly_direction'], 'UNKNOWN')).strip().upper()
    date=as_date(key(s,['reference_weekly_master_date','weekly_master_date','master_weekly_date','master_date','latest_master_weekly_date']))
    # If the snapshot lacks the direction, do not claim OPEN based on weekly_status alone.
    if status == 'ACTIVE' and direction == 'BULLISH':
        gate='OPEN'; reason='Weekly STATUS ACTIVE and latest master Weekly reversal BULLISH'
    elif status != 'ACTIVE' or direction == 'BEARISH':
        gate='CLOSED';reason='Weekly STATUS is not ACTIVE or master reversal is BEARISH'
    else:
        gate='UNKNOWN';reason='Master Weekly direction not confirmed in snapshot; trading eligibility must be checked separately'
    return dict(gate=gate, weekly_status=status, master_direction=direction, master_date=date, reason=reason)


def export(source, target, cfg=None):
    cfg=dict(DEFAULTS,**(cfg or {}))
    src=Path(source).expanduser().resolve()
    dst=Path(target).expanduser().resolve()
    if src == dst: raise ValueError('Refusing to write to master database; source and target paths are identical')
    if not src.is_file(): raise FileNotFoundError('Master database not found: '+str(src))
    if not dst.is_file(): raise FileNotFoundError('Existing compact cloud database not found: '+str(dst))
    if cfg['min_samples']<1 or cfg['max_lag_days']<0: raise ValueError('Invalid research thresholds')
    # Use read-only URI for the master; never modify/attach it to the cloud DB.
    with closing(sqlite3.connect(src.as_uri()+'?mode=ro',uri=True)) as master:
        universe=source_universe(master)
        picks,stats=best_records(master,universe,cfg)
        gate=weekly_gate(master)
    stamp=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if not picks:
        raise ValueError('Research screen returned zero candidates; refusing to overwrite previous export. Check source columns and thresholds')
    # A transaction leaves the existing research dataset unchanged if export fails.
    con=sqlite3.connect(dst)
    try:
        con.execute('BEGIN IMMEDIATE')
        con.execute('''CREATE TABLE IF NOT EXISTS mv_tbt_positive_research_watch (
            symbol TEXT PRIMARY KEY, etfs TEXT NOT NULL, sectors TEXT NOT NULL,
            lag_days INTEGER NOT NULL, hit_rate REAL NOT NULL,
            correlation REAL NOT NULL, samples INTEGER NOT NULL,
            score REAL NOT NULL, research_confidence TEXT NOT NULL,
            exported_at_utc TEXT NOT NULL)''')
        con.execute('''CREATE TABLE IF NOT EXISTS mv_tbt_research_gate_status (
            id INTEGER PRIMARY KEY CHECK(id=1), gate TEXT NOT NULL,
            weekly_status TEXT, master_direction TEXT, master_date TEXT,
            reason TEXT NOT NULL, exported_at_utc TEXT NOT NULL,
            source_stats TEXT NOT NULL, research_config TEXT NOT NULL)''')
        con.execute('DELETE FROM mv_tbt_positive_research_watch')
        con.executemany('''INSERT INTO mv_tbt_positive_research_watch
            (symbol,etfs,sectors,lag_days,hit_rate,correlation,samples,score,research_confidence,exported_at_utc)
            VALUES (:symbol,:etfs,:sectors,:lag_days,:hit_rate,:correlation,:samples,:score,:research_confidence,:exported_at_utc)''',
            [{**p,'exported_at_utc':stamp} for p in picks])
        con.execute('DELETE FROM mv_tbt_research_gate_status')
        con.execute('''INSERT INTO mv_tbt_research_gate_status
            (id,gate,weekly_status,master_direction,master_date,reason,exported_at_utc,source_stats,research_config)
            VALUES(1,?,?,?,?,?,?,?,?)''',(
            gate['gate'],gate['weekly_status'],gate['master_direction'],gate['master_date'],gate['reason'],stamp,
            json.dumps(stats,sort_keys=True),json.dumps(cfg,sort_keys=True)))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {'candidate_count':len(picks),'gate':gate, 'stats':stats, 'updated':stamp}
