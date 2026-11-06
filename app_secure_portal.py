"""MarketVector private Streamlit portal with always-visible dashboard navigation.

Portal v1.2a: keep v1.0 login and v1.2 Weekly-gate Active Daily home.
Use explicit sidebar links instead of Streamlit's automatically collapsing
navigation widget.  Do not import or read any dashboard data before login.
"""
from __future__ import annotations

from pathlib import Path

from marketvector_auth import require_login

# Access protection precedes all page discovery, routing, and data loading.
require_login(show_logout=False)

import streamlit as st

ROOT = Path(__file__).resolve().parent

HOME = ("Home · Active Daily", "app_active_daily.py", "🏠")
DASHBOARDS = [
    ("Market Pulse", "app_market_pulse.py", "📊", "market_pulse_dashboard.py"),
    ("TLT Daily", "app_tlt_actionable_daily_fast.py", "📈", "client_actionable_dashboard.py"),
    ("TLT Weekly", "app_tlt_actionable_weekly_fast.py", "📅", "client_actionable_dashboard.py"),
    ("TBT Daily", "app_tbt_actionable_daily_fast.py", "📉", "client_actionable_dashboard.py"),
    ("TBT Weekly", "app_tbt_actionable_weekly_fast.py", "🗓️", "client_actionable_dashboard.py"),
    ("Monthly TLT vs SPY/QQQ/DIA", "app_monthly_tlt_comparison.py", "📆", "monthly_tlt_comparison_dashboard.py"),
]

# This router must retain the real multipage model. st.page_link works only
# for pages included in st.navigation, and keeps the authenticated session.
if not (ROOT / HOME[1]).is_file():
    st.error("Active Daily home page is missing from the Cloud deployment.")
    st.stop()

home_page = st.Page(str(ROOT / HOME[1]), title=HOME[0], icon=HOME[2], default=True)
page_entries = [(HOME[0], home_page, HOME[2])]
missing = []
for label, filename, icon, dependency in DASHBOARDS:
    path = ROOT / filename
    if path.is_file() and (ROOT / dependency).is_file():
        page_entries.append((label, st.Page(str(path), title=label, icon=icon), icon))
    else:
        missing.append(label)

# Explicit stable sidebar links make all available destinations visible,
# even when the native Streamlit navigation collapses or is configured hidden.
page = st.navigation([entry[1] for entry in page_entries], position="hidden")
with st.sidebar:
    st.markdown("### 🧭 MarketVector Dashboards")
    st.caption("Choose any dashboard, or use the automatically selected home.")
    for label, page_obj, icon in page_entries:
        st.page_link(page_obj, label=label, icon=icon, use_container_width=True)
    if missing:
        # The user must know if files were not deployed rather than think the
        # navigation menu is hiding them.
        st.caption("Not deployed: " + ", ".join(missing))
    st.divider()

page.run()
