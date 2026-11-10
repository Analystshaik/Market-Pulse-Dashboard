from __future__ import annotations
from pathlib import Path
import py_compile, sqlite3
ROOT=Path(__file__).resolve().parent; DB=ROOT/"data"/"actionable_cloud.sqlite"
BASE_APPS=["client_actionable_dashboard.py","market_pulse_dashboard.py","app_tlt_actionable_daily_fast.py","app_tlt_actionable_weekly_fast.py","app_tbt_actionable_daily_fast.py","app_tbt_actionable_weekly_fast.py","app_market_pulse.py"]
BASE_TABLES=["mv_actionable_dashboard_daily","mv_actionable_dashboard_weekly"]
MONTHLY_APPS=["monthly_tlt_comparison_dashboard.py","app_monthly_tlt_comparison.py"]
MONTHLY_TABLES=["mv_index_constituents_current","mv_tlt_monthly_bars","mv_tlt_reversals_month","mv_tlt_monthly_stock_relationship","mv_tlt_monthly_data_coverage","mv_tlt_monthly_current_snapshot","mv_tlt_monthly_index_event_summary"]
def fail(m): print("ERROR:",m); raise SystemExit(1)
print("="*88); print("TLT/TBT STREAMLIT CLOUD PACKAGE VALIDATION"); print("="*88)
if not DB.exists(): fail(f"Missing {DB}")
if DB.read_bytes()[:64].startswith(b"version https://git-lfs.github.com/spec"): fail("SQLite is a Git LFS pointer, not actual DB")
monthly_enabled=all((ROOT/p).exists() for p in MONTHLY_APPS)
for p in BASE_APPS+(MONTHLY_APPS if monthly_enabled else []):
    if not (ROOT/p).exists(): fail(f"Missing app file: {p}")
    py_compile.compile(str(ROOT/p),doraise=True)
with sqlite3.connect(str(DB)) as con:
    existing={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in BASE_TABLES+(MONTHLY_TABLES if monthly_enabled else []):
        if t not in existing: fail(f"Missing required cloud table: {t}")
        n=con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        if n<=0: fail(f"Cloud table is empty: {t}")
        print(f"{t}: {n} rows")
    for t in BASE_TABLES:
        cols=[r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
        if "row_type" in cols:
            sql = f"SELECT COUNT(*) FROM \"{t}\" WHERE UPPER(TRIM(COALESCE(row_type,'')))='STATUS'"
            n=con.execute(sql).fetchone()[0]
            if n!=2: fail(f"{t}: expected 2 STATUS rows, found {n}")
print("CLOUD PACKAGE VALIDATION: PASS")
