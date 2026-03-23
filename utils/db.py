"""PostgreSQL connection pool and schema management."""
import logging

import psycopg2
import psycopg2.pool
import psycopg2.extras

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
        conn.commit()
    finally:
        put_conn(conn)
