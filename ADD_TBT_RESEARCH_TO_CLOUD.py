"""Copy only qualified TBT research rows from the master into the compact cloud DB.

Run after the existing prepare step has copied actionable_cloud.sqlite to Cloud/data.
"""
from pathlib import Path
import json
import sys
from tbt_research_core import export, DEFAULTS

HERE=Path(__file__).resolve().parent
SOURCE=HERE.parent/'data'/'marketvector_tlt_tbt.sqlite'
CLOUD=HERE/'data'/'actionable_cloud.sqlite'
CONFIG=HERE/'tbt_research_config.json'

if __name__=='__main__':
    try:
        options=json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.exists() else dict(DEFAULTS)
        info=export(SOURCE,CLOUD,options)
        print('TBT POSITIVE RESEARCH CLOUD EXPORT: PASS')
        print('Source master (READ ONLY):',SOURCE)
        print('Cloud target:',CLOUD)
        print('Qualified candidates:',info['candidate_count'])
        print('Weekly Buy gate:',info['gate']['gate'],'| direction:',info['gate']['master_direction'],
              '| status:',info['gate']['weekly_status'])
        print('Research source stats:',info['stats'])
        print('As of (UTC):',info['updated'])
    except Exception as err:
        print('TBT POSITIVE RESEARCH CLOUD EXPORT: FAILED:',str(err),file=sys.stderr)
        sys.exit(1)
