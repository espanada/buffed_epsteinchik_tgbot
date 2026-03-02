import sqlite3
from pathlib import Path
from typing import Iterable

import psycopg

from config import DB_PATH, get_database_url


TABLES: tuple[str, ...] = (
    "profiles",
    "likes",
    "likes_daily",
    "views",
    "matches",
    "blocks",
    "reports",
    "moderation_audit_log",
)


def fetch_rows(sqlite_conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()


def build_insert(table: str, columns: Iterable[str]) -> str:
    cols = list(columns)
    placeholders = ", ".join(["%s"] * len(cols))
    col_sql = ", ".join(cols)

    conflict_keys = {
        "profiles": "user_id",
        "likes": "(from_user_id, to_user_id)",
        "likes_daily": "(user_id, day)",
        "views": "(from_user_id, to_user_id)",
        "matches": "(user1_id, user2_id)",
        "blocks": "(from_user_id, to_user_id)",
    }
    conflict = conflict_keys.get(table)
    if conflict:
        return (
            f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT {conflict} DO NOTHING"
        )
    return f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"


def main() -> None:
    db_url = get_database_url()
    if not db_url:
        raise RuntimeError("Set DATABASE_URL to Postgres before running migration.")

    sqlite_path = Path(DB_PATH)
    if not sqlite_path.exists():
        raise RuntimeError(f"SQLite DB not found: {sqlite_path}")

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    with psycopg.connect(db_url) as pg_conn:
        with pg_conn.cursor() as cur:
            for table in TABLES:
                rows = fetch_rows(sqlite_conn, table)
                if not rows:
                    continue
                columns = rows[0].keys()
                sql = build_insert(table, columns)
                payload = [tuple(row[col] for col in columns) for row in rows]
                cur.executemany(sql, payload)
                print(f"Migrated {len(rows)} rows into {table}")

    sqlite_conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    main()
