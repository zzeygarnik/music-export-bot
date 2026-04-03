"""PostgreSQL connection pool and schema management — uses asyncpg."""
import logging
from datetime import datetime, timedelta
import zoneinfo

import asyncpg
from rapidfuzz import fuzz

log = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def init_pool(dsn: str) -> None:
    global _pool
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    await _create_tables()
    log.info("PostgreSQL pool initialized (asyncpg)")


async def _create_tables() -> None:
    async with _pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id   BIGINT PRIMARY KEY,
                username  TEXT,
                banned_at TIMESTAMPTZ DEFAULT NOW(),
                reason    TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS batch_whitelist (
                user_id   BIGINT PRIMARY KEY,
                username  TEXT,
                added_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          SERIAL PRIMARY KEY,
                ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                user_hash   VARCHAR(8)  NOT NULL,
                username    VARCHAR(255),
                action      VARCHAR(50) NOT NULL,
                result      VARCHAR(20) NOT NULL,
                track_count INTEGER,
                detail      TEXT
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS events_ts_idx     ON events (ts)")
        await conn.execute("CREATE INDEX IF NOT EXISTS events_action_idx ON events (action)")
        await conn.execute("CREATE INDEX IF NOT EXISTS events_user_idx   ON events (user_hash)")
        await conn.execute("""
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS track_cache (
                cache_key  TEXT PRIMARY KEY,
                file_id    TEXT NOT NULL,
                source     TEXT,
                cached_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("ALTER TABLE track_cache ADD COLUMN IF NOT EXISTS artist TEXT DEFAULT ''")
        await conn.execute("ALTER TABLE track_cache ADD COLUMN IF NOT EXISTS title  TEXT DEFAULT ''")
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS track_cache_title_trgm_idx
            ON track_cache USING GIN (title gin_trgm_ops)
        """)
        await conn.execute("""
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS contact_messages (
                id         SERIAL PRIMARY KEY,
                user_id    BIGINT NOT NULL,
                username   TEXT,
                sent_at    TIMESTAMPTZ DEFAULT NOW(),
                replied    BOOLEAN DEFAULT FALSE
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS contact_messages_user_idx
            ON contact_messages (user_id, sent_at DESC)
        """)


async def get_cached_file_id(cache_key: str) -> str | None:
    """Return Telegram file_id for a cached track, or None if not cached."""
    try:
        async with _pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT file_id FROM track_cache WHERE cache_key = $1", cache_key
            )
    except Exception as e:
        log.warning("track_cache lookup failed: %s", e)
        return None


async def save_cached_file_id(cache_key: str, file_id: str, source: str,
                               artist: str = '', title: str = '') -> None:
    """Insert or update a track's file_id in the cache."""
    try:
        async with _pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO track_cache (cache_key, file_id, source, artist, title)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (cache_key) DO UPDATE SET
                        file_id   = EXCLUDED.file_id,
                        source    = EXCLUDED.source,
                        artist    = EXCLUDED.artist,
                        title     = EXCLUDED.title,
                        cached_at = NOW()
                """, cache_key, file_id, source, artist, title)
    except Exception as e:
        log.warning("track_cache save failed: %s", e)


async def delete_cached_file_id(cache_key: str) -> None:
    """Remove a stale/expired file_id from the cache."""
    try:
        async with _pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM track_cache WHERE cache_key = $1", cache_key
                )
    except Exception as e:
        log.warning("track_cache delete failed: %s", e)


async def search_cache_fuzzy(query: str, threshold: int = 75) -> list[dict]:
    """
    Fuzzy-search track_cache by title (and full artist+title).
    Uses pg_trgm to pre-filter candidates on PG side, then re-scores with rapidfuzz.
    Returns up to 5 best matches above threshold, sorted by score desc.
    """
    q = query.lower().strip()
    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT cache_key, file_id, artist, title
                FROM track_cache
                WHERE (title <> '' AND (title % $1 OR (artist || ' ' || title) % $1))
                   OR title = ''
                LIMIT 100
            """, q)
    except Exception as e:
        log.warning("track_cache fuzzy search failed: %s", e)
        return []

    scored = []
    top_misses = []
    for r in rows:
        cache_key, file_id, artist, title = r[0], r[1], r[2], r[3]
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

async def is_banned(user_id: int) -> bool:
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM banned_users WHERE user_id = $1", user_id
            )
            return row is not None
    except Exception as e:
        log.warning("is_banned check failed: %s", e)
        return False


async def ban_user(user_id: int, username: str | None, reason: str | None = None) -> None:
    try:
        async with _pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO banned_users (user_id, username, reason)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id) DO UPDATE SET
                        username = EXCLUDED.username,
                        reason   = EXCLUDED.reason
                """, user_id, username, reason)
    except Exception as e:
        log.warning("ban_user failed: %s", e)


async def unban_user(user_id: int) -> None:
    try:
        async with _pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM banned_users WHERE user_id = $1", user_id)
    except Exception as e:
        log.warning("unban_user failed: %s", e)


async def get_banned_users() -> list[dict]:
    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id, username, banned_at, reason FROM banned_users ORDER BY banned_at DESC"
            )
            return [{"user_id": r[0], "username": r[1], "banned_at": r[2], "reason": r[3]}
                    for r in rows]
    except Exception as e:
        log.warning("get_banned_users failed: %s", e)
        return []


# ── Batch whitelist ───────────────────────────────────────────────────────

async def _check_db_whitelist(user_id: int) -> bool:
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM batch_whitelist WHERE user_id = $1", user_id
            )
            return row is not None
    except Exception as e:
        log.warning("batch_whitelist check failed: %s", e)
        return False


async def is_batch_allowed(user_id: int, username: str | None) -> bool:
    """Check if user is allowed to run batch downloads."""
    from config import settings  # local import to avoid circular
    val = settings.BATCH_ALLOWED_USERS.strip()
    if not val:
        return await _check_db_whitelist(user_id)
    if val == "*":
        return True
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
    return await _check_db_whitelist(user_id)


async def add_batch_whitelist(user_id: int, username: str | None) -> None:
    try:
        async with _pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO batch_whitelist (user_id, username)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username
                """, user_id, username)
    except Exception as e:
        log.warning("add_batch_whitelist failed: %s", e)


async def remove_batch_whitelist(user_id: int) -> None:
    try:
        async with _pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM batch_whitelist WHERE user_id = $1", user_id)
    except Exception as e:
        log.warning("remove_batch_whitelist failed: %s", e)


async def get_batch_whitelist() -> list[dict]:
    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id, username, added_at FROM batch_whitelist ORDER BY added_at DESC"
            )
            return [{"user_id": r[0], "username": r[1], "added_at": r[2]} for r in rows]
    except Exception as e:
        log.warning("get_batch_whitelist failed: %s", e)
        return []


# ── Batch access requests ─────────────────────────────────────────────────

async def create_batch_request(user_id: int, username: str | None) -> int:
    """Create a new pending batch access request. Returns the new request id, or -1 on error."""
    try:
        async with _pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "INSERT INTO batch_access_requests (user_id, username) VALUES ($1, $2) RETURNING id",
                    user_id, username,
                )
                return row[0]
    except Exception as e:
        log.warning("create_batch_request failed: %s", e)
        return -1


async def get_pending_request(user_id: int) -> dict | None:
    """Return the pending batch access request for a user, or None."""
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, user_id, username, status, created_at, admin_msg_id, admin_chat_id
                FROM batch_access_requests
                WHERE user_id = $1 AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1
            """, user_id)
            if not row:
                return None
            return {"id": row[0], "user_id": row[1], "username": row[2], "status": row[3],
                    "created_at": row[4], "admin_msg_id": row[5], "admin_chat_id": row[6]}
    except Exception as e:
        log.warning("get_pending_request failed: %s", e)
        return None


async def get_request_by_id(request_id: int) -> dict | None:
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, user_id, username, status, created_at, admin_msg_id, admin_chat_id
                FROM batch_access_requests WHERE id = $1
            """, request_id)
            if not row:
                return None
            return {"id": row[0], "user_id": row[1], "username": row[2], "status": row[3],
                    "created_at": row[4], "admin_msg_id": row[5], "admin_chat_id": row[6]}
    except Exception as e:
        log.warning("get_request_by_id failed: %s", e)
        return None


async def get_pending_requests() -> list[dict]:
    """Return all pending batch access requests ordered by creation time."""
    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, user_id, username, created_at
                FROM batch_access_requests
                WHERE status = 'pending'
                ORDER BY created_at ASC
            """)
            return [{"id": r[0], "user_id": r[1], "username": r[2], "created_at": r[3]}
                    for r in rows]
    except Exception as e:
        log.warning("get_pending_requests failed: %s", e)
        return []


async def resolve_batch_request(request_id: int, status: str) -> None:
    """Update request status to 'approved' or 'rejected'."""
    try:
        async with _pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE batch_access_requests SET status = $1 WHERE id = $2",
                    status, request_id,
                )
    except Exception as e:
        log.warning("resolve_batch_request failed: %s", e)


async def set_request_admin_msg(request_id: int, admin_msg_id: int, admin_chat_id: int) -> None:
    """Save the admin notification message location for later editing."""
    try:
        async with _pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE batch_access_requests SET admin_msg_id = $1, admin_chat_id = $2 WHERE id = $3",
                    admin_msg_id, admin_chat_id, request_id,
                )
    except Exception as e:
        log.warning("set_request_admin_msg failed: %s", e)


# ── Admin stats ───────────────────────────────────────────────────────────

async def get_admin_stats() -> dict:
    try:
        async with _pool.acquire() as conn:
            result = {}
            for interval, key in [("1 day", "today"), ("7 days", "week")]:
                row = await conn.fetchrow(f"""
                    SELECT
                        COUNT(DISTINCT user_hash),
                        COALESCE(SUM(CASE WHEN action IN ('sc_search','yt_search','sc_batch') AND result='success'
                                         THEN COALESCE(track_count,1) ELSE 0 END), 0),
                        COUNT(CASE WHEN action='sc_batch' AND result IN ('success','stopped') THEN 1 END),
                        COUNT(CASE WHEN result='error' THEN 1 END)
                    FROM events WHERE ts >= NOW() - INTERVAL '{interval}'
                """)
                result[key] = {"users": row[0], "tracks": row[1], "batches": row[2], "errors": row[3]}
            rows = await conn.fetch("""
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
            result["top_users"] = [{"username": r[0], "tracks": r[1]} for r in rows]
            return result
    except Exception as e:
        log.warning("get_admin_stats failed: %s", e)
        return {}


# ── Contact message cooldown ──────────────────────────────────────────────

async def create_contact_message(user_id: int, username: str | None) -> None:
    """Record a new contact message from the user."""
    try:
        async with _pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO contact_messages (user_id, username) VALUES ($1, $2)",
                    user_id, username,
                )
    except Exception as e:
        log.warning("create_contact_message failed: %s", e)


async def get_active_contact(user_id: int) -> dict | None:
    """Return the unanswered contact message sent within the last 24 hours, or None."""
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, sent_at FROM contact_messages
                WHERE user_id = $1
                  AND replied = FALSE
                  AND sent_at >= NOW() - INTERVAL '24 hours'
                ORDER BY sent_at DESC LIMIT 1
            """, user_id)
            if not row:
                return None
            return {"id": row[0], "sent_at": row[1]}
    except Exception as e:
        log.warning("get_active_contact failed: %s", e)
        return None


async def mark_contact_replied(user_id: int) -> None:
    """Mark all pending contact messages from user as replied."""
    try:
        async with _pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE contact_messages SET replied = TRUE WHERE user_id = $1 AND replied = FALSE",
                    user_id,
                )
    except Exception as e:
        log.warning("mark_contact_replied failed: %s", e)


async def get_user_stats(user_id: int) -> dict:
    """Return per-user statistics based on the events table."""
    import hashlib
    user_hash = hashlib.sha256(str(user_id).encode()).hexdigest()[:8]
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow("""
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
                FROM events WHERE user_hash = $1
            """, user_hash)

            if not row or row[5] is None:
                return {}

            week_row = await conn.fetchrow("""
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
                FROM events WHERE user_hash = $1 AND ts >= NOW() - INTERVAL '7 days'
            """, user_hash)

        return {
            "all": {
                "single":           int(row[0]),
                "batch":            int(row[1]),
                "batches":          int(row[2]),
                "ym_exported":      int(row[3]),
                "spotify_exported": int(row[4]),
                "first_ts":         row[5],
            },
            "week": {
                "single":           int(week_row[0]),
                "batch":            int(week_row[1]),
                "ym_exported":      int(week_row[2]),
                "spotify_exported": int(week_row[3]),
            },
        }
    except Exception as e:
        log.warning("get_user_stats failed: %s", e)
        return {}


async def get_dashboard_stats() -> dict:
    """Summary stats for all dashboard tabs."""
    try:
        async with _pool.acquire() as conn:
            ym = await conn.fetchrow("""
                SELECT
                    COALESCE(SUM(CASE WHEN result='success' THEN COALESCE(track_count,0) ELSE 0 END), 0),
                    COUNT(CASE WHEN result='success' THEN 1 END),
                    COALESCE(SUM(CASE WHEN result='success' AND ts >= NOW() - INTERVAL '7 days'
                                     THEN COALESCE(track_count,0) ELSE 0 END), 0),
                    COUNT(DISTINCT CASE WHEN result='success' THEN user_hash END)
                FROM events
                WHERE action IN ('export_liked','export_playlist','export_by_link','export_filtered')
            """)
            sp = await conn.fetchrow("""
                SELECT
                    COALESCE(SUM(CASE WHEN result='success' THEN COALESCE(track_count,0) ELSE 0 END), 0),
                    COUNT(CASE WHEN result='success' THEN 1 END),
                    COALESCE(SUM(CASE WHEN result='success' AND ts >= NOW() - INTERVAL '7 days'
                                     THEN COALESCE(track_count,0) ELSE 0 END), 0)
                FROM events WHERE action = 'spotify_export'
            """)
            sc = await conn.fetchrow("""
                SELECT
                    COALESCE(SUM(CASE WHEN result IN ('success','stopped') THEN COALESCE(track_count,1) ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN result IN ('success','stopped') AND ts >= NOW() - INTERVAL '7 days'
                                     THEN COALESCE(track_count,1) ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN action IN ('sc_search','sc_batch') AND result IN ('success','stopped')
                                     THEN COALESCE(track_count,1) ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN action='yt_search' AND result IN ('success','stopped')
                                     THEN COALESCE(track_count,1) ELSE 0 END), 0)
                FROM events WHERE action IN ('sc_search','yt_search','sc_batch')
            """)
            active_batches = await conn.fetchval(
                "SELECT COUNT(*) FROM batch_live WHERE status='running'"
            )
        return {
            "ym": {
                "total":  int(ym[0]), "events": int(ym[1]),
                "week":   int(ym[2]), "users":  int(ym[3]),
            },
            "spotify": {
                "total":  int(sp[0]), "events": int(sp[1]),
                "week":   int(sp[2]),
            },
            "sc": {
                "total":          int(sc[0]), "week":      int(sc[1]),
                "sc_tracks":      int(sc[2]), "yt_tracks": int(sc[3]),
                "active_batches": int(active_batches),
            },
        }
    except Exception as e:
        log.warning("get_dashboard_stats failed: %s", e)
        return {}


async def get_chart_data(source: str, days: int = 7) -> list[dict]:
    """Per-day track counts for the given source over the last N days (MSK timezone)."""
    _FILTERS = {
        "ym":      "action IN ('export_liked','export_playlist','export_by_link','export_filtered')",
        "spotify": "action = 'spotify_export'",
        "sc":      "action IN ('sc_search','yt_search','sc_batch')",
    }
    filter_sql = _FILTERS.get(source, "")
    if not filter_sql:
        return []
    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch(f"""
                SELECT DATE(ts AT TIME ZONE 'Europe/Moscow') AS day,
                       COALESCE(SUM(COALESCE(track_count, 1)), 0) AS tracks
                FROM events
                WHERE {filter_sql}
                  AND result IN ('success','stopped')
                  AND ts >= NOW() - INTERVAL '{int(days)} days'
                GROUP BY day ORDER BY day ASC
            """)
        tz = zoneinfo.ZoneInfo("Europe/Moscow")
        today = datetime.now(tz).date()
        day_map = {r[0]: int(r[1]) for r in rows}
        return [
            {
                "day":    (today - timedelta(days=days - 1 - i)).isoformat(),
                "tracks": day_map.get(today - timedelta(days=days - 1 - i), 0),
            }
            for i in range(days)
        ]
    except Exception as e:
        log.warning("get_chart_data failed: %s", e)
        return []


async def get_events_dashboard(limit: int = 50, source: str = "", offset: int = 0) -> list[dict]:
    """Events for the dashboard with source filtering and datetime serialization."""
    _SOURCE_FILTERS = {
        "ym":      "('export_liked','export_playlist','export_by_link','export_filtered')",
        "spotify": "('spotify_export',)",
        "sc":      "('sc_search','yt_search','sc_batch')",
    }
    try:
        async with _pool.acquire() as conn:
            if source in _SOURCE_FILTERS:
                rows = await conn.fetch(f"""
                    SELECT ts, username, action, result, track_count, detail
                    FROM events
                    WHERE action IN {_SOURCE_FILTERS[source]}
                    ORDER BY ts DESC LIMIT $1 OFFSET $2
                """, limit, offset)
            else:
                rows = await conn.fetch("""
                    SELECT ts, username, action, result, track_count, detail
                    FROM events ORDER BY ts DESC LIMIT $1 OFFSET $2
                """, limit, offset)
            return [
                {
                    "ts":          r[0].isoformat() if r[0] else None,
                    "username":    r[1] or "—",
                    "action":      r[2],
                    "result":      r[3],
                    "track_count": r[4],
                    "detail":      r[5],
                }
                for r in rows
            ]
    except Exception as e:
        log.warning("get_events_dashboard failed: %s", e)
        return []


async def get_batch_live_data() -> list[dict]:
    """Currently running batch downloads."""
    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT user_label, total, current_idx, current_track, downloaded, status
                FROM batch_live WHERE status = 'running' ORDER BY started_at ASC
            """)
            return [
                {
                    "user_label":    r[0] or "unknown",
                    "total":         r[1] or 0,
                    "current_idx":   r[2] or 0,
                    "current_track": r[3] or "",
                    "downloaded":    r[4] or 0,
                    "status":        r[5],
                }
                for r in rows
            ]
    except Exception as e:
        log.warning("get_batch_live_data failed: %s", e)
        return []


async def get_recent_events(n: int = 20) -> list[dict]:
    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT ts, username, user_hash, action, result, track_count, detail
                FROM events ORDER BY ts DESC LIMIT $1
            """, n)
            return [
                {"ts": r[0], "username": r[1], "user_hash": r[2],
                 "action": r[3], "result": r[4], "track_count": r[5], "detail": r[6]}
                for r in rows
            ]
    except Exception as e:
        log.warning("get_recent_events failed: %s", e)
        return []
