"""Structured event logger — writes JSONL to logs/events.jsonl."""
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path

_LOG_DIR = Path("logs")
_LOG_FILE = _LOG_DIR / "events.jsonl"
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
    Append one event line to logs/events.jsonl.
    Token is never included — only user_id hash and username.

    Actions:  auth_ok, auth_fail,
              export_liked, export_playlist, export_by_link, export_error,
              sc_search (single track: track_count=1, detail=title),
              sc_batch  (playlist: track_count=downloaded, detail="not_found:N", result=success|stopped)
    Results:  success, error, stopped
    """
    _LOG_DIR.mkdir(exist_ok=True)
    event = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "user_hash": _user_hash(user_id),
        "username": username,
        "action": action,
        "result": result,
        "track_count": track_count,
        "detail": detail,
    }
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        _log.warning("Could not write event log: %s", e)

    # Also emit to Python logger for console visibility
    level = logging.INFO if result == "success" else logging.WARNING
    _log.log(
        level,
        "[%s] user=%s action=%s result=%s tracks=%s detail=%s",
        event["ts"],
        event["user_hash"],
        action,
        result,
        track_count,
        detail,
    )
