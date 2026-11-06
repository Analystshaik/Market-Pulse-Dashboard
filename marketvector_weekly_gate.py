"""Read-only Weekly gate selection for the MarketVector secure portal.

Authoritative dashboard source: row_type=STATUS in
mv_actionable_dashboard_weekly; use the same weekly_status field that the
TLT/TBT client dashboards use. Never infer TBT ACTIVE from TLT CLOSED.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
from typing import Optional


@dataclass(frozen=True)
class WeeklyGate:
    reference: str
    status: str = "UNKNOWN"
    active: bool = False
    master_direction: str = ""
    master_date: str = ""
    snapshot_ts: str = ""
    reference_asof: str = ""
    reason: str = "No Weekly STATUS row is available."


@dataclass(frozen=True)
class HomeChoice:
    reference: Optional[str]
    explanation: str
    tlt: WeeklyGate
    tbt: WeeklyGate


def resolve_db(root: Path, environ: Optional[dict] = None) -> Path:
    """Match the existing client_actionable_dashboard.resolve_db precedence."""
    root = Path(root)
    env = os.environ if environ is None else environ
    override = str(env.get("ACTIONABLE_SNAPSHOT_DB", "")).strip()
    if override:
        p = Path(override)
        return p if p.is_absolute() else root / p

    cfg = root / "config.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            raw = data.get("database", {}).get("app_copy") or data.get("database", {}).get("path")
            if raw:
                p = Path(raw)
                if not p.is_absolute():
                    p = root / p
                if p.is_file():
                    return p
        except (OSError, TypeError, ValueError, AttributeError):
            pass

    cloud = root / "data" / "actionable_cloud.sqlite"
    if cloud.is_file():
        return cloud
    return root / "data" / "marketvector_tlt_tbt.sqlite"


def _s(value) -> str:
    return str(value).strip() if value is not None else ""


def _read_gate(con: sqlite3.Connection, reference: str) -> WeeklyGate:
    table = "mv_actionable_dashboard_weekly"
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row is None:
        return WeeklyGate(reference=reference, reason=f"Missing {table} table")

    column_names = [item[1] for item in con.execute(f'PRAGMA table_info("{table}")')]
    if not {'row_type', 'reference', 'weekly_status'}.issubset(column_names):
        return WeeklyGate(reference=reference, reason="Weekly STATUS fields are missing from snapshot")

    # All rows, not just the first or an arbitrary row. An obsolete duplicated
    # STATUS row must not silently override the newest rebuilt snapshot.
    con.row_factory = sqlite3.Row
    rows = con.execute(
        f'SELECT * FROM "{table}" WHERE UPPER(TRIM(reference))=? '
        "AND UPPER(TRIM(row_type))='STATUS'", (reference,)
    ).fetchall()
    if not rows:
        return WeeklyGate(reference=reference, reason="Missing Weekly STATUS row")

    def timestamp(row: sqlite3.Row):
        return (
            _s(row['snapshot_ts']) if 'snapshot_ts' in row.keys() else '',
            _s(row['reference_asof']) if 'reference_asof' in row.keys() else '',
        )

    row = max(rows, key=timestamp)
    get = lambda key: _s(row[key]) if key in row.keys() else ''
    status = get('weekly_status').upper()
    direction = get('weekly_master_direction').upper()
    master_date = get('weekly_master_date')
    # Explicit BEARISH reversal always vetoes ACTIVE, even if older status text
    # erroneously claims that the bullish Weekly gate is still open.
    active = status == 'ACTIVE' and direction != 'BEARISH'
    if not status:
        reason = "Weekly STATUS is blank; no safe automatic selection"
    elif status == 'ACTIVE' and direction == 'BEARISH':
        reason = "Conflicting gate: ACTIVE status but latest master reversal is BEARISH"
    elif active:
        reason = "Weekly bullish gate ACTIVE"
    else:
        reason = f"Weekly gate {status}"
    return WeeklyGate(
        reference=reference,
        status=status or 'UNKNOWN',
        active=active,
        master_direction=direction,
        master_date=master_date,
        snapshot_ts=get('snapshot_ts'),
        reference_asof=get('reference_asof'),
        reason=reason,
    )


def load_gates(db_path: Path) -> tuple[WeeklyGate, WeeklyGate]:
    db_path = Path(db_path)
    if not db_path.is_file():
        reason = "Snapshot database is missing; automatic gate selection unavailable"
        return (WeeklyGate('TLT', reason=reason), WeeklyGate('TBT', reason=reason))
    try:
        # SQLite URI ensures read-only access: the portal must never update data.
        uri = db_path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=3) as con:
            return _read_gate(con, 'TLT'), _read_gate(con, 'TBT')
    except (sqlite3.Error, OSError, ValueError) as exc:
        reason = f"Weekly snapshot could not be read ({type(exc).__name__})"
        return (WeeklyGate('TLT', reason=reason), WeeklyGate('TBT', reason=reason))


def select_home(db_path: Path) -> HomeChoice:
    tlt, tbt = load_gates(db_path)
    if tlt.active:
        if tbt.active:
            note = "Both Weekly gates are ACTIVE; TLT Daily has configured priority. Review the source snapshots."
        else:
            note = "TLT Weekly gate ACTIVE — open TLT Daily"
        return HomeChoice('TLT', note, tlt, tbt)
    if tbt.active:
        return HomeChoice('TBT', "TLT Weekly gate closed; TBT Weekly gate ACTIVE — open TBT Daily", tlt, tbt)
    return HomeChoice(None, "Neither TLT nor TBT has an active Weekly bullish gate. No automatic Buy dashboard is selected.", tlt, tbt)
