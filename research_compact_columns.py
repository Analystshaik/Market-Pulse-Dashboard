"""Client presentation-only compact TLT/TBT research-watch grids (v1.1).

IMPORTANT: This helper is called only by the research grids, immediately before
search/export/render. It never touches stored SQLite data or trade-state flags.
"""
from __future__ import annotations

# Previous v1.0 cleanup concealed statistics and dates, but mistakenly left
# four TLT-only research-status columns (Research Status, In Daily Snapshot,
# In Weekly Snapshot, Research Reason) in the embedded and standalone grids.
RESEARCH_DETAIL_COLUMNS = (
    "Research Status",
    "In Daily Snapshot",
    "In Weekly Snapshot",
    "Research Reason",
    "Research Lag",
    "Research Lag (days)",
    "Hit Rate (%)",
    "Correlation",
    "Samples",
    "Research Score",
    "Research Confidence",
)

RESEARCH_NONESSENTIAL_COLUMNS = (
    "Signal Comment",
    "Reversal Date",
    "Daily Reversal Date",
    "Weekly Reversal Date",
)


def compact_research_frame(frame, timeframe):
    """Hide noisy research columns from view AND this grid's CSV/XLSX export.

    Preserve Symbol, Sector, reversal PATTERN, Signal, RSI, Risk/Caution,
    confidence, analyst rating, price/target/upside, Chart, and hidden analyst
    tooltip support. Does not modify the input frame, database, or Buy rules.
    """
    if frame is None:
        return frame
    hidden = set(RESEARCH_DETAIL_COLUMNS + RESEARCH_NONESSENTIAL_COLUMNS)
    hidden.add(f"{str(timeframe).title()} Reversal Date")
    return frame.drop(columns=[c for c in frame.columns if c in hidden], errors="ignore")
