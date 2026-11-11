from __future__ import annotations

if __name__ == "__main__":
    from marketvector_auth import require_login as _mv_require_login
    _mv_require_login()


from io import BytesIO

import html
import json
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import streamlit as st

# MARKETVECTOR_UI_FIT_V2 view helper
from mv_ui_fit_v2 import compact_risk_grid as _mv_compact_risk_grid, responsive_options as _mv_responsive_options, sidebar_button_css as _mv_sidebar_button_css

try:
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except ImportError:
    go = None
    PLOTLY_AVAILABLE = False

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    yf = None
    YFINANCE_AVAILABLE = False

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

# Presentation-only exclusions. The 25%-upside eligibility gate still uses
# the full underlying snapshot; no rating, quote, target or trade data changes.
MP_REMOVED_GRID_COLUMNS = (
    "Daily Reversal Date",
    "Weekly Reversal Date",
    "Analyst Median",
)

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


MARKET_INDEX_SPECS = [
    {"name": "S&P 500", "ticker": "^GSPC", "proxy": "SPY"},
    {"name": "NASDAQ", "ticker": "^IXIC", "proxy": "QQQ"},
    {"name": "DOW", "ticker": "^DJI", "proxy": "DIA"},
    {"name": "RUSSELL 2000", "ticker": "^RUT", "proxy": "IWM"},
]


def _resolve_market_data_db() -> Path | None:
    cfg = APP_DIR / "config.json"
    candidates = []

    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            database = data.get("database", {})

            for key in ["path", "source", "app_copy"]:
                raw = database.get(key)

                if raw:
                    p = Path(raw)

                    if not p.is_absolute():
                        p = APP_DIR / p

                    candidates.append(p)

        except Exception:
            pass

    candidates.extend(
        [
            APP_DIR / "data" / "marketvector_tlt_tbt.sqlite",
            Path(r"C:\MarketVector\Database\ibkr_market_data.sqlite"),
        ]
    )

    for p in candidates:
        try:
            if p.exists():
                return p
        except Exception:
            continue

    return None


def _flatten_yf_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()

    x = frame.copy()

    if isinstance(x.columns, pd.MultiIndex):
        level0 = [str(v) for v in x.columns.get_level_values(0)]

        standard = {
            "Open",
            "High",
            "Low",
            "Close",
            "Adj Close",
            "Volume",
        }

        if any(v in standard for v in level0):
            x.columns = x.columns.get_level_values(0)
        else:
            x.columns = x.columns.get_level_values(-1)

    return x


@st.cache_data(ttl=300, show_spinner=False)
def _load_yahoo_daily_history(
    ticker: str,
):
    """
    Daily chart history.  Use ~3 months / 60+ trading sessions.
    """
    if not YFINANCE_AVAILABLE:
        return pd.DataFrame()

    try:
        x = yf.download(
            ticker,
            period="3mo",
            interval="1d",
            auto_adjust=False,
            prepost=False,
            progress=False,
            threads=False,
        )

        x = _flatten_yf_columns(
            x
        )

        if x.empty:
            return pd.DataFrame()

        x = x.copy()
        x.index = pd.to_datetime(
            x.index,
            errors="coerce",
        )
        x = x[
            ~x.index.isna()
        ]

        for c in [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume",
        ]:
            if c in x.columns:
                x[c] = pd.to_numeric(
                    x[c],
                    errors="coerce",
                )

        x = x.dropna(
            subset=[
                "Open",
                "High",
                "Low",
                "Close",
            ]
        )

        return x.tail(
            65
        )

    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=90, show_spinner=False)
def _load_yahoo_latest_quote(
    ticker: str,
):
    """
    Try to obtain the freshest Yahoo quote available.

    Priority:
      1. fast_info last_price / previous_close
      2. 1-minute history (latest available trade)
      3. 5-minute history
      4. latest Daily close
    """
    if not YFINANCE_AVAILABLE:
        return None, None, None, "NO YAHOO QUOTE"

    try:
        t = yf.Ticker(
            ticker
        )

        try:
            info = t.fast_info

            last_price = getattr(
                info,
                "last_price",
                None,
            )

            previous_close = getattr(
                info,
                "previous_close",
                None,
            )

            if (
                last_price is not None
                and previous_close is not None
                and float(
                    previous_close
                )
                != 0
            ):
                last_price = float(
                    last_price
                )
                previous_close = float(
                    previous_close
                )

                change = (
                    last_price
                    - previous_close
                )

                change_pct = (
                    change
                    / previous_close
                    * 100.0
                )

                return (
                    last_price,
                    change,
                    change_pct,
                    "YAHOO LATEST",
                )

        except Exception:
            pass

        for interval, period, label in [
            (
                "1m",
                "1d",
                "YAHOO 1M",
            ),
            (
                "5m",
                "2d",
                "YAHOO 5M",
            ),
        ]:
            try:
                q = t.history(
                    period=period,
                    interval=interval,
                    auto_adjust=False,
                    prepost=False,
                )

                if q is None or q.empty:
                    continue

                q = q.copy()
                q.index = pd.to_datetime(
                    q.index,
                    errors="coerce",
                )
                q = q[
                    ~q.index.isna()
                ]

                if q.empty or "Close" not in q.columns:
                    continue

                closes = pd.to_numeric(
                    q["Close"],
                    errors="coerce",
                ).dropna()

                if closes.empty:
                    continue

                latest = float(
                    closes.iloc[-1]
                )

                # Use fast_info previous close if available.
                previous_close = None

                try:
                    info = t.fast_info
                    previous_close = getattr(
                        info,
                        "previous_close",
                        None,
                    )
                except Exception:
                    previous_close = None

                if previous_close is None:
                    session_dates = pd.Series(
                        q.index.date,
                        index=q.index,
                    )

                    latest_date = max(
                        session_dates
                    )

                    prior = q[
                        session_dates.lt(
                            latest_date
                        )
                    ]

                    if not prior.empty:
                        previous_close = pd.to_numeric(
                            prior["Close"],
                            errors="coerce",
                        ).dropna()

                        previous_close = (
                            float(
                                previous_close.iloc[-1]
                            )
                            if not previous_close.empty
                            else None
                        )

                if (
                    previous_close is not None
                    and float(
                        previous_close
                    )
                    != 0
                ):
                    previous_close = float(
                        previous_close
                    )

                    change = (
                        latest
                        - previous_close
                    )

                    change_pct = (
                        change
                        / previous_close
                        * 100.0
                    )

                    return (
                        latest,
                        change,
                        change_pct,
                        label,
                    )

                return (
                    latest,
                    None,
                    None,
                    label,
                )

            except Exception:
                continue

        # Last Yahoo fallback: completed Daily close.
        daily = t.history(
            period="5d",
            interval="1d",
            auto_adjust=False,
        )

        if daily is not None and not daily.empty:
            closes = pd.to_numeric(
                daily["Close"],
                errors="coerce",
            ).dropna()

            if not closes.empty:
                latest = float(
                    closes.iloc[-1]
                )

                previous_close = (
                    float(
                        closes.iloc[-2]
                    )
                    if len(
                        closes
                    )
                    >= 2
                    else None
                )

                if (
                    previous_close is not None
                    and previous_close != 0
                ):
                    change = (
                        latest
                        - previous_close
                    )

                    change_pct = (
                        change
                        / previous_close
                        * 100.0
                    )
                else:
                    change = None
                    change_pct = None

                return (
                    latest,
                    change,
                    change_pct,
                    "YAHOO DAILY",
                )

    except Exception:
        pass

    return None, None, None, "NO YAHOO QUOTE"


@st.cache_data(ttl=300, show_spinner=False)
def _load_local_proxy_bars(db_path: str, proxy: str):
    path = Path(db_path)

    if not path.exists():
        return pd.DataFrame(), None, None, None

    with sqlite3.connect(str(path)) as con:
        tables = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        table = None

        for candidate in ["relationship_bars_day", "bars_day"]:
            if candidate in tables:
                table = candidate
                break

        if table is None:
            return pd.DataFrame(), None, None, None

        sql = (
            'SELECT bar_date, open, high, low, close, volume '
            f'FROM "{table}" '
            'WHERE UPPER(symbol)=? '
            'ORDER BY bar_date DESC '
            'LIMIT 70'
        )

        try:
            x = pd.read_sql_query(
                sql,
                con,
                params=[proxy.upper()],
            )
        except Exception:
            return pd.DataFrame(), None, None, None

    if x.empty:
        return pd.DataFrame(), None, None, None

    x["bar_date"] = pd.to_datetime(x["bar_date"], errors="coerce")

    for c in ["open", "high", "low", "close", "volume"]:
        x[c] = pd.to_numeric(x[c], errors="coerce")

    x = (
        x.dropna(subset=["bar_date", "open", "high", "low", "close"])
        .sort_values("bar_date")
        .reset_index(drop=True)
    )

    if x.empty:
        return pd.DataFrame(), None, None, None

    x = x.rename(
        columns={
            "bar_date": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    ).set_index("Date")

    last_price = float(x["Close"].iloc[-1])

    if len(x) >= 2:
        prior_close = float(x["Close"].iloc[-2])
    else:
        prior_close = float(x["Open"].iloc[-1])

    change = last_price - prior_close
    change_pct = (change / prior_close * 100.0) if prior_close else None

    return x, last_price, change, change_pct


def _market_card_data(
    spec: dict,
):
    """
    Daily chart + freshest available quote.

    Quote priority:
      1. Yahoo direct index
      2. Yahoo proxy ETF
      3. latest completed Daily index/proxy close
      4. local DB proxy

    Chart priority:
      1. Yahoo direct index Daily candles
      2. Yahoo ETF proxy Daily candles
      3. local DB proxy Daily candles
    """
    direct_daily = _load_yahoo_daily_history(
        spec["ticker"]
    )

    direct_last, direct_change, direct_change_pct, direct_quote_source = (
        _load_yahoo_latest_quote(
            spec["ticker"]
        )
    )

    # Best case: real index chart and real index quote.
    if (
        direct_daily is not None
        and not direct_daily.empty
    ):
        last = direct_last
        change = direct_change
        change_pct = direct_change_pct
        quote_source = direct_quote_source

        if last is None:
            last = float(
                direct_daily["Close"].iloc[-1]
            )

            if len(
                direct_daily
            ) >= 2:
                previous_close = float(
                    direct_daily["Close"].iloc[-2]
                )

                change = (
                    last
                    - previous_close
                )

                change_pct = (
                    change
                    / previous_close
                    * 100.0
                    if previous_close
                    else None
                )

            quote_source = "YAHOO DAILY"

        return {
            "bars": direct_daily,
            "last": last,
            "change": change,
            "change_pct": change_pct,
            "source": "DAILY · INDEX",
            "quote_source": quote_source,
            "symbol": spec["ticker"],
        }

    # If direct index history is unavailable, use the ETF proxy from Yahoo.
    proxy_daily = _load_yahoo_daily_history(
        spec["proxy"]
    )

    proxy_last, proxy_change, proxy_change_pct, proxy_quote_source = (
        _load_yahoo_latest_quote(
            spec["proxy"]
        )
    )

    if (
        proxy_daily is not None
        and not proxy_daily.empty
    ):
        last = proxy_last
        change = proxy_change
        change_pct = proxy_change_pct

        if last is None:
            last = float(
                proxy_daily["Close"].iloc[-1]
            )

            if len(
                proxy_daily
            ) >= 2:
                previous_close = float(
                    proxy_daily["Close"].iloc[-2]
                )

                change = (
                    last
                    - previous_close
                )

                change_pct = (
                    change
                    / previous_close
                    * 100.0
                    if previous_close
                    else None
                )

            proxy_quote_source = "YAHOO DAILY"

        return {
            "bars": proxy_daily,
            "last": last,
            "change": change,
            "change_pct": change_pct,
            "source": f"DAILY · {spec['proxy']} PROXY",
            "quote_source": proxy_quote_source,
            "symbol": spec["proxy"],
        }

    # Final fallback: local DB Daily ETF proxy.
    local_db = _resolve_market_data_db()

    if local_db is not None:
        local, last, change, change_pct = _load_local_proxy_bars(
            str(
                local_db
            ),
            spec["proxy"],
        )

        if (
            local is not None
            and not local.empty
            and last is not None
        ):
            return {
                "bars": local.tail(
                    65
                ),
                "last": last,
                "change": change,
                "change_pct": change_pct,
                "source": f"DAILY · LOCAL {spec['proxy']}",
                "quote_source": "LOCAL CLOSE",
                "symbol": spec["proxy"],
            }

    return {
        "bars": pd.DataFrame(),
        "last": None,
        "change": None,
        "change_pct": None,
        "source": "DAILY · NO DATA",
        "quote_source": "NO QUOTE",
        "symbol": spec["ticker"],
    }



def _market_card_figure(bars: pd.DataFrame, day_mode: bool = False):
    if not PLOTLY_AVAILABLE or bars is None or bars.empty:
        return None

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=bars.index,
                open=bars["Open"],
                high=bars["High"],
                low=bars["Low"],
                close=bars["Close"],
                increasing_line_color="#16c784",
                increasing_fillcolor="#16c784",
                decreasing_line_color="#ff4d5a",
                decreasing_fillcolor="#ff4d5a",
                whiskerwidth=0.45,
                name="",
            )
        ]
    )

    chart_bg = "#ffffff" if day_mode else "#11161f"
    chart_text = "#475569" if day_mode else "#9aa4b2"
    chart_ticks = "#64748b" if day_mode else "#778192"
    chart_grid = "rgba(71,85,105,0.12)" if day_mode else "rgba(148,163,184,0.10)"

    fig.update_layout(
        height=160,
        margin=dict(l=4, r=4, t=4, b=0),
        paper_bgcolor=chart_bg,
        plot_bgcolor=chart_bg,
        showlegend=False,
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        font=dict(color=chart_text, size=10),
        xaxis=dict(
            showgrid=False,
            zeroline=False,
            showline=False,
            tickfont=dict(color=chart_ticks, size=9),
            nticks=5,
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor=chart_grid,
            zeroline=False,
            side="right",
            tickfont=dict(color=chart_ticks, size=9),
            nticks=4,
        ),
    )

    return fig


def _render_market_index_strip(day_mode: bool = False):
    st.markdown(
        '<div class="market-bottom-heading">Market Overview · Major Indexes</div>',
        unsafe_allow_html=True,
    )

    cols = st.columns(4, gap="medium")

    for col, spec in zip(cols, MARKET_INDEX_SPECS):
        data = _market_card_data(spec)

        last = data["last"]
        change = data["change"]
        change_pct = data["change_pct"]

        positive = change is not None and change >= 0
        move_class = "market-up" if positive else "market-down"

        last_text = "—" if last is None else f"{last:,.2f}"

        if change is None or change_pct is None:
            move_text = "No market data"
        else:
            sign = "+" if change >= 0 else ""
            move_text = f"{sign}{change:,.2f} ({sign}{change_pct:.2f}%)"

        with col:
            with st.container(border=True):
                st.markdown(
                    f"""
                    <div class="market-card-head">
                        <div>
                            <div class="market-card-title">{html.escape(spec['name'])}</div>
                            <div class="market-card-source">{html.escape(data['source'])}</div>
                            <div class="market-card-quote">{html.escape(data.get('quote_source', ''))}</div>
                        </div>
                        <div class="market-card-right">
                            <div class="market-card-last">{last_text}</div>
                            <div class="{move_class}">{move_text}</div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                fig = _market_card_figure(data["bars"], day_mode=day_mode)

                if fig is not None:
                    st.plotly_chart(
                        fig,
                        use_container_width=True,
                        config={
                            "displayModeBar": False,
                            "scrollZoom": False,
                        },
                        key=f"market_chart_{spec['proxy']}",
                    )
                elif data["bars"] is not None and not data["bars"].empty:
                    st.line_chart(
                        data["bars"]["Close"],
                        height=160,
                    )
                else:
                    st.markdown(
                        "<div class='market-no-data'>Index data unavailable</div>",
                        unsafe_allow_html=True,
                    )



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


def _parse_analyst_date(value):
    """Parse stored analyst as-of dates, including legacy YYYYMMDD strings."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or raw.lower() in {"nan", "nat", "none", "—", "null"}:
        return None
    try:
        d = pd.to_datetime(raw, format="%Y%m%d" if re.fullmatch(r"\d{8}", raw) else None, errors="coerce")
        if pd.isna(d):
            return None
        dt = pd.Timestamp(d)
        if dt.year < 2000 or dt > pd.Timestamp.now() + pd.Timedelta(days=1):
            return None
        return dt
    except Exception:
        return None


def _analyst_asof_from_table(db_path: Path):
    """Return date of actual stored Yahoo analyst records; never the snapshot date."""
    if not db_path.is_file():
        return None
    try:
        with sqlite3.connect(str(db_path)) as con:
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='analyst_targets'"
            ).fetchone()
            if not exists:
                return None
            cols = {r[1].lower() for r in con.execute("PRAGMA table_info(analyst_targets)")}
            date_col = "asof_date" if "asof_date" in cols else (
                "fetched_at" if "fetched_at" in cols else None
            )
            if date_col is None:
                return None
            where = "WHERE LOWER(COALESCE(source,'')) LIKE '%yahoo%'" if "source" in cols else ""
            values = con.execute(
                f"SELECT DISTINCT {date_col} FROM analyst_targets {where} "
                f"ORDER BY {date_col} DESC LIMIT 200"
            ).fetchall()
            dates = [d for r in values if (d := _parse_analyst_date(r[0])) is not None]
            return max(dates) if dates else None
    except (sqlite3.Error, OSError):
        return None


def _market_pulse_analyst_asof(db: Path, status_row, stock: pd.DataFrame) -> str:
    """Use real source dates, including Cloud STATUS date stamped at export."""
    dates = []
    status_date = _parse_analyst_date(status_row.get("analyst_asof"))
    if status_date is not None:
        dates.append(status_date)
    if "analyst_asof" in stock.columns:
        for value in stock["analyst_asof"].dropna().unique():
            parsed = _parse_analyst_date(value)
            if parsed is not None:
                dates.append(parsed)

    for path in [db, APP_DIR / "data" / "marketvector_tlt_tbt.sqlite"]:
        parsed = _analyst_asof_from_table(path)
        if parsed is not None:
            dates.append(parsed)

    return max(dates).strftime("%B %d, %Y") if dates else "date unavailable"


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


def _trend_status_for_row(row, timeframe: str) -> str:
    """Price structure from the daily HH/HL overlay, independent of MA filters.

    A Buy from the *separate* reversal path need not have HH/HL yet; don't
    report a broken trend as its Buy catalyst.  The trade decision remains
    controlled by the existing snapshot's client_trade_valid flag.
    """
    if str(timeframe).upper() == "WEEKLY":
        weekly_dir = str(row.get("weekly_trend_direction") or "").upper()
        provisional = _num(row.get("weekly_trend_provisional"))
        wb = _num(row.get("client_weekly_bearish_block"))
        wd = _num(row.get("client_weekly_downtrend_block"))
        if pd.notna(wb) and int(wb) == 1:
            return "Weekly bearish reversal"
        if pd.notna(wd) and int(wd) == 1:
            return "Weekly downtrend"
        if weekly_dir == "BULLISH":
            return "Weekly uptrend (forming)" if pd.notna(provisional) and int(provisional) else "Weekly uptrend intact"
        if weekly_dir == "BEARISH":
            return "Weekly downtrend"
        return "Weekly trend unconfirmed"

    dc = _num(row.get("client_daily_candlestick_block"))
    dd = _num(row.get("client_daily_downtrend_block"))
    if pd.notna(dc) and int(dc) == 1:
        return "Daily bearish reversal"
    if pd.notna(dd) and int(dd) == 1:
        return "Uptrend broken"
    confirmed = _num(row.get("daily_uptrend_confirmed"))
    hh = _num(row.get("trend_higher_high"))
    hl = _num(row.get("trend_higher_low"))
    basis = _safe_text(row.get("client_trade_basis"), "").upper()
    valid = _num(row.get("client_trade_valid"))

    if pd.notna(confirmed):
        if int(confirmed) == 1:
            return "Uptrend intact"
        if pd.notna(valid) and int(valid) == 1:
            return "Bullish reversal active"
        return "Uptrend broken"

    if pd.notna(hh) and pd.notna(hl):
        if int(hh) == 1 and int(hl) == 1:
            return "Uptrend intact"
        if pd.notna(valid) and int(valid) == 1:
            return "Bullish reversal active"
        return "Uptrend broken"

    if basis == "DAILY_UPTREND":
        return "Uptrend intact"
    if pd.notna(valid) and int(valid) == 1:
        return "Bullish reversal active"
    return "Trend not confirmed"


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
    columns = [
        "Symbol",
        "Sector",
        "Trend Status",
        "RSI D/W",
        "Risk / Caution",
        "Lag vs TLT",
        "Expected In",
        "Expected Turn",
        "Signal",
        "Signal Comment",
        "Confidence",
        "Analyst Rating",
        "Current Price",
        "Analyst Median",
        "Analyst High Target",
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
                "Trend Status": _trend_status_for_row(row, timeframe),
                "RSI D/W": _rsi_pair_for_row(row),
                "Risk / Caution": _risk_comment_for_row(row),
                "Lag vs TLT": _tlt_lag_display(row, timeframe),
                "Expected In": _forecast_in_display(row),
                "Expected Turn": _forecast_turn_date(row),
                "Signal": _signal_for_row(row, weekly_valid),
                "Signal Comment": _signal_comment_for_row(
                    row, reference, timeframe, weekly_valid
                ),
                "Confidence": _confidence_for_row(row),
                "Analyst Rating": _safe_text(row.get("analyst_rating")),
                "Current Price": _current_price_value(row),
                "Analyst Median": _median_value(row),
                "Analyst High Target": _num(row.get("target_12m_high")),
                "_Analyst Low": _num(row.get("target_12m_low")),
                "_Analyst High": _num(row.get("target_12m_high")),
                "_Target Tooltip": (
                    f"Analyst Low: ${_num(row.get('target_12m_low')):,.2f} | "
                    f"Analyst Median: ${_median_value(row):,.2f} | "
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
    """Native fallback mirrors the conditional colors in the AG Grid view."""
    if frame.empty:
        return frame

    def row_style(row):
        signal = str(row.get("Signal", "")).strip().upper()
        if signal == "BUY":
            return ["background-color:#eefbf3; color:#14532d;"] * len(row)
        if signal == "HOLD":
            return ["background-color:#fff1f2; color:#7f1d1d;"] * len(row)
        return [""] * len(row)

    def signal_style(value):
        signal = str(value or "").strip().upper()
        if signal == "BUY":
            return "background-color:#bbf7d0; color:#166534; font-weight:800;"
        if signal == "HOLD":
            return "background-color:#fecdd3; color:#991b1b; font-weight:800;"
        return ""

    def upside_style(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return ""
        if value >= 50:
            return "background-color:#bbf7d0; color:#14532d; font-weight:800;"
        if value >= 35:
            return "background-color:#dcfce7; color:#166534; font-weight:750;"
        if value >= 25:
            return "background-color:#fef3c7; color:#92400e; font-weight:750;"
        return "background-color:#fee2e2; color:#991b1b; font-weight:750;"

    def confidence_style(value):
        v = str(value or "").strip().upper()
        return "font-weight:800;" if v == "STRONG" else (
            "font-weight:700;" if v == "GOOD" else ""
        )

    styler = frame.style.apply(row_style, axis=1)
    for col, fn in (
        ("Signal", signal_style),
        ("Upside Potential", upside_style),
        ("Confidence", confidence_style),
    ):
        if col in frame.columns:
            styler = styler.map(fn, subset=[col])
    return styler


def _aggrid_row_quality(row):
    return _row_quality_bucket(row)


def _render_aggrid(
    frame: pd.DataFrame,
    reference: str,
    timeframe: str,
    grid_height: int,
    key: str,
):
    """
    Excel-style grid with SELECTABLE DROPDOWN FILTERS INSIDE THE GRID.

    Each visible column gets a custom dropdown floating filter immediately
    below its header.  No typing is required.
    """
    grid = frame.copy().reset_index().rename(
        columns={
            "index": "#",
        }
    )

    # Compute row quality using hidden underlying analyst upside, then remove
    # all excluded fields before GridOptionsBuilder (no floating filters).
    grid["_Quality"] = grid.apply(_aggrid_row_quality, axis=1)
    grid = grid.drop(columns=list(MP_REMOVED_GRID_COLUMNS), errors="ignore")
    # MARKETVECTOR_UI_FIT_V2 render-only risk labels
    grid = _mv_compact_risk_grid(grid, key)

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

    # Custom dropdown component embedded in AG Grid's floating-filter row.
    dropdown_filter = JsCode("""
    class DropdownFloatingFilter {
        init(params) {
            this.params = params;
            this.eGui = document.createElement('select');
            this.eGui.className = 'mv-grid-filter-select';

            const allOption = document.createElement('option');
            allOption.value = '';
            allOption.textContent = 'All';
            this.eGui.appendChild(allOption);

            const values = (params.values || []);
            values.forEach((value) => {
                const option = document.createElement('option');
                option.value = String(value);
                option.textContent = String(value);
                this.eGui.appendChild(option);
            });

            this.eGui.addEventListener('change', () => {
                const selected = this.eGui.value;

                params.parentFilterInstance((instance) => {
                    if (!selected) {
                        instance.setModel(null);
                    } else {
                        instance.setModel({
                            type: 'equals',
                            filter: selected
                        });
                    }
                    params.api.onFilterChanged();
                });
            });
        }

        getGui() {
            return this.eGui;
        }

        onParentModelChanged(parentModel) {
            if (!parentModel) {
                this.eGui.value = '';
                return;
            }

            this.eGui.value = parentModel.filter || '';
        }
    }
    """)

    # For every field, filter against the exact display label used in the
    # dropdown, including formatted price/date/upside labels.
    def configure_dropdown_filter(
        gb,
        column,
        *,
        hide=False,
        width=None,
        min_width=None,
        pinned=None,
        header_name=None,
        value_formatter=None,
        tooltip_field=None,
        header_tooltip=None,
        sortable=True,
    ):
        if column not in grid.columns:
            return

        # Build dropdown values straight from the VISIBLE field. Previous
        # versions added __filter__ helper fields after GridOptionsBuilder
        # initialized its column definitions, which could leave misaligned
        # phantom header/filter columns on some AG Grid versions.
        filter_values = _in_grid_filter_values(
            pd.DataFrame({column: grid[column]}), column,
        )

        # Format the filter's comparison text exactly like the options shown
        # by the custom dropdown. No companion data columns are required.
        # This retains exact-match filtering for price, target and upside.
        filter_getter = JsCode(
            f"""function(params) {{
                const column = {json.dumps(column)};
                const value = params.data ? params.data[column] : null;
                if (value === null || value === undefined || value === '') return '';
                if (column === 'Current Price' || column === 'Analyst High Target' || column === 'Analyst Median') {{
                    const n = Number(value);
                    return Number.isFinite(n)
                        ? '$' + n.toLocaleString('en-US', {{ minimumFractionDigits:2, maximumFractionDigits:2 }})
                        : '';
                }}
                if (column === 'Upside Potential') {{
                    const n = Number(value);
                    return Number.isFinite(n) ? n.toFixed(1) + '%' : '';
                }}
                if (column === 'Chart') return value ? 'View Chart' : '';
                if (column === 'Expected Turn') {{
                    const d = new Date(value);
                    return Number.isNaN(d.getTime())
                        ? ''
                        : d.toLocaleDateString('en-US', {{month:'short',day:'2-digit',year:'numeric',timeZone:'UTC'}});
                }}
                const valueText = String(value).trim();
                return ['', '—', 'None', 'NaT', 'nan'].includes(valueText) ? '' : valueText;
            }}"""
        )

        kwargs = {
            "hide": hide,
            "sortable": sortable,
            "filter": "agTextColumnFilter",
            "floatingFilter": True,
            "floatingFilterComponent": dropdown_filter,
            # Official AG Grid column property: suppresses the blue popover
            # button in the floating-filter row, not our custom dropdown.
            "suppressFloatingFilterButton": True,
            "floatingFilterComponentParams": {
                "values": filter_values,
            },
            "filterValueGetter": filter_getter,
            "suppressMenu": True,
            "suppressHeaderMenuButton": True,
            "menuTabs": [],
        }

        if width is not None:
            kwargs["width"] = width

        if min_width is not None:
            kwargs["minWidth"] = min_width

        if pinned is not None:
            kwargs["pinned"] = pinned

        if header_name is not None:
            kwargs["header_name"] = header_name

        if value_formatter is not None:
            kwargs["valueFormatter"] = value_formatter

        if tooltip_field is not None:
            kwargs["tooltipField"] = tooltip_field

        if header_tooltip is not None:
            kwargs["headerTooltip"] = header_tooltip

        gb.configure_column(
            column,
            **kwargs,
        )


    gb = GridOptionsBuilder.from_dataframe(
        grid
    )

    gb.configure_default_column(
        sortable=True,
        filter=False,
        floatingFilter=False,
        resizable=True,
        suppressMovable=False,
        suppressMenu=True,
    )

    # Compact Excel-like header/filter/data row sizing.
    gb.configure_grid_options(
        headerHeight=26,
        floatingFiltersHeight=24,
        rowHeight=26,
        suppressHorizontalScroll=False,
    )

    gb.configure_column(
        "#",
        header_name="#",
        width=52,
        minWidth=48,
        maxWidth=62,
        pinned="left",
        filter=False,
        floatingFilter=False,
        suppressMenu=True,
        suppressHeaderMenuButton=True,
        menuTabs=[],
    )

    configure_dropdown_filter(
        gb,
        "Symbol",
        width=78,
        min_width=70,
        pinned="left",
    )

    configure_dropdown_filter(
        gb,
        "Sector",
        width=145,
        min_width=115,
    )

    configure_dropdown_filter(
        gb,
        "Trend Status",
        width=175,
        min_width=145,
    )

    show_tlt_timing = (
        reference.upper()
        == "TLT"
    )

    show_expected_turn = (
        show_tlt_timing
        and key.endswith(
            "_hold"
        )
    )

    configure_dropdown_filter(
        gb,
        "Lag vs TLT",
        width=88,
        min_width=78,
        hide=not show_tlt_timing,
    )

    configure_dropdown_filter(
        gb,
        "Expected In",
        header_name="Expected In",
        width=125,
        min_width=110,
        hide=not show_expected_turn,
    )

    configure_dropdown_filter(
        gb,
        "Expected Turn",
        header_name="Expected Turn",
        width=112,
        min_width=100,
        hide=not show_expected_turn,
        value_formatter=(
            "value == null || value === '' || value === 'NaT' || value === 'None' "
            "? '' "
            ": (isNaN(new Date(value).getTime()) "
            "? '' "
            ": new Date(value).toLocaleDateString('en-US',"
            "{month:'short',day:'2-digit',year:'numeric'}))"
        ),
    )

    configure_dropdown_filter(
        gb,
        "Signal",
        width=72,
        min_width=65,
    )

    configure_dropdown_filter(
        gb, "RSI D/W", width=102, min_width=92,
        header_tooltip="Wilder RSI(14) daily / weekly. Values >=70 may be overbought.",
    )
    configure_dropdown_filter(
        gb, "Risk / Caution", width=340, min_width=290,
        header_tooltip="Bearish reversal / trend break blocks; RSI and slowing momentum are warnings only.",
    )

    configure_dropdown_filter(
        gb,
        "Signal Comment",
        width=210,
        min_width=165,
        hide=True,
    )

    configure_dropdown_filter(
        gb,
        "Confidence",
        width=90,
        min_width=82,
    )

    configure_dropdown_filter(
        gb,
        "Analyst Rating",
        header_name="Analyst Rating",
        width=130,
        min_width=120,
    )

    configure_dropdown_filter(
        gb,
        "Current Price",
        header_name="Current Price",
        width=110,
        min_width=102,
        value_formatter=(
            "value == null || isNaN(value) "
            "? '' "
            ": '$' + Number(value).toFixed(2)"
        ),
    )

    configure_dropdown_filter(
        gb,
        "Analyst High Target",
        header_name="Analyst High Target",
        width=170,
        min_width=158,
        tooltip_field="_Target Tooltip",
        value_formatter=(
            "value == null || isNaN(value) "
            "? '' "
            ": '$' + Number(value).toFixed(2)"
        ),
        header_tooltip=(
            "Highest Yahoo analyst price target (not the median). "
            "Hover for the low / median / high range."
        ),
    )

    configure_dropdown_filter(
        gb,
        "Upside Potential",
        header_name="Upside Potential",
        width=118,
        min_width=108,
        value_formatter=(
            "value == null || isNaN(value) "
            "? '' "
            ": Number(value).toFixed(1) + '%'"
        ),
    )


    # Chart is a real clickable link, just as before the v1.3 column cleanup.
    chart_click = JsCode("""function(params) {
        if (!params.value) return;
        window.open(String(params.value), '_blank', 'noopener,noreferrer');
    }""")
    chart_style = JsCode("""function(params) {
        if (!params.value) return null;
        return { color:'#1155cc', fontWeight:'600', cursor:'pointer',
                 textDecoration:'underline' };
    }""")

    configure_dropdown_filter(
        gb,
        "Chart",
        header_name="Chart",
        width=112,
        min_width=100,
        value_formatter="value ? 'View Chart' : ''",
        sortable=False,
    )
    gb.configure_column("Chart", onCellClicked=chart_click, cellStyle=chart_style)

    # Financial columns: right-aligned values and stable column widths.
    for numeric_col in ("Current Price", "Analyst High Target", "Upside Potential"):
        gb.configure_column(
            numeric_col,
            cellStyle={"textAlign": "right"},
            headerClass="mp-numeric-header",
            suppressSizeToFit=True,
        )

    for hidden in [
        "_Analyst Low",
        "_Analyst High",
        "_Target Tooltip",
        "_Quality",
    ]:
        if hidden in grid.columns:
            gb.configure_column(
                hidden,
                hide=True,
            )

    comment_style = JsCode("""function(params) {
        const t = String(params.value || '').toLowerCase();
        if (t.startsWith('hold:')) {
            return {
                backgroundColor:'#fff7e6',
                fontWeight:'700',
                color:'#92400e'
            };
        }
        if (t.startsWith('intact:')) {
            return {
                backgroundColor:'#ecfdf3',
                fontWeight:'650',
                color:'#166534'
            };
        }
        if (t.startsWith('waiting:')) {
            return {
                backgroundColor:'#eff6ff',
                fontWeight:'600',
                color:'#1d4ed8'
            };
        }
        return null;
    }""")

    gb.configure_column(
        "Signal Comment", cellStyle=comment_style,
    )
    risk_style = JsCode("""function(params) {
        const t = String(params.value || '').toLowerCase();
        if (t.startsWith('hold:')) return {backgroundColor:'#fecdd3',color:'#991b1b',fontWeight:'700'};
        if (t) return {backgroundColor:'#fef3c7',color:'#92400e',fontWeight:'650'};
        return null;
    }""")
    gb.configure_column("Risk / Caution", cellStyle=risk_style, tooltipField="Risk / Caution")

    signal_style = JsCode("""function(params) {
        const signal = String(params.value || '').trim().toUpperCase();
        if (signal === 'BUY') {
            return { backgroundColor:'#bbf7d0', color:'#166534', fontWeight:'800' };
        }
        if (signal === 'HOLD') {
            return { backgroundColor:'#fecdd3', color:'#991b1b', fontWeight:'800' };
        }
        return null;
    }""")

    gb.configure_column(
        "Signal",
        cellStyle=signal_style,
    )

    # Tiered upside visual.  This field is still calculated using the
    # existing analyst HIGH target / current price, unchanged from v1.5.
    upside_cell_style = JsCode("""function(params) {
        if (params.value === null || params.value === undefined || params.value === '') {
            return { textAlign:'right' };
        }
        const n = Number(params.value);
        if (!Number.isFinite(n)) return { textAlign:'right' };
        if (n >= 50) return {
            backgroundColor:'#bbf7d0', color:'#14532d',
            fontWeight:'800', textAlign:'right'
        };
        if (n >= 35) return {
            backgroundColor:'#dcfce7', color:'#166534',
            fontWeight:'800', textAlign:'right'
        };
        if (n >= 25) return {
            backgroundColor:'#fef3c7', color:'#92400e',
            fontWeight:'800', textAlign:'right'
        };
        return {
            backgroundColor:'#fee2e2', color:'#991b1b',
            fontWeight:'800', textAlign:'right'
        };
    }""")
    gb.configure_column("Upside Potential", cellStyle=upside_cell_style)

    confidence_style = JsCode("""function(params) {
        const t = String(params.value || '').toUpperCase();
        if (t === 'STRONG') {
            return {fontWeight:'800'};
        }
        if (t === 'GOOD') {
            return {fontWeight:'700'};
        }
        return null;
    }""")

    gb.configure_column(
        "Confidence",
        cellStyle=confidence_style,
    )

    # Make the visible columns explicit.  This also fixes a v1.4 alignment
    # artifact where helper/hidden fields could produce blank floating filters.
    visible_order = [
        "#", "Symbol", "Sector", "Trend Status", "RSI D/W", "Risk / Caution", "Lag vs TLT",
        "Expected In", "Expected Turn", "Signal", "Confidence",
        "Analyst Rating", "Current Price", "Analyst High Target",
        "Upside Potential", "Chart",
    ]
    if reference.upper() != "TLT":
        visible_order = [c for c in visible_order if c not in
                         {"Lag vs TLT", "Expected In", "Expected Turn"}]
    elif not key.endswith("_hold"):
        visible_order = [c for c in visible_order if c not in
                         {"Expected In", "Expected Turn"}]
    options = gb.build()
    coldefs = options.get("columnDefs", [])
    defs_by_field = {c.get("field"): c for c in coldefs if c.get("field")}
    # No trailing unnamed or implicit columns; include only visible fields
    # followed by explicitly hidden data used for styling/tooltips.
    hidden_order = [
        "Signal Comment", "_Analyst Low", "_Analyst High",
        "_Target Tooltip", "_Quality", "Analyst Median",
    ]
    options["columnDefs"] = [
        defs_by_field[c] for c in visible_order
        if c in defs_by_field
    ] + [
        {**defs_by_field[c], "hide": True} for c in hidden_order
        if c in defs_by_field
    ]

    # Apply Buy/Hold color to the full row. No selection/filter/logic changes.
    options["rowClassRules"] = {
        "mp-buy-row": JsCode("""function(params) {
            return params.data && String(params.data['Signal'] || '').trim().toUpperCase() === 'BUY';
        }"""),
        "mp-hold-row": JsCode("""function(params) {
            return params.data && String(params.data['Signal'] || '').trim().toUpperCase() === 'HOLD';
        }"""),
    }

    # Styling must be delivered to the AG Grid component itself. Keep the
    # Signal and Upside Potential cells free for their stronger tier colors.
    market_pulse_grid_css = {
        ".ag-row.mp-buy-row .ag-cell:not([col-id='Signal']):not([col-id='Upside Potential']):not([col-id='Risk / Caution'])": {
            "background-color": "#eefbf3 !important",
        },
        ".ag-row.mp-hold-row .ag-cell:not([col-id='Signal']):not([col-id='Upside Potential']):not([col-id='Risk / Caution'])": {
            "background-color": "#fff1f2 !important",
        },
        ".ag-row.mp-buy-row .ag-cell[col-id='Signal']": {
            "background-color": "#bbf7d0 !important",
            "color": "#166534 !important",
            "font-weight": "800 !important",
        },
        ".ag-row.mp-hold-row .ag-cell[col-id='Signal']": {
            "background-color": "#fecdd3 !important",
            "color": "#991b1b !important",
            "font-weight": "800 !important",
        },
        ".ag-row .ag-cell[col-id='Upside Potential']": {
            "font-weight": "800 !important",
        },
    }

    st.markdown(
        '<div class="excel-grid-wrap">',
        unsafe_allow_html=True,
    )

    AgGrid(
        grid,
        gridOptions=_mv_responsive_options(options, reference, timeframe, key),
        custom_css=market_pulse_grid_css,
        height=grid_height,
        width="100%",
        theme="balham",
        fit_columns_on_grid_load=True,
        allow_unsafe_jscode=True,
        enable_enterprise_modules=False,
        update_on=[],
        key=f"ag_{key}",
    )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )


def _render_native_grid_fallback(frame: pd.DataFrame, reference: str, timeframe: str, grid_height: int, key: str):
    """Streamlit-native fallback: same visible columns as the AG Grid version."""
    display = frame.drop(
        columns=list(MP_REMOVED_GRID_COLUMNS) + [
            "_Analyst Low", "_Analyst High", "_Target Tooltip", "Signal Comment"
        ],
        errors="ignore",
    )
    if reference.upper() != "TLT":
        display = display.drop(
            columns=["Lag vs TLT", "Expected In", "Expected Turn"],
            errors="ignore",
        )
    elif not key.endswith("_hold"):
        display = display.drop(columns=["Expected In", "Expected Turn"], errors="ignore")

    st.dataframe(
        _style_grid(display),
        width="stretch",
        height=grid_height,
        hide_index=False,
        column_config={
            "Symbol": st.column_config.TextColumn("Symbol", width="small"),
            "Sector": st.column_config.TextColumn("Sector", width="medium"),
            "Trend Status": st.column_config.TextColumn("Trend Status", width="medium"),
            "RSI D/W": st.column_config.TextColumn("RSI D/W", width="small"),
            "Risk / Caution": st.column_config.TextColumn("Risk / Caution", width="large"),
            "Lag vs TLT": st.column_config.TextColumn("Lag vs TLT", width="small"),
            "Expected In": st.column_config.TextColumn("Expected In", width="small"),
            "Expected Turn": st.column_config.DateColumn("Expected Turn", format="MMM DD, YYYY", width="small"),
            "Signal": st.column_config.TextColumn("Signal", width="small"),
            "Confidence": st.column_config.TextColumn("Confidence", width="small"),
            "Analyst Rating": st.column_config.TextColumn("Analyst Rating", width="small"),
            "Current Price": st.column_config.NumberColumn(
                "Current Price", format="$%.2f", width="small"
            ),
            "Analyst High Target": st.column_config.NumberColumn(
                "Analyst High Target", format="$%.2f", width="small"
            ),
            "Upside Potential": st.column_config.NumberColumn(
                "Upside", format="%.1f%%", width="small"
            ),
            "Chart": st.column_config.LinkColumn(
                "Chart", display_text="View Chart", width="small"
            ),
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
        columns=list(MP_REMOVED_GRID_COLUMNS) + [
            "_Analyst Low", "_Analyst High", "_Target Tooltip", "Signal Comment"
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

    # Excel gets a clickable 'View Chart' cell and a hidden Chart URL
    # helper column. CSV retains both the label and the actual URL.
    if "Chart" in export.columns:
        export["Chart URL"] = export["Chart"].fillna("")
        export["Chart"] = export["Chart URL"].map(
            lambda url: "View Chart" if str(url).strip() else ""
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


def _filter_display_value(
    column: str,
    value,
) -> str:
    """Human-readable value used by the in-grid dropdown filter."""
    try:
        if value is None or pd.isna(value):
            return ""
    except Exception:
        if value is None:
            return ""

    if column in {
        "Daily Reversal Date",
        "Weekly Reversal Date",
        "Expected Turn",
    }:
        d = pd.to_datetime(
            value,
            errors="coerce",
        )

        if pd.isna(d):
            return ""

        return pd.Timestamp(
            d
        ).strftime(
            "%b %d, %Y"
        )

    if column in {
        "Current Price",
        "Analyst Median",
        "Analyst High Target",
    }:
        n = pd.to_numeric(
            pd.Series(
                [value]
            ),
            errors="coerce",
        ).iloc[0]

        if pd.isna(n):
            return ""

        return f"${float(n):,.2f}"

    if column == "Upside Potential":
        n = pd.to_numeric(
            pd.Series(
                [value]
            ),
            errors="coerce",
        ).iloc[0]

        if pd.isna(n):
            return ""

        return f"{float(n):.1f}%"

    if column == "Chart":
        return (
            "View Chart"
            if str(value).strip()
            else ""
        )

    s = str(
        value
    ).strip()

    if s in {
        "",
        "—",
        "None",
        "NaT",
        "nan",
    }:
        return ""

    return s


def _in_grid_filter_values(
    frame: pd.DataFrame,
    column: str,
) -> list[str]:
    if (
        frame is None
        or frame.empty
        or column not in frame.columns
    ):
        return []

    raw_values = frame[column]

    # Defensive handling: duplicate column labels can make pandas return a
    # DataFrame instead of a Series.  Use the first matching column only.
    if isinstance(
        raw_values,
        pd.DataFrame,
    ):
        raw_values = raw_values.iloc[
            :,
            0,
        ]

    values = raw_values.map(
        lambda value: _filter_display_value(
            column,
            value,
        )
    )

    values = values[
        values.ne("")
    ]

    result = values.drop_duplicates().tolist()

    if column in {
        "Current Price",
        "Analyst Median",
        "Analyst High Target",
        "Upside Potential",
    }:
        def numeric_key(value):
            cleaned = (
                str(value)
                .replace("$", "")
                .replace("%", "")
                .replace(",", "")
            )
            try:
                return (0, float(cleaned))
            except Exception:
                return (1, str(value).upper())

        return sorted(
            result,
            key=numeric_key,
        )

    if column in {
        "Daily Reversal Date",
        "Weekly Reversal Date",
        "Expected Turn",
    }:
        return sorted(
            result,
            key=lambda value: (
                pd.to_datetime(
                    value,
                    errors="coerce",
                )
                if pd.notna(
                    pd.to_datetime(
                        value,
                        errors="coerce",
                    )
                )
                else pd.Timestamp.max
            ),
        )

    return sorted(
        result,
        key=lambda value: str(value).upper(),
    )


def _render_grid_toolbar(
    frame: pd.DataFrame,
    reference: str,
    timeframe: str,
    key: str,
) -> pd.DataFrame:
    """
    Export toolbar only.

    Filters are intentionally NOT rendered here.  They live inside AG Grid,
    directly below each column header.
    """
    tools_left, count_col, excel_col, csv_col = st.columns(
        [2.8, 0.9, 1.15, 1.05],
        gap="small",
        vertical_alignment="bottom",
    )

    with tools_left:
        st.caption(
            "Use the dropdown directly under each grid column heading to filter."
        )

    with count_col:
        st.caption(
            f"{len(frame):,} row"
            + (
                ""
                if len(frame) == 1
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
                frame,
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
            frame,
            reference,
            timeframe,
            key,
        ).to_csv(
            index=True,
        ).encode(
            "utf-8-sig"
        )

        st.download_button(
            "⬇ CSV",
            data=csv_export,
            file_name=f"{clean_name}.csv",
            mime="text/csv",
            key=f"csv_{key}",
            use_container_width=True,
        )

    return frame



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
    grid_height = 54 + max(visible_rows, 1) * 26

    if AGGRID_AVAILABLE:
        _render_aggrid(
            frame,
            reference,
            timeframe,
            grid_height,
            key,
        )
    else:
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


def render_market_pulse_dashboard(reference_override=None, timeframe_override=None, home_mode=False):
    # MARKETVECTOR_HOME_PULSE_AND_COLUMNS_V1_0
    # Overrides are for the gate-selected Home only; the normal Market Pulse
    # page still exposes both selectors. No trading-state override occurs.
    if home_mode and (reference_override is None or str(timeframe_override).upper() != 'DAILY'):
        raise ValueError('Home requires a selected TLT/TBT reference and DAILY timeframe')
    st.set_page_config(
        page_title="Market Pulse · TLT/TBT Signal Intelligence",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Fixed light theme. Preserve the existing Market Pulse design.
    day_mode = True

    # Gate-selected Home fixes reference/timeframe; standalone Market Pulse
    # remains interactive with both reference and timeframe radio buttons.
    if reference_override is None:
        st.sidebar.markdown("## Market Pulse")
        reference = st.sidebar.radio(
            "Reference", options=["TLT", "TBT"], horizontal=True,
            key="market_pulse_reference",
        )
    else:
        reference = str(reference_override).strip().upper()

    if timeframe_override is None:
        timeframe = st.sidebar.radio(
            "Timeframe", options=["DAILY", "WEEKLY"], horizontal=True,
            format_func=lambda x: x.title(), key="market_pulse_timeframe",
        )
    else:
        timeframe = str(timeframe_override).strip().upper()

    reference = reference.upper()
    timeframe = timeframe.upper()
    if reference not in ("TLT", "TBT") or timeframe not in ("DAILY", "WEEKLY"):
        raise ValueError("Invalid Market Pulse reference/timeframe")

    st.markdown(
        """
        <style>
        .stApp {
            background:#081019;
            color:#e6edf6;
        }

        [data-testid="stSidebar"] {
            background:#111a24;
            border-right:1px solid #45546a;
        }

        [data-testid="stSidebar"] * {
            color:#e7edf5 !important;
        }

        [data-testid="stHeader"] {
            background:rgba(8,16,25,.96);
        }

        /* Make the collapsed slide-out control clearly visible */
        [data-testid="collapsedControl"] {
            background:#182231 !important;
            border:1px solid #5a6c87 !important;
            border-radius:10px !important;
            box-shadow:0 0 0 1px rgba(255,255,255,.04), 0 6px 18px rgba(0,0,0,.35) !important;
        }

        [data-testid="collapsedControl"]:hover {
            background:#213044 !important;
            border-color:#87a0c0 !important;
        }

        [data-testid="collapsedControl"] svg,
        [data-testid="collapsedControl"] path,
        [data-testid="collapsedControl"] * {
            color:#eef4ff !important;
            fill:#eef4ff !important;
            stroke:#eef4ff !important;
            opacity:1 !important;
        }

        /* Improve sidebar form control contrast/readability */
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] .stMarkdown,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] div {
            color:#e7edf5 !important;
        }

        [data-testid="stSidebar"] [role="radiogroup"] {
            background:#0d1520;
            border:1px solid #405168;
            border-radius:10px;
            padding:.25rem;
        }

        [data-testid="stSidebar"] input,
        [data-testid="stSidebar"] select {
            color:#eef4ff !important;
        }

        .market-pulse-kicker {
            font-size:.76rem;
            font-weight:800;
            letter-spacing:.16em;
            color:#aebacc;
            margin:.10rem 0 .42rem 0;
        }

        [data-testid="stVerticalBlockBorderWrapper"] {
            background:#11161f;
            border-color:#4a596d !important;
            border-radius:8px;
        }

        .market-card-head {
            display:flex;
            justify-content:space-between;
            gap:.65rem;
            align-items:flex-start;
            margin-bottom:-.20rem;
        }

        .market-card-title {
            font-size:.92rem;
            line-height:1.15;
            font-weight:800;
            color:#f1f5fb;
        }

        .market-card-source {
            margin-top:.15rem;
            font-size:.61rem;
            color:#a6b3c5;
            letter-spacing:.08em;
        }

        .market-card-quote {
            margin-top:.08rem;
            font-size:.57rem;
            color:#92a0b4;
            letter-spacing:.055em;
        }

        .market-card-right {
            text-align:right;
            white-space:nowrap;
        }

        .market-card-last {
            font-size:.83rem;
            font-weight:700;
            color:#ffffff;
        }

        .market-up {
            font-size:.73rem;
            font-weight:800;
            color:#19c37d;
        }

        .market-down {
            font-size:.73rem;
            font-weight:800;
            color:#ff5c6c;
        }

        .market-no-data {
            height:145px;
            display:flex;
            align-items:center;
            justify-content:center;
            color:#a3afc0;
            font-size:.78rem;
        }

        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {
            color:#ffffff !important;
            letter-spacing:.02em;
        }

        [data-testid="stSidebar"] button[kind="secondary"] {
            background:#182231 !important;
            border:1px solid #566986 !important;
            color:#eef4ff !important;
        }

        /* Higher-contrast populated dropdown filters */
        [data-baseweb="select"] > div {
            background:#f7f9fc !important;
            border-color:#9aa8bb !important;
            color:#111827 !important;
        }

        [data-baseweb="select"] span,
        [data-baseweb="select"] input {
            color:#111827 !important;
        }

        [role="listbox"] {
            background:#f8fafc !important;
            color:#111827 !important;
        }

        [role="option"] {
            color:#111827 !important;
        }

        [role="option"]:hover {
            background:#dbe8f6 !important;
        }

        [data-baseweb="popover"] {
            z-index:999999 !important;
        }

        [data-baseweb="menu"] {
            background:#ffffff !important;
            color:#111827 !important;
            border:1px solid #9aa8bb !important;
        }

        /* Spreadsheet / Excel-style grid */
        .excel-grid-wrap {
            background:#ffffff;
            border:1px solid #d7dde6;
            border-radius:4px;
            padding:.25rem;
            box-shadow:none;
        }

        .excel-grid-wrap .ag-theme-balham,
        .excel-grid-wrap .ag-root-wrapper,
        .excel-grid-wrap .ag-root,
        .excel-grid-wrap .ag-center-cols-clipper,
        .excel-grid-wrap .ag-center-cols-container,
        .excel-grid-wrap .ag-body-viewport,
        .excel-grid-wrap .ag-body-viewport-wrapper {
            background:#ffffff !important;
        }

        .excel-grid-wrap .ag-root-wrapper {
            border:1px solid #d7dde6 !important;
            border-radius:2px !important;
        }

        .excel-grid-wrap .ag-header {
            background:#f3f4f6 !important;
            border-bottom:1px solid #d1d5db !important;
        }

        .excel-grid-wrap .ag-header-cell,
        .excel-grid-wrap .ag-header-group-cell {
            background:#f3f4f6 !important;
            color:#111827 !important;
            border-right:1px solid #d1d5db !important;
            font-weight:600 !important;
            font-size:10.5px !important;
            padding-left:4px !important;
            padding-right:4px !important;
        }

        .excel-grid-wrap .ag-header-cell-label,
        .excel-grid-wrap .ag-header-cell-text {
            color:#111827 !important;
        }

        /* Price/target/upside labels and values align inside the same column. */
        .excel-grid-wrap .mp-numeric-header .ag-header-cell-label {
            justify-content:flex-end !important;
        }
        .excel-grid-wrap .ag-header-cell-text {
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }

        .excel-grid-wrap .ag-floating-filter {
            background:#ffffff !important;
            border-right:1px solid #d1d5db !important;
            border-bottom:1px solid #d1d5db !important;
            padding:1px 2px !important;
        }

        .excel-grid-wrap .ag-floating-filter-body {
            width:100% !important;
        }

        /* Only the custom dropdown is shown; hide the redundant native
           blue AG Grid filter-menu button (including on filtered columns). */
        .excel-grid-wrap .ag-floating-filter-button,
        .excel-grid-wrap .ag-floating-filter-button-button,
        .excel-grid-wrap .ag-floating-filter-button .ag-icon,
        .excel-grid-wrap .ag-floating-filter-button-button .ag-icon {
            display:none !important;
            width:0 !important;
            min-width:0 !important;
        }

        .excel-grid-wrap .mv-grid-filter-select {
            width:100% !important;
            height:20px !important;
            min-height:20px !important;
            padding:0 16px 0 3px !important;
            border:1px solid #b8c0cc !important;
            border-radius:2px !important;
            background:#ffffff !important;
            color:#111827 !important;
            font-size:10px !important;
            outline:none !important;
            cursor:pointer !important;
        }

        .excel-grid-wrap .mv-grid-filter-select:focus {
            border-color:#4f81bd !important;
            box-shadow:0 0 0 1px #4f81bd !important;
        }

        .excel-grid-wrap .ag-floating-filter-body,
        .excel-grid-wrap .ag-floating-filter-full-body {
            height:20px !important;
            min-height:20px !important;
        }

        .excel-grid-wrap .ag-header-row.ag-header-row-column-filter {
            min-height:24px !important;
            height:24px !important;
        }

        .excel-grid-wrap .ag-header-row.ag-header-row-column {
            min-height:26px !important;
            height:26px !important;
        }

        .excel-grid-wrap .ag-row {
            min-height:26px !important;
            height:26px !important;
        }

        .excel-grid-wrap .ag-row {
            background:#ffffff !important;
            color:#111827 !important;
            border-bottom:1px solid #e5e7eb !important;
        }

        .excel-grid-wrap .ag-row-hover .ag-cell {
            background:#f8fafc !important;
        }

        .excel-grid-wrap .ag-cell {
            color:#111827 !important;
            background:#ffffff !important;
            border-right:1px solid #edf2f7 !important;
            border-bottom:1px solid #edf2f7 !important;
            font-size:10.5px !important;
            padding-top:1px !important;
            padding-bottom:1px !important;
            padding-left:4px !important;
            padding-right:4px !important;
            line-height:1.1 !important;
        }

        .excel-grid-wrap .ag-cell a {
            color:#1155cc !important;
            text-decoration:underline !important;
            font-weight:500 !important;
        }

        .excel-grid-wrap .ag-icon {
            color:#6b7280 !important;
        }

        .excel-grid-wrap .ag-row-selected .ag-cell {
            background:#e8f0fe !important;
        }

        .signal-header {
            margin-top:.30rem;
        }

        .grid-title {
            color:#e7ebf1;
        }

        .grid-subtitle,
        .asof-note,
        .signal-subtitle {
            color:#bcc7d6 !important;
            opacity:1 !important;
        }

        .bulletin {
            background:#0f1823 !important;
            border-color:#4a596d !important;
        }

        .bulletin-title {
            color:#ffffff;
        }

        .bulletin-copy,
        .bulletin-label {
            color:#9ba6b5;
            opacity:1 !important;
        }
        /* v1.2 layout: show the title completely above every data grid. */
        .block-container {
            padding-top:2.65rem !important;
            padding-bottom:2.5rem !important;
            max-width:1540px;
        }
        .market-pulse-page-heading {
            display:block;
            width:100%;
            text-align:center;
            font-size:clamp(1.65rem, 2.5vw, 2.45rem);
            font-weight:800;
            line-height:1.30;
            letter-spacing:-.025em;
            color:#111827 !important;
            padding:.40rem .3rem .18rem;
            margin:.25rem auto .22rem;
            overflow:visible;
        }
        .market-pulse-asof-centered {
            width:100%;
            text-align:center !important;
            font-size:.83rem !important;
            font-weight:520;
            color:#64748b !important;
            margin:.1rem auto .85rem !important;
            opacity:1 !important;
        }
        .market-bottom-heading {
            color:#111827 !important;
            text-align:center;
            font-weight:760;
            font-size:1.18rem;
            margin:2rem 0 .75rem;
            border-top:1px solid #dbe2ea;
            padding-top:1.15rem;
        }
        .bulletin {min-height:92px;}
        .bulletin-title,.bulletin-label,.bulletin-copy {
            text-align:center;
        }

        /* Layout governed by .block-container above. */
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
        
        [data-baseweb="select"] > div {
            background:#ffffff !important;
            border:1px solid #d1d5db !important;
            color:#111827 !important;
            min-height:38px !important;
        }

        [data-baseweb="select"] span,
        [data-baseweb="select"] input,
        [data-baseweb="select"] svg {
            color:#111827 !important;
            fill:#6b7280 !important;
        }

</style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <style>
        /* ============================
           DAY MODE OVERRIDES
           ============================ */
        .stApp {
            background:#f5f7fa !important;
            color:#1f2937 !important;
        }

        [data-testid="stHeader"] {
            background:rgba(245,247,250,.97) !important;
        }

        [data-testid="stSidebar"] {
            background:#ffffff !important;
            border-right:1px solid #cbd5e1 !important;
        }

        [data-testid="stSidebar"] *,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] .stMarkdown,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] div,
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {
            color:#1f2937 !important;
        }

        [data-testid="stSidebar"] [role="radiogroup"] {
            background:#f1f5f9 !important;
            border:1px solid #cbd5e1 !important;
        }

        [data-testid="collapsedControl"] {
            background:#ffffff !important;
            border:1px solid #94a3b8 !important;
            box-shadow:0 4px 14px rgba(15,23,42,.12) !important;
        }

        [data-testid="collapsedControl"]:hover {
            background:#e2e8f0 !important;
            border-color:#64748b !important;
        }

        [data-testid="collapsedControl"] svg,
        [data-testid="collapsedControl"] path,
        [data-testid="collapsedControl"] * {
            color:#334155 !important;
            fill:#334155 !important;
            stroke:#334155 !important;
        }

        [data-testid="stVerticalBlockBorderWrapper"] {
            background:#ffffff !important;
            border-color:#cbd5e1 !important;
        }

        .market-pulse-kicker {
            color:#475569 !important;
        }

        .market-card-title {
            color:#111827 !important;
        }

        .market-card-source {
            color:#64748b !important;
        }

        .market-card-quote {
            color:#7c8798 !important;
        }

        .market-card-last {
            color:#111827 !important;
        }

        .market-no-data {
            color:#64748b !important;
        }

        .signal-title,
        .grid-title {
            color:#111827 !important;
        }

        .signal-subtitle,
        .grid-subtitle,
        .asof-note {
            color:#64748b !important;
        }

        .bulletin {
            background:#ffffff !important;
            border-color:#cbd5e1 !important;
        }

        .bulletin-title {
            color:#111827 !important;
        }

        .bulletin-copy,
        .bulletin-label {
            color:#64748b !important;
        }

        /* Market Pulse v1.6: status cards are recognizable at a glance.
           Green = active bullish; red = on hold / unsupported;
           amber = softened daily conditions under an active weekly thesis. */
        .bulletin {
            border-radius:11px !important;
            box-shadow:0 1px 2px rgba(15,23,42,.06) !important;
        }
        .bulletin-valid, .bulletin-constructive {
            background:#e9f9f0 !important;
            border:1px solid #86dba4 !important;
            border-left:6px solid #16a34a !important;
        }
        .bulletin-hold, .bulletin-neutral {
            background:#fff0f1 !important;
            border:1px solid #f4a3ac !important;
            border-left:6px solid #dc2626 !important;
        }
        .bulletin-soft {
            background:#fff6df !important;
            border:1px solid #f2cf84 !important;
            border-left:6px solid #d97706 !important;
        }
        .bulletin-valid .bulletin-title,
        .bulletin-constructive .bulletin-title {color:#166534 !important;}
        .bulletin-hold .bulletin-title,
        .bulletin-neutral .bulletin-title {color:#991b1b !important;}
        .bulletin-soft .bulletin-title {color:#92400e !important;}
        .bulletin-valid .bulletin-label,
        .bulletin-constructive .bulletin-label {color:#15803d !important;opacity:1 !important;}
        .bulletin-hold .bulletin-label,
        .bulletin-neutral .bulletin-label {color:#b91c1c !important;opacity:1 !important;}
        .bulletin-soft .bulletin-label {color:#b45309 !important;opacity:1 !important;}
        .bulletin-copy {color:#334155 !important;opacity:1 !important;}

        /* Keep Streamlit controls readable in Day mode */
        [data-baseweb="select"] > div {
            background:#ffffff !important;
            border-color:#cbd5e1 !important;
            color:#111827 !important;
        }

        [data-baseweb="select"] span,
        [data-baseweb="select"] input {
            color:#111827 !important;
        }

        [role="listbox"],
        [data-baseweb="menu"] {
            background:#ffffff !important;
            color:#111827 !important;
        }

        /* Excel grid already uses a light sheet style; strengthen border */
        .excel-grid-wrap {
            background:#ffffff !important;
            border-color:#cbd5e1 !important;
        }

        /* Buttons */
        button[kind="secondary"],
        [data-testid="stDownloadButton"] button {
            background:#ffffff !important;
            color:#1f2937 !important;
            border-color:#cbd5e1 !important;
        }

        button[kind="secondary"]:hover,
        [data-testid="stDownloadButton"] button:hover {
            background:#f1f5f9 !important;
            border-color:#94a3b8 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # MARKETVECTOR_UI_FIT_V2 — light sidebar buttons after day-mode CSS
    st.markdown(_mv_sidebar_button_css(), unsafe_allow_html=True)

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

    page_heading = (
        f"{reference} Market Pulse · {timeframe.title()}"
        if home_mode else f"Market Pulse · {timeframe.title()}"
    )
    st.markdown(
        f'<h1 class="market-pulse-page-heading">{page_heading}</h1>',
        unsafe_allow_html=True,
    )

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

    analyst_asof_text = _market_pulse_analyst_asof(db, s, stock)
    st.markdown(
        f"<div class='asof-note market-pulse-asof-centered'>"
        f"Market data through {html.escape(asof_text)} · "
        f"Analyst data through {html.escape(analyst_asof_text)}"
        f"</div>",
        unsafe_allow_html=True,
    )

    # Outer spacer columns keep the two bulletin cards centered as a pair.
    _, weekly_col, setup_col, _ = st.columns(
        [0.65, 2.0, 2.0, 0.65],
        gap="medium",
        vertical_alignment="top",
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

    # Per-grid filters now use populated dropdowns above each grid.
    # Keep the sidebar focused on Reference / Timeframe selection.
    search_text = ""
    signals = []
    confidences = []
    ratings = []


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

    # Market overview comes AFTER both actionable and on-hold stock grids.
    _render_market_index_strip(day_mode=True)


# Allow Streamlit Community Cloud to launch this module directly.
# The normal wrapper entrypoint (app_market_pulse.py) still works because
# this block runs only when this file itself is the Streamlit main module.
if __name__ == "__main__":
    render_market_pulse_dashboard()

