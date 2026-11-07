"""TLT research-universe export to compact Streamlit cloud SQLite.

Source master is opened read-only. All research rows remain non-trading.
No TLT/TBT snapshot, ownership, reversal or Buy/Hold logic is modified.
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
import sys

SECTOR_NAMES = {
    'XLC':'Communication Services','XLY':'Consumer Discretionary',
    'XLP':'Consumer Staples','XLE':'Energy','XLF':'Financials',
    'XLV':'Health Care','XLI':'Industrials','XLB':'Materials',
    'XLRE':'Real Estate','XLK':'Technology','XLU':'Utilities',
}
WATCH = 'mv_tlt_positive_research_watch'
META = 'mv_tlt_research_export_meta'
TICKER = re.compile(r'^[A-Z][A-Z0-9.\-]{0,14}$')
DEFAULT = {'min_score':45.0, 'min_hit_rate':0.4, 'min_samples':3, 'max_lag_days':7}


def qi(x):
    return '"' + str(x).replace('"','""') + '"'


def exists(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",(name,)).fetchone() is not None


def cols(con,name):
    return [r[1] for r in con.execute('PRAGMA table_info('+qi(name)+')')]


def rows(con,name,where='',values=()):
    cur=con.execute('SELECT * FROM '+qi(name)+' '+where,values)
    names=[c[0] for c in cur.description]
    return [dict(zip(names,x)) for x in cur.fetchall()]


def first(row, *aliases):
    lookup={str(k).lower():k for k in row}
    for alias in aliases:
        key=lookup.get(alias.lower())
        if key is not None and row[key] is not None and str(row[key]).strip():
            return row[key]
    return None


def number(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (ValueError,TypeError):
        return None


def hit_rate(v):
    x=number(v)
    if x is None:return None
    if 1<x<=100:x/=100
    return x if 0<=x<=1 else None


def symbol(v):
    x=str(v or '').strip().upper()
    return x if TICKER.fullmatch(x) and x not in {'TLT','TBT','NAN','NONE','NULL','NA','XLC','XLY','XLP','XLE','XLF','XLV','XLI','XLB','XLRE','XLK','XLU'} else ''


def etf_values(value):
    if value is None:return set()
    txt=str(value).upper().strip()
    out=set()
    for piece in re.split(r'[\s,;|/]+',txt):
        if piece in SECTOR_NAMES:out.add(piece)
    for etf, sector in SECTOR_NAMES.items():
        if txt==sector.upper():out.add(etf)
    return out


def read_universe(con):
    table='mv_tlt_stock_universe_current'
    if not exists(con,table):raise RuntimeError(f'Missing {table}; run run_analysis.py first')
    records=rows(con,table)
    if not records:raise RuntimeError('TLT current universe has zero records')
    universe=defaultdict(set)
    unmapped=[]
    for item in records:
        stock=symbol(first(item,'stock_ticker','symbol','ticker','stock_symbol','constituent_ticker'))
        if not stock:continue
        etfs=etf_values(first(item,'etf_ticker','etf','sector_etf','sector_etf_ticker','sector_name'))
        if not etfs:
            unmapped.append((stock,dict(item)))
        universe[stock].update(etfs)
    if not universe:raise RuntimeError(f'TLT current universe has no usable stock names. Source columns: {cols(con,table)}')
    unmapped_syms=sorted(s for s,etfs in universe.items() if not etfs)
    if unmapped_syms:
        raise RuntimeError(f'{len(unmapped_syms)} TLT universe stocks have no recognized ETF membership: {unmapped_syms[:12]}; columns={cols(con,table)}. Cloud export stopped.')
    return dict(universe)


def best_lags(con,universe):
    table='mv_tlt_stock_reversal_leadlag_day'
    if not exists(con,table):raise RuntimeError(f'Missing {table}; run run_analysis.py first')
    record_list=rows(con,table)
    if not record_list:raise RuntimeError(f'{table} has zero rows')
    grouped=defaultdict(list)
    issues=0
    for row in record_list:
        sym=symbol(first(row,'ticker','symbol','stock_ticker','stock_symbol'))
        if sym not in universe:continue
        lag=number(first(row,'lag','lag_days','lag_bars','response_lag','best_lag'))
        hit=hit_rate(first(row,'hit_rate','response_hit_rate','hit_ratio','hit_pct'))
        corr=number(first(row,'corr','correlation','lag_corr','event_corr'))
        score=number(first(row,'score','event_score','relationship_score'))
        samples=number(first(row,'samples','sample_size','n_events','event_count','event_samples'))
        if None in (lag,hit,corr,score,samples) or not 0<=lag<=250 or abs(corr)>1 or samples<0:
            issues+=1
            continue
        flag=first(row,'is_best_lag','best_lag_flag','is_best')
        qualified_flag=str(flag).strip().lower() in ('1','true','yes','y')
        min_samples_flag=first(row,'meets_min_samples')
        meets_min_flag=(min_samples_flag is None or str(min_samples_flag).lower() in ('1','true','yes','y'))
        grouped[sym].append(dict(lag_days=int(lag),hit_rate=hit,correlation=corr,
                                  score=score,samples=int(samples),is_best=qualified_flag,
                                  sample_flag=meets_min_flag))
    if not grouped:
        raise RuntimeError(f'No interpretable TLT lead/lag data. Columns={cols(con,table)}; rejected={issues}. Cloud export stopped.')
    # Prioritize engine-selected best row, then score, not just raw maximum correlation.
    best={sym:max(opts,key=lambda o:(o['is_best'],o['score'],o['hit_rate'],o['samples'],-o['lag_days']))
          for sym,opts in grouped.items()}
    return best,{'raw_rows':len(record_list),'parsed_symbols':len(grouped),'invalid_rows':issues}


def config_values(project):
    cfg=dict(DEFAULT)
    file=Path(project)/'config.json'
    if file.exists():
        raw=json.loads(file.read_text(encoding='utf-8-sig'))
        a=raw.get('analysis',{})
        cfg.update(min_score=float(a.get('daily_min_score',cfg['min_score'])),
                   min_hit_rate=float(a.get('daily_min_hit_rate',cfg['min_hit_rate'])),
                   min_samples=int(a.get('daily_min_samples',cfg['min_samples'])),
                   max_lag_days=int(a.get('daily_event_max_lag',cfg['max_lag_days'])))
    if not 0<=cfg['min_hit_rate']<=1 or cfg['min_samples']<1 or cfg['max_lag_days']<0:
        raise RuntimeError('Invalid TLT research thresholds from config.json')
    return cfg


def research_grade(row):
    if row['research_status']!='QUALIFIED':return 'BELOW_GATE'
    score,hit,corr,n=(row[x] for x in ('score','hit_rate','correlation','samples'))
    if score>=65 and hit>=0.55 and corr>=0.25 and n>=15:return 'STRONG'
    if score>=55 and hit>=0.45 and corr>=0.1 and n>=10:return 'GOOD'
    return 'WATCH'


def find_meta(con):
    table='mv_actionable_dashboard_weekly'
    output=dict(gate='UNKNOWN',weekly_status='UNKNOWN',master_direction='UNKNOWN',master_date='',asof='')
    if not exists(con,table):return output
    columns=cols(con,table)
    if not {'reference','row_type'}.issubset(set(columns)):return output
    group=rows(con,table,"WHERE UPPER(TRIM(reference))='TLT' AND UPPER(TRIM(row_type))='STATUS'")
    if not group:return output
    group.sort(key=lambda r:str(first(r,'snapshot_ts','reference_asof','asof') or ''))
    r=group[-1]
    output['weekly_status']=str(first(r,'weekly_status') or 'UNKNOWN').upper()
    output['master_direction']=str(first(r,'reference_weekly_master_direction','weekly_master_direction','master_direction','latest_master_weekly_direction') or 'UNKNOWN').upper()
    output['master_date']=str(first(r,'reference_weekly_master_date','weekly_master_date','master_date') or '')[:10]
    output['asof']=str(first(r,'reference_asof','snapshot_ts') or '')[:19]
    # Fail closed: the research grid's display gate is not an authority to permit a trade.
    if output['weekly_status']=='ACTIVE' and output['master_direction']=='BULLISH':output['gate']='OPEN'
    elif output['weekly_status']!='ACTIVE' or output['master_direction']=='BEARISH':output['gate']='CLOSED'
    return output


def snapshot_tickers(con,timeframe):
    table='mv_actionable_dashboard_'+timeframe.lower()
    if not exists(con,table):return set()
    fields=cols(con,table)
    if not {'symbol','reference','row_type'}.issubset(fields):return set()
    return {symbol(r[0]) for r in con.execute(
        "SELECT DISTINCT symbol FROM "+qi(table)+" WHERE UPPER(TRIM(reference))='TLT' AND UPPER(TRIM(row_type))='STOCK'")}


def collect_rows(universe,lags,cfg,daily_snap,weekly_snap):
    allrows=[]
    for sym, etfs in sorted(universe.items()):
        lag=lags.get(sym)
        reasons=[]
        if not lag:reasons.append('NO_COMPLETE_LEADLAG')
        else:
            if not lag['is_best']:reasons.append('BEST_LAG_NOT_MARKED')
            if not 0<=lag['lag_days']<=cfg['max_lag_days']:reasons.append('OUTSIDE_LAG_WINDOW')
            if lag['score']<cfg['min_score']:reasons.append('SCORE_BELOW_MINIMUM')
            if lag['hit_rate']<cfg['min_hit_rate']:reasons.append('HIT_RATE_BELOW_MINIMUM')
            if lag['samples']<cfg['min_samples'] or not lag['sample_flag']:reasons.append('SAMPLE_GATE_FAILED')
        row=dict(symbol=sym,etfs=', '.join(sorted(etfs)),
                 sectors=' / '.join(SECTOR_NAMES[e] for e in sorted(etfs)),
                 lag_days=lag['lag_days'] if lag else None,
                 hit_rate=lag['hit_rate'] if lag else None,
                 correlation=lag['correlation'] if lag else None,
                 samples=lag['samples'] if lag else None,
                 score=lag['score'] if lag else None,
                 research_status='QUALIFIED' if not reasons else 'BELOW_RESEARCH_GATE',
                 research_reason='; '.join(reasons) if reasons else 'Passed score / hit / samples / lag',
                 daily_snapshot=1 if sym in daily_snap else 0,
                 weekly_snapshot=1 if sym in weekly_snap else 0,
                 research_confidence='WATCH')
        row['research_confidence']=research_grade(row)
        allrows.append(row)
    return allrows


def export(source_path,cloud_path,config=None):
    src=Path(source_path).resolve();dst=Path(cloud_path).resolve()
    if src==dst:raise RuntimeError('Refusing to write to master SQLite source')
    if not src.is_file() or not dst.is_file():raise FileNotFoundError('Master or cloud database missing: '+str(src)+' | '+str(dst))
    cfg=dict(DEFAULT,**(config or {}))
    with closing(sqlite3.connect(src.as_uri()+'?mode=ro',uri=True)) as con:
        universe=read_universe(con)
        lags, stats=best_lags(con,universe)
        meta=find_meta(con)
        rows_out=collect_rows(universe,lags,cfg,snapshot_tickers(con,'daily'),snapshot_tickers(con,'weekly'))
    if len(rows_out)!=len(universe):raise RuntimeError('Universe export incomplete')
    if not any(r['research_status']=='QUALIFIED' for r in rows_out):raise RuntimeError('Zero qualified TLT research rows. Check source inputs; cloud DB not modified')
    stamp=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for row in rows_out:row['exported_at_utc']=stamp
    with closing(sqlite3.connect(dst)) as con:
        try:
            con.execute('BEGIN IMMEDIATE')
            con.execute('''CREATE TABLE IF NOT EXISTS mv_tlt_positive_research_watch (
                symbol TEXT PRIMARY KEY, etfs TEXT, sectors TEXT,lag_days INTEGER,
                hit_rate REAL,correlation REAL,samples INTEGER,score REAL,
                research_status TEXT NOT NULL,research_reason TEXT,
                daily_snapshot INTEGER,weekly_snapshot INTEGER,
                research_confidence TEXT,exported_at_utc TEXT NOT NULL)''')
            con.execute('''CREATE TABLE IF NOT EXISTS mv_tlt_research_export_meta (
                id INTEGER PRIMARY KEY CHECK(id=1),gate TEXT,weekly_status TEXT,
                master_direction TEXT,master_date TEXT,asof TEXT,
                universe_count INTEGER,qualified_count INTEGER,exported_at_utc TEXT,
                config_json TEXT,stats_json TEXT)''')
            con.execute('DELETE FROM '+qi(WATCH))
            columns=list(rows_out[0])
            sql='INSERT INTO '+qi(WATCH)+' ('+','.join(map(qi,columns))+') VALUES ('+','.join(':'+k for k in columns)+')'
            con.executemany(sql,rows_out)
            con.execute('DELETE FROM '+qi(META))
            con.execute('''INSERT INTO mv_tlt_research_export_meta
                (id,gate,weekly_status,master_direction,master_date,asof,universe_count,qualified_count,exported_at_utc,config_json,stats_json)
                VALUES (1,?,?,?,?,?,?,?,?,?,?)''',(
                meta['gate'],meta['weekly_status'],meta['master_direction'],meta['master_date'],meta['asof'],
                len(rows_out),sum(r['research_status']=='QUALIFIED' for r in rows_out),stamp,
                json.dumps(cfg,sort_keys=True),json.dumps(stats,sort_keys=True)))
            assert con.execute('SELECT COUNT(*) FROM '+qi(WATCH)).fetchone()[0]==len(universe)
            con.commit()
        except Exception:
            con.rollback()
            raise
    return dict(total=len(rows_out),qualified=sum(r['research_status']=='QUALIFIED' for r in rows_out),
                missing_daily=sum(not r['daily_snapshot'] for r in rows_out),
                missing_weekly=sum(not r['weekly_snapshot'] for r in rows_out),gate=meta,stats=stats,updated=stamp)


if __name__=='__main__':
    home=Path(__file__).resolve().parent
    root=home.parent
    source=root/'data'/'marketvector_tlt_tbt.sqlite'
    target=home/'data'/'actionable_cloud.sqlite'
    try:
        result=export(source,target,config_values(root))
        print('TLT RESEARCH VISIBILITY CLOUD EXPORT: PASS')
        print('TLT research universe / qualified / missing daily / missing weekly:',
              result['total'],result['qualified'],result['missing_daily'],result['missing_weekly'])
        print('Weekly gate display:',result['gate'])
    except Exception as exc:
        print('TLT RESEARCH VISIBILITY CLOUD EXPORT: FAILED:',str(exc),file=sys.stderr)
        sys.exit(1)
