from __future__ import annotations

from pathlib import Path
import py_compile
import sqlite3

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "actionable_cloud.sqlite"

APP_FILES = [
    "client_actionable_dashboard.py",
    "market_pulse_dashboard.py",
    "app_tlt_actionable_daily_fast.py",
    "app_tlt_actionable_weekly_fast.py",
    "app_tbt_actionable_daily_fast.py",
    "app_tbt_actionable_weekly_fast.py",
    "app_market_pulse.py",
]

REQUIRED_TABLES = [
    "mv_actionable_dashboard_daily",
    "mv_actionable_dashboard_weekly",
]


def fail(message: str) -> None:
    print(f"ERROR: {message}")
    raise SystemExit(1)


print("=" * 88)
print("TLT/TBT STREAMLIT CLOUD PACKAGE VALIDATION")
print("=" * 88)
print(f"Cloud directory: {ROOT}")
print(f"Database:        {DB}")

if not DB.exists():
    fail(
        "data/actionable_cloud.sqlite is missing. "
        "Run 01_PREPARE_CLOUD.bat after placing this Cloud folder inside "
        r"C:\TLT-TBT-Trading System."
    )

head = DB.read_bytes()[:64]
if head.startswith(b"version https://git-lfs.github.com/spec"):
    fail("The local SQLite file is only a Git LFS pointer, not the actual database.")

print(f"Database size:   {DB.stat().st_size / 1024 / 1024:.2f} MB")

for name in APP_FILES:
    path = ROOT / name
    if not path.exists():
        fail(f"Missing application file: {name}")
    py_compile.compile(str(path), doraise=True)

print("Python compile:  PASS")

with sqlite3.connect(str(DB)) as con:
    existing = {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    for table in REQUIRED_TABLES:
        if table not in existing:
            fail(f"Required table is missing: {table}")

        cols = [
            r[1]
            for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()
        ]

        row_type_col = "row_type" if "row_type" in cols else None
        ref_col = "reference" if "reference" in cols else None
        symbol_col = (
            "symbol" if "symbol" in cols
            else ("ticker" if "ticker" in cols else None)
        )

        total = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        if total <= 0:
            fail(f"{table} is empty.")

        print(f"\n{table}: total={total}")

        if row_type_col:
            dist = con.execute(
                f'''
                SELECT UPPER(TRIM(COALESCE("{row_type_col}",''))) AS rt,
                       COUNT(*)
                FROM "{table}"
                GROUP BY 1
                ORDER BY 2 DESC
                '''
            ).fetchall()
            print(
                "  row_type: "
                + " | ".join(f"{rt or '(blank)'}={n}" for rt, n in dist)
            )

            status_count = con.execute(
                f'''
                SELECT COUNT(*)
                FROM "{table}"
                WHERE UPPER(TRIM(COALESCE("{row_type_col}",'')))='STATUS'
                '''
            ).fetchone()[0]

            if status_count != 2:
                fail(f"{table}: expected 2 STATUS rows, found {status_count}.")

        if ref_col and row_type_col:
            by_ref = con.execute(
                f'''
                SELECT UPPER(TRIM(COALESCE("{ref_col}",''))) AS ref,
                       COUNT(*)
                FROM "{table}"
                WHERE UPPER(TRIM(COALESCE("{row_type_col}",'')))='STOCK'
                GROUP BY 1
                ORDER BY 1
                '''
            ).fetchall()
            print(
                "  stock rows: "
                + " | ".join(f"{ref}={n}" for ref, n in by_ref)
            )

        if symbol_col and ref_col and row_type_col:
            overlaps = con.execute(
                f'''
                SELECT UPPER(TRIM("{symbol_col}")) AS symbol
                FROM "{table}"
                WHERE UPPER(TRIM(COALESCE("{row_type_col}",'')))='STOCK'
                  AND TRIM(COALESCE("{symbol_col}",'')) <> ''
                GROUP BY 1
                HAVING COUNT(DISTINCT UPPER(TRIM("{ref_col}"))) > 1
                ORDER BY 1
                '''
            ).fetchall()

            if overlaps:
                examples = ", ".join(r[0] for r in overlaps[:20])
                fail(
                    f"{table}: TLT/TBT ownership overlap remains for "
                    f"{len(overlaps)} symbols. Examples: {examples}"
                )

            print("  ownership: PASS - no TLT/TBT stock overlap")

print("\n" + "=" * 88)
print("CLOUD PACKAGE VALIDATION: PASS")
print("=" * 88)
