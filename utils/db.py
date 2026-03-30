"""PostgreSQL connection pool and schema management."""
import logging

import psycopg2
import psycopg2.pool
import psycopg2.extras
from rapidfuzz import fuzz

log = logging.getLogger(__name__)

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def init_pool(dsn: str) -> None:
    global _pool
    _pool = psycopg2.pool.ThreadedConnectionPool(1, 5, dsn)
    _create_tables()
    log.info("PostgreSQL pool initialized")


def get_conn():
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool() first")
    return _pool.getconn()


def put_conn(conn) -> None:
    if _pool:
        _pool.putconn(conn)


def _create_tables() -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_id   BIGINT PRIMARY KEY,
                    username  TEXT,
                    banned_at TIMESTAMPTZ DEFAULT NOW(),
                    reason    TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS batch_whitelist (
                    user_id   BIGINT PRIMARY KEY,
                    username  TEXT,
                    added_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id        SERIAL PRIMARY KEY,
                    ts        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    user_hash VARCHAR(8)  NOT NULL,
                    username  VARCHAR(255),
                    action    VARCHAR(50) NOT NULL,
                    result    VARCHAR(20) NOT NULL,
                    track_count INTEGER,
                    detail    TEXT
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS events_ts_idx      ON events (ts);
                CREATE INDEX IF NOT EXISTS events_action_idx  ON events (action);
                CREATE INDEX IF NOT EXISTS events_user_idx    ON events (user_hash);
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS batch_live (
                    user_hash     VARCHAR(8)  PRIMARY KEY,
                    user_label    VARCHAR(255),
                    started_at    TIMESTAMPTZ,
                    finished_at   TIMESTAMPTZ,
                    total         INTEGER     DEFAULT 0,
                    current_idx   INTEGER     DEFAULT 0,
                    current_track TEXT        DEFAULT '',
                    downloaded    INTEGER     DEFAULT 0,
                    failed        TEXT[]      DEFAULT '{}',
                    status        VARCHAR(20) DEFAULT 'running'
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS track_cache (
                    cache_key  TEXT PRIMARY KEY,
                    file_id    TEXT NOT NULL,
                    source     TEXT,
                    cached_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            # Add artist/title columns if they don't exist yet (safe to run repeatedly)
            cur.execute("ALTER TABLE track_cache ADD COLUMN IF NOT EXISTS artist TEXT DEFAULT ''")
            cur.execute("ALTER TABLE track_cache ADD COLUMN IF NOT EXISTS title  TEXT DEFAULT ''")
            cur.execute("""
                CREATE INDEX IF NOT EXISTS track_cache_title_trgm_idx
                ON track_cache USING GIN (title gin_trgm_ops)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS batch_access_requests (
                    id            SERIAL PRIMARY KEY,
                    user_id       BIGINT NOT NULL,
                    username      TEXT,
                    status        VARCHAR(20) DEFAULT 'pending',
                    created_at    TIMESTAMPTZ DEFAULT NOW(),
                    admin_msg_id  BIGINT,
                    admin_chat_id BIGINT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS contact_messages (
                    id         SERIAL PRIMARY KEY,
                    user_id    BIGINT NOT NULL,
                    username   TEXT,
                    sent_at    TIMESTAMPTZ DEFAULT NOW(),
                    replied    BOOLEAN DEFAULT FALSE
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS contact_messages_user_idx ON contact_messages (user_id, sent_at DESC)
            """)
        conn.commit()
    finally:
        put_conn(conn)


def get_cached_file_id(cache_key: str) -> str | None:
    """Return Telegram file_id for a cached track, or None if not cached."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT file_id FROM track_cache WHERE cache_key = %s", (cache_key,))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as e:
        log.warning("track_cache lookup failed: %s", e)
        return None
    finally:
        put_conn(conn)


def save_cached_file_id(cache_key: str, file_id: str, source: str,
                        artist: str = '', title: str = '') -> None:
    """Insert or update a track's file_id in the cache."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO track_cache (cache_key, file_id, source, artist, title)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (cache_key) DO UPDATE SET file_id    = EXCLUDED.file_id,
                                                      source     = EXCLUDED.source,
                                                      artist     = EXCLUDED.artist,
                                                      title      = EXCLUDED.title,
                                                      cached_at  = NOW()
            """, (cache_key, file_id, source, artist, title))
        conn.commit()
    except Exception as e:
        log.warning("track_cache save failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        put_conn(conn)


def delete_cached_file_id(cache_key: str) -> None:
    """Remove a stale/expired file_id from the cache."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM track_cache WHERE cache_key = %s", (cache_key,))
        conn.commit()
    except Exception as e:
        log.warning("track_cache delete failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        put_conn(conn)


def search_cache_fuzzy(query: str, threshold: int = 75) -> list[dict]:
    """
    Fuzzy-search track_cache by title (and full artist+title).
    Uses pg_trgm to pre-filter candidates on PG side, then re-scores with rapidfuzz.
    Returns up to 5 best matches above threshold, sorted by score desc.
    """
    conn = get_conn()
    q = query.lower().strip()
    try:
        with conn.cursor() as cur:
            # pg_trgm pre-filter: similarity threshold ~0.2 casts a wide net,
            # rapidfuzz does the precise scoring below. Also fetch legacy rows
            # without title (title = '') via separate OR branch.
            cur.execute("""
                SELECT cache_key, file_id, artist, title
                FROM track_cache
                WHERE (title <> '' AND (title %% %s OR (artist || ' ' || title) %% %s))
                   OR title = ''
                LIMIT 100
            """, (q, q))
            rows = cur.fetchall()
    except Exception as e:
        log.warning("track_cache fuzzy search failed: %s", e)
        return []
    finally:
        put_conn(conn)

    scored = []
    top_misses = []
    for cache_key, file_id, artist, title in rows:
        if title:
            full = f"{artist} {title}".lower()
            score = max(
                fuzz.partial_ratio(q, title.lower()),
                fuzz.token_sort_ratio(q, full),
                fuzz.token_set_ratio(q, full),
            )
        else:
            score = max(
                fuzz.token_sort_ratio(q, cache_key),
                fuzz.token_set_ratio(q, cache_key),
            )
        if score >= threshold:
            scored.append((score, {"cache_key": cache_key, "file_id": file_id,
                                   "artist": artist, "title": title}))
        elif score >= 50:
            top_misses.append((score, f"{artist} — {title}"))

    if not scored and top_misses:
        top_misses.sort(reverse=True)
        log.info("search_cache_fuzzy: query=%r no hits (threshold=%d), top misses: %s",
                 q, threshold, top_misses[:3])
    elif scored:
        log.info("search_cache_fuzzy: query=%r found %d hits (candidates=%d)", q, len(scored), len(rows))
    else:
        log.info("search_cache_fuzzy: query=%r no hits (candidates=%d)", q, len(rows))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:5]]


# ── Ban management ────────────────────────────────────────────────────────

def is_banned(user_id: int) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM banned_users WHERE user_id = %s", (user_id,))
            return cur.fetchone() is not None
    except Exception as e:
        log.warning("is_banned check failed: %s", e)
        return False
    finally:
        put_conn(conn)


def ban_user(user_id: int, username: str | None, reason: str | None = None) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO banned_users (user_id, username, reason)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, reason = EXCLUDED.reason
            """, (user_id, username, reason))
        conn.commit()
    except Exception as e:
        log.warning("ban_user failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        put_conn(conn)


def unban_user(user_id: int) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM banned_users WHERE user_id = %s", (user_id,))
        conn.commit()
    except Exception as e:
        log.warning("unban_user failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        put_conn(conn)


def get_banned_users() -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, username, banned_at, reason FROM banned_users ORDER BY banned_at DESC")
            return [{"user_id": r[0], "username": r[1], "banned_at": r[2], "reason": r[3]}
                    for r in cur.fetchall()]
    except Exception as e:
        log.warning("get_banned_users failed: %s", e)
        return []
    finally:
        put_conn(conn)


# ── Batch whitelist ───────────────────────────────────────────────────────

def _check_db_whitelist(user_id: int) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM batch_whitelist WHERE user_id = %s", (user_id,))
            return cur.fetchone() is not None
    except Exception as e:
        log.warning("batch_whitelist check failed: %s", e)
        return False
    finally:
        put_conn(conn)


def is_batch_allowed(user_id: int, username: str | None) -> bool:
    """Check if user is allowed to run batch downloads.

    Logic:
      BATCH_ALLOWED_USERS="*"  → everyone allowed
      BATCH_ALLOWED_USERS=""   → check DB whitelist only
      anything else            → check static .env list first, then DB whitelist
    """
    from config import settings  # local import to avoid circular
    val = settings.BATCH_ALLOWED_USERS.strip()
    if not val:
        return _check_db_whitelist(user_id)
    if val == "*":
        return True
    # Static list entries
    for entry in val.split(","):
        entry = entry.strip()
        if entry.startswith("@"):
            if username and username.lower() == entry[1:].lower():
                return True
        else:
            try:
                if int(entry) == user_id:
                    return True
            except ValueError:
                pass
    # Also check DB whitelist on top of static list
    return _check_db_whitelist(user_id)


def add_batch_whitelist(user_id: int, username: str | None) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO batch_whitelist (user_id, username)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username
            """, (user_id, username))
        conn.commit()
    except Exception as e:
        log.warning("add_batch_whitelist failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        put_conn(conn)


def remove_batch_whitelist(user_id: int) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM batch_whitelist WHERE user_id = %s", (user_id,))
        conn.commit()
    except Exception as e:
        log.warning("remove_batch_whitelist failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        put_conn(conn)


def get_batch_whitelist() -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, username, added_at FROM batch_whitelist ORDER BY added_at DESC")
            return [{"user_id": r[0], "username": r[1], "added_at": r[2]}
                    for r in cur.fetchall()]
    except Exception as e:
        log.warning("get_batch_whitelist failed: %s", e)
        return []
    finally:
        put_conn(conn)


# ── Batch access requests ─────────────────────────────────────────────────

def create_batch_request(user_id: int, username: str | None) -> int:
    """Create a new pending batch access request. Returns the new request id, or -1 on error."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO batch_access_requests (user_id, username) VALUES (%s, %s) RETURNING id",
                (user_id, username),
            )
            row = cur.fetchone()
        conn.commit()
        return row[0]
    except Exception as e:
        log.warning("create_batch_request failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return -1
    finally:
        put_conn(conn)


def get_pending_request(user_id: int) -> dict | None:
    """Return the pending batch access request for a user, or None."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, user_id, username, status, created_at, admin_msg_id, admin_chat_id
                FROM batch_access_requests
                WHERE user_id = %s AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1
            """, (user_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {"id": row[0], "user_id": row[1], "username": row[2], "status": row[3],
                    "created_at": row[4], "admin_msg_id": row[5], "admin_chat_id": row[6]}
    except Exception as e:
        log.warning("get_pending_request failed: %s", e)
        return None
    finally:
        put_conn(conn)


def get_request_by_id(request_id: int) -> dict | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, user_id, username, status, created_at, admin_msg_id, admin_chat_id
                FROM batch_access_requests WHERE id = %s
            """, (request_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {"id": row[0], "user_id": row[1], "username": row[2], "status": row[3],
                    "created_at": row[4], "admin_msg_id": row[5], "admin_chat_id": row[6]}
    except Exception as e:
        log.warning("get_request_by_id failed: %s", e)
        return None
    finally:
        put_conn(conn)


def get_pending_requests() -> list[dict]:
    """Return all pending batch access requests ordered by creation time."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, user_id, username, created_at
                FROM batch_access_requests
                WHERE status = 'pending'
                ORDER BY created_at ASC
            """)
            return [{"id": r[0], "user_id": r[1], "username": r[2], "created_at": r[3]}
                    for r in cur.fetchall()]
    except Exception as e:
        log.warning("get_pending_requests failed: %s", e)
        return []
    finally:
        put_conn(conn)


def resolve_batch_request(request_id: int, status: str) -> None:
    """Update request status to 'approved' or 'rejected'."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE batch_access_requests SET status = %s WHERE id = %s",
                (status, request_id),
            )
        conn.commit()
    except Exception as e:
        log.warning("resolve_batch_request failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        put_conn(conn)


def set_request_admin_msg(request_id: int, admin_msg_id: int, admin_chat_id: int) -> None:
    """Save the admin notification message location for later editing."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE batch_access_requests SET admin_msg_id = %s, admin_chat_id = %s WHERE id = %s",
                (admin_msg_id, admin_chat_id, request_id),
            )
        conn.commit()
    except Exception as e:
        log.warning("set_request_admin_msg failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        put_conn(conn)


# ── Admin stats ───────────────────────────────────────────────────────────

def get_admin_stats() -> dict:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for interval, key in [("1 day", "today"), ("7 days", "week")]:
                cur.execute(f"""
                    SELECT
                        COUNT(DISTINCT user_hash),
                        COALESCE(SUM(CASE WHEN action IN ('sc_search','yt_search','sc_batch') AND result='success'
                                         THEN COALESCE(track_count,1) ELSE 0 END), 0),
                        COUNT(CASE WHEN action='sc_batch' AND result IN ('success','stopped') THEN 1 END),
                        COUNT(CASE WHEN result='error' THEN 1 END)
                    FROM events WHERE ts >= NOW() - INTERVAL '{interval}'
                """)
                row = cur.fetchone()
                if key == "today":
                    today = {"users": row[0], "tracks": row[1], "batches": row[2], "errors": row[3]}
                else:
                    week = {"users": row[0], "tracks": row[1], "batches": row[2], "errors": row[3]}
            cur.execute("""
                SELECT username, SUM(COALESCE(track_count,1)) as total
                FROM events
                WHERE ts >= NOW() - INTERVAL '7 days'
                  AND action IN ('sc_search','yt_search','sc_batch')
                  AND result IN ('success','stopped')
                  AND username IS NOT NULL
                GROUP BY username
                ORDER BY total DESC
                LIMIT 5
            """)
            top_users = [{"username": r[0], "tracks": r[1]} for r in cur.fetchall()]
        return {"today": today, "week": week, "top_users": top_users}
    except Exception as e:
        log.warning("get_admin_stats failed: %s", e)
        return {}
    finally:
        put_conn(conn)


# ── Contact message cooldown ──────────────────────────────────────────────

def create_contact_message(user_id: int, username: str | None) -> None:
    """Record a new contact message from the user."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO contact_messages (user_id, username) VALUES (%s, %s)",
                (user_id, username),
            )
        conn.commit()
    except Exception as e:
        log.warning("create_contact_message failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        put_conn(conn)


def get_active_contact(user_id: int) -> dict | None:
    """Return the unanswered contact message sent within the last 24 hours, or None."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, sent_at FROM contact_messages
                WHERE user_id = %s
                  AND replied = FALSE
                  AND sent_at >= NOW() - INTERVAL '24 hours'
                ORDER BY sent_at DESC LIMIT 1
            """, (user_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {"id": row[0], "sent_at": row[1]}
    except Exception as e:
        log.warning("get_active_contact failed: %s", e)
        return None
    finally:
        put_conn(conn)


def mark_contact_replied(user_id: int) -> None:
    """Mark all pending contact messages from user as replied."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE contact_messages SET replied = TRUE WHERE user_id = %s AND replied = FALSE",
                (user_id,),
            )
        conn.commit()
    except Exception as e:
        log.warning("mark_contact_replied failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        put_conn(conn)


def get_user_stats(user_id: int) -> dict:
    """Return per-user statistics based on the events table."""
    import hashlib
    user_hash = hashlib.sha256(str(user_id).encode()).hexdigest()[:8]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN action IN ('sc_search','yt_search') AND result='success'
                                     THEN COALESCE(track_count,1) ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN action='sc_batch' AND result IN ('success','stopped')
                                     THEN COALESCE(track_count,0) ELSE 0 END), 0),
                    COUNT(CASE WHEN action='sc_batch' AND result IN ('success','stopped') THEN 1 END),
                    COALESCE(SUM(CASE WHEN action IN ('export_liked','export_playlist','export_by_link','export_filtered')
                                      AND result='success'
                                     THEN COALESCE(track_count,0) ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN action='spotify_export' AND result='success'
                                     THEN COALESCE(track_count,0) ELSE 0 END), 0),
                    MIN(ts)
                FROM events WHERE user_hash = %s
            """, (user_hash,))
            row = cur.fetchone()
            if not row or row[5] is None:
                return {}

            cur.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN action IN ('sc_search','yt_search') AND result='success'
                                     THEN COALESCE(track_count,1) ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN action='sc_batch' AND result IN ('success','stopped')
                                     THEN COALESCE(track_count,0) ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN action IN ('export_liked','export_playlist','export_by_link','export_filtered')
                                      AND result='success'
                                     THEN COALESCE(track_count,0) ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN action='spotify_export' AND result='success'
                                     THEN COALESCE(track_count,0) ELSE 0 END), 0)
                FROM events WHERE user_hash = %s AND ts >= NOW() - INTERVAL '7 days'
            """, (user_hash,))
            week_row = cur.fetchone()

        return {
            "all": {
                "single":          int(row[0]),
                "batch":           int(row[1]),
                "batches":         int(row[2]),
                "ym_exported":     int(row[3]),
                "spotify_exported": int(row[4]),
                "first_ts":        row[5],
            },
            "week": {
                "single":          int(week_row[0]),
                "batch":           int(week_row[1]),
                "ym_exported":     int(week_row[2]),
                "spotify_exported": int(week_row[3]),
            },
        }
    except Exception as e:
        log.warning("get_user_stats failed: %s", e)
        return {}
    finally:
        put_conn(conn)


def get_recent_events(n: int = 20) -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ts, username, user_hash, action, result, track_count, detail
                FROM events ORDER BY ts DESC LIMIT %s
            """, (n,))
            return [
                {"ts": r[0], "username": r[1], "user_hash": r[2],
                 "action": r[3], "result": r[4], "track_count": r[5], "detail": r[6]}
                for r in cur.fetchall()
            ]
    except Exception as e:
        log.warning("get_recent_events failed: %s", e)
        return []
    finally:
        put_conn(conn)
