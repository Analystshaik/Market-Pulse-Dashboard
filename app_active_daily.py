"""Automatic MarketVector landing page, based on latest Weekly STATUS gate.

This is the default portal page. It switches the *visible Daily dashboard*
without changing the TLT/TBT trading rules or data. Direct launches still
require login, exactly like the other Cloud entrypoints.
"""
from __future__ import annotations

from pathlib import Path

from marketvector_auth import require_login

require_login()

import streamlit as st
from marketvector_weekly_gate import resolve_db, select_home

ROOT = Path(__file__).resolve().parent
DB_PATH = resolve_db(ROOT)
choice = select_home(DB_PATH)

# Rerun explicitly to check the current database; also re-evaluated on every
# browser refresh or page interaction. No background service is required.
with st.sidebar:
    st.caption("Home selection: latest Weekly gate")
    if st.button("Recheck Weekly Gate", key="_mv_recheck_weekly_gate"):
        st.rerun()
    st.caption(f"TLT: {choice.tlt.status}  |  TBT: {choice.tbt.status}")
    if choice.tlt.active and choice.tbt.active:
        st.warning("Both Weekly gates report ACTIVE. Home prioritizes TLT.")

if choice.reference is None:
    st.set_page_config(page_title="MarketVector · Weekly Gates", page_icon="🛡️", layout="wide")
    st.title("MarketVector · Weekly Gate Overview")
    st.warning("Neither Weekly bullish gate is currently open. The portal will not select a trading dashboard automatically.")
    st.write("**TLT:** " + choice.tlt.reason)
    st.write("**TBT:** " + choice.tbt.reason)
    st.caption("You can still open any Daily, Weekly, Market Pulse, or Monthly page from the left navigation to review the underlying signals.")
    if not DB_PATH.is_file():
        st.error("Snapshot data is unavailable. Confirm that the Cloud database was deployed and ACTIONABLE_SNAPSHOT_DB is correct.")
else:
    # Gate-selected Home uses Market Pulse Daily presentation.
    # Confirmation and Buy/Hold eligibility remain snapshot-driven.
    # MARKETVECTOR_HOME_PULSE_AND_COLUMNS_V1_0
    # Use the SAME Daily Market Pulse renderer for both TLT and TBT.
    # select_home(DB_PATH) determines the choice from confirmed Weekly STATUS.
    from market_pulse_dashboard import render_market_pulse_dashboard

    render_market_pulse_dashboard(
        reference_override=choice.reference,
        timeframe_override="DAILY",
        home_mode=True,
    )
    st.caption(f"Auto-selected {choice.reference} Daily from Weekly STATUS. {choice.explanation}. Updated when snapshot data refreshes and the app reruns.")
