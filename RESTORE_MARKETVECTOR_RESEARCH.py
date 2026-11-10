"""Safe, idempotent MarketVector research page and embedded-grid restoration.

Does not modify analysis engines, trade rules, any SQLite database, or research
modules that already exist. Restores missing files only from packaged copies.
Python 3.10+. Usage: python RESTORE_MARKETVECTOR_RESEARCH.py --root ROOT --repair
"""
from __future__ import annotations

import argparse
import ast
from contextlib import closing
from datetime import datetime
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from typing import Optional

VERSION = '1.0.2'
HERE = Path(__file__).resolve().parent
BUNDLED = HERE / 'research_src'
PORTAL = 'app_secure_portal.py'
CLIENT = 'client_actionable_dashboard.py'
PREPARE = '01_PREPARE_CLOUD.bat'
PUSH = '03_PUSH_CLOUD_UPDATE.bat'
VERIFY = 'VERIFY_MARKETVECTOR_RESEARCH_DEPLOY.py'
REQUIRED = {
    'TLT Research / Watch': ('app_tlt_positive_research.py', 'tlt_research_client_grid.py'),
    'TBT Positive Research / Watch': ('app_tbt_positive_research.py', 'tbt_positive_research_dashboard.py'),
}
PULSE_OPTIONAL = {
    'TLT Pulse Daily': ('app_tlt_market_pulse_daily.py', 'market_pulse_dashboard.py'),
    'TBT Pulse Daily': ('app_tbt_market_pulse_daily.py', 'market_pulse_dashboard.py'),
}
RESEARCH_TABLES = (
    'mv_tlt_positive_research_watch', 'mv_tlt_research_export_meta',
    'mv_tlt_research_stock_context', 'mv_tbt_positive_research_watch',
    'mv_tbt_research_gate_status', 'mv_tbt_research_stock_context',
)
EXPORT_STEPS = (
    ('ADD_TBT_RESEARCH_TO_CLOUD.py', 'TBT research export'),
    ('tbt_research_client_context.py', 'TBT research context'),
    ('tlt_research_export.py', 'TLT research export'),
    ('tlt_research_client_context.py', 'TLT research context'),
)
GRID_HOOKS = {
    'TLT': '''    # MV_RESEARCH_ROUTE_RESTORE_V1: research-only after On Hold.
    if reference.upper() == "TLT":
        from tlt_research_client_grid import render_research_grid
        render_research_grid(db, timeframe, filter_state)

''',
    'TBT': '''    # MV_RESEARCH_ROUTE_RESTORE_V1: research-only after On Hold.
    if reference.upper() == "TBT":
        from tbt_research_client_grid import render_research_grid
        render_research_grid(db_path=db, timeframe=timeframe, filter_state=filter_state)

'''
}


def cloud_for(root: Path) -> Path:
    """Resolve actual deployment directory; NEVER prefer an accidental root copy.

    v1.0 chose root/app_secure_portal.py before root/Cloud/app_secure_portal.py,
    causing the local MASTER project folder to be patched and the Cloud DB to
    be falsely reported missing. Prefer Cloud when invoking from a project root.
    """
    root = root.expanduser().resolve()
    if root.name.casefold() == 'cloud':
        if (root / PORTAL).is_file():
            return root
        raise RuntimeError(f'Explicit Cloud folder is missing {PORTAL}: {root}')
    cloud = root / 'Cloud'
    if cloud.is_dir():
        if (cloud / PORTAL).is_file():
            return cloud
        raise RuntimeError(f'Project has Cloud folder but its real portal is missing: {cloud / PORTAL}. '
                           'Will NOT patch root copy; investigate Cloud folder first.')
    # Also support a standalone deploy folder not named Cloud, but only when
    # there is NO nested Cloud folder that should have been selected.
    if (root / PORTAL).is_file():
        return root
    raise RuntimeError(f'Cannot find a deployable {PORTAL} in {root} or {cloud}')


def read(path: Path) -> str:
    # Keep CRLF byte-for-byte through the Windows batch and Python patch stages.
    with path.open('r', encoding='utf-8-sig', newline='') as fh:
        return fh.read()


def write_exact(path: Path, body: str):
    with path.open('w', encoding='utf-8', newline='') as fh:
        fh.write(body)


def parse(text: str, label: str):
    try:
        return ast.parse(text, filename=label, feature_version=(3, 10))
    except SyntaxError as ex:
        raise RuntimeError(f'{label}: invalid Python syntax on line {ex.lineno}: {ex.msg}') from ex


def navigation_list(tree: ast.Module) -> ast.List:
    nodes = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(t, ast.Name) and t.id == 'DASHBOARDS' for t in node.targets):
            nodes.append(node.value)
    if len(nodes) != 1 or not isinstance(nodes[0], ast.List):
        raise RuntimeError('Portal DASHBOARDS literal navigation list not found. No blind rewrite allowed.')
    return nodes[0]


def nav_labels(source: str) -> set[str]:
    tree = parse(source, PORTAL)
    node = navigation_list(tree)
    labels = set()
    for x in node.elts:
        if isinstance(x, (ast.Tuple, ast.List)) and len(x.elts) >= 4 and isinstance(x.elts[0], ast.Constant):
            labels.add(str(x.elts[0].value))
    return labels


def _place_nav_item(source: str, entry: str, after_label: str) -> str:
    pattern = re.compile(r'(?m)^[ \t]*\([\"\']' + re.escape(after_label) + r'[\"\'][^\n]*\),[ \t]*(?:\#.*)?\r?\n')
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise RuntimeError(f'Cannot find exactly one portal navigation anchor {after_label!r}; no files changed')
    m = matches[0]
    return source[:m.end()] + '    ' + entry + ('\r\n' if '\r\n' in source else '\n') + source[m.end():]


def patch_portal(source: str, cloud: Path) -> str:
    if 'require_login' not in source or 'st.navigation(' not in source or 'st.page_link(' not in source:
        raise RuntimeError('Portal authentication/navigation not recognized; refusing changes')
    labels = nav_labels(source)
    for label, (page, dep) in REQUIRED.items():
        if label in labels:
            continue
        if label.startswith('TLT'):
            line = '("TLT Research / Watch", "app_tlt_positive_research.py", "🔎", "tlt_research_client_grid.py"),'
            source = _place_nav_item(source, line, 'TLT Weekly')
        else:
            line = '("TBT Positive Research / Watch", "app_tbt_positive_research.py", "🔎", "tbt_positive_research_dashboard.py"),'
            source = _place_nav_item(source, line, 'TBT Weekly')
        labels = nav_labels(source)
    # Restore previously-installed fixed-reference Pulse links ONLY if their
    # entrypoint files exist; do not create a new renderer or change Home.
    for label, (page, dep) in PULSE_OPTIONAL.items():
        if label in labels or not (cloud / page).is_file():
            continue
        if label.startswith('TLT'):
            source = _place_nav_item(source,
                '("TLT Pulse Daily", "app_tlt_market_pulse_daily.py", "📈", "market_pulse_dashboard.py"),',
                'TLT Weekly')
        else:
            source = _place_nav_item(source,
                '("TBT Pulse Daily", "app_tbt_market_pulse_daily.py", "📉", "market_pulse_dashboard.py"),',
                'TBT Weekly')
        labels = nav_labels(source)
    parse(source, PORTAL)
    missing = set(REQUIRED) - nav_labels(source)
    if missing:
        raise RuntimeError(f'Portal restoration incomplete: {sorted(missing)}')
    return source


def render_function(tree: ast.Module) -> ast.FunctionDef:
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'render_client_dashboard']
    if len(funcs) != 1:
        raise RuntimeError('Could not locate unique render_client_dashboard() function')
    return funcs[0]


def grid_hook_refs(source: str) -> set[str]:
    tree = parse(source, CLIENT)
    fn = render_function(tree)
    # Only count hooks that execute before the optional 'show_more' sections.
    # A research call accidentally nested there is hidden when show_more=False.
    breakpoints = [n.lineno for n in fn.body if isinstance(n,ast.If) and
                   isinstance(n.test,ast.Name) and n.test.id == 'show_more']
    cutoff = min(breakpoints) if breakpoints else fn.end_lineno + 1
    imports = {n.module for n in ast.walk(fn) if isinstance(n, ast.ImportFrom)
               and n.lineno < cutoff}
    return {ref for ref in GRID_HOOKS if f'{ref.lower()}_research_client_grid' in imports}


def patch_client(source: str) -> str:
    tree = parse(source, CLIENT)
    fn = render_function(tree)
    installed = grid_hook_refs(source)
    missing = set(GRID_HOOKS) - installed
    if not missing:
        return source
    # Inject immediately after the primary Hold grid and before the optional
    # section breakdown. Never bypass the existing Buy/Hold qualification code.
    anchors = [node for node in fn.body if isinstance(node, ast.If) and
               isinstance(node.test, ast.Name) and node.test.id == 'show_more']
    if len(anchors) != 1:
        raise RuntimeError('Missing unique if show_more: anchor in render_client_dashboard; no files changed')
    anchor = anchors[0]
    lines = source.splitlines(keepends=True)
    index = sum(len(x) for x in lines[:anchor.lineno-1])
    fn_start = sum(len(x) for x in lines[:fn.lineno-1])
    preceding = source[fn_start:index]
    # Verify original Hold section before insertion, regardless of slight changes
    if not re.search(r'(?s)hold_list.*?_render_grid\(', preceding):
        raise RuntimeError('Cannot verify the existing On Hold grid before insertion; refusing to guess')
    insertion = ''.join(GRID_HOOKS[ref] for ref in ('TBT', 'TLT') if ref in missing)
    if '\r\n' in source: insertion = insertion.replace('\n','\r\n')
    new_source = source[:index] + insertion + source[index:]
    parse(new_source, CLIENT)
    if grid_hook_refs(new_source) != {'TLT','TBT'}:
        raise RuntimeError('Failed to install both embedded research grids')
    return new_source


def patch_prepare(batch: str) -> str:
    # Rebuild order matters: research exports populate the compact cloud DB,
    # contexts run only AFTER their corresponding research tables exist.
    found = {script: bool(re.search(r'(?im)^\s*(?:python|py(?:thon)?(?:\.exe)?)\s+' + re.escape(script) + r'(?:\s|$)', batch))
             for script, _ in EXPORT_STEPS}
    if all(found.values()):
        return batch
    validator = re.compile(r'(?im)^\s*python\s+validate_cloud_package\.py(?:\s.*)?\r?\n')
    matches = list(validator.finditer(batch))
    if len(matches) != 1:
        raise RuntimeError('Cannot find unique Cloud validation command in 01_PREPARE_CLOUD.bat')
    eol = '\r\n' if '\r\n' in batch else '\n'
    to_add=[]
    for script, desc in EXPORT_STEPS:
        if not found[script]:
            to_add.extend([f'REM MV_RESEARCH_RESTORE_V1 -- {desc}', f'python {script}',
                           'if errorlevel 1 (', f'    echo ERROR: {desc} failed; CLOUD PUSH BLOCKED.',
                           '    exit /b 1', ')', ''])
    # If some commands exist, inserting before the validator runs them later.
    # Therefore ensure the original order is sound, else stop for a manual review.
    commands = []
    for script, _ in EXPORT_STEPS:
        match = re.search(r'(?im)^\s*(?:python|py(?:thon)?(?:\.exe)?)\s+' + re.escape(script) + r'(?:\s|$)',batch)
        if match:
            commands.append((script,match.start()))
    if commands and commands != sorted(commands, key=lambda x: [n for n,_ in EXPORT_STEPS].index(x[0])):
        raise RuntimeError('Existing Cloud research exporter order is unexpected; refusing automatic reorder')
    missing_pos=[n for n,_ in EXPORT_STEPS if not found[n]]
    existing_pos=[n for n,_ in EXPORT_STEPS if found[n]]
    # Safe insertion is possible if all missing exports form a suffix, i.e.
    # anything inserted before validator must run after the existing commands.
    if existing_pos and missing_pos and max([n for n,_ in EXPORT_STEPS].index(n) for n in existing_pos) > min([n for n,_ in EXPORT_STEPS].index(n) for n in missing_pos):
        raise RuntimeError('Cloud research exporter commands are interleaved/missing; reinstall original exporter add-ons before repair')
    m=matches[0]
    out=batch[:m.start()] + eol.join(to_add)+eol +batch[m.start():]
    return out


def patch_push(batch: str) -> str:
    if re.search(r'(?im)^\s*python\s+VERIFY_MARKETVECTOR_RESEARCH_DEPLOY\.py\b',batch):
        return batch
    if 'call 01_PREPARE_CLOUD.bat' not in batch and 'call "01_PREPARE_CLOUD.bat"' not in batch:
        raise RuntimeError('Unrecognized GitHub push script: no Cloud prep call; refusing modification')
    anchor = re.compile(r'(?im)^\s*git\s+(?:lfs\s+install|add\s+)')
    matches=list(anchor.finditer(batch))
    if not matches:
        raise RuntimeError('Cannot locate Git steps in Cloud push script')
    m=matches[0]
    eol='\r\n' if '\r\n' in batch else '\n'
    block=eol.join(['REM MV_RESEARCH_DEPLOY_GUARD_V1 -- Fail closed if research pages/grids/data are missing.',
                    'python VERIFY_MARKETVECTOR_RESEARCH_DEPLOY.py --root ".." --strict-data',
                    'if errorlevel 1 (',
                    '    echo ERROR: MarketVector Research deployment check failed. Nothing pushed.',
                    '    exit /b 1', ')',''])+eol
    return batch[:m.start()] + block + batch[m.start():]


def source_status(cloud: Path):
    portal = cloud / PORTAL
    client = cloud / CLIENT
    if not portal.is_file() or not client.is_file():
        raise RuntimeError('Portal or client trading dashboard source missing')
    p = read(portal); c = read(client)
    labels=nav_labels(p)
    hooks=grid_hook_refs(c)
    return labels,hooks


def research_data_status(cloud: Path) -> dict[str, Optional[int]]:
    path=cloud/'data'/'actionable_cloud.sqlite'
    if not path.is_file():
        return {n:None for n in RESEARCH_TABLES}
    if path.read_bytes()[:80].startswith(b'version https://git-lfs.github.com/spec'):
        raise RuntimeError('Cloud SQLite file is a Git LFS pointer, not a database')
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as con:
            existing={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return {n: con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
                    if n in existing else None for n in RESEARCH_TABLES}
    except sqlite3.Error as e:
        raise RuntimeError(f'Cloud research SQLite cannot be read ({type(e).__name__}): {e}') from e


def local_git_info(cloud:Path):
    def capture(*args):
        try:
            p=subprocess.run(['git','-C',str(cloud),*args],capture_output=True,text=True,timeout=7)
            return p.stdout.strip() if p.returncode==0 else '(unavailable)'
        except (OSError,subprocess.TimeoutExpired):
            return '(unavailable)'
    return capture('remote','get-url','origin'), capture('branch','--show-current'), capture('rev-parse','--short','HEAD')


def inspect(root:Path, strict_data=False, verbose=True):
    cloud=cloud_for(root)
    labels,hooks=source_status(cloud)
    missing=[]
    if verbose:
        print('MarketVector Research Restore v'+VERSION)
        print('Cloud:',cloud)
        if root.expanduser().resolve() != cloud and (root.expanduser().resolve() / PORTAL).is_file():
            print('NOTICE: Duplicate root-level portal detected and IGNORED; actual Cloud selected.')
    for label,(entry,dep) in REQUIRED.items():
        registered=label in labels
        entry_ok=(cloud/entry).is_file()
        dep_ok=(cloud/dep).is_file()
        if verbose:print(f'NAV: {label} | registered={registered} | entry={entry_ok} | dependency={dep_ok}')
        if not (registered and entry_ok and dep_ok):missing.append(f'{label}: navigation/entrypoint/dependency missing')
    for ref in ('TLT','TBT'):
        if verbose:print(f'GRID: {ref} research third grid | hook={ref in hooks}')
        if ref not in hooks:missing.append(f'{ref} embedded research grid hook missing')
    for key in ['research_compact_columns.py','tlt_research_export.py','tlt_research_client_context.py',
                'ADD_TBT_RESEARCH_TO_CLOUD.py','tbt_research_client_context.py',
                'tbt_research_core.py','tbt_research_config.json']:
        if not (cloud/key).is_file():missing.append(f'missing {key}')
    for py in ['app_tlt_positive_research.py','app_tbt_positive_research.py',
               'tlt_research_client_grid.py','tbt_research_client_grid.py',
               'research_compact_columns.py','tbt_positive_research_dashboard.py']:
        if (cloud/py).is_file():
            try: parse(read(cloud/py),py)
            except RuntimeError as err:missing.append(str(err))
    for label,(entry,dep) in PULSE_OPTIONAL.items():
        if verbose:print(f'OTHER: {label} | entry={(cloud/entry).is_file()} | registered={label in labels}')
        if (cloud/entry).is_file() and label not in labels:
            missing.append(f'{label}: existing page not registered')
        if label in labels and not (cloud/entry).is_file():
            missing.append(f'{label}: navigation points to missing page')
    prepare=cloud/PREPARE;push=cloud/PUSH
    if not prepare.is_file():missing.append(f'{PREPARE} is missing')
    else:
        b=read(prepare)
        for script,_ in EXPORT_STEPS:
            if not re.search(r'(?im)^\s*python\s+'+re.escape(script)+r'(?:\s|$)',b):
                missing.append(f'Cloud preparation missing {script}')
    if not push.is_file():missing.append(f'{PUSH} is missing')
    elif not re.search(r'(?im)^\s*python\s+VERIFY_MARKETVECTOR_RESEARCH_DEPLOY\.py\b',read(push)):
        missing.append('Push guard not installed')
    for filename in (VERIFY,'RESTORE_MARKETVECTOR_RESEARCH.py'):
        if not (cloud/filename).is_file():missing.append(f'{filename} is missing')
    data=research_data_status(cloud)
    if verbose:
        print('CLOUD DB:', cloud/'data'/'actionable_cloud.sqlite')
        for name,count in data.items():print(f'  {name}: {"MISSING" if count is None else str(count)+" rows"}')
        git=local_git_info(cloud)
        print('GIT: remote='+git[0]+' | branch='+git[1]+' | commit='+git[2])
    if strict_data:
        for name,count in data.items():
            if count is None:missing.append(f'Cloud DB missing research table {name}')
        for name in ('mv_tlt_positive_research_watch','mv_tbt_positive_research_watch'):
            if data.get(name) == 0:
                missing.append(f'Cloud DB research table {name} is empty')
    if verbose:
        if missing:
            print('\nRESEARCH RESTORE: NOT READY')
            for item in missing:print(' - '+item)
        else:print('RESEARCH RESTORE: PASS')
    return missing


def prepare_changes(root: Path):
    cloud=cloud_for(root)
    requested={}
    files_to_patch={PORTAL:lambda s:patch_portal(s,cloud),CLIENT:patch_client,
                    PREPARE:patch_prepare,PUSH:patch_push}
    for name,fn in files_to_patch.items():
        p=cloud/name
        if not p.is_file():raise RuntimeError(f'Required Cloud file is missing: {p}')
        old=read(p);new=fn(old)
        if name.endswith('.py'):parse(new,name)
        if new!=old:requested[name]=new
    # Missing source modules only: never overwrite local code from newer fixes.
    for source in sorted(BUNDLED.iterdir()):
        if not source.is_file():continue
        dest=cloud/source.name
        if not dest.exists():
            body=source.read_text(encoding='utf-8')
            if source.suffix=='.py':parse(body,source.name)
            requested[source.name]=body
    # The deployed pre-push verifier imports the restoration inspector, so
    # copy BOTH scripts into Cloud (not only the verifier).
    for filename in (VERIFY, 'RESTORE_MARKETVECTOR_RESEARCH.py'):
        helper=HERE/filename
        if not helper.is_file():raise RuntimeError(f'Package missing verification helper {filename}')
        content=helper.read_text(encoding='utf-8')
        parse(content,filename)
        if not (cloud/filename).is_file() or read(cloud/filename)!=content:
            requested[filename]=content
    return cloud,requested


def repair(root:Path):
    cloud,changes=prepare_changes(root)
    if not changes:
        print('No restoration code changes required.')
        inspect(root,verbose=True)
        return
    logs=cloud.parent/'logs'/'MARKETVECTOR_RESEARCH_RESTORE_BACKUPS'
    backup=logs/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup.mkdir(parents=True,exist_ok=False)
    for name in changes:
        path=cloud/name
        if path.exists():shutil.copy2(path,backup/name)
        else:(backup/(name+'.ABSENT')).write_text('File did not exist before repair\n',encoding='utf-8')
    applied=[]
    try:
        for name,new in changes.items():
            path=cloud/name
            temp=path.with_name(path.name+'.mv_research_stage')
            write_exact(temp,new)
            temp.replace(path)
            applied.append(name)
        # No DB writes, no Cloud prep, no source GitHub push.
        remaining=inspect(root,verbose=True)
        # Missing DB tables should be fixed by normal Cloud preparation, not
        # by this code repair. This check only blocks source-level regressions.
        if remaining:raise RuntimeError('Restored files still fail source verification: '+', '.join(remaining))
    except Exception:
        for name in reversed(applied):
            original=backup/name
            if original.exists():shutil.copy2(original,cloud/name)
            else:(cloud/name).unlink(missing_ok=True)
        raise
    print('RESEARCH PAGES/GRIDS RESTORED. Backup:',backup)
    print('Next: cd /d "'+str(cloud)+'" && 01_PREPARE_CLOUD.bat')
    print('Then: python VERIFY_MARKETVECTOR_RESEARCH_DEPLOY.py --root ".." --strict-data')
    print('Then: streamlit run app_secure_portal.py')


def main(argv=None):
    ap=argparse.ArgumentParser(description='Read-only diagnostic / safe restore of MarketVector research pages and grids')
    ap.add_argument('--root',default=r'C:\TLT-TBT-Trading System')
    group=ap.add_mutually_exclusive_group()
    group.add_argument('--diagnose',action='store_true',help='Read-only diagnose (default)')
    group.add_argument('--repair',action='store_true',help='Back up and restore missing research UI pieces')
    group.add_argument('--check',action='store_true',help='Read-only verify source installation')
    ap.add_argument('--strict-data',action='store_true',help='Also require nonempty compact Cloud research exports')
    args=ap.parse_args(argv)
    try:
        if args.repair:repair(Path(args.root))
        elif args.check:
            missing=inspect(Path(args.root),strict_data=args.strict_data)
            if missing:raise RuntimeError(f'{len(missing)} deployment checks failed')
        else:inspect(Path(args.root),strict_data=args.strict_data)
    except Exception as exc:
        print(f'MV RESEARCH RESTORE: FAILED: {exc}',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':sys.exit(main())
