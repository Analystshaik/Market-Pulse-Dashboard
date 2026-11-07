"""New *research-only* grid shared by TBT Daily and TBT Weekly client pages.

Uses their existing grid renderer, colors, hover tooltips, search and Excel/CSV.
NEVER alters, or claims to reproduce, the trading engine's Buy/Hold eligibility.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import sqlite3

import pandas as pd
import streamlit as st

WATCH='mv_tbt_positive_research_watch'
META='mv_tbt_research_gate_status'
CONTEXT='mv_tbt_research_stock_context'


def make_rows(research:pd.DataFrame, context:pd.DataFrame|None, gate:str)->pd.DataFrame:
    """One synthetic, always-Hold presentation row for EACH research ticker.

    All trade-valid flags are forced OFF; 'confidence' is the research tier,
    not the trading engine's independent confidence rating.
    """
    if research.empty:
        return pd.DataFrame()
    ctx={}
    if context is not None and not context.empty and 'symbol' in context.columns:
        ctx={str(r['symbol']).strip().upper():r for r in context.to_dict(orient='records')}
    data=[]
    known=set()
    for r in research.to_dict(orient='records'):
        symbol=str(r.get('symbol') or '').strip().upper()
        if not symbol or symbol in known:continue
        known.add(symbol)
        d=ctx.get(symbol,{})
        grade=str(r.get('research_confidence') or 'WATCH').upper()
        # No made-up price, analyst rating, reversal dates, or RSI.
        row={
            'symbol':symbol,
            'etf':r.get('etfs',''),
            'confidence':grade,
            'client_signal':'Hold',
            'client_trade_valid':0,
            'client_actionable_now':0,
            'top_actionable_flag':0,
            'client_trade_basis':'RESEARCH_ONLY',
            'actual_response_date':None,
            'client_activation_date':None,
            'client_stock_weekly_bearish_reversal_date':None,
            'client_stock_bearish_reversal_date':None,
            'client_weekly_bearish_block':0,
            'tlt_observed_lag':None,
            'tlt_expected_lag':None,
            'tlt_forecast_date':None,
            'tlt_forecast_in_days':None,
            'trade_caution_comment':(
                'Hold: TBT Weekly Buy gate CLOSED; research candidate only. '
                'Recheck Daily/Weekly reversal, trend, RSI and momentum.'
                if gate!='OPEN' else
                'Hold: Weekly gate was OPEN at export, but this is RESEARCH ONLY. '
                'All stock-specific Buy conditions require independent validation.'
            ),
        }
        for field in ('latest_close','analyst_rating','target_12m_low','target_12m_high',
                      'analyst_high_upside_pct','daily_rsi14','weekly_rsi14',
                      'daily_reversal_date','weekly_reversal_date',
                      'daily_reversal_pattern','weekly_reversal_pattern'):
            row[field]=d.get(field)
        row.update({
            '_research_lag_days':r.get('lag_days'),
            '_research_hit_rate':r.get('hit_rate'),
            '_research_correlation':r.get('correlation'),
            '_research_samples':r.get('samples'),
            '_research_score':r.get('score'),
            '_research_grade':grade,
        })
        data.append(row)
    return pd.DataFrame(data)


def build_frame(rows:pd.DataFrame,timeframe:str,frame_converter)->pd.DataFrame:
    """Produce original client columns PLUS lag/hit/corr/sample research metrics."""
    if rows.empty:return pd.DataFrame()
    # Use production grid formatter to retain reversal displays, price, analyst
    # target low/high tooltip and per-row chart navigation.
    frame=frame_converter(rows,'TBT',timeframe,False)
    # TBT does not use the TLT timing/forecast columns. Display the empirical
    # daily event lag by its real name, without pretending it is a trade date.
    frame=frame.drop(columns=['Lag vs TLT','Expected In','Expected Turn'],errors='ignore')
    frame['Signal']='Hold'
    frame['Signal Comment']='Hold: Research candidate only — independent Buy confirmation required'
    frame['Confidence']=rows['_research_grade'].map({'STRONG':'Strong','GOOD':'Good','WATCH':'Weak'}).fillna('Weak').tolist()
    frame['Research Lag']=pd.to_numeric(rows['_research_lag_days'],errors='coerce').tolist()
    frame['Hit Rate (%)']=(pd.to_numeric(rows['_research_hit_rate'],errors='coerce')*100).round(1).tolist()
    frame['Correlation']=pd.to_numeric(rows['_research_correlation'],errors='coerce').round(3).tolist()
    frame['Samples']=pd.to_numeric(rows['_research_samples'],errors='coerce').tolist()
    frame['Research Score']=pd.to_numeric(rows['_research_score'],errors='coerce').round(1).tolist()
    frame['Research Confidence']=rows['_research_grade'].tolist()
    order=list(frame.columns)
    for col in ['Research Lag','Hit Rate (%)','Correlation','Samples','Research Score','Research Confidence']:
        order.remove(col)
    idx=order.index('Sector')+1
    order[idx:idx]=['Research Lag','Hit Rate (%)','Correlation','Samples','Research Score','Research Confidence']
    return frame[order]


@st.cache_data(ttl=90,show_spinner=False)
def _load(path:str,mtime_ns:int):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as con:
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {WATCH,META}.issubset(tables):
            return pd.DataFrame(),pd.DataFrame(),None
        a=pd.read_sql_query('SELECT * FROM "'+WATCH+'"',con)
        b=pd.read_sql_query('SELECT * FROM "'+CONTEXT+'"',con) if CONTEXT in tables else pd.DataFrame()
        g=pd.read_sql_query('SELECT * FROM "'+META+'" WHERE id=1',con)
    return a,b,g.iloc[0].to_dict() if not g.empty else None


def render_research_grid(db_path,timeframe,filter_state):
    """Called at the end of BOTH TBT client dashboard render functions."""
    import client_actionable_dashboard as common
    path=Path(db_path)
    st.markdown('---')
    st.markdown("<div class='grid-title'>Potential Trades After the TBT Weekly Gate Opens — Research Watch</div>",unsafe_allow_html=True)
    st.caption('Every qualifying TBT Positive Research / Watch stock is included, even when absent from Actionable Now / On Hold or below the 25% analyst-upside trade screen. This is a research grid, NOT an automatic future Buy list.')
    if not path.is_file():
        st.warning('Research cloud DB not found. Refresh the cloud export first.')
        return
    try:
        research,context,meta=_load(str(path),path.stat().st_mtime_ns)
    except Exception as exc:
        st.warning('TBT research data cannot be read from this cloud DB; existing Buy/Hold grids are unaffected.')
        st.caption(type(exc).__name__+': '+str(exc))
        return
    if meta is None:
        st.warning('Research watch table/gate status not available. Run the research export before deploying.')
        return
    gate=str(meta.get('gate') or 'UNKNOWN').upper()
    stamp=str(meta.get('exported_at_utc') or 'unknown')
    direction=str(meta.get('master_direction') or 'UNKNOWN').upper()
    date=str(meta.get('master_date') or 'unknown date')
    if gate=='OPEN':
        st.info(f'TBT Weekly gate reported OPEN at research export ({stamp}); this list still contains only research candidates, not verified Buy signals. Latest Weekly reversal: {direction} ({date}).')
    else:
        st.error(f'TBT Weekly Buy gate CLOSED / NOT VERIFIED ({direction}, {date}). None of these candidates is marked Buy. Data as of export: {stamp}.')
    try:
        exported=pd.to_datetime(stamp,utc=True,errors='coerce')
        if pd.notna(exported) and (pd.Timestamp.now(tz='UTC')-exported).total_seconds()>7*86400:
            st.warning('Research export is more than 7 days old. Refresh before relying on this list.')
    except Exception:pass
    if research.empty:
        st.info('No research-qualified candidates are present in the export.')
        return
    model=make_rows(research,context,gate)
    frame=build_frame(model,timeframe,common._client_grid_frame)
    # Match global sidebar filters; a filter may hide candidates, but never
    # remove candidates from the underlying research data.
    frame=common._filter_grid(frame,filter_state['search'],filter_state['signals'],
                              filter_state['confidences'],filter_state['ratings'])
    st.caption(f'{len(model)} distinct research candidates in the export · {len(frame)} match the current sidebar filters · Research lag measured in trading days (not a predicted Buy date). "Confidence" is statistical research quality.')
    key=f'TBT_{timeframe}_research_watch'
    frame=common._render_grid_toolbar(frame,'TBT',timeframe,key)
    if frame.empty:
        st.info('No research stocks match the sidebar filters or grid quick search.')
        return
    height=76+max(min(len(frame),25),1)*34
    if common.AGGRID_AVAILABLE:
        common._render_aggrid(frame,'TBT',timeframe,height,key)
    else:
        common._render_native_grid_fallback(frame,'TBT',timeframe,height,key)
