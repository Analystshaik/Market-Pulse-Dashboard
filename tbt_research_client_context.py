"""Read-only enrichment of TBT research candidates for the separate client grids.

The source is always opened in SQLite read-only mode. This module publishes
market context, NEVER classifications or actionable trading decisions.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3
import math
import sys
from collections import defaultdict

SOURCE_CONTEXT_TABLE = 'mv_tbt_research_stock_context'
RESEARCH_TABLE = 'mv_tbt_positive_research_watch'
FIELDS = (
    'symbol','latest_close','analyst_rating','target_12m_low','target_12m_high',
    'analyst_high_upside_pct','daily_rsi14','weekly_rsi14',
    'daily_reversal_date','weekly_reversal_date',
    'daily_reversal_pattern','weekly_reversal_pattern',
    'price_asof','context_source','exported_at_utc'
)
NUMERIC = ('latest_close','target_12m_low','target_12m_high','analyst_high_upside_pct','daily_rsi14','weekly_rsi14')
SNAPSHOT_FIELDS = (
    'latest_close','entry_price','current_price','analyst_rating',
    'target_12m_low','target_12m_high','analyst_high_upside_pct',
    'daily_rsi14','weekly_rsi14','daily_reversal_date','weekly_reversal_date',
    'daily_reversal_pattern','weekly_reversal_pattern'
)


def ident(name):
    return '"'+str(name).replace('"','""')+'"'


def present(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() is not None


def columns(con, table):
    return [c[1] for c in con.execute('PRAGMA table_info('+ident(table)+')')]


def numeric(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError):
        return None


def nonempty(v):
    return v is not None and str(v).strip() not in ('','None','nan','NaN','NaT','NULL')


def pick(raw, candidates):
    keys={str(k).lower():k for k in raw}
    for key in candidates:
        col=keys.get(key.lower())
        if col is not None and nonempty(raw[col]): return raw[col]
    return None


def _snapshot_context(master, symbols):
    """Use TBT rows for reference-specific reversals; other snapshots for universal market context."""
    context={sym:{'symbol':sym} for sym in symbols}
    for timeframe in ('daily','weekly'):
        table='mv_actionable_dashboard_'+timeframe
        if not present(master,table):continue
        fields=columns(master,table)
        if not {'symbol','reference','row_type'}.issubset(fields):continue
        needed=['symbol','reference']+[f for f in SNAPSHOT_FIELDS if f in fields]
        stmt='SELECT '+','.join(map(ident,needed))+' FROM '+ident(table)+" WHERE UPPER(TRIM(row_type))='STOCK' AND UPPER(TRIM(reference)) IN ('TBT','TLT')"
        items=[]
        for row in master.execute(stmt):
            d=dict(zip(needed,row))
            sym=str(d.get('symbol') or '').strip().upper()
            if sym in context:items.append(d)
        # More relevant TBT rows take precedence; never assign TLT-based reversal
        # signals to research-only TBT candidates.
        items.sort(key=lambda d:(0 if str(d.get('reference','')).upper()=='TBT' else 1))
        for r in items:
            sym=str(r['symbol']).upper().strip()
            dst=context[sym]
            origin=str(r['reference']).upper().strip()
            for k in SNAPSHOT_FIELDS:
                if k in ('entry_price','current_price'):continue
                if (k.endswith('_reversal_date') or k.endswith('_reversal_pattern')) and origin!='TBT':continue
                if not nonempty(dst.get(k)) and nonempty(r.get(k)):
                    dst[k]=r[k]
                    dst['context_source']='TBT snapshot' if origin=='TBT' else 'Market snapshot'
            if not nonempty(dst.get('latest_close')):
                for alias in ('entry_price','current_price'):
                    if nonempty(r.get(alias)):
                        dst['latest_close']=r[alias];break
    return context


def _wilder_rsi(closes, length=14):
    """Standard Wilder RSI from settled closes. For display context only."""
    if len(closes) < length+1:return None
    diffs=[closes[i]-closes[i-1] for i in range(1,len(closes))]
    ups=[max(v,0.0) for v in diffs]
    downs=[max(-v,0.0) for v in diffs]
    gain=sum(ups[:length])/length
    loss=sum(downs[:length])/length
    for up,down in zip(ups[length:],downs[length:]):
        gain=(gain*(length-1)+up)/length
        loss=(loss*(length-1)+down)/length
    if loss==0:return 100.0 if gain>0 else 50.0
    return round(100.0-(100.0/(1.0+gain/loss)),5)


def _market_date(value):
    raw=str(value).strip()[:10]
    try:
        return datetime.strptime(raw, '%Y%m%d' if raw.isdigit() and len(raw)==8 else '%Y-%m-%d').date()
    except ValueError:return None


def _analyst_targets_context(master,context):
    """Optional analyst source enrichment; never invent ratings or targets.

    Read only clearly named analyst-target tables with explicit ticker fields;
    the existing trade snapshot always takes precedence over these sources.
    """
    names=[r[0] for r in master.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    candidates=[t for t in names if 'analyst' in t.lower() and 'target' in t.lower()
                and t not in ('mv_actionable_dashboard_daily','mv_actionable_dashboard_weekly')]
    symbols=set(context)
    options={}
    for table in candidates:
        raw_cols=columns(master,table)
        lower={c.lower():c for c in raw_cols}
        def find(*names):
            return next((lower[x] for x in names if x in lower),None)
        symbol_col=find('symbol','ticker','stock_symbol','stock_ticker','ticker_symbol')
        high=find('target_12m_high','target_high','high_target','target_price_high','analyst_target_high','price_target_high','high_price_target')
        low=find('target_12m_low','target_low','low_target','target_price_low','analyst_target_low','price_target_low','low_price_target')
        rating=find('analyst_rating','recommendation','recommendation_key','analyst_recommendation')
        updated=find('fetched_at','updated_at','asof','as_of','target_date','data_date')
        if not symbol_col or not (high or low or rating):continue
        select=[symbol_col]+[x for x in (high,low,rating,updated) if x]
        # Qualify and parameterize symbols; do not scan unrelated user tables.
        syms=sorted(symbols)
        for k in range(0,len(syms),400):
            chunk=syms[k:k+400]
            where=ident(symbol_col)+' IN ('+','.join('?' for _ in chunk)+')'
            sql='SELECT '+','.join(map(ident,select))+' FROM '+ident(table)+' WHERE '+where
            for item in master.execute(sql,chunk):
                row=dict(zip(select,item))
                sym=str(row[symbol_col] or '').strip().upper()
                if sym not in symbols:continue
                record={
                    'target_12m_high':numeric(row.get(high)) if high else None,
                    'target_12m_low':numeric(row.get(low)) if low else None,
                    'analyst_rating':str(row.get(rating)).strip() if rating and nonempty(row.get(rating)) else None,
                }
                if record['target_12m_high'] is not None and record['target_12m_high']<=0:
                    record['target_12m_high']=None
                if record['target_12m_low'] is not None and record['target_12m_low']<=0:
                    record['target_12m_low']=None
                complete=sum(nonempty(v) for v in record.values())
                if not complete:continue
                timestamp=str(row.get(updated) or '') if updated else ''
                rank=(timestamp,complete)
                if sym not in options or rank>options[sym][0]:
                    options[sym]=(rank,record,table)
    for sym,(_,values,source) in options.items():
        dst=context[sym]
        changed=False
        for field,value in values.items():
            if not nonempty(dst.get(field)) and nonempty(value):
                dst[field]=value
                changed=True
        if changed:
            prior=dst.get('context_source')
            dst['context_source']=(str(prior)+' + '+source) if nonempty(prior) else source


def _price_and_rsi_context(master,context):
    """Bulk-read reference bars; fill quote and RSI without guessing Buy signals."""
    table='relationship_bars_day'
    if not present(master,table):return
    fields=columns(master,table)
    if not {'symbol','close','bar_date'}.issubset(fields):return
    symbols=sorted(context)
    history=defaultdict(dict)  # symbol -> {date: close}; dedupe exact daily dates
    # SQLite variable bound is conservatively 400 per chunk.
    for i in range(0,len(symbols),400):
        chunk=symbols[i:i+400]
        placeholders=','.join('?' for _ in chunk)
        sql=('SELECT symbol,bar_date,close FROM '+ident(table)+' WHERE symbol IN ('+placeholders+')')
        for sym,day,close in master.execute(sql,chunk):
            ticker=str(sym or '').upper().strip()
            d=_market_date(day)
            price=numeric(close)
            if ticker in context and d is not None and price is not None and price>0:
                history[ticker][d]=price
    for ticker,days in history.items():
        if not days:continue
        vals=sorted(days.items())
        dst=context[ticker]
        latest_date,latest_close=vals[-1]
        dst['latest_close']=latest_close
        # Previously stored upside percentages may use an older price.
        # Let the client grid recompute high-target upside from this close.
        dst['analyst_high_upside_pct']=None
        dst['price_asof']=latest_date.isoformat()
        closes=[v for _,v in vals]
        if not nonempty(dst.get('daily_rsi14')):
            dst['daily_rsi14']=_wilder_rsi(closes)
        if not nonempty(dst.get('weekly_rsi14')):
            weekly={}
            for day,price in vals:
                # Date of the Friday belonging to this market week.
                end=day+timedelta(days=(4-day.weekday())%7)
                weekly[end]=price
            weekly_closes=[v for _,v in sorted(weekly.items())]
            dst['weekly_rsi14']=_wilder_rsi(weekly_closes)
        dst['context_source']=('relationship_bars_day + snapshot'
            if nonempty(dst.get('context_source')) else 'relationship_bars_day')


def collect(master,symbols):
    data=_snapshot_context(master,symbols)
    _price_and_rsi_context(master,data)
    _analyst_targets_context(master,data)
    now=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    records=[]
    for symbol in sorted(symbols):
        raw=data[symbol]
        rec={f:None for f in FIELDS}
        for k in FIELDS:
            if k in raw:rec[k]=raw[k]
        for field in NUMERIC:rec[field]=numeric(rec[field])
        rec['symbol']=symbol
        rec['exported_at_utc']=now
        records.append(rec)
    return records


def export(source_path,cloud_path):
    src=Path(source_path).resolve();dst=Path(cloud_path).resolve()
    if src==dst:raise ValueError('Refusing to write to master source DB')
    if not src.is_file() or not dst.is_file():raise FileNotFoundError('Source or compact cloud SQLite database is missing')
    with closing(sqlite3.connect(dst.as_uri()+'?mode=ro',uri=True)) as cloud:
        if not present(cloud,RESEARCH_TABLE):raise RuntimeError('Research watch table missing; run ADD_TBT_RESEARCH_TO_CLOUD.py first')
        symbols={str(r[0]).strip().upper() for r in cloud.execute('SELECT symbol FROM '+ident(RESEARCH_TABLE))}
    if not symbols:raise RuntimeError('Research watch table is empty; refusing to publish empty context')
    with closing(sqlite3.connect(src.as_uri()+'?mode=ro',uri=True)) as master:
        records=collect(master,symbols)
    with closing(sqlite3.connect(dst)) as cloud:
        try:
            cloud.execute('BEGIN IMMEDIATE')
            cloud.execute('''CREATE TABLE IF NOT EXISTS mv_tbt_research_stock_context (
                symbol TEXT PRIMARY KEY,latest_close REAL,analyst_rating TEXT,
                target_12m_low REAL,target_12m_high REAL,analyst_high_upside_pct REAL,
                daily_rsi14 REAL,weekly_rsi14 REAL,daily_reversal_date TEXT,
                weekly_reversal_date TEXT,daily_reversal_pattern TEXT,
                weekly_reversal_pattern TEXT,price_asof TEXT,context_source TEXT,
                exported_at_utc TEXT NOT NULL)''')
            cloud.execute('DELETE FROM '+ident(SOURCE_CONTEXT_TABLE))
            cloud.executemany('INSERT INTO '+ident(SOURCE_CONTEXT_TABLE)+' ('+','.join(map(ident,FIELDS))+') VALUES ('+','.join(':'+f for f in FIELDS)+')',records)
            n=cloud.execute('SELECT COUNT(*) FROM '+ident(SOURCE_CONTEXT_TABLE)).fetchone()[0]
            if n!=len(symbols):raise RuntimeError('Incomplete research context export; transaction rolled back')
            cloud.commit()
        except Exception:
            cloud.rollback();raise
    return dict(candidates=len(symbols),context=n,prices=sum(d['latest_close'] is not None for d in records),analyst_targets=sum(d['target_12m_high'] is not None for d in records),date=records[0]['exported_at_utc'])


if __name__=='__main__':
    home=Path(__file__).resolve().parent
    root=home.parent
    src=root/'data'/'marketvector_tlt_tbt.sqlite'
    dst=home/'data'/'actionable_cloud.sqlite'
    try:
        result=export(src,dst)
        print('TBT RESEARCH CLIENT GRID CONTEXT: PASS',result)
        print('Missing quote/analyst/reversal details remain blank rather than inferred.')
    except Exception as exc:
        print('TBT RESEARCH CLIENT GRID CONTEXT: FAILED:',str(exc),file=sys.stderr)
        sys.exit(1)
