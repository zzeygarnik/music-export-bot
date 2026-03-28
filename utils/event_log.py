"""Structured event logger — writes to PostgreSQL (events + batch_live tables)."""
import hashlib
import logging
from datetime import datetime, timezone

from utils.db import get_conn, put_conn

_log = logging.getLogger(__name__)


def _user_hash(user_id: int) -> str:
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:8]


def log_event(
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

    Actions:  auth_ok, auth_fail,
              export_liked, export_playlist, export_by_link, export_error,
              sc_search  (single track: track_count=1, detail="artist — title"),
              sc_batch   (playlist end: track_count=downloaded, detail="not_found:N"),
              sc_track_fail (per failed track: detail="artist — title"),
              yms_load   (shared playlist loaded)
    Results:  success, error, stopped
    """
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    level = logging.INFO if result == "success" else logging.WARNING
    _log.log(
        level,
        "[%s] user=%s action=%s result=%s tracks=%s detail=%s",
        ts, _user_hash(user_id), action, result, track_count, detail,
    )

    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO events (ts, user_hash, username, action, result, track_count, detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (ts, _user_hash(user_id), username, action, result, track_count, detail),
            )
        conn.commit()
    except Exception as e:
        _log.warning("Could not write event to DB: %s", e)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            put_conn(conn)


def update_batch_live(user_id: int, username: str | None, payload: dict) -> None:
    """
    Upsert current batch state into batch_live table for this user.

    payload fields:
      started_at    — ISO timestamp of batch start
      finished_at   — ISO timestamp when done/stopped (optional)
      total         — total tracks in batch
      current_idx   — 1-based index of track being processed right now
      current_track — "Artist — Title" of current track
      downloaded    — number successfully sent so far
      failed        — list of "Artist — Title" strings that could not be downloaded
      status        — "running" | "done" | "stopped"
    """
    key = _user_hash(user_id)
    user_label = f"@{username}" if username else f"#{key}"

    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO batch_live
                    (user_hash, user_label, started_at, finished_at, total,
                     current_idx, current_track, downloaded, failed, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                (
                    key,
                    user_label,
                    payload.get("started_at"),
                    payload.get("finished_at"),
                    payload.get("total", 0),
                    payload.get("current_idx", 0),
                    payload.get("current_track", ""),
                    payload.get("downloaded", 0),
                    payload.get("failed", []),
                    payload.get("status", "running"),
                ),
            )
        conn.commit()
    except Exception as e:
        _log.warning("Could not update batch_live in DB: %s", e)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            put_conn(conn)
