#!/usr/bin/env python3
"""
migrate.py — Migrate alexpavsky.com data from SQLite → PostgreSQL.

Usage:
    python migrate.py [--sqlite chat.db] [--pg postgresql://alex:pass@localhost/alexpavsky]

Environment variables (override CLI defaults):
    SQLITE_PATH   path to chat.db            (default: ./chat.db)
    DATABASE_URL  PostgreSQL connection URL   (required if not passed as arg)
"""
import argparse
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("psycopg2 not installed. Run: pip install psycopg2-binary")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def to_uuid(value: Any) -> Optional[str]:
    """Convert SQLite UUID (hex TEXT with/without dashes) to PG-compatible UUID string."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Already formatted
    if len(s) == 36 and s.count("-") == 4:
        return s
    # 32-char hex → add dashes
    if len(s) == 32:
        try:
            return str(uuid.UUID(s))
        except ValueError:
            pass
    # Last resort: try parsing as-is
    try:
        return str(uuid.UUID(s))
    except ValueError:
        # Generate a new deterministic UUID from the original value
        return str(uuid.uuid5(uuid.NAMESPACE_OID, s))


def to_ts(value: Any) -> Optional[datetime]:
    """Convert SQLite timestamp (REAL epoch, ISO string, or None) → aware datetime."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        # Try float string
        try:
            return datetime.fromtimestamp(float(s), tz=timezone.utc)
        except ValueError:
            pass
    return None


def rows_as_dicts(cursor: sqlite3.Cursor) -> list[dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def print_progress(table: str, count: int) -> None:
    print(f"  Migrating {table}... {count} rows done")


# ---------------------------------------------------------------------------
# Table migrators
# ---------------------------------------------------------------------------

def migrate_users(sqlite_conn: sqlite3.Connection, pg_cur) -> int:
    cur = sqlite_conn.execute("SELECT * FROM users")
    rows = rows_as_dicts(cur)
    count = 0
    for r in rows:
        pg_cur.execute(
            """
            INSERT INTO users (id, name, email, password_hash, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                to_uuid(r.get("id")),
                r.get("name", ""),
                r.get("email", ""),
                r.get("password_hash", ""),
                to_ts(r.get("created_at")) or datetime.now(tz=timezone.utc),
            ),
        )
        count += pg_cur.rowcount
    return count


def migrate_sessions(sqlite_conn: sqlite3.Connection, pg_cur) -> int:
    cur = sqlite_conn.execute("SELECT * FROM sessions")
    rows = rows_as_dicts(cur)
    count = 0
    for r in rows:
        user_id = to_uuid(r.get("user_id"))
        if not user_id:
            continue
        pg_cur.execute(
            """
            INSERT INTO sessions (token, user_id, created_at, expires_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (token) DO NOTHING
            """,
            (
                r.get("token"),
                user_id,
                to_ts(r.get("created_at")) or datetime.now(tz=timezone.utc),
                to_ts(r.get("expires_at")) or datetime.now(tz=timezone.utc),
            ),
        )
        count += pg_cur.rowcount
    return count


def migrate_chat_logs(sqlite_conn: sqlite3.Connection, pg_cur) -> int:
    cur = sqlite_conn.execute("SELECT * FROM chat_logs")
    rows = rows_as_dicts(cur)
    count = 0
    batch = []
    for r in rows:
        role = r.get("role", "user")
        if role not in ("user", "assistant", "system"):
            role = "user"
        batch.append((
            to_uuid(r.get("id")) or str(uuid.uuid4()),
            r.get("session_id", ""),
            r.get("ip_hash"),
            role,
            r.get("message", ""),
            r.get("model"),
            to_ts(r.get("created_at")) or datetime.now(tz=timezone.utc),
        ))
        if len(batch) >= 500:
            psycopg2.extras.execute_values(
                pg_cur,
                """
                INSERT INTO chat_logs (id, session_id, ip_hash, role, message, model, created_at)
                VALUES %s ON CONFLICT (id) DO NOTHING
                """,
                batch,
            )
            count += pg_cur.rowcount
            batch = []
    if batch:
        psycopg2.extras.execute_values(
            pg_cur,
            """
            INSERT INTO chat_logs (id, session_id, ip_hash, role, message, model, created_at)
            VALUES %s ON CONFLICT (id) DO NOTHING
            """,
            batch,
        )
        count += pg_cur.rowcount
    return count


def migrate_subscribers(sqlite_conn: sqlite3.Connection, pg_cur) -> int:
    cur = sqlite_conn.execute("SELECT * FROM subscribers")
    rows = rows_as_dicts(cur)
    count = 0
    for r in rows:
        pg_cur.execute(
            """
            INSERT INTO subscribers (id, email, ip_hash, created_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (email) DO NOTHING
            """,
            (
                to_uuid(r.get("id")) or str(uuid.uuid4()),
                r.get("email", ""),
                r.get("ip_hash"),
                to_ts(r.get("created_at")) or datetime.now(tz=timezone.utc),
            ),
        )
        count += pg_cur.rowcount
    return count


def migrate_forum_posts(sqlite_conn: sqlite3.Connection, pg_cur) -> int:
    cur = sqlite_conn.execute("SELECT * FROM forum_posts ORDER BY created_at ASC")
    rows = rows_as_dicts(cur)
    count = 0
    for r in rows:
        pg_cur.execute(
            """
            INSERT INTO forum_posts (id, user_id, user_name, text, parent_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                to_uuid(r.get("id")) or str(uuid.uuid4()),
                to_uuid(r.get("user_id")),
                r.get("user_name", "Anonymous"),
                r.get("text", ""),
                to_uuid(r.get("parent_id")),
                to_ts(r.get("created_at")) or datetime.now(tz=timezone.utc),
            ),
        )
        count += pg_cur.rowcount
    return count


def migrate_messages(sqlite_conn: sqlite3.Connection, pg_cur) -> int:
    cur = sqlite_conn.execute("SELECT * FROM messages")
    rows = rows_as_dicts(cur)
    count = 0
    for r in rows:
        pg_cur.execute(
            """
            INSERT INTO messages (id, user_id, sender, text, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                to_uuid(r.get("id")) or str(uuid.uuid4()),
                to_uuid(r.get("user_id")),
                r.get("sender", ""),
                r.get("text", ""),
                to_ts(r.get("created_at")) or datetime.now(tz=timezone.utc),
            ),
        )
        count += pg_cur.rowcount
    return count


def migrate_newsletter_runs(sqlite_conn: sqlite3.Connection, pg_cur) -> int:
    cur = sqlite_conn.execute("SELECT * FROM newsletter_runs")
    rows = rows_as_dicts(cur)
    count = 0
    for r in rows:
        status = r.get("status", "done")
        pg_cur.execute(
            """
            INSERT INTO newsletter_runs
                (id, week_key, run_type, status, subject, article_count,
                 subscriber_count, sent_count, created_at, completed_at, error)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                to_uuid(r.get("id")) or str(uuid.uuid4()),
                r.get("week_key", ""),
                r.get("run_type", ""),
                status,
                r.get("subject", ""),
                int(r.get("article_count", 0) or 0),
                int(r.get("subscriber_count", 0) or 0),
                int(r.get("sent_count", 0) or 0),
                to_ts(r.get("created_at")) or datetime.now(tz=timezone.utc),
                to_ts(r.get("completed_at")),
                r.get("error"),
            ),
        )
        count += pg_cur.rowcount
    return count


def migrate_newsletter_deliveries(sqlite_conn: sqlite3.Connection, pg_cur) -> int:
    cur = sqlite_conn.execute("SELECT * FROM newsletter_deliveries")
    rows = rows_as_dicts(cur)
    count = 0
    for r in rows:
        status = r.get("status", "sent")
        if status not in ("pending", "sent", "failed"):
            status = "sent"
        pg_cur.execute(
            """
            INSERT INTO newsletter_deliveries (id, run_id, email, status, error, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                to_uuid(r.get("id")) or str(uuid.uuid4()),
                to_uuid(r.get("run_id")),
                r.get("email", ""),
                status,
                r.get("error"),
                to_ts(r.get("created_at")) or datetime.now(tz=timezone.utc),
            ),
        )
        count += pg_cur.rowcount
    return count


def migrate_password_resets(sqlite_conn: sqlite3.Connection, pg_cur) -> int:
    cur = sqlite_conn.execute("SELECT * FROM password_resets")
    rows = rows_as_dicts(cur)
    count = 0
    for r in rows:
        user_id = to_uuid(r.get("user_id"))
        if not user_id:
            continue
        pg_cur.execute(
            """
            INSERT INTO password_resets (token, user_id, created_at, used)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (token) DO NOTHING
            """,
            (
                r.get("token", ""),
                user_id,
                to_ts(r.get("created_at")) or datetime.now(tz=timezone.utc),
                bool(r.get("used", False)),
            ),
        )
        count += pg_cur.rowcount
    return count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

TABLES = [
    ("users",                  migrate_users),
    ("sessions",               migrate_sessions),
    ("chat_logs",              migrate_chat_logs),
    ("subscribers",            migrate_subscribers),
    ("forum_posts",            migrate_forum_posts),
    ("messages",               migrate_messages),
    ("newsletter_runs",        migrate_newsletter_runs),
    ("newsletter_deliveries",  migrate_newsletter_deliveries),
    ("password_resets",        migrate_password_resets),
]


def sqlite_table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate SQLite → PostgreSQL")
    parser.add_argument("--sqlite", default=os.environ.get("SQLITE_PATH", "chat.db"))
    parser.add_argument("--pg", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()

    if not args.pg:
        sys.exit("ERROR: PostgreSQL URL required. Set DATABASE_URL or pass --pg.")

    print(f"SQLite source : {args.sqlite}")
    print(f"PostgreSQL dst: {args.pg}\n")

    sqlite_conn = sqlite3.connect(args.sqlite)
    sqlite_conn.row_factory = sqlite3.Row

    pg_conn = psycopg2.connect(args.pg)
    pg_conn.autocommit = False
    pg_cur = pg_conn.cursor()

    results: list[tuple[str, int]] = []

    for table_name, migrator in TABLES:
        if not sqlite_table_exists(sqlite_conn, table_name):
            print(f"  Skipping {table_name} (table not in SQLite)")
            results.append((table_name, 0))
            continue
        try:
            count = migrator(sqlite_conn, pg_cur)
            pg_conn.commit()
            print_progress(table_name, count)
            results.append((table_name, count))
        except Exception as exc:
            pg_conn.rollback()
            print(f"  ERROR migrating {table_name}: {exc}", file=sys.stderr)
            results.append((table_name, -1))

    sqlite_conn.close()
    pg_cur.close()
    pg_conn.close()

    # Summary table
    print("\n" + "=" * 40)
    print(f"{'Table':<30} {'Rows migrated':>12}")
    print("-" * 40)
    total = 0
    for name, cnt in results:
        label = str(cnt) if cnt >= 0 else "ERROR"
        print(f"{name:<30} {label:>12}")
        if cnt > 0:
            total += cnt
    print("-" * 40)
    print(f"{'TOTAL':<30} {total:>12}")
    print("=" * 40)


if __name__ == "__main__":
    main()
