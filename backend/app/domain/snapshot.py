"""Process-wide dataset snapshot clock.

Every dataset-relative calculation MUST use this instead of the host
machine's current time -- the assessment records are pinned to the
workbook's README snapshot, which is a Sunday in the past/near-future
relative to a real clock.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import DATASET_TZ

_snapshot_at: datetime | None = None
_snapshot_raw: str | None = None


def set_snapshot(snapshot_at: datetime, raw: str) -> None:
    global _snapshot_at, _snapshot_raw
    _snapshot_at = snapshot_at
    _snapshot_raw = raw


def get_snapshot() -> datetime:
    if _snapshot_at is None:
        raise RuntimeError("Dataset snapshot has not been loaded yet. Run ingestion first.")
    return _snapshot_at


def get_snapshot_raw() -> str:
    return _snapshot_raw or ""


def parse_workbook_timestamp(value: str | None) -> datetime | None:
    """Parse a naive 'YYYY-MM-DD HH:MM' workbook cell as DATASET_TZ, per the
    assessment instruction to treat workbook timestamps as Asia/Kolkata
    unless stated otherwise."""
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    fmt = "%Y-%m-%d %H:%M:%S" if value.count(":") == 2 else "%Y-%m-%d %H:%M"
    naive = datetime.strptime(value, fmt)
    return naive.replace(tzinfo=ZoneInfo(DATASET_TZ))
