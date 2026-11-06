from __future__ import annotations

from io import BytesIO

import html
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import streamlit as st

try:
    from st_aggrid import AgGrid, GridOptionsBuilder, JsCode
    AGGRID_AVAILABLE = True
except ImportError:
    AgGrid = None
    GridOptionsBuilder = None
    JsCode = None
    AGGRID_AVAILABLE = False


APP_DIR = Path(__file__).resolve().parent

MIN_CLIENT_UPSIDE_PCT = 25.0

SECTOR_NAMES = {
    "XLC": "Communication Services",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLE": "Energy",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLK": "Technology",
    "XLU": "Utilities",
}


def resolve_db() -> Path:
    env = os.getenv("ACTIONABLE_SNAPSHOT_DB", "").strip()
    if env:
        p = Path(env)
        if not p.is_absolute():
            p = APP_DIR / p
        return p

    cfg = APP_DIR / "config.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            raw = (
                data.get("database", {}).get("app_copy")
                or data.get("database", {}).get("path")
            )
            if raw:
                p = Path(raw)
                if not p.is_absolute():
                    p = APP_DIR / p
                if p.exists():
                    return p
        except Exception:
            pass

    cloud = APP_DIR / "data" / "actionable_cloud.sqlite"
    if cloud.exists():
        return cloud

    return APP_DIR / "data" / "marketvector_tlt_tbt.sqlite"


@st.cache_data(ttl=30)
def load_snapshot(db_text: str, reference: str, timeframe: str) -> pd.DataFrame:
    table = (
        "mv_actionable_dashboard_daily"
        if timeframe == "DAILY"
        else "mv_actionable_dashboard_weekly"
    )

    db = Path(db_text)
    if not db.exists():
        return pd.DataFrame()

    with sqlite3.connect(str(db)) as con:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()

        if not exists:
            return pd.DataFrame()

        return pd.read_sql_query(
            f'SELECT * FROM "{table}" WHERE reference=?',
            con,
            params=[reference],
        )


def _safe_text(v, default="—"):
    if v is None:
        return default
    try:
        if pd.isna(v):
            return default
    except Exception:
        pass
    s = str(v).strip()
    return s if s else default


def _num(v):
    try:
        if v is None or pd.isna(v):
            return np.nan
        return float(v)
    except Exception:
        return np.nan


def _sector_description(etf_value):
    raw = _safe_text(etf_value, "")
    if not raw:
        return "—"
    values = [x.strip().upper() for x in raw.split(",") if x.strip()]
    names = [SECTOR_NAMES.get(v, v) for v in values]
    return " / ".join(names) if names else "—"


def _signal_for_row(row, weekly_valid: bool):
    saved = str(row.get("client_signal", "")).strip().title()
    if saved in {"Buy", "Hold"}:
        return saved
    if not weekly_valid:
        return "Hold"
    flag = _num(row.get("client_actionable_now"))
    return "Buy" if pd.notna(flag) and int(flag) == 1 else "Hold"


def _confidence_for_row(row):
    raw = str(row.get("confidence", "")).upper()
    if raw == "STRONG":
        return "Strong"
    if raw == "GOOD":
        return "Good"
    return "Weak"


def _actionable_now(row):
    for field in ["client_trade_valid", "client_actionable_now", "top_actionable_flag"]:
        flag = _num(row.get(field))
        if pd.notna(flag):
            return "Yes" if int(flag) == 1 else "No"
    return "No"


def _entry_reversal_date(row, timeframe: str):
    primary = "daily_reversal_date" if timeframe == "DAILY" else "weekly_reversal_date"

    value = row.get(primary)

    if value is None or pd.isna(value) or str(value).strip() in {"", "NaT", "None", "nan"}:
        value = row.get("actual_response_date")

    parsed = pd.to_datetime(
        value,
        errors="coerce",
    )

    # Do not pass pandas.NaT into AG Grid.  Depending on serialization it can
    # arrive in JavaScript as the literal string "NaT", which is truthy and
    # formats as "Invalid Date".
    if pd.isna(parsed):
        return None

    return parsed


def _entry_reversal_pattern(row, timeframe: str):
    primary = "daily_reversal_pattern" if timeframe == "DAILY" else "weekly_reversal_pattern"
    # Price structure is a valid independent setup; do not label a Buy as
    # "Awaiting reversal" or show an old bearish candle as the Buy catalyst.
    if _safe_text(row.get("client_trade_basis"), "") == "DAILY_UPTREND":
        return ("Higher high + higher low (daily)" if timeframe == "DAILY"
                else "Daily higher high + higher low (weekly pending)")
    pattern = _safe_text(row.get(primary), "")
    if pattern:
        return pattern
    actual = row.get("actual_response_date")
    if actual is not None and not pd.isna(actual):
        return "Bullish response"
    return "Awaiting reversal"


def _signal_comment_for_row(row, reference: str, timeframe: str, weekly_valid: bool):
    if timeframe == "DAILY":
        bearish_date_field = "client_stock_bearish_reversal_date"
        bearish_pattern_field = "client_stock_bearish_reversal_pattern"
    else:
        bearish_date_field = "client_stock_weekly_bearish_reversal_date"
        bearish_pattern_field = "client_stock_weekly_bearish_reversal_pattern"

    bearish_date = pd.to_datetime(row.get(bearish_date_field), errors="coerce")
    bearish_pattern = _safe_text(row.get(bearish_pattern_field), "")
    activation_date = pd.to_datetime(row.get("client_activation_date"), errors="coerce")
    trade_valid = _num(row.get("client_trade_valid"))
    actionable_now = _num(row.get("client_actionable_now"))

    # A confirmed uptrend can reactivate a stock after a *past* daily bearish
    # pattern, but never when a confirmed WEEKLY bearish reversal or the
    # reference weekly regime has invalidated the long thesis.
    if not weekly_valid:
        return f"Hold: {reference} Weekly thesis inactive"

    weekly_bearish_block = _num(row.get("client_weekly_bearish_block"))
    if pd.notna(weekly_bearish_block) and int(weekly_bearish_block) == 1:
        pattern = _safe_text(row.get("client_weekly_bearish_reversal_pattern_detected"), "Weekly bearish reversal")
        return f"Hold: {pattern} (weekly)"

    valid = (pd.notna(trade_valid) and int(trade_valid) == 1)
    basis = _safe_text(row.get("client_trade_basis"), "")
    if valid and basis == "DAILY_UPTREND":
        return "Intact: daily higher high + higher low"

    if pd.notna(bearish_date) and not valid:
        pattern = bearish_pattern or "Bearish reversal"
        return f"Hold: {pattern} · {bearish_date:%b %d, %Y}"

    if pd.notna(trade_valid) and int(trade_valid) == 1:
        return "Intact: long setup favorable"

    if pd.notna(actionable_now) and int(actionable_now) == 1:
        return "Intact: long setup favorable"

    if pd.notna(activation_date):
        return "Intact: long setup favorable"

    return f"Waiting: {timeframe.title()} bullish setup not activated"


def _current_price_value(row):
    for field in ["latest_close", "entry_price", "current_price"]:
        value = _num(row.get(field))
        if pd.notna(value):
            return value
    return np.nan


def _median_value(row):
    return _num(row.get("target_12m_median"))


def _upside_potential_value(row):
    raw = _num(row.get("analyst_high_upside_pct"))
    if pd.notna(raw):
        return raw * 100.0

    high = _num(row.get("target_12m_high"))
    price = _current_price_value(row)
    if pd.notna(high) and pd.notna(price) and price > 0:
        return (high / price - 1.0) * 100.0

    return np.nan


def _ma_distance_value(row):
    raw = _num(row.get("ma_distance_pct"))
    if pd.notna(raw):
        return raw * 100.0

    text = _safe_text(row.get("ma_distance_display"), "").replace("%", "").replace("+", "")
    try:
        return float(text)
    except Exception:
        return np.nan


def _chart_url(symbol: str, timeframe: str) -> str:
    ticker = quote(str(symbol).strip().upper())
    period = "d" if timeframe == "DAILY" else "w"
    return f"https://finviz.com/quote.ashx?t={ticker}&p={period}"


def _dedupe_watch_list(stock: pd.DataFrame) -> pd.DataFrame:
    if stock.empty:
        return stock

    x = stock.copy()

    action_flag = pd.to_numeric(
        x.get(
            "client_trade_valid",
            x.get(
                "client_actionable_now",
                x.get("top_actionable_flag", pd.Series(0, index=x.index)),
            ),
        ),
        errors="coerce",
    ).fillna(0)

    lag = pd.to_numeric(
        x.get("normal_lag", pd.Series(np.nan, index=x.index)),
        errors="coerce",
    )

    score = pd.to_numeric(
        x.get("score", pd.Series(0, index=x.index)),
        errors="coerce",
    ).fillna(0)

    x["_action_pref"] = -action_flag
    x["_lag_pref"] = lag.map(
        lambda v: 0 if v == 0 else 1 if v == -1 else 10 + int(v) if pd.notna(v) else 999
    )
    x["_score_pref"] = -score

    return (
        x.sort_values(["symbol", "_action_pref", "_lag_pref", "_score_pref"])
        .drop_duplicates("symbol", keep="first")
        .drop(columns=["_action_pref", "_lag_pref", "_score_pref"])
        .reset_index(drop=True)
    )



def _tlt_lag_display(
    row,
    timeframe: str,
) -> str:
    """
    Actionable rows prefer the observed current-cycle lag.
    Waiting rows fall back to the recent-pattern expected lag.
    """
    observed = pd.to_numeric(
        pd.Series(
            [
                row.get(
                    "tlt_observed_lag"
                )
            ]
        ),
        errors="coerce",
    ).iloc[0]

    expected = pd.to_numeric(
        pd.Series(
            [
                row.get(
                    "tlt_expected_lag"
                )
            ]
        ),
        errors="coerce",
    ).iloc[0]

    value = (
        observed
        if pd.notna(observed)
        else expected
    )

    if pd.isna(value):
        return ""

    value = int(
        round(
            float(value)
        )
    )

    unit = (
        "d"
        if timeframe.upper() == "DAILY"
        else "w"
    )

    if value == -1:
        return f"-1{unit}"

    if value == 0:
        return f"0{unit}"

    return f"{value}{unit}"


def _forecast_turn_date(
    row,
):
    value = pd.to_datetime(
        row.get(
            "tlt_forecast_date"
        ),
        errors="coerce",
    )

    if pd.isna(value):
        return None

    return value


def _forecast_in_display(
    row,
) -> str:
    value = pd.to_numeric(
        pd.Series(
            [
                row.get(
                    "tlt_forecast_in_days"
                )
            ]
        ),
        errors="coerce",
    ).iloc[0]

    if pd.isna(value):
        return ""

    days = int(
        round(
            float(
                value
            )
        )
    )

    if days <= 0:
        return ""

    return (
        f"In {days} trading day"
        + (
            ""
            if days == 1
            else "s"
        )
    )




def _passes_minimum_upside(
    row,
    minimum_pct: float = MIN_CLIENT_UPSIDE_PCT,
) -> bool:
    """
    Global client-facing eligibility gate.

    Uses the exact same Upside Potential calculation displayed in the grid.
    Missing upside is treated as ineligible.
    """
    value = _upside_potential_value(
        row
    )

    numeric = pd.to_numeric(
        pd.Series(
            [value]
        ),
        errors="coerce",
    ).iloc[0]

    if pd.isna(numeric):
        return False

    return float(numeric) >= float(
        minimum_pct
    )





def _rsi_pair_for_row(row):
    daily = _num(row.get("daily_rsi14"))
    weekly = _num(row.get("weekly_rsi14"))
    d = f"{float(daily):.0f}" if pd.notna(daily) else "—"
    w = f"{float(weekly):.0f}" if pd.notna(weekly) else "—"
    return f"D {d} / W {w}"


def _risk_comment_for_row(row):
    value = row.get("trade_caution_comment")
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()

def _client_grid_frame(df, reference, timeframe, weekly_valid):
    reversal_date_label = f"{timeframe.title()} Reversal Date"
    reversal_label = f"{timeframe.title()} Reversal"

    columns = [
        "Symbol",
        "Sector",
        reversal_date_label,
        reversal_label,
        "Lag vs TLT",
        "Expected In",
        "Expected Turn",
        "Signal",
        "Signal Comment",
        "RSI D/W",
        "Risk / Caution",
        "Confidence",
        "Analyst Rating",
        "Current Price",
        "Analyst Median",
        "_Analyst Low",
        "_Analyst High",
        "_Target Tooltip",
        "Upside Potential",
        "Chart",
    ]

    if df is None or df.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for _, row in df.iterrows():
        symbol = _safe_text(row.get("symbol"))
        rows.append(
            {
                "Symbol": symbol,
                "Sector": _sector_description(row.get("etf")),
                reversal_date_label: _entry_reversal_date(row, timeframe),
                reversal_label: _entry_reversal_pattern(row, timeframe),
                "Lag vs TLT": _tlt_lag_display(row, timeframe),
                "Expected In": _forecast_in_display(row),
                "Expected Turn": _forecast_turn_date(row),
                "Signal": _signal_for_row(row, weekly_valid),
                "Signal Comment": _signal_comment_for_row(
                    row, reference, timeframe, weekly_valid
                ),
                "RSI D/W": _rsi_pair_for_row(row),
                "Risk / Caution": _risk_comment_for_row(row),
                "Confidence": _confidence_for_row(row),
                "Analyst Rating": _safe_text(row.get("analyst_rating")),
                "Current Price": _current_price_value(row),
                "Analyst Median": _median_value(row),
                "_Analyst Low": _num(row.get("target_12m_low")),
                "_Analyst High": _num(row.get("target_12m_high")),
                "_Target Tooltip": (
                    f"Analyst Low: ${_num(row.get('target_12m_low')):,.2f} | "
                    f"Analyst High: ${_num(row.get('target_12m_high')):,.2f}"
                    if pd.notna(_num(row.get("target_12m_low")))
                    and pd.notna(_num(row.get("target_12m_high")))
                    else (
                        f"Analyst Low: ${_num(row.get('target_12m_low')):,.2f}"
                        if pd.notna(_num(row.get("target_12m_low")))
                        else (
                            f"Analyst High: ${_num(row.get('target_12m_high')):,.2f}"
                            if pd.notna(_num(row.get("target_12m_high")))
                            else "Analyst low/high targets unavailable"
                        )
                    )
                ),
                "Upside Potential": _upside_potential_value(row),
                "Chart": _chart_url(symbol, timeframe),
            }
        )

    out = pd.DataFrame(rows)

    # Normalize missing reversal dates to Python None.  This prevents pandas
    # NaT from serializing into a truthy "NaT" value in AG Grid.
    reversal_date_col = f"{timeframe.title()} Reversal Date"
    if reversal_date_col in out.columns:
        out[reversal_date_col] = out[reversal_date_col].map(
            lambda v: None if v is None or pd.isna(v) else v
        )

    out.index = range(1, len(out) + 1)
    out.index.name = "#"
    return out


def _filter_grid(frame, search_text, signals, confidences, ratings):
    if frame.empty:
        return frame

    x = frame.copy()
    q = str(search_text or "").strip().lower()

    if q:
        mask = (
            x["Symbol"].astype(str).str.lower().str.contains(q, regex=False, na=False)
            | x["Sector"].astype(str).str.lower().str.contains(q, regex=False, na=False)
        )
        x = x[mask].copy()

    if signals:
        x = x[x["Signal"].isin(signals)].copy()
    if confidences:
        x = x[x["Confidence"].isin(confidences)].copy()
    if ratings:
        x = x[x["Analyst Rating"].isin(ratings)].copy()
    x.index = range(1, len(x) + 1)
    x.index.name = "#"
    return x


def _row_quality_bucket(row):
    score = 0

    confidence = str(row.get("Confidence", "")).strip().upper()
    if confidence == "STRONG":
        score += 2
    elif confidence == "GOOD":
        score += 1

    rating = str(row.get("Analyst Rating", "")).strip().upper()
    if rating in {"STRONG BUY", "BUY", "OUTPERFORM", "OVERWEIGHT"}:
        score += 2
    elif rating in {"HOLD", "NEUTRAL", "MARKET PERFORM", "EQUAL-WEIGHT"}:
        score += 1
    elif rating in {"SELL", "STRONG SELL", "UNDERPERFORM", "UNDERWEIGHT"}:
        score -= 1

    upside = pd.to_numeric(pd.Series([row.get("Upside Potential")]), errors="coerce").iloc[0]
    if pd.notna(upside):
        if upside >= 20:
            score += 2
        elif upside >= 10:
            score += 1
        elif upside < 0:
            score -= 1

    if score >= 5:
        return "VERY_FAVORABLE"
    if score >= 3:
        return "FAVORABLE"
    if score >= 1:
        return "MIXED"
    return "CAUTION"


def _style_grid(frame):
    if frame.empty:
        return frame

    def row_style(row):
        bucket = _row_quality_bucket(row)
        styles = {
            "VERY_FAVORABLE": "background-color: rgba(22,163,74,.13);",
            "FAVORABLE": "background-color: rgba(59,130,246,.085);",
            "MIXED": "background-color: rgba(234,179,8,.095);",
            "CAUTION": "background-color: rgba(220,38,38,.075);",
        }
        return [styles[bucket]] * len(row)

    def comment_style(v):
        text = str(v or "").strip().lower()
        if text.startswith("hold:"):
            return "background-color: rgba(245,158,11,.20); font-weight:700;"
        if text.startswith("intact:"):
            return "background-color: rgba(22,163,74,.09); font-weight:650;"
        if text.startswith("waiting:"):
            return "background-color: rgba(59,130,246,.08); font-weight:600;"
        return ""

    def conf_style(v):
        if v == "Strong":
            return "font-weight:800;"
        if v == "Good":
            return "font-weight:700;"
        return ""

    def rating_style(v):
        rating = str(v).strip().upper()
        if rating in {"STRONG BUY", "BUY", "OUTPERFORM", "OVERWEIGHT"}:
            return "font-weight:800;"
        if rating in {"SELL", "STRONG SELL", "UNDERPERFORM", "UNDERWEIGHT"}:
            return "font-weight:700;"
        return ""

    def upside_style(v):
        try:
            value = float(v)
        except Exception:
            return ""
        if value >= 20:
            return "font-weight:800;"
        if value >= 10:
            return "font-weight:700;"
        return ""

    styler = frame.style.apply(row_style, axis=1)
    styler = styler.map(comment_style, subset=["Signal Comment"])
    styler = styler.map(conf_style, subset=["Confidence"])
    styler = styler.map(rating_style, subset=["Analyst Rating"])
    styler = styler.map(upside_style, subset=["Upside Potential"])
    return styler


def _aggrid_row_quality(row):
    return _row_quality_bucket(row)


def _render_aggrid(frame: pd.DataFrame, reference: str, timeframe: str, grid_height: int, key: str):
    """Render sortable/filterable grid with per-cell analyst target hover."""
    grid = frame.copy().reset_index().rename(columns={"index": "#"})

    # The Chart field should always be a raw URL.  If a prior dashboard
    # version left literal anchor markup in an in-memory frame, extract the
    # href rather than displaying the markup.
    if "Chart" in grid.columns:
        grid["Chart"] = (
            grid["Chart"]
            .fillna("")
            .astype(str)
            .str.replace(
                r'^\s*<a\s+href=["\']([^"\']+)["\'].*$',
                r'\1',
                regex=True,
            )
        )

    grid["_Quality"] = grid.apply(_aggrid_row_quality, axis=1)

    gb = GridOptionsBuilder.from_dataframe(grid)
    gb.configure_default_column(
        sortable=True,
        filter=True,
        floatingFilter=True,
        resizable=True,
        suppressMovable=False,
    )

    reversal_date_col = f"{timeframe.title()} Reversal Date"
    reversal_col = f"{timeframe.title()} Reversal"
    gb.configure_column("#", header_name="#", width=52, minWidth=48, maxWidth=62, pinned="left", filter=False)
    gb.configure_column("Symbol", width=78, minWidth=70, pinned="left")
    gb.configure_column("Sector", width=145, minWidth=115)
    gb.configure_column(
        reversal_date_col,
        header_name="Reversal Date",
        width=110,
        minWidth=98,
        valueFormatter=(
            "value == null || value === '' || value === 'NaT' || value === 'None' "
            "? '' "
            ": (isNaN(new Date(value).getTime()) "
            "? '' "
            ": new Date(value).toLocaleDateString('en-US',"
            "{month:'short',day:'2-digit',year:'numeric'}))"
        ),
    )
    gb.configure_column(reversal_col, width=130, minWidth=110)

    show_tlt_timing = reference.upper() == "TLT"
    show_expected_turn = (
        show_tlt_timing
        and key.endswith("_hold")
    )

    gb.configure_column(
        "Lag vs TLT",
        width=88,
        minWidth=78,
        hide=not show_tlt_timing,
        filter=True,
    )

    gb.configure_column(
        "Expected In",
        header_name="Expected In",
        width=125,
        minWidth=110,
        hide=not show_expected_turn,
        filter=True,
    )

    gb.configure_column(
        "Expected Turn",
        header_name="Expected Turn",
        width=112,
        minWidth=100,
        hide=not show_expected_turn,
        valueFormatter=(
            "value == null || value === '' || value === 'NaT' || value === 'None' "
            "? '' "
            ": (isNaN(new Date(value).getTime()) "
            "? '' "
            ": new Date(value).toLocaleDateString('en-US',"
            "{month:'short',day:'2-digit',year:'numeric'}))"
        ),
    )

    gb.configure_column("Signal", width=72, minWidth=65)
    gb.configure_column("Signal Comment", width=210, minWidth=165)
    gb.configure_column("RSI D/W", width=102, minWidth=90,
                        headerTooltip="Wilder RSI(14) daily / weekly")
    gb.configure_column("Risk / Caution", width=340, minWidth=280,
                        tooltipField="Risk / Caution",
                        headerTooltip="Trend/reversal blocks; RSI/momentum cautions")
    gb.configure_column("Confidence", width=90, minWidth=82)
    gb.configure_column("Analyst Rating", header_name="Analyst Rating", width=112, minWidth=102)
    gb.configure_column(
        "Current Price",
        header_name="Price",
        width=88,
        minWidth=80,
        valueFormatter="value == null || isNaN(value) ? '' : '$' + Number(value).toFixed(2)",
    )
    gb.configure_column(
        "Analyst Median",
        header_name="Median Target",
        width=105,
        minWidth=96,
        tooltipField="_Target Tooltip",
        valueFormatter="value == null || isNaN(value) ? '' : '$' + Number(value).toFixed(2)",
        headerTooltip="Hover a Median Target cell to see analyst Low and High targets",
    )
    gb.configure_column(
        "Upside Potential",
        header_name="Upside",
        width=85,
        minWidth=78,
        valueFormatter="value == null || isNaN(value) ? '' : Number(value).toFixed(1) + '%'",
    )
    chart_click = JsCode("""function(params) {
        if (!params.value) {
            return;
        }

        // React-safe chart navigation:
        // keep the raw URL as the cell value, display only "View Chart",
        // and open the URL from the click event.
        window.open(
            String(params.value),
            '_blank',
            'noopener,noreferrer'
        );
    }""")

    chart_style = JsCode("""function(params) {
        if (!params.value) {
            return null;
        }

        return {
            color: '#2563eb',
            fontWeight: '600',
            cursor: 'pointer',
            textDecoration: 'none'
        };
    }""")

    gb.configure_column(
        "Chart",
        header_name="Chart",
        width=92,
        minWidth=84,
        valueFormatter="value ? 'View Chart' : ''",
        onCellClicked=chart_click,
        cellStyle=chart_style,
        filter=False,
        sortable=False,
    )

    for hidden in ["_Analyst Low", "_Analyst High", "_Target Tooltip", "_Quality"]:
        gb.configure_column(hidden, hide=True)

    comment_style = JsCode("""function(params) {
        const t = String(params.value || '').toLowerCase();
        if (t.startsWith('hold:')) return {backgroundColor:'rgba(245,158,11,.20)', fontWeight:'700'};
        if (t.startsWith('intact:')) return {backgroundColor:'rgba(22,163,74,.09)', fontWeight:'650'};
        if (t.startsWith('waiting:')) return {backgroundColor:'rgba(59,130,246,.08)', fontWeight:'600'};
        return null;
    }""")
    gb.configure_column("Signal Comment", cellStyle=comment_style)
    risk_style = JsCode("""function(params) {
        const t=String(params.value || '').toLowerCase();
        if (t.startsWith('hold:')) return {backgroundColor:'#fecdd3',color:'#991b1b',fontWeight:'700'};
        if (t) return {backgroundColor:'#fef3c7',color:'#92400e',fontWeight:'650'};
        return null;
    }""")
    gb.configure_column("Risk / Caution", cellStyle=risk_style)

    signal_style = JsCode("""function(params) {
        const t = String(params.value || '').toUpperCase();
        if (t === 'BUY') return {fontWeight:'800', color:'#15803d'};
        if (t === 'HOLD') return {fontWeight:'700', color:'#a16207'};
        return null;
    }""")
    gb.configure_column("Signal", cellStyle=signal_style)

    confidence_style = JsCode("""function(params) {
        const t = String(params.value || '').toUpperCase();
        if (t === 'STRONG') return {fontWeight:'800'};
        if (t === 'GOOD') return {fontWeight:'700'};
        return null;
    }""")
    gb.configure_column("Confidence", cellStyle=confidence_style)

    upside_style = JsCode("""function(params) {
        const v = Number(params.value);
        if (isNaN(v)) return null;
        if (v >= 20) return {fontWeight:'800'};
        if (v >= 10) return {fontWeight:'700'};
        return null;
    }""")
    gb.configure_column("Upside Potential", cellStyle=upside_style)

    row_style = JsCode("""function(params) {
        const q = params.data ? params.data._Quality : '';
        if (q === 'VERY_FAVORABLE') return {backgroundColor:'rgba(22,163,74,.13)'};
        if (q === 'FAVORABLE') return {backgroundColor:'rgba(59,130,246,.085)'};
        if (q === 'MIXED') return {backgroundColor:'rgba(234,179,8,.095)'};
        if (q === 'CAUTION') return {backgroundColor:'rgba(220,38,38,.075)'};
        return null;
    }""")

    gb.configure_grid_options(
        getRowStyle=row_style,
        tooltipShowDelay=250,
        tooltipHideDelay=5000,
        suppressRowClickSelection=True,
        enableCellTextSelection=True,
        ensureDomOrder=True,
    )

    AgGrid(
        grid,
        gridOptions=gb.build(),
        height=grid_height,
        width="100%",
        theme="streamlit",
        fit_columns_on_grid_load=True,
        allow_unsafe_jscode=True,
        enable_enterprise_modules=False,
        update_on=[],
        key=f"ag_{key}",
    )


def _render_native_grid_fallback(frame: pd.DataFrame, reference: str, timeframe: str, grid_height: int, key: str):
    """Fallback used only when streamlit-aggrid is not installed."""
    display = frame.drop(
        columns=["_Analyst Low", "_Analyst High", "_Target Tooltip"],
        errors="ignore",
    )

    if reference.upper() != "TLT":
        display = display.drop(
            columns=[
                "Lag vs TLT",
                "Expected In",
                "Expected Turn",
            ],
            errors="ignore",
        )
    elif not key.endswith("_hold"):
        display = display.drop(
            columns=[
                "Expected In",
                "Expected Turn",
            ],
            errors="ignore",
        )
    reversal_date_col = f"{timeframe.title()} Reversal Date"
    reversal_col = f"{timeframe.title()} Reversal"
    ma_title = "Vs 200DMA" if timeframe == "DAILY" else "Vs 200WMA"

    st.dataframe(
        _style_grid(display),
        width="stretch",
        height=grid_height,
        hide_index=False,
        column_config={
            "Symbol": st.column_config.TextColumn("Symbol", width="small"),
            "Sector": st.column_config.TextColumn("Sector", width="small"),
            reversal_date_col: st.column_config.DateColumn("Reversal Date", format="MMM DD, YYYY", width="small"),
            reversal_col: st.column_config.TextColumn(reversal_col, width="small"),
            "Lag vs TLT": st.column_config.TextColumn("Lag vs TLT", width="small"),
            "Expected In": st.column_config.TextColumn("Expected In", width="small"),
            "Expected Turn": st.column_config.DateColumn(
                "Expected Turn",
                format="MMM DD, YYYY",
                width="small",
            ),
            "Signal": st.column_config.TextColumn("Signal", width="small"),
            "Signal Comment": st.column_config.TextColumn("Signal Comment", width="medium"),
            "RSI D/W": st.column_config.TextColumn("RSI D/W", width="small"),
            "Risk / Caution": st.column_config.TextColumn("Risk / Caution", width="large"),
            "Confidence": st.column_config.TextColumn("Confidence", width="small"),
            "Analyst Rating": st.column_config.TextColumn("Analyst Rating", width="small"),
            "Current Price": st.column_config.NumberColumn("Price", format="$%.2f", width="small"),
            "Analyst Median": st.column_config.NumberColumn(
                "Median Target",
                format="$%.2f",
                width="small",
                help="Install streamlit-aggrid to hover and see analyst Low / High targets.",
            ),
            "Upside Potential": st.column_config.NumberColumn("Upside", format="%.1f%%", width="small"),
            "Chart": st.column_config.LinkColumn("Chart", display_text="View Chart", width="small"),
        },
        key=f"grid_{key}",
    )



def _apply_grid_quick_search(
    frame: pd.DataFrame,
    query: str,
) -> pd.DataFrame:
    """Case-insensitive search across client-visible grid columns."""
    if frame is None or frame.empty:
        return frame

    q = str(query or "").strip().lower()
    if not q:
        return frame

    hidden = {
        "_Analyst Low",
        "_Analyst High",
        "_Target Tooltip",
    }

    searchable_cols = [
        c
        for c in frame.columns
        if c not in hidden
        and c != "Chart"
    ]

    if not searchable_cols:
        return frame

    search_text = (
        frame[searchable_cols]
        .fillna("")
        .astype(str)
        .agg(" | ".join, axis=1)
        .str.lower()
    )

    return frame[
        search_text.str.contains(
            q,
            regex=False,
            na=False,
        )
    ].copy()


def _grid_export_frame(
    frame: pd.DataFrame,
    reference: str,
    timeframe: str,
    key: str,
) -> pd.DataFrame:
    """Build the clean client-facing export view."""
    export = frame.drop(
        columns=[
            "_Analyst Low",
            "_Analyst High",
            "_Target Tooltip",
        ],
        errors="ignore",
    ).copy()

    if reference.upper() != "TLT":
        export = export.drop(
            columns=[
                "Lag vs TLT",
                "Expected In",
                "Expected Turn",
            ],
            errors="ignore",
        )
    elif not key.endswith("_hold"):
        export = export.drop(
            columns=[
                "Expected In",
                "Expected Turn",
            ],
            errors="ignore",
        )

    reversal_date_col = f"{timeframe.title()} Reversal Date"

    if reversal_date_col in export.columns:
        parsed = pd.to_datetime(
            export[reversal_date_col],
            errors="coerce",
        )
        export[reversal_date_col] = parsed.dt.strftime(
            "%Y-%m-%d"
        ).where(
            parsed.notna(),
            "",
        )

    if "Expected Turn" in export.columns:
        parsed_expected = pd.to_datetime(
            export["Expected Turn"],
            errors="coerce",
        )
        export["Expected Turn"] = parsed_expected.dt.strftime(
            "%Y-%m-%d"
        ).where(
            parsed_expected.notna(),
            "",
        )

    if "Chart" in export.columns:
        export["Chart URL"] = export["Chart"].fillna("")
        export["Chart"] = export["Chart URL"].map(
            lambda x: "View Chart" if str(x).strip() else ""
        )

    export.index = range(
        1,
        len(export) + 1,
    )
    export.index.name = "#"

    return export


def _grid_excel_bytes(
    frame: pd.DataFrame,
    reference: str,
    timeframe: str,
    key: str,
    sheet_name: str,
) -> bytes:
    """Build an XLSX workbook for the current grid view."""
    export = _grid_export_frame(
        frame,
        reference,
        timeframe,
        key,
    )

    output = BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl",
    ) as writer:
        export.to_excel(
            writer,
            sheet_name=sheet_name[:31],
            index=True,
        )

        worksheet = writer.book[
            sheet_name[:31]
        ]

        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

        headers = {
            cell.value: cell.column
            for cell in worksheet[1]
        }

        chart_col = headers.get("Chart")
        url_col = headers.get("Chart URL")

        if chart_col and url_col:
            for row_num in range(
                2,
                worksheet.max_row + 1,
            ):
                url = worksheet.cell(
                    row=row_num,
                    column=url_col,
                ).value

                if url:
                    cell = worksheet.cell(
                        row=row_num,
                        column=chart_col,
                    )
                    cell.value = "View Chart"
                    cell.hyperlink = str(url)
                    cell.style = "Hyperlink"

            worksheet.column_dimensions[
                worksheet.cell(
                    row=1,
                    column=url_col,
                ).column_letter
            ].hidden = True

        for column_cells in worksheet.columns:
            letter = column_cells[0].column_letter

            if worksheet.column_dimensions[letter].hidden:
                continue

            max_len = 0

            for cell in column_cells[:100]:
                value = "" if cell.value is None else str(cell.value)
                max_len = max(
                    max_len,
                    min(
                        len(value),
                        45,
                    ),
                )

            worksheet.column_dimensions[letter].width = max(
                9,
                min(
                    max_len + 2,
                    34,
                ),
            )

    return output.getvalue()


def _render_grid_toolbar(
    frame: pd.DataFrame,
    reference: str,
    timeframe: str,
    key: str,
) -> pd.DataFrame:
    """Per-grid quick search + export toolbar."""
    search_col, count_col, excel_col, csv_col = st.columns(
        [3.2, 1.0, 1.15, 1.05],
        gap="small",
        vertical_alignment="bottom",
    )

    with search_col:
        quick_search = st.text_input(
            "Quick search",
            value="",
            placeholder="Search symbol, sector, signal, analyst rating...",
            key=f"grid_search_{key}",
            label_visibility="collapsed",
        )

    searched = _apply_grid_quick_search(
        frame,
        quick_search,
    )

    with count_col:
        st.caption(
            f"{len(searched):,} row"
            + (
                ""
                if len(searched) == 1
                else "s"
            )
        )

    clean_name = (
        f"{reference}_{timeframe}_{key}"
        .replace(" ", "_")
        .replace("/", "_")
    )

    sheet_name = clean_name[:31]

    with excel_col:
        try:
            excel_bytes = _grid_excel_bytes(
                searched,
                reference,
                timeframe,
                key,
                sheet_name,
            )

            st.download_button(
                "⬇ Excel",
                data=excel_bytes,
                file_name=f"{clean_name}.xlsx",
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                key=f"excel_{key}",
                use_container_width=True,
            )
        except Exception as exc:
            st.download_button(
                "⬇ Excel",
                data=b"",
                file_name=f"{clean_name}.xlsx",
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                key=f"excel_{key}",
                disabled=True,
                help=(
                    "Excel export requires openpyxl. "
                    f"Details: {type(exc).__name__}"
                ),
                use_container_width=True,
            )

    with csv_col:
        csv_export = _grid_export_frame(
            searched,
            reference,
            timeframe,
            key,
        ).to_csv(
            index=True,
        ).encode("utf-8-sig")

        st.download_button(
            "⬇ CSV",
            data=csv_export,
            file_name=f"{clean_name}.csv",
            mime="text/csv",
            key=f"csv_{key}",
            use_container_width=True,
        )

    return searched



def _render_grid(
    title,
    subtitle,
    source_df,
    reference,
    timeframe,
    weekly_valid,
    filter_state,
    key,
    minimum_visible_rows=0,
):
    st.markdown(
        f"<div class='grid-title'>{html.escape(title)}</div>",
        unsafe_allow_html=True,
    )
    if subtitle:
        st.markdown(
            f"<div class='grid-subtitle'>{html.escape(subtitle)}</div>",
            unsafe_allow_html=True,
        )

    frame = _client_grid_frame(source_df, reference, timeframe, weekly_valid)
    frame = _filter_grid(
        frame,
        filter_state["search"],
        filter_state["signals"],
        filter_state["confidences"],
        filter_state["ratings"],
    )

    frame = _render_grid_toolbar(
        frame,
        reference,
        timeframe,
        key,
    )

    if frame.empty:
        st.info(
            "No stocks match this view, the current filters, and the grid search."
        )
        return

    visible_rows = max(
        min(len(frame), 25),
        min(minimum_visible_rows, len(frame)),
    )
    grid_height = 76 + max(visible_rows, 1) * 34

    if AGGRID_AVAILABLE:
        _render_aggrid(
            frame,
            reference,
            timeframe,
            grid_height,
            key,
        )
    else:
        st.warning(
            "Median-target Low/High hover requires `streamlit-aggrid`. "
            "Run `python -m pip install streamlit-aggrid` once, then restart the dashboard.",
            icon="⚠️",
        )
        _render_native_grid_fallback(
            frame,
            reference,
            timeframe,
            grid_height,
            key,
        )


def _daily_tone(status_row, reference, weekly_valid):
    daily_status = str(status_row.get("daily_status", "")).upper()

    if weekly_valid and daily_status == "ACTIVE":
        return (
            "constructive",
            "Daily trend constructive",
            "No confirmed Daily bearish reversal is active. "
            "Current long setups have a favorable Daily backdrop.",
        )

    if weekly_valid:
        pattern = _safe_text(
            status_row.get("reference_reversal_pattern"),
            "Daily bearish reversal",
        )
        date = pd.to_datetime(
            status_row.get("reference_reversal_date"),
            errors="coerce",
        )
        date_text = date.strftime("%B %d, %Y") if pd.notna(date) else "the latest session"
        return (
            "soft",
            "Daily momentum softened",
            f"{pattern} developed on {date_text}. Fresh entries are more selective "
            f"while the Weekly bullish thesis remains intact. The opportunity set can "
            f"broaden after a new {reference} Daily bullish reversal.",
        )

    return (
        "neutral",
        "Daily setup not currently supportive",
        "The Daily setup is not in a confirmed bullish regime. "
        "The follower list remains useful for monitoring.",
    )


def _weekly_tone(weekly_valid):
    if weekly_valid:
        return (
            "constructive",
            "Weekly follower window active",
            "The reference Weekly bullish thesis remains active. Activated Weekly "
            "stock setups remain eligible until each stock prints its own Weekly bearish reversal.",
        )

    return (
        "neutral",
        "Weekly follower window on hold",
        "The reference Weekly bullish thesis is not active. New Weekly long entries "
        "wait for the next confirmed Weekly bullish pivot.",
    )


def render_client_dashboard(reference: str, timeframe: str):
    reference = reference.upper()
    timeframe = timeframe.upper()

    st.set_page_config(
        page_title=f"{reference} Signal Intelligence",
        page_icon="📈",
        layout="wide",
    )

    st.markdown(
        """
        <style>
        .block-container {padding-top:2.15rem; padding-bottom:2rem; max-width:1650px;}
        .client-kicker {font-size:.84rem; text-transform:uppercase; letter-spacing:.12em; font-weight:700; opacity:.62; margin-bottom:.35rem;}
        .client-title {font-size:2.02rem; line-height:1.08; font-weight:790; letter-spacing:-.035em; margin-bottom:.34rem;}
        .client-subtitle {font-size:.94rem; opacity:.70; max-width:760px; line-height:1.45; margin-bottom:.45rem;}

        .signal-header {
            text-align:center;
            padding:.18rem 0 .52rem 0;
        }

        .signal-title {
            font-size:1.98rem;
            line-height:1.15;
            font-weight:820;
            letter-spacing:-.030em;
            margin:0;
            padding-top:.10rem;
        }

        .signal-subtitle {
            margin-top:.24rem;
            font-size:.88rem;
            opacity:.64;
            letter-spacing:.012em;
        }

        .header-controls {
            padding-top:.34rem;
        }

        .bulletin {
            border:1px solid rgba(128,128,128,.20);
            border-radius:10px;
            padding:.48rem .62rem;
            margin:0;
            background:rgba(128,128,128,.028);
            min-height:86px;
        }
        .bulletin-valid,.bulletin-constructive {border-left:4px solid #16a34a;}
        .bulletin-hold {border-left:4px solid #dc2626;}
        .bulletin-soft {border-left:4px solid #d97706;}
        .bulletin-neutral {border-left:4px solid #64748b;}
        .bulletin-label {font-size:.66rem; text-transform:uppercase; letter-spacing:.10em; font-weight:800; opacity:.60; margin-bottom:.12rem;}
        .bulletin-title {font-size:.84rem; font-weight:780; line-height:1.20; margin-bottom:.10rem;}
        .bulletin-copy {font-size:.70rem; line-height:1.28; opacity:.74;}
        .grid-title {font-size:1.24rem; font-weight:760; letter-spacing:-.018em; margin-top:.48rem; margin-bottom:.1rem;}
        .grid-subtitle {font-size:.88rem; opacity:.65; margin-bottom:.55rem;}
        .asof-note {font-size:.77rem; opacity:.55; margin-top:.3rem; margin-bottom:.6rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    db = resolve_db()
    df = load_snapshot(str(db), reference, timeframe)

    if df.empty:
        st.error(
            f"No {reference} {timeframe.title()} snapshot is available. "
            "Run the snapshot build first."
        )
        st.stop()

    status = df[df["row_type"].eq("STATUS")].copy()
    stock = df[df["row_type"].eq("STOCK")].copy()

    if status.empty:
        st.error("Snapshot is missing its STATUS row.")
        st.stop()

    s = status.iloc[-1]
    weekly_valid = str(s.get("weekly_status", "")).upper() == "ACTIVE"

    weekly_pivot = pd.to_datetime(s.get("weekly_bullish_date"), errors="coerce")
    weekly_pivot_text = (
        weekly_pivot.strftime("%B %d, %Y")
        if pd.notna(weekly_pivot)
        else "the latest confirmed Weekly bullish pivot"
    )

    asof = pd.to_datetime(s.get("reference_asof"), errors="coerce")
    asof_text = asof.strftime("%B %d, %Y") if pd.notna(asof) else "latest available session"

    st.markdown(
        f"""
        <div class="signal-header">
            <div class="signal-title">{reference} Signal Intelligence</div>
            <div class="signal-subtitle">
                {timeframe.title()} follower opportunities · relationship-driven equity signals
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if weekly_valid:
        weekly_class = "bulletin-valid"
        weekly_title = "Weekly bullish thesis valid"
        weekly_copy = (
            f"Pivot {weekly_pivot_text}. Not nullified. Activated stock trades remain "
            f"eligible until each stock prints its own {timeframe.title()} bearish reversal."
        )
    else:
        weekly_class = "bulletin-hold"
        weekly_title = "Weekly bullish thesis on hold"
        weekly_copy = (
            f"Prior pivot {weekly_pivot_text} is no longer active. "
            "New long entries wait for a fresh Weekly bullish pivot."
        )

    tone_key, tone_title, tone_copy = (
        _daily_tone(s, reference, weekly_valid)
        if timeframe == "DAILY"
        else _weekly_tone(weekly_valid)
    )
    tone_class = {
        "constructive": "bulletin-constructive",
        "soft": "bulletin-soft",
        "neutral": "bulletin-neutral",
    }[tone_key]

    controls_col, weekly_col, setup_col = st.columns(
        [1.35, 1.0, 1.0],
        gap="medium",
        vertical_alignment="top",
    )

    with controls_col:
        st.markdown(
            "<div class='header-controls'>",
            unsafe_allow_html=True,
        )

        st.markdown(
            f"<div class='asof-note'>Market data through {html.escape(asof_text)} · "
            f"Analyst data through {html.escape(_safe_text(s.get('analyst_asof')))}</div>",
            unsafe_allow_html=True,
        )

        st.markdown(
            "</div>",
            unsafe_allow_html=True,
        )

    # Client dashboards intentionally show only the two primary sections:
    # Actionable Now and Follower List — On Hold.
    show_more = False

    with weekly_col:
        st.markdown(
            f"<div class='bulletin {weekly_class}'>"
            f"<div class='bulletin-label'>{reference} Weekly</div>"
            f"<div class='bulletin-title'>{html.escape(weekly_title)}</div>"
            f"<div class='bulletin-copy'>{html.escape(weekly_copy)}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    with setup_col:
        st.markdown(
            f"<div class='bulletin {tone_class}'>"
            f"<div class='bulletin-label'>{reference} {timeframe.title()} Setup</div>"
            f"<div class='bulletin-title'>{html.escape(tone_title)}</div>"
            f"<div class='bulletin-copy'>{html.escape(tone_copy)}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    if "visible_default" in stock.columns:
        stock = stock[
            pd.to_numeric(stock["visible_default"], errors="coerce").fillna(1).eq(1)
        ].copy()

    # Global minimum-upside filter across ALL four client dashboards.
    # Apply this before Actionable Now / On Hold are split, so the same
    # threshold governs every primary grid and every export/search result.
    stock = stock[
        stock.apply(
            _passes_minimum_upside,
            axis=1,
        )
    ].copy()

    base_watch = _dedupe_watch_list(stock)
    preview = _client_grid_frame(base_watch, reference, timeframe, weekly_valid)

    with st.sidebar:
        st.markdown("### Filters")
        search_text = st.text_input(
            "Symbol or sector",
            placeholder="e.g. HD or Financials",
            key=f"filter_search_{reference}_{timeframe}",
        )
        signals = st.multiselect(
            "Signal",
            options=sorted(preview["Signal"].dropna().unique().tolist()),
            default=[],
            key=f"filter_signal_{reference}_{timeframe}",
        )
        confidences = st.multiselect(
            "Confidence",
            options=[x for x in ["Strong", "Good", "Weak"] if x in set(preview["Confidence"].dropna())],
            default=[],
            key=f"filter_conf_{reference}_{timeframe}",
        )
        ratings = st.multiselect(
            "Analyst Rating",
            options=sorted(preview["Analyst Rating"].dropna().unique().tolist()),
            default=[],
            key=f"filter_rating_{reference}_{timeframe}",
        )
        st.caption("Click grid headers to sort. Use the grid search icon for quick lookup.")
        st.markdown(
            "**Row quality**\n- 🟩 Very favorable\n- 🟦 Favorable\n- 🟨 Mixed\n- 🟥 Caution"
        )

    filter_state = {
        "search": search_text,
        "signals": signals,
        "confidences": confidences,
        "ratings": ratings,
    }

    if "client_trade_valid" in stock.columns:
        actionable_flag = pd.to_numeric(stock["client_trade_valid"], errors="coerce").fillna(0)
    elif "client_actionable_now" in stock.columns:
        actionable_flag = pd.to_numeric(stock["client_actionable_now"], errors="coerce").fillna(0)
    else:
        actionable_flag = pd.to_numeric(
            stock.get("top_actionable_flag", pd.Series(0, index=stock.index)),
            errors="coerce",
        ).fillna(0)

    actionable_now = _dedupe_watch_list(stock[actionable_flag.eq(1)].copy())

    _render_grid(
        "Actionable Now",
        f"Buy: valid reversal setup OR confirmed daily uptrend, while the {reference} "
        f"Weekly thesis remains active and no newer confirmed stock Weekly "
        f"bearish reversal blocks the trade.",
        actionable_now,
        reference,
        timeframe,
        weekly_valid,
        filter_state,
        key=f"{reference}_{timeframe}_actionable",
        minimum_visible_rows=1,
    )

    # Keep the two primary client grids mutually exclusive.
    # Anything already shown in Actionable Now must not repeat below.
    actionable_symbols = set(
        actionable_now.get(
            "symbol",
            pd.Series(dtype=object),
        )
        .dropna()
        .astype(str)
        .str.upper()
        .str.strip()
        .tolist()
    )

    hold_stock = stock.copy()

    if actionable_symbols:
        hold_stock = hold_stock[
            ~hold_stock.get(
                "symbol",
                pd.Series("", index=hold_stock.index),
            )
            .astype(str)
            .str.upper()
            .str.strip()
            .isin(actionable_symbols)
        ].copy()

    hold_list = _dedupe_watch_list(
        hold_stock
    )

    _render_grid(
        f"Current {reference} {timeframe.title()} Follower List — On Hold",
        "Follower candidates that are not currently actionable. "
        "Stocks shown in Actionable Now are excluded from this list.",
        hold_list,
        reference,
        timeframe,
        weekly_valid,
        filter_state,
        key=f"{reference}_{timeframe}_hold",
        minimum_visible_rows=25,
    )

    if show_more:
        if timeframe == "DAILY":
            sections = [
                ("DIRECT_OVERRIDE_WATCH", "Direct Override Watch"),
                ("IN_SYNC", "In Sync"),
                ("EARLY_LEADERS", "Early Leaders"),
                ("NEAR_TERM", "Near-Term Followers"),
                ("MID_LAG", "Mid-Lag Followers"),
                ("EXTENDED", "Extended Lag Followers"),
            ]
        else:
            sections = [
                ("DIRECT_OVERRIDE_WATCH", "Direct Override Watch"),
                ("IN_SYNC", "In Sync"),
                ("EARLY_LEADERS", "Early Leaders"),
                ("NEAR_TERM", "Near-Term Weekly Followers"),
                ("DELAYED", "Delayed / Weak Weekly Followers"),
                ("EXTENDED", "Extended Weekly Followers"),
            ]

        for section_key, title in sections:
            part = _dedupe_watch_list(
                stock[stock["section"].eq(section_key)].copy()
            )
            _render_grid(
                title,
                "",
                part,
                reference,
                timeframe,
                weekly_valid,
                filter_state,
                key=f"{reference}_{timeframe}_{section_key}",
            )
