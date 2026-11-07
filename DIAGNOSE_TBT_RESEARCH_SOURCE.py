"""Read-only TBT research mapping diagnostic; no secrets or DB writes."""
from pathlib import Path
from contextlib import closing
import sqlite3
import json
from tbt_research_core import source_universe, best_records, DEFAULTS, weekly_gate

master=Path(__file__).resolve().parent.parent/'data'/'marketvector_tlt_tbt.sqlite'
print('TBT research source schema diagnosis v1.1 - READ ONLY')
print('Master DB:',master)
if not master.exists():
    raise SystemExit('Missing master DB; run from installed Cloud folder.')
with closing(sqlite3.connect(master.resolve().as_uri()+'?mode=ro',uri=True)) as con:
    for table in ('mv_tbt_stock_universe_current','mv_tbt_stock_reversal_leadlag_day','mv_tbt_etf_relationship_week','mv_actionable_dashboard_weekly'):
        print('\nTABLE:',table)
        exists=con.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",(table,)).fetchone()
        if not exists:
            print('  NOT FOUND')
            continue
        print('  Rows:',con.execute('SELECT count(*) FROM "'+table+'"').fetchone()[0])
        columns=[r[1] for r in con.execute('PRAGMA table_info("'+table+'")')]
        print('  Columns:',', '.join(columns))
        if table=='mv_tbt_stock_universe_current':
            for row in con.execute('SELECT * FROM "'+table+'" LIMIT 4'):
                print('  Example:',json.dumps({k:str(v)[:80] for k,v in zip(columns,row)}))
        if table=='mv_tbt_stock_reversal_leadlag_day':
            for row in con.execute('SELECT * FROM "'+table+'" LIMIT 2'):
                print('  Example:',json.dumps({k:str(v)[:80] for k,v in zip(columns,row)}))
        if table=='mv_actionable_dashboard_weekly':
            colnames=[c for c in ('reference','row_type','weekly_status','reference_weekly_master_direction','reference_weekly_master_date','reference_asof') if c in columns]
            if colnames and {'reference','row_type'} <= set(columns):
                selected=','.join('"'+c+'"' for c in colnames)
                for row in con.execute(f"SELECT {selected} FROM \"{table}\" WHERE reference='TBT' AND row_type='STATUS' LIMIT 2"):
                    print('  TBT status:',json.dumps(dict(zip(colnames,row)),default=str))
    try:
        universe=source_universe(con)
        print('\nUNIVERSE: PASS, mapped stocks=',len(universe))
        print('  Sample symbol/ETF maps:',[(k,sorted(v)) for k,v in list(universe.items())[:8]])
        picks,stats=best_records(con,universe,DEFAULTS)
        print('LEAD-LAG: PASS, research candidates=',len(picks),'source stats=',stats)
        print('WEEKLY GATE:',weekly_gate(con))
    except Exception as exc:
        print('\nSOURCE MAPPING ISSUE:',exc)
        print('No changes made. Send this output for an exact source mapping fix.')
