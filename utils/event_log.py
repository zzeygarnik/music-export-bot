"""Structured event logger — writes to PostgreSQL (events + batch_live tables)."""
import hashlib
import logging
from datetime import datetime, timezone

from utils import db

_log = logging.getLogger(__name__)


def _user_hash(user_id: int) -> str:
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:8]


def _parse_ts(val) -> datetime | None:
    """Convert ISO string or None to datetime; pass-through if already datetime."""
    if val is None or isinstance(val, datetime):
        return val
    return datetime.fromisoformat(val)


async def log_event(
    user_id: int,
    username: str | None,
    action: str,
    result: str,
    detail: str | None = None,
    track_count: int | None = None,
) -> None:
    """
    Insert one event row into the events table.
    Token is never included — only user_id hash and username.
    """
    now = datetime.now(timezone.utc)
    level = logging.INFO if result == "success" else logging.WARNING
    _log.log(
        level,
        "[%s] user=%s action=%s result=%s tracks=%s detail=%s",
        now.isoformat(timespec="seconds"), _user_hash(user_id), action, result, track_count, detail,
    )
    try:
        await db._pool.execute("""
            INSERT INTO events (ts, user_hash, username, action, result, track_count, detail)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, now, _user_hash(user_id), username, action, result, track_count, detail)
    except Exception as e:
        _log.warning("Could not write event to DB: %s", e)


async def update_batch_live(user_id: int, username: str | None, payload: dict) -> None:
    """
    Upsert current batch state into batch_live table for this user.
    """
    key = _user_hash(user_id)
    user_label = f"@{username}" if username else f"#{key}"
    try:
        await db._pool.execute("""
            INSERT INTO batch_live
                (user_hash, user_label, started_at, finished_at, total,
                 current_idx, current_track, downloaded, failed, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (user_hash) DO UPDATE SET
                user_label    = EXCLUDED.user_label,
                started_at    = EXCLUDED.started_at,
                finished_at   = EXCLUDED.finished_at,
                total         = EXCLUDED.total,
                current_idx   = EXCLUDED.current_idx,
                current_track = EXCLUDED.current_track,
                downloaded    = EXCLUDED.downloaded,
                failed        = EXCLUDED.failed,
                status        = EXCLUDED.status
        """,
            key,
            user_label,
            _parse_ts(payload.get("started_at")),
            _parse_ts(payload.get("finished_at")),
            payload.get("total", 0),
            payload.get("current_idx", 0),
            payload.get("current_track", ""),
            payload.get("downloaded", 0),
            payload.get("failed", []),
            payload.get("status", "running"),
        )
    except Exception as e:
        _log.warning("Could not update batch_live in DB: %s", e)
