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


# ── Admin stats ───────────────────────────────────────────────────────────

def get_admin_stats() -> dict:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for interval, key in [("1 day", "today"), ("7 days", "week")]:
                cur.execute(f"""
                    SELECT
                        COUNT(DISTINCT user_hash),
                        COALESCE(SUM(CASE WHEN action IN ('sc_search','sc_batch') AND result='success'
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
                  AND action IN ('sc_search','sc_batch')
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
