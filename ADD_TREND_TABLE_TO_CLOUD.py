"""Copy the compact local trend diagnostic table into the existing Cloud DB.

Non-destructive to the monthly comparison tables or the main client snapshots.
"""
from pathlib import Path
import sqlite3
import pandas as pd

CLOUD = Path(__file__).resolve().parent
ROOT = CLOUD.parent
SRC = ROOT / "data" / "marketvector_tlt_tbt.sqlite"
DST = CLOUD / "data" / "actionable_cloud.sqlite"
TABLE = "mv_stock_daily_trend_overlay"


def main():
    if not SRC.exists():
        raise FileNotFoundError(f"Project database missing: {SRC}")
    if not DST.exists():
        raise FileNotFoundError(f"Compact Cloud database missing: {DST}")
    with sqlite3.connect(str(SRC)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
            raise RuntimeError(f"{TABLE} has not been built. Run APPLY_UPTREND_TO_EXISTING_SNAPSHOTS.py first.")
        df = pd.read_sql_query(f'SELECT * FROM "{TABLE}"', con)
    with sqlite3.connect(str(DST)) as con:
        df.to_sql(TABLE, con, if_exists="replace", index=False)
        con.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_symbol ON "{TABLE}"(symbol)')
        con.commit()
    print(f"Cloud trend diagnostics: {TABLE} ({len(df)} stocks) copied; existing monthly tables preserved.")


if __name__ == '__main__':
    main()
