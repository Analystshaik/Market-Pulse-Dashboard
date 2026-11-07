"""Read-only proof that research rows have matching prices/technical context."""
from __future__ import annotations

from pathlib import Path
from contextlib import closing
import sqlite3
import sys

WATCH = 'mv_tlt_positive_research_watch'
CONTEXT = 'mv_tlt_research_stock_context'


def validate(db_path: Path) -> dict:
    if not db_path.exists():
        raise RuntimeError(f'Cloud SQLite not found: {db_path}')
    with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in (WATCH, CONTEXT):
            if name not in tables:
                raise RuntimeError(f'Missing table {name}; run the research export before cloud deployment')
        cols = {r[1] for r in con.execute('PRAGMA table_info("' + CONTEXT + '")')}
        need = {'symbol', 'latest_close', 'daily_rsi14', 'weekly_rsi14'}
        if not need.issubset(cols):
            raise RuntimeError(f'Research context is missing columns: {sorted(need - cols)}')
        total = con.execute('SELECT COUNT(DISTINCT symbol) FROM "' + WATCH + '"').fetchone()[0]
        count = con.execute('SELECT COUNT(DISTINCT symbol) FROM "' + CONTEXT + '"').fetchone()[0]
        matched = con.execute('SELECT COUNT(*) FROM "' + WATCH + '" w JOIN "' + CONTEXT + '" c ON w.symbol=c.symbol').fetchone()[0]
        prices, drsi, wrsi = con.execute('SELECT SUM(CASE WHEN latest_close>0 THEN 1 ELSE 0 END), SUM(CASE WHEN daily_rsi14 IS NOT NULL THEN 1 ELSE 0 END), SUM(CASE WHEN weekly_rsi14 IS NOT NULL THEN 1 ELSE 0 END) FROM "' + CONTEXT + '"').fetchone()
        prices, drsi, wrsi = int(prices or 0), int(drsi or 0), int(wrsi or 0)
    if total <= 0 or count != total or matched != total:
        raise RuntimeError(f'Incomplete research context: research={total} context={count} matched={matched}')
    if prices == 0:
        raise RuntimeError(f'ALL {count} research prices are missing. Do not publish blank research context.')
    return {'research_stocks': total, 'context_rows': count, 'price_rows': prices,
            'daily_rsi_rows': drsi, 'weekly_rsi_rows': wrsi}


if __name__ == '__main__':
    db = Path(__file__).resolve().parent / 'data' / 'actionable_cloud.sqlite'
    try:
        result = validate(db)
        print('TLT RESEARCH CONTEXT VALIDATION: PASS', result)
        if result['price_rows'] < result['research_stocks']:
            print('WARNING: Some stocks lack current prices; inspect upstream bar coverage.')
        if result['daily_rsi_rows'] == 0 or result['weekly_rsi_rows'] == 0:
            print('WARNING: RSI coverage missing; inspect historical quote depth.')
    except Exception as exc:
        print('TLT RESEARCH CONTEXT VALIDATION: FAILED:', str(exc), file=sys.stderr)
        sys.exit(1)
