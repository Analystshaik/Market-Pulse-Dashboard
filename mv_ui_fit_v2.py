"""MarketVector presentation-only responsive trading grids (v2).

Never changes source DataFrames, database records, eligibility, or Research/Watch grids.
"""
from __future__ import annotations

import re

try:
    from st_aggrid import JsCode
except ImportError:  # Lets tests and Streamlit native fallback import the module.
    JsCode = lambda code: code

RESEARCH_KEY = "research_watch"
FULL_RISK = "_MV Risk Detail"

# The baseline widths preserve legibility at desktop sizes; AG Grid's
# sizeColumnsToFit distributes extra/remaining space within these minima.
WIDTHS = {
    "#": (38, 32),
    "Symbol": (74, 48),
    "Sector": (110, 65),
    "Trend Status": (115, 72),
    "Daily Reversal": (100, 62),
    "Weekly Reversal": (100, 62),
    "RSI D/W": (85, 62),
    "Risk / Caution": (195, 115),
    "Lag vs TLT": (70, 48),
    "Expected In": (86, 55),
    "Expected Turn": (98, 58),
    "Signal": (64, 46),
    "Confidence": (88, 56),
    "Analyst Rating": (101, 68),
    "Current Price": (90, 66),
    "Analyst High Target": (116, 75),
    "Upside Potential": (85, 62),
    "Chart": (82, 58),
}

# Keep previously requested hidden columns hidden in the trading grids only.
HIDE_TRADE_COLUMNS = {
    "Daily Reversal Date", "Weekly Reversal Date", "Signal Comment",
    "_Analyst Low", "_Analyst High", "_Target Tooltip", "_Quality", FULL_RISK,
    "Analyst Median",
}


def is_research_grid(key) -> bool:
    return RESEARCH_KEY in str(key).lower()


def short_risk(value) -> str:
    """Short screen label. Full, unchanged text remains in tooltip and exports."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s or s.lower() in {"none", "nan", "nat", "—"}:
        return ""
    t = s.casefold()
    if "weekly" in t and any(z in t for z in ("thesis inactive", "gate closed", "thesis not active", "weekly gate")):
        return "Hold: weekly gate"
    if any(z in t for z in ("bearish reversal", "reversal bearish")):
        return "Hold: bearish reversal" if t.startswith("hold") else "Bearish reversal"
    if "trend" in t and any(z in t for z in ("broken", "break", "below")):
        return "Hold: trend break" if t.startswith("hold") else "Trend weakening"
    if "research" in t and any(z in t for z in ("pending", "confirmation", "qualified")):
        return "Hold: confirmation"
    if "weekly" in t and "rsi" in t and any(z in t for z in ("overbought", "above 70", ">70")):
        return "Weekly RSI high"
    if "daily" in t and "rsi" in t and any(z in t for z in ("overbought", "above 70", ">70")):
        return "Daily RSI high"
    if "momentum" in t and any(z in t for z in ("slowing", "fading", "weakening")):
        return "Momentum fading"
    if "hold" in t[:8]:
        detail = re.sub(r"^hold\s*:\s*", "", s, flags=re.I)
        return "Hold: " + (detail[:21].rstrip() + "…" if len(detail) > 21 else detail)
    if len(s) > 29:
        return s[:28].rstrip() + "…"
    return s


def compact_risk_grid(frame, key):
    """Copy just the rendered grid and retain original caution text in a hidden field."""
    if is_research_grid(key) or "Risk / Caution" not in frame.columns:
        return frame
    output = frame.copy()
    if FULL_RISK not in output.columns:
        output[FULL_RISK] = output["Risk / Caution"]
    output["Risk / Caution"] = output[FULL_RISK].map(short_risk)
    return output


def responsive_options(options, reference, timeframe, key):
    """Fit visible trading columns on load and whenever the browser width changes."""
    if is_research_grid(key):
        return options
    if not isinstance(options, dict):
        return options
    defs = options.get("columnDefs", [])
    if not isinstance(defs, list):
        return options
    for col in defs:
        if not isinstance(col, dict):
            continue
        field = col.get("field") or col.get("colId")
        if not field:
            continue
        field = str(field)
        if field in HIDE_TRADE_COLUMNS:
            col["hide"] = True
            col["floatingFilter"] = False
            continue
        if col.get("hide"):
            continue
        baseline, minimum = WIDTHS.get(field, (86, 48))
        col["width"] = baseline
        col["minWidth"] = minimum
        col["suppressSizeToFit"] = False  # Critical: old numeric cols opted out.
        col.pop("flex", None)
        col["resizable"] = True
        col.setdefault("headerTooltip", field)
        if field == "Risk / Caution":
            col["tooltipField"] = FULL_RISK

    # Some AG Grid options versions accept these callbacks, while the
    # st-aggrid `fit_columns_on_grid_load=True` covers the initial render.
    fit = JsCode("""function(params) {
        try {
            if (params && params.api && typeof params.api.sizeColumnsToFit === 'function') {
                params.api.sizeColumnsToFit();
            }
        } catch (e) { /* Graceful fallback on legacy AG Grid. */ }
    }""")
    options["onFirstDataRendered"] = fit
    options["onGridSizeChanged"] = fit
    options["suppressHorizontalScroll"] = False  # Never trap data on small screens.
    return options


def sidebar_button_css() -> str:
    """Light sidebar control styles with sufficient selector specificity."""
    return '''<style id="marketvector-sidebar-button-fix-v2">
    [data-testid="stSidebar"] [data-testid="stButton"] button,
    [data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"],
    [data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] {
        background: #ffffff !important;
        border: 1px solid #94a3b8 !important;
        color: #1f2937 !important;
        opacity: 1 !important;
    }
    [data-testid="stSidebar"] [data-testid="stButton"] button *,
    [data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] * {
        color: #1f2937 !important;
        opacity: 1 !important;
    }
    [data-testid="stSidebar"] [data-testid="stButton"] button:hover,
    [data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:hover {
        background: #eff6ff !important;
        border-color: #64748b !important;
    }
    /* Give Home / Pulse all available width for wide data grids. */
    .stApp .block-container {
        max-width: 1820px !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
    }
    </style>'''
