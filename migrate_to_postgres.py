"""
One-time migration script: events.jsonl → PostgreSQL.

Usage:
    python migrate_to_postgres.py

Steps:
  1. Connects to PostgreSQL using POSTGRES_URL from .env
  2. Creates database music_bot if it doesn't exist
  3. Creates tables (events, batch_live)
  4. Imports all rows from logs/events.jsonl
"""
import json
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras


def main() -> None:
    dsn = os.environ.get("POSTGRES_URL") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not dsn:
        print("ERROR: pass POSTGRES_URL as env var or first argument", file=sys.stderr)
        sys.exit(1)

    # ── 1. Ensure the database exists ────────────────────────────────────────
    # Connect to default 'postgres' DB to run CREATE DATABASE
    from urllib.parse import urlparse
    parsed = urlparse(dsn)
    db_name = parsed.path.lstrip("/")
    admin_dsn = dsn.replace(f"/{db_name}", "/postgres")

    print(f"Connecting to postgres DB to create '{db_name}'...")
    conn0 = psycopg2.connect(admin_dsn)
    conn0.autocommit = True
    with conn0.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        exists = cur.fetchone()
        if not exists:
            cur.execute(f'CREATE DATABASE "{db_name}"')
            print(f"  Database '{db_name}' created.")
        else:
            print(f"  Database '{db_name}' already exists.")
    conn0.close()

    # ── 2. Connect to music_bot and create tables ─────────────────────────────
    print("Creating tables...")
    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute("""
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
        cur.execute("CREATE INDEX IF NOT EXISTS events_ts_idx     ON events (ts)")
        cur.execute("CREATE INDEX IF NOT EXISTS events_action_idx ON events (action)")
        cur.execute("CREATE INDEX IF NOT EXISTS events_user_idx   ON events (user_hash)")
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
    print("  Tables ready.")

    # ── 3. Migrate events.jsonl ───────────────────────────────────────────────
    log_file = Path("logs/events.jsonl")
    if not log_file.exists():
        print("  logs/events.jsonl not found — nothing to migrate.")
        conn.close()
        return

    rows = []
    with open(log_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not rows:
        print("  events.jsonl is empty — nothing to migrate.")
        conn.close()
        return

    print(f"  Migrating {len(rows)} events...")
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(
            cur,
            """
            INSERT INTO events (ts, user_hash, username, action, result, track_count, detail)
            VALUES (%(ts)s, %(user_hash)s, %(username)s, %(action)s, %(result)s, %(track_count)s, %(detail)s)
            """,
            rows,
            page_size=500,
        )
    conn.commit()
    print(f"  Done! {len(rows)} events migrated.")
    conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
