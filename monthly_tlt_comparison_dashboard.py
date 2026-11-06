from __future__ import annotations

if __name__ == "__main__":
    from marketvector_auth import require_login as _mv_require_login
    _mv_require_login()


from pathlib import Path
import json, os, sqlite3
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
try:
    from st_aggrid import AgGrid, GridOptionsBuilder, JsCode
    AGGRID=True
except Exception:
    AGGRID=False

APP_DIR=Path(__file__).resolve().parent
TABLES=["mv_tlt_monthly_bars","mv_tlt_reversals_month","mv_tlt_monthly_current_snapshot","mv_tlt_monthly_index_event_summary"]


def resolve_db():
    env=os.getenv("ACTIONABLE_SNAPSHOT_DB","").strip()
    if env:
        p=Path(env); return p if p.is_absolute() else APP_DIR/p
    cloud=APP_DIR/"data"/"actionable_cloud.sqlite"
    if cloud.exists():return cloud
    return APP_DIR/"data"/"marketvector_tlt_tbt.sqlite"

@st.cache_data(ttl=60)
def load_all(db_text):
    db=Path(db_text)
    if not db.exists(): return {},[f"Database not found: {db}"]
    out={}; missing=[]
    with sqlite3.connect(str(db)) as con:
        existing={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in TABLES:
            if t in existing: out[t]=pd.read_sql_query(f'SELECT * FROM "{t}"',con)
            else: missing.append(t)
    return out,missing

def css(day):
    if day:
        bg="#f5f7fa"; card="#ffffff"; text="#172033"; muted="#667085"; border="#d7dde5"
    else:
        bg="#081019"; card="#111923"; text="#eef2f7"; muted="#98a2b3"; border="#273243"
    st.markdown(f"""<style>
    .stApp{{background:{bg};color:{text}}}
    .block-container{{padding-top:1rem;max-width:1700px}}
    .mv-card{{background:{card};border:1px solid {border};border-radius:10px;padding:.7rem .9rem;min-height:86px}}
    .mv-k{{font-size:.72rem;color:{muted};text-transform:uppercase;letter-spacing:.08em}}
    .mv-v{{font-size:1.35rem;font-weight:750;color:{text};margin-top:.2rem}}
    .mv-s{{font-size:.78rem;color:{muted};margin-top:.12rem}}
    .mv-section{{font-size:1.05rem;font-weight:750;color:{text};margin:.9rem 0 .35rem}}
    div[data-testid="stMetric"]{{background:{card};border:1px solid {border};border-radius:10px;padding:.5rem .65rem}}
    </style>""",unsafe_allow_html=True)

def card(k,v,s=""):
    st.markdown(f'<div class="mv-card"><div class="mv-k">{k}</div><div class="mv-v">{v}</div><div class="mv-s">{s}</div></div>',unsafe_allow_html=True)

def pct(v,d=1):
    try:
        if pd.isna(v): return "—"
        return f"{float(v)*100:.{d}f}%" if abs(float(v))<=1.000001 else f"{float(v):.{d}f}%"
    except:return "—"

def numpct(v):
    try:return f"{float(v):+.1f}%" if pd.notna(v) else "—"
    except:return "—"

def candlestick(bars,revs,day):
    x=bars.copy(); x["bar_date"]=pd.to_datetime(x["bar_date"],errors="coerce")
    x=x.sort_values("bar_date").tail(42)
    fig=go.Figure(go.Candlestick(x=x.bar_date,open=x.open,high=x.high,low=x.low,close=x.close,name="TLT"))
    if not revs.empty:
        rr=revs[revs.confirmed==1].copy(); rr=rr[rr.signal_month.isin(set(x.month.astype(str)))]
        for direction,symbol in [("BULLISH","triangle-up"),("BEARISH","triangle-down")]:
            z=rr[rr.direction==direction]
            if not z.empty:
                ys=[]; xs=[]; texts=[]
                lookup=x.set_index("month")
                for _,r in z.iterrows():
                    if r.signal_month in lookup.index:
                        br=lookup.loc[r.signal_month]; br=br.iloc[-1] if isinstance(br,pd.DataFrame) else br
                        xs.append(br.bar_date); ys.append(float(br.low)*0.985 if direction=="BULLISH" else float(br.high)*1.015); texts.append(r.pattern)
                if xs: fig.add_trace(go.Scatter(x=xs,y=ys,mode="markers",marker={"symbol":symbol,"size":10},name=direction,text=texts,hovertemplate="%{x|%b %Y}<br>%{text}<extra></extra>"))
    fig.update_layout(height=300,margin=dict(l=8,r=8,t=25,b=8),xaxis_rangeslider_visible=False,template="plotly_white" if day else "plotly_dark",legend=dict(orientation="h",y=1.02,x=0))
    return fig

def explode_index(df):
    rows=[]
    for _,r in df.iterrows():
        for idx in str(r.get("indexes","")).split(" / "):
            if idx: d=r.to_dict(); d["index_etf"]=idx; rows.append(d)
    return pd.DataFrame(rows)

def grid(df,key,height=480):
    if df.empty: st.caption("No rows match the current filters."); return
    show=df.copy()
    if not AGGRID:
        st.dataframe(show,use_container_width=True,hide_index=True,height=height); return
    categorical=[c for c in ["Symbol","Indexes","Sector","Relationship","Best Lag","Confidence","Current Status","Expected"] if c in show.columns]
    dropdown=JsCode("""
    class MVSelectFilter { init(params){ this.params=params; this.eGui=document.createElement('select'); this.eGui.style.width='100%'; this.eGui.style.height='20px'; this.eGui.style.fontSize='11px'; const a=document.createElement('option'); a.value=''; a.textContent='All'; this.eGui.appendChild(a); (params.values||[]).forEach(v=>{const o=document.createElement('option');o.value=String(v);o.textContent=String(v);this.eGui.appendChild(o)}); this.eGui.addEventListener('change',()=>{const v=this.eGui.value;params.parentFilterInstance(inst=>{inst.setModel(v?{type:'equals',filter:v}:null);params.api.onFilterChanged();});}); } getGui(){return this.eGui;} onParentModelChanged(m){this.eGui.value=m?m.filter||'':'';} }
    """)
    gb=GridOptionsBuilder.from_dataframe(show)
    gb.configure_default_column(sortable=True,resizable=True,filter=False,minWidth=90)
    for c in categorical:
        vals=sorted(show[c].fillna("").astype(str).replace("",np.nan).dropna().unique().tolist())
        gb.configure_column(c,filter="agTextColumnFilter",floatingFilter=True,floatingFilterComponent=dropdown,floatingFilterComponentParams={"values":vals})
    for c in ["Hit Rate","Bull Hit","Bear Hit","Avg Response","Corr","Samples","Current Mo Return","Response Return"]:
        if c in show.columns: gb.configure_column(c,filter="agNumberColumnFilter")
    rowstyle=JsCode("""function(p){const s=(p.data['Current Status']||'').toString(); if(s==='RESPONDING'||s==='RESPONDED') return {'backgroundColor':'rgba(34,197,94,.10)'}; if(s.includes('DIVERG')) return {'backgroundColor':'rgba(239,68,68,.10)'}; if(s.includes('UPCOMING')||s.includes('DUE')) return {'backgroundColor':'rgba(234,179,8,.10)'}; return {}; }""")
    gb.configure_grid_options(headerHeight=27,floatingFiltersHeight=24,rowHeight=27,getRowStyle=rowstyle,suppressMenuHide=True)
    AgGrid(show,gridOptions=gb.build(),height=height,fit_columns_on_grid_load=False,allow_unsafe_jscode=True,theme="balham",key=key)

def render_monthly_tlt_comparison_dashboard():
    st.set_page_config(page_title="Monthly TLT Index Comparison",page_icon="🗓️",layout="wide")
    day=st.sidebar.toggle("☀️ Day mode",value=False,key="monthly_day_mode")
    css(day)
    db=resolve_db(); data,missing=load_all(str(db))
    st.title("Monthly TLT Reversal · SPY / QQQ / DIA Comparison")
    st.caption("Current constituent universe · Monthly candlestick event study · Best response lag 0–3 months")
    if missing:
        st.error("Monthly analysis tables are missing: "+", ".join(missing))
        st.code("python update_market_index_constituents.py\npython update_monthly_index_universe_ibkr.py\npython analyze_tlt_monthly_index_comparison.py")
        st.stop()
    snap=data["mv_tlt_monthly_current_snapshot"].copy(); rev=data["mv_tlt_reversals_month"].copy(); bars=data["mv_tlt_monthly_bars"].copy(); hist=data["mv_tlt_monthly_index_event_summary"].copy()
    confirmed=rev[(pd.to_numeric(rev.confirmed,errors="coerce")==1)&rev.effective_month.notna()].sort_values("effective_month")
    if confirmed.empty: st.error("No confirmed monthly TLT reversals are available."); st.stop()
    latest=confirmed.iloc[-1]
    pending=rev[pd.to_numeric(rev.confirmed,errors="coerce").fillna(0)==0].sort_values("signal_month")

    indexes=st.sidebar.multiselect("Index membership",["SPY","QQQ","DIA"],default=["SPY","QQQ","DIA"])
    rels=st.sidebar.multiselect("Relationship",["POSITIVE_TLT","INVERSE_TLT","NO_DATA"],default=["POSITIVE_TLT","INVERSE_TLT"])
    confs=st.sidebar.multiselect("Confidence",["STRONG","GOOD","DEVELOPING","LOW DATA","NO DATA"],default=["STRONG","GOOD","DEVELOPING","LOW DATA"])
    minhit=st.sidebar.slider("Minimum historical hit rate",0,100,0,5)
    search=st.sidebar.text_input("Search symbol / company","").strip().upper()

    f=snap.copy()
    if indexes: f=f[f.indexes.fillna("").map(lambda s:any(i in str(s).split(" / ") for i in indexes))]
    if rels: f=f[f.relationship_type.isin(rels)]
    if confs: f=f[f.confidence.isin(confs)]
    f=f[pd.to_numeric(f.hit_rate,errors="coerce").fillna(0)*100>=minhit]
    if search: f=f[f.symbol.astype(str).str.upper().str.contains(search)|f.company.astype(str).str.upper().str.contains(search)]

    c=st.columns([1.15,1.35,1,1,1])
    with c[0]: card("Latest TLT monthly signal",latest.direction,latest.signal_month)
    with c[1]: card("Reversal pattern",latest.pattern,f"Effective {latest.effective_month}")
    with c[2]: card("Universe coverage",f"{(snap.relationship_type!='NO_DATA').sum()} / {len(snap)}","unique current stocks")
    with c[3]: card("Positive followers",str((snap.relationship_type=='POSITIVE_TLT').sum()),"best historical relationship")
    with c[4]: card("Inverse followers",str((snap.relationship_type=='INVERSE_TLT').sum()),"best historical relationship")
    if not pending.empty and str(pending.iloc[-1].signal_month)>=str(latest.signal_month):
        p=pending.iloc[-1]; st.warning(f"Pending monthly reversal confirmation: {p.direction} · {p.pattern} · {p.signal_month} — {p.confirmation_note}")

    st.markdown('<div class="mv-section">TLT monthly reversal map</div>',unsafe_allow_html=True)
    st.plotly_chart(candlestick(bars,rev,day),use_container_width=True,config={"displayModeBar":False})

    tabs=st.tabs(["Current Signal","All Stocks","TLT Reversal History","Index Event History","Method"])
    with tabs[0]:
        ex=explode_index(f)
        sums=[]
        for idx in indexes or ["SPY","QQQ","DIA"]:
            z=ex[ex.index_etf==idx] if not ex.empty else pd.DataFrame()
            valid=z[z.response_alignment.isin(["ALIGNED","INVERSE","FLAT"])] if not z.empty else z
            sums.append({"Index":idx,"Stocks":len(z),"Aligned %":100*(valid.response_alignment.eq("ALIGNED").mean() if len(valid) else np.nan),"Avg Hit %":100*(pd.to_numeric(z.hit_rate,errors="coerce").mean() if len(z) else np.nan)})
        sm=pd.DataFrame(sums)
        a,b=st.columns(2)
        with a:
            fig=go.Figure(go.Bar(x=sm.Index,y=sm["Aligned %"],text=sm["Aligned %"].map(lambda v:f"{v:.0f}%" if pd.notna(v) else "—"),textposition="auto")); fig.update_layout(title="Response aligned with expected relationship",height=280,template="plotly_white" if day else "plotly_dark",margin=dict(l=5,r=5,t=45,b=5),yaxis_title="Percent")
            st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False})
        with b:
            z=f.dropna(subset=["best_lag_months"]); counts=z.best_lag_months.astype(int).value_counts().sort_index(); fig=go.Figure(go.Bar(x=[f"Lag {i}" for i in counts.index],y=counts.values,text=counts.values,textposition="auto")); fig.update_layout(title="Best monthly response lag",height=280,template="plotly_white" if day else "plotly_dark",margin=dict(l=5,r=5,t=45,b=5),yaxis_title="Stocks")
            st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False})
        current=f[f.current_status.ne("PAST WINDOW")].copy()
        current=current.sort_values(["confidence","hit_rate"],ascending=[True,False])
        view=pd.DataFrame({"Symbol":current.symbol,"Indexes":current.indexes,"Company":current.company,"Sector":current.sector,"Relationship":current.relationship_type,"Best Lag":current.best_lag_months,"Hit Rate":pd.to_numeric(current.hit_rate,errors="coerce")*100,"Bull Hit":pd.to_numeric(current.bullish_hit_rate,errors="coerce")*100,"Bear Hit":pd.to_numeric(current.bearish_hit_rate,errors="coerce")*100,"Avg Response":current.avg_response_pct,"Corr":current.corr_signal,"Samples":current.samples,"Confidence":current.confidence,"Expected":current.expected_stock_direction,"Current Status":current.current_status,"Current Mo Return":current.current_month_return_pct,"Response Return":current.response_month_return_pct})
        st.markdown('<div class="mv-section">Stocks in the current 0–3 month response window</div>',unsafe_allow_html=True)
        grid(view,"monthly_current",520)
    with tabs[1]:
        allv=pd.DataFrame({"Symbol":f.symbol,"Indexes":f.indexes,"Company":f.company,"Sector":f.sector,"Relationship":f.relationship_type,"Best Lag":f.best_lag_months,"Hit Rate":pd.to_numeric(f.hit_rate,errors="coerce")*100,"Bull Hit":pd.to_numeric(f.bullish_hit_rate,errors="coerce")*100,"Bear Hit":pd.to_numeric(f.bearish_hit_rate,errors="coerce")*100,"Avg Response":f.avg_response_pct,"Corr":f.corr_signal,"Samples":f.samples,"Confidence":f.confidence,"Expected":f.expected_stock_direction,"Current Status":f.current_status,"Current Mo Return":f.current_month_return_pct,"Response Return":f.response_month_return_pct})
        allv=allv.sort_values(["Hit Rate","Samples"],ascending=[False,False]); grid(allv,"monthly_all",650)
        st.download_button("Download filtered CSV",allv.to_csv(index=False).encode(),"monthly_tlt_spy_qqq_dia.csv","text/csv")
    with tabs[2]:
        rr=rev.sort_values("signal_month",ascending=False).copy(); rr["Confirmed"]=pd.to_numeric(rr.confirmed,errors="coerce").map({1:"Yes",0:"Pending"}); st.dataframe(rr[["signal_month","effective_month","direction","pattern","trend_context","Confirmed","confirmation_note","tlt_close"]],use_container_width=True,hide_index=True)
    with tabs[3]:
        hh=hist.copy(); hh["aligned_breadth_pct"]=pd.to_numeric(hh.aligned_breadth,errors="coerce")*100
        choice=st.selectbox("Event lag to compare",[0,1,2,3],index=1,key="event_lag")
        h=hh[hh.lag_months==choice].sort_values(["effective_month","index_etf"],ascending=[False,True])
        st.dataframe(h[["index_etf","signal_month","effective_month","direction","pattern","sample_count","aligned_breadth_pct","average_return_pct","median_return_pct"]],use_container_width=True,hide_index=True)
    with tabs[4]:
        st.markdown("""
**How the analysis works**

- Daily OHLC bars are aggregated into calendar-month candles.
- TLT bullish and bearish reversal candles are detected with the same pattern families used elsewhere in the project: engulfing, key/outside reversal, hammer/shooting star, piercing/dark-cloud, harami, and morning/evening star.
- Dark Cloud Cover follows your existing rule: next-month confirmation is required only when the monthly TLT trend is UP.
- Each stock is tested at lag 0, 1, 2, and 3 months after every confirmed TLT monthly reversal.
- `POSITIVE_TLT` means the stock historically moves in the same direction as the TLT reversal signal more often; `INVERSE_TLT` means it more often moves opposite.
- Best Lag is chosen by directional hit rate, then correlation strength, average oriented response, then shorter lag.
- The constituent universe is the **current** SPY/S&P 500, QQQ/Nasdaq-100, and DIA/Dow 30 membership. Historical event statistics therefore have current-constituent survivorship bias; the dashboard does not pretend historical membership was identical.
        """)
        st.caption(f"Database: {db}")

if __name__=="__main__": render_monthly_tlt_comparison_dashboard()
