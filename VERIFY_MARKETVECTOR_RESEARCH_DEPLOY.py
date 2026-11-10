"""Read-only source + compact DB pre-push safety gate for MarketVector Research."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

from RESTORE_MARKETVECTOR_RESEARCH import inspect


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--root',default=r'C:\TLT-TBT-Trading System')
    p.add_argument('--strict-data',action='store_true')
    args=p.parse_args(argv)
    try:
        problems=inspect(Path(args.root),strict_data=args.strict_data)
        if problems:
            print('MARKETVECTOR RESEARCH DEPLOY: FAILED, no GitHub push permitted')
            return 1
    except Exception as ex:
        print('MARKETVECTOR RESEARCH DEPLOY: FAILED:',ex,file=sys.stderr)
        return 1
    print('MARKETVECTOR RESEARCH DEPLOY: PASS')
    return 0

if __name__=='__main__':sys.exit(main())
