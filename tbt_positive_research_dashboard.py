"""TBT Positive Research / Watch (presentation only, not a trading engine)."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

import pandas as pd
import streamlit as st

HERE=Path(__file__).resolve().parent


def resolve_db():
    raw=os.getenv('ACTIONABLE_SNAPSHOT_DB','').strip()
    if raw:
        p=Path(raw)
        return p if p.is_absolute() else HERE / p
    return HERE / 'data' / 'actionable_cloud.sqlite'


@st.cache_data(ttl=60,show_spinner=False)
def load_research(db_path,mtime_ns):
    with sqlite3.connect(f'{Path(db_path).resolve().as_uri()}?mode=ro',uri=True) as con:
        names={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'mv_tbt_positive_research_watch','mv_tbt_research_gate_status'}<=names:
            return pd.DataFrame(),None
        frame=pd.read_sql_query('SELECT * FROM mv_tbt_positive_research_watch',con)
        meta=pd.read_sql_query('SELECT * FROM mv_tbt_research_gate_status WHERE id=1',con)
    return frame,(meta.iloc[0].to_dict() if not meta.empty else None)


def render_tbt_positive_research():
    st.set_page_config(page_title='TBT Positive Research | MarketVector',page_icon='🔎',layout='wide')
    st.title('TBT Positive Research / Watch')
    st.caption('Independent research screen • not the TBT Daily or Weekly Buy/Hold dashboard')
    db=resolve_db()
    if not db.exists():
        st.error('The compact cloud database is missing. Run Cloud\\01_PREPARE_CLOUD.bat first.')
        st.stop()
    try:
        df,meta=load_research(str(db),db.stat().st_mtime_ns)
    except Exception as exc:
        st.error('Unable to load TBT research table from the cloud database.')
        st.caption(str(exc))
        st.stop()
    if meta is None:
        st.warning('Research export has not been installed into the compact database. Run the TBT research export after refreshing the cloud database.')
        st.stop()
    gate=str(meta.get('gate','UNKNOWN')).upper()
    direction=str(meta.get('master_direction','UNKNOWN'))
    date=str(meta.get('master_date') or 'not available')
    weekly_status=str(meta.get('weekly_status','UNKNOWN'))
    if gate == 'CLOSED':
        st.error(f'Weekly Buy gate CLOSED · latest master Weekly: {direction} ({date}) · weekly STATUS: {weekly_status}')
    elif gate == 'OPEN':
        st.success(f'Weekly Buy gate OPEN at export · latest master Weekly: {direction} ({date})')
    else:
        st.warning('Weekly Buy gate NOT VERIFIED — treat as CLOSED until the trading engine verifies the Weekly regime.')
    st.info('Research candidates are **not** Buy recommendations. Even when the Weekly gate opens, the existing trading dashboards must independently confirm Daily/Weekly reversals, trend, RSI, momentum, and all other eligibility rules.')
    exported=str(meta.get('exported_at_utc','unknown'))
    st.caption(f'Research snapshot created (UTC): {exported} • Displayed gate is as of that snapshot, not a live intraday signal.')
    try:
        hours=(datetime.now(timezone.utc)-pd.to_datetime(exported,utc=True).to_pydatetime()).total_seconds()/3600
        if hours>24*7:
            st.warning('This research export is over seven days old. Refresh before relying on the screen.')
    except Exception:
        pass
    if df.empty:
        st.warning('No research candidates in the exported dataset.')
        st.stop()
    df['hit_rate']=pd.to_numeric(df['hit_rate'],errors='coerce')
    df['correlation']=pd.to_numeric(df['correlation'],errors='coerce')
    df['score']=pd.to_numeric(df['score'],errors='coerce')
    df['lag_days']=pd.to_numeric(df['lag_days'],errors='coerce').fillna(-1).astype(int)
    df['samples']=pd.to_numeric(df['samples'],errors='coerce').fillna(0).astype(int)

    c1,c2,c3,c4=st.columns(4)
    c1.metric('Qualified research candidates',f'{df.symbol.nunique():,}')
    c2.metric('Represented SPDR ETFs',len(set(x.strip() for v in df.etfs for x in str(v).split(',') if x.strip())))
    c3.metric('Average hit rate',f'{df.hit_rate.mean()*100:.1f}%')
    c4.metric('Weekly Buy gate','OPEN' if gate=='OPEN' else 'CLOSED / UNVERIFIED')

    with st.expander('Research filters',expanded=True):
        c1,c2,c3,c4=st.columns([1.2,1.3,1.1,1.1])
        ticker=c1.text_input('Symbol contains','',placeholder='e.g., HD').strip().upper()
        all_etfs=sorted({x.strip() for v in df.etfs for x in str(v).split(',') if x.strip()})
        etfs=c2.multiselect('ETF sectors',all_etfs,default=[])
        tiers=c3.multiselect('Research confidence',['STRONG','GOOD','WATCH'],default=[])
        min_n=c4.number_input('Minimum samples',min_value=0,max_value=10000,value=0,step=1)
        min_lag=int(df.lag_days.min()); max_lag=int(df.lag_days.max())
        l1,l2=st.columns(2)
        lagrange=l1.slider('Lag (trading days)',min_value=min_lag,max_value=max_lag,value=(min_lag,max_lag)) if max_lag>min_lag else (min_lag,max_lag)
        min_corr=l2.slider('Minimum correlation',min_value=-1.0,max_value=1.0,value=0.0,step=0.05)
    view=df.copy()
    if ticker: view=view[view.symbol.str.contains(ticker,regex=False,na=False)]
    if etfs: view=view[view.etfs.apply(lambda v: bool(set(str(v).replace(' ','').split(','))&set(etfs)))]
    if tiers: view=view[view.research_confidence.isin(tiers)]
    view=view[(view.samples>=min_n)&(view.lag_days.between(*lagrange))&(view.correlation>=min_corr)]
    view=view.sort_values(['score','hit_rate','samples'],ascending=[False,False,False]).reset_index(drop=True)
    st.subheader(f'Watch list · {len(view)} matching candidates')
    showing=view.rename(columns={'symbol':'Symbol','etfs':'ETF','sectors':'ETF Sector','lag_days':'Lag (days)',
        'hit_rate':'Hit Rate (%)','correlation':'Correlation','samples':'Samples','score':'Research Score',
        'research_confidence':'Research Confidence'})
    keep=['Symbol','ETF','ETF Sector','Lag (days)','Hit Rate','Correlation','Samples','Research Score','Research Confidence']
    showing['Hit Rate (%)']=showing['Hit Rate (%)']*100
    keep=['Symbol','ETF','ETF Sector','Lag (days)','Hit Rate (%)','Correlation','Samples','Research Score','Research Confidence']
    st.dataframe(showing[keep],hide_index=True,use_container_width=True,
        height=min(720,max(240,40*(len(view)+1))),column_config={
            'Hit Rate (%)':st.column_config.NumberColumn(format='%.1f%%'),
            'Correlation':st.column_config.NumberColumn(format='%.3f'),
            'Research Score':st.column_config.NumberColumn(format='%.1f'),
        })
    st.download_button('Download filtered research CSV',data=showing[keep].to_csv(index=False).encode('utf-8'),
        file_name='TBT_Positive_Research_Watch.csv',mime='text/csv')
    left,right=st.columns(2)
    with left:
        st.caption('Research candidates by ETF')
        counts={e:int(df.etfs.apply(lambda v:e in [x.strip() for x in str(v).split(',')]).sum()) for e in all_etfs}
        st.bar_chart(pd.DataFrame.from_dict(counts,orient='index',columns=['Candidates']))
    with right:
        st.caption('Lag distribution (trading days)')
        st.bar_chart(df.groupby('lag_days').size().rename('Candidates'))
    with st.expander('Definitions, thresholds and safety notes'):
        try: config=json.loads(meta.get('research_config') or '{}')
        except Exception: config={}
        st.write(f"Qualification: research score ≥ {config.get('min_score','?')}, hit rate ≥ {float(config.get('min_hit_rate',0))*100:.0f}%, "
                 f"samples ≥ {config.get('min_samples','?')}, maximum lag {config.get('max_lag_days','?')} trading days; "
                 'positive correlation required.' if config.get('require_positive_correlation') else 'no positive-correlation requirement.')
        st.write('STRONG: score ≥65, hit ≥55%, correlation ≥0.25, samples ≥15. '
                 'GOOD: score ≥55, hit ≥45%, correlation ≥0.10, samples ≥10. Otherwise WATCH. '
                 'These are transparent display tiers, not statistical confidence intervals or buy permissions.')
        try: stats=json.loads(meta.get('source_stats') or '{}')
        except Exception: stats={}
        if stats: st.json(stats)
        st.write('Source tables: master mv_tbt_stock_universe_current and mv_tbt_stock_reversal_leadlag_day; '
                 'Weekly gate from master mv_actionable_dashboard_weekly STATUS. '
                 'This page never modifies trade rules or source records.')
