"""Keep the compact Cloud STATUS analyst date tied to actual Yahoo records.

Runs after the Cloud SQLite copy/export; does not modify trading logic or
research tables. SQLite updates are restricted to STATUS rows in the two
client-facing actionable snapshot tables.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import re
import sqlite3


ROOT = Path(__file__).resolve().parent
DEFAULT_CLOUD = ROOT / "data" / "actionable_cloud.sqlite"
DEFAULT_LOCAL = ROOT.parent / "data" / "marketvector_tlt_tbt.sqlite"
TABLES = ("mv_actionable_dashboard_daily", "mv_actionable_dashboard_weekly")


def _valid_date(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        if re.fullmatch(r"\d{8}", s):
            d = datetime.strptime(s, "%Y%m%d")
        else:
            d = datetime.fromisoformat(s[:10])
        if 2000 <= d.year <= datetime.now().year + 1:
            return d.strftime("%Y-%m-%d")
    except ValueError:
        pass
    return None


def yahoo_asof(path: Path):
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(str(path)) as con:
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='analyst_targets'").fetchone()
            if not exists:
                return None
            cols = {r[1].lower() for r in con.execute('PRAGMA table_info("analyst_targets")')}
            date_col = "asof_date" if "asof_date" in cols else (
                "fetched_at" if "fetched_at" in cols else None
            )
            if not date_col:
                return None
            where = "WHERE LOWER(COALESCE(source,'')) LIKE '%yahoo%'" if "source" in cols else ""
            rows = con.execute(
                f'SELECT DISTINCT "{date_col}" FROM analyst_targets {where} ORDER BY "{date_col}" DESC LIMIT 300'
            )
            dates = [_valid_date(row[0]) for row in rows]
            return max((d for d in dates if d), default=None)
    except (sqlite3.Error, OSError) as exc:
        print(f"WARN: Could not read Yahoo analyst dates from {path}: {exc}")
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cloud-db", type=Path, default=DEFAULT_CLOUD)
    parser.add_argument("--source-db", type=Path, default=DEFAULT_LOCAL)
    args = parser.parse_args()

    cloud = args.cloud_db.resolve()
    source = args.source_db.resolve()
    if not cloud.is_file():
        print(f"WARN: Cloud DB unavailable for analyst-date stamp: {cloud}")
        return

    asof = yahoo_asof(source) or yahoo_asof(cloud)
    if not asof:
        print("WARN: No dated Yahoo analyst records found; existing STATUS date unchanged.")
        return

    with sqlite3.connect(str(cloud)) as con:
        for table in TABLES:
            if not con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                print(f"WARN: {table} missing; skipping")
                continue
            cols = {r[1].lower() for r in con.execute(f'PRAGMA table_info("{table}")')}
            if not {"row_type", "analyst_asof"}.issubset(cols):
                print(f"WARN: {table} has no STATUS analyst_asof column; skipping")
                continue
            before = con.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]
            updated = con.execute(
                f'''UPDATE "{table}"
                    SET analyst_asof=?
                    WHERE UPPER(TRIM(COALESCE(row_type,'')))='STATUS' ''',
                (asof,),
            ).rowcount
            after = con.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]
            assert before == after, "Row count changed during metadata stamp"
            print(f"{table}: analyst_asof={asof}, STATUS rows updated={updated}")
        con.commit()
    print("MARKET PULSE ANALYST DATE: STAMP COMPLETE")


if __name__ == "__main__":
    main()
