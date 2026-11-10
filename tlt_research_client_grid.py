"""Presentation-only TLT research universe for both Daily/Weekly dashboards.

The research module does not grant or modify trade eligibility. All synthesized
client rows are always Hold and independent of authoritative snapshot Buy logic.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
import pandas as pd
import streamlit as st

WATCH='mv_tlt_positive_research_watch'
META='mv_tlt_research_export_meta'
CONTEXT='mv_tlt_research_stock_context'


def make_rows(research, context, gate):
    if research is None or research.empty:return pd.DataFrame()
    lookup={}
    if context is not None and not context.empty and 'symbol' in context.columns:
        lookup={str(row['symbol']).strip().upper():row for row in context.to_dict('records')}
    out=[]
    for rec in research.to_dict('records'):
        symbol=str(rec.get('symbol') or '').upper().strip()
        if not symbol:continue
        d=lookup.get(symbol,{})
        status=str(rec.get('research_status') or 'BELOW_RESEARCH_GATE').upper()
        grade=str(rec.get('research_confidence') or 'WATCH').upper()
        reason=str(rec.get('research_reason') or '')
        row={
            'symbol':symbol,
            'etf':rec.get('etfs',''),
            'confidence':grade if grade in ('STRONG','GOOD') else 'WATCH',
            'client_signal':'Hold',
            'client_trade_valid':0,
            'client_actionable_now':0,
            'top_actionable_flag':0,
            'client_trade_basis':'RESEARCH_ONLY',
            'client_activation_date':None,
            'actual_response_date':None,
            'client_weekly_bearish_block':0,
            'tlt_observed_lag':None,
            'tlt_expected_lag':None,
            'tlt_forecast_date':None,
            'tlt_forecast_in_days':None,
            'trade_caution_comment':(
                'Hold: Research only. '+('Statistics below research screen. ' if status!='QUALIFIED' else '')+
                'TLT Weekly gate at export: '+str(gate).upper()+'. '+
                'Stock-specific reversal, trend, RSI, momentum and analyst eligibility still required.'
            ),
        }
        for field in ('latest_close','analyst_rating','target_12m_low','target_12m_high',
                      'daily_rsi14','weekly_rsi14','daily_reversal_date',
                      'weekly_reversal_date','daily_reversal_pattern','weekly_reversal_pattern'):
            row[field]=d.get(field)
        # Current price and high target recompute upside to prevent stale targets.
        row['analyst_high_upside_pct']=None
        row.update({
            '_research_status':status,
            '_research_reason':reason,
            '_research_lag':rec.get('lag_days'),
            '_research_hit':rec.get('hit_rate'),
            '_research_corr':rec.get('correlation'),
            '_research_samples':rec.get('samples'),
            '_research_score':rec.get('score'),
            '_research_grade':grade,
            '_daily_snapshot':rec.get('daily_snapshot'),
            '_weekly_snapshot':rec.get('weekly_snapshot'),
        })
        out.append(row)
    return pd.DataFrame(out)


def frame_from_rows(rows,timeframe,frame_converter):
    if rows.empty:return pd.DataFrame()
    frame=frame_converter(rows,'TLT',timeframe,False)
    frame=frame.drop(columns=['Lag vs TLT','Expected In','Expected Turn'],errors='ignore')
    frame['Signal']='Hold'
    frame['Signal Comment']='Hold: Research/watch only — no automated Buy eligibility'
    frame['Research Status']=rows['_research_status'].map({'QUALIFIED':'Qualified','BELOW_RESEARCH_GATE':'Below Gate'}).fillna('Below Gate').tolist()
    frame['Research Reason']=rows['_research_reason'].tolist()
    frame['Research Lag (days)']=pd.to_numeric(rows['_research_lag'],errors='coerce').tolist()
    frame['Hit Rate (%)']=(pd.to_numeric(rows['_research_hit'],errors='coerce')*100).round(1).tolist()
    frame['Correlation']=pd.to_numeric(rows['_research_corr'],errors='coerce').round(3).tolist()
    frame['Samples']=pd.to_numeric(rows['_research_samples'],errors='coerce').tolist()
    frame['Research Score']=pd.to_numeric(rows['_research_score'],errors='coerce').round(2).tolist()
    frame['Research Confidence']=rows['_research_grade'].tolist()
    frame['In Daily Snapshot']=rows['_daily_snapshot'].map({1:'Yes',0:'No'}).fillna('No').tolist()
    frame['In Weekly Snapshot']=rows['_weekly_snapshot'].map({1:'Yes',0:'No'}).fillna('No').tolist()
    columns=list(frame.columns)
    research_fields=['Research Status','Research Lag (days)','Hit Rate (%)',
                     'Correlation','Samples','Research Score','Research Confidence',
                     'In Daily Snapshot','In Weekly Snapshot','Research Reason']
    for col in research_fields:columns.remove(col)
    pos=columns.index('Sector')+1
    columns[pos:pos]=research_fields
    return frame[columns]


@st.cache_data(ttl=90,show_spinner=False)
def load(db_path,mtime):
    path=Path(db_path)
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as con:
        present={r[0] for r in con.execute('SELECT name FROM sqlite_master WHERE type="table"')}
        if WATCH not in present or META not in present:
            return pd.DataFrame(),pd.DataFrame(),None
        research=pd.read_sql_query('SELECT * FROM "'+WATCH+'"',con)
        context=pd.read_sql_query('SELECT * FROM "'+CONTEXT+'"',con) if CONTEXT in present else pd.DataFrame()
        info=pd.read_sql_query('SELECT * FROM "'+META+'" WHERE id=1',con)
    return research,context,info.iloc[0].to_dict() if len(info) else None


def render_research_grid(db_path,timeframe,filter_state,standalone=False):
    import client_actionable_dashboard as common
    if not standalone:st.divider()
    st.subheader('TLT Positive Research Universe — Not Yet Trade-Qualified')
    st.caption('Full current TLT sector research universe, including sectors absent from the Actionable and On Hold snapshots. All rows remain research-only. Qualified indicates a statistical research screen, not an actionable Buy.')
    path=Path(db_path)
    if not path.is_file():
        st.warning('Research cloud database missing. Run 01_PREPARE_CLOUD.bat first.')
        return
    try:research,context,meta=load(str(path),path.stat().st_mtime_ns)
    except Exception as exc:
        st.error('TLT research tables could not be opened. Existing trading grids are unaffected.')
        st.caption(str(type(exc).__name__)+': '+str(exc))
        return
    if meta is None:
        st.warning('TLT research export missing. Run 01_PREPARE_CLOUD.bat, verify PASS, then redeploy.')
        return
    gate=str(meta.get('gate') or 'UNKNOWN').upper()
    stamp=str(meta.get('exported_at_utc') or '')
    direction=str(meta.get('master_direction') or 'UNKNOWN')
    if gate=='OPEN':
        st.info(f'TLT Weekly gate: OPEN at research export. Research candidates still require independent trade qualification. Latest master direction: {direction}; export: {stamp}.')
    elif gate=='CLOSED':
        st.error(f'TLT Weekly gate: CLOSED at export ({direction}). All research rows remain Hold. Export: {stamp}.')
    else:
        st.warning(f'TLT Weekly gate: NOT VERIFIED ({direction}). All research rows remain Hold. Export: {stamp}.')
    try:
        asof=pd.to_datetime(stamp,utc=True,errors='coerce')
        if pd.notna(asof) and (pd.Timestamp.now(tz='UTC')-asof).total_seconds()>7*86400:
            st.warning('Research export is over seven days old. Refresh data before interpretation.')
    except Exception:pass
    if research.empty:
        st.warning('Research export is empty. Do not treat this as evidence of zero followers.')
        return
    rows=make_rows(research,context,gate)
    qualified=int(rows['_research_status'].eq('QUALIFIED').sum())
    missing_daily=int(pd.to_numeric(rows['_daily_snapshot'],errors='coerce').fillna(0).eq(0).sum())
    missing_weekly=int(pd.to_numeric(rows['_weekly_snapshot'],errors='coerce').fillna(0).eq(0).sum())
    c1,c2,c3,c4=st.columns(4)
    c1.metric('TLT research stocks',len(rows))
    c2.metric('Pass research screen',qualified)
    c3.metric('Not in Daily snapshot',missing_daily)
    c4.metric('Not in Weekly snapshot',missing_weekly)
    filter_state=filter_state or {'search':'','signals':[],'confidences':[],'ratings':[]}
    frame=frame_from_rows(rows,timeframe,common._client_grid_frame)
    frame=common._filter_grid(frame,filter_state.get('search',''),filter_state.get('signals',[]),
                             filter_state.get('confidences',[]),filter_state.get('ratings',[]))
    st.caption(f'No 25% upside filter is applied to this grid. {len(frame)} rows match sidebar filters.')
    key=f'TLT_{timeframe}_tlt_research_watch'
    # RESEARCH_GRID_COMPACT_COLUMNS_v1.0 — watch-grid-only display, no DB changes
    from research_compact_columns import compact_research_frame
    frame = compact_research_frame(frame, timeframe)
    frame=common._render_grid_toolbar(frame,'TLT',timeframe,key)
    if frame.empty:
        st.info('No research rows match the current filters.')
        return
    height=76+max(1,min(25,len(frame)))*34
    # The base renderer uses reference to control timing-column visibility.
    # TLT research timing is DAYS, not a validated weekly forecast; pass the
    # non-TLT timing mode only to the table renderer to avoid fake W units.
    if common.AGGRID_AVAILABLE:
        common._render_aggrid(frame,'TBT',timeframe,height,key)
    else:
        common._render_native_grid_fallback(frame,'TBT',timeframe,height,key)
