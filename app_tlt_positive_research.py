"""Protected TLT research page; no Buy generation or database writes."""
from marketvector_auth import require_login as _require_login
_require_login()

import streamlit as st
from client_actionable_dashboard import resolve_db
from tlt_research_client_grid import render_research_grid

st.title('TLT Positive Research / Watch')
st.caption('Research universe and missing-snapshot diagnostics; not a list of actionable Buy signals.')
timeframe=st.radio('Context timeframe',('DAILY','WEEKLY'),horizontal=True,key='tlt_research_context_timeframe')
with st.sidebar:
    st.markdown('### TLT Research Filters')
    search=st.text_input('Research symbol or sector',key='tlt_research_sidebar_search')
    st.caption('For sorting, filtering and exports, use the grid controls below.')
render_research_grid(resolve_db(),timeframe,
                     {'search':search,'signals':[],'confidences':[],'ratings':[]},
                     standalone=True)
