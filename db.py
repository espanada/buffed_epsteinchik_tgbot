import logging
import random
import re
import sqlite3
from typing import Any, Optional

from config import (
    CANDIDATE_POOL_SIZE,
    DB_PATH,
    MAX_LIKES_PER_DAY,
    REPORT_AUTO_PAUSE_THRESHOLD,
    get_database_url,
)
from models import Profile

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None


class ConnectionWrapper:
    def __init__(self, conn: Any, backend: str) -> None:
        self._conn = conn
        self.backend = backend

    def __enter__(self) -> "ConnectionWrapper":
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._conn.__exit__(exc_type, exc, tb)

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        q = query
        if self.backend == "postgres":
            q = q.replace("date('now')", "CURRENT_DATE")
            q = _rewrite_insert_or_ignore(q)
            q = q.replace("?", "%s")
        return self._conn.execute(q, params)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()


def _rewrite_insert_or_ignore(query: str) -> str:
    if "INSERT OR IGNORE" not in query.upper():
        return query
    q = re.sub(r"INSERT\s+OR\s+IGNORE", "INSERT", query, flags=re.IGNORECASE)
    if "ON CONFLICT" not in q.upper():
        q = q.rstrip() + " ON CONFLICT DO NOTHING"
    return q


def _is_postgres_url(url: Optional[str]) -> bool:
    if not url:
        return False
    value = url.lower()
    return value.startswith("postgres://") or value.startswith("postgresql://")


def is_postgres_enabled() -> bool:
    return _is_postgres_url(get_database_url())


def get_conn() -> ConnectionWrapper:
    db_url = get_database_url()
    if _is_postgres_url(db_url):
        if psycopg is None or dict_row is None:
            raise RuntimeError("Postgres URL is set, but psycopg is not installed.")
        conn = psycopg.connect(db_url, row_factory=dict_row)
        return ConnectionWrapper(conn, "postgres")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return ConnectionWrapper(conn, "sqlite")


def add_column_if_missing(conn: ConnectionWrapper, table: str, column_def: str) -> None:
    col_name = column_def.split()[0]
    if conn.backend == "postgres":
        existing = {
            row["column_name"]
            for row in conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = ?
                """,
                (table,),
            ).fetchall()
        }
    else:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if col_name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


def init_db() -> None:
    with get_conn() as conn:
        if conn.backend == "postgres":
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    display_name TEXT NOT NULL,
                    age INTEGER NOT NULL,
                    city TEXT NOT NULL,
                    bio TEXT NOT NULL,
                    gender TEXT NOT NULL DEFAULT 'any',
                    looking_for TEXT NOT NULL DEFAULT 'any',
                    min_age INTEGER NOT NULL DEFAULT 18,
                    max_age INTEGER NOT NULL DEFAULT 99,
                    photo_file_id TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active SMALLINT NOT NULL DEFAULT 1
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS likes (
                    from_user_id BIGINT NOT NULL,
                    to_user_id BIGINT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (from_user_id, to_user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS likes_daily (
                    user_id BIGINT NOT NULL,
                    day DATE NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, day)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS views (
                    from_user_id BIGINT NOT NULL,
                    to_user_id BIGINT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (from_user_id, to_user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS matches (
                    user1_id BIGINT NOT NULL,
                    user2_id BIGINT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user1_id, user2_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS blocks (
                    from_user_id BIGINT NOT NULL,
                    to_user_id BIGINT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (from_user_id, to_user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    from_user_id BIGINT NOT NULL,
                    to_user_id BIGINT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    reviewed_by BIGINT,
                    reviewed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS moderation_audit_log (
                    id BIGSERIAL PRIMARY KEY,
                    actor_user_id BIGINT,
                    target_user_id BIGINT,
                    action TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    display_name TEXT NOT NULL,
                    age INTEGER NOT NULL,
                    city TEXT NOT NULL,
                    bio TEXT NOT NULL,
                    gender TEXT NOT NULL DEFAULT 'any',
                    looking_for TEXT NOT NULL DEFAULT 'any',
                    min_age INTEGER NOT NULL DEFAULT 18,
                    max_age INTEGER NOT NULL DEFAULT 99,
                    photo_file_id TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            add_column_if_missing(conn, "profiles", "gender TEXT NOT NULL DEFAULT 'any'")
            add_column_if_missing(conn, "profiles", "looking_for TEXT NOT NULL DEFAULT 'any'")
            add_column_if_missing(conn, "profiles", "min_age INTEGER NOT NULL DEFAULT 18")
            add_column_if_missing(conn, "profiles", "max_age INTEGER NOT NULL DEFAULT 99")
            add_column_if_missing(conn, "profiles", "photo_file_id TEXT")
            add_column_if_missing(conn, "profiles", "updated_at DATETIME")
            conn.execute("UPDATE profiles SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS likes (
                    from_user_id INTEGER NOT NULL,
                    to_user_id INTEGER NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (from_user_id, to_user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS likes_daily (
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, day)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS views (
                    from_user_id INTEGER NOT NULL,
                    to_user_id INTEGER NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (from_user_id, to_user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS matches (
                    user1_id INTEGER NOT NULL,
                    user2_id INTEGER NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user1_id, user2_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS blocks (
                    from_user_id INTEGER NOT NULL,
                    to_user_id INTEGER NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (from_user_id, to_user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    from_user_id INTEGER NOT NULL,
                    to_user_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    reviewed_by INTEGER,
                    reviewed_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            add_column_if_missing(conn, "reports", "status TEXT NOT NULL DEFAULT 'new'")
            add_column_if_missing(conn, "reports", "reviewed_by INTEGER")
            add_column_if_missing(conn, "reports", "reviewed_at DATETIME")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS moderation_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_user_id INTEGER,
                    target_user_id INTEGER,
                    action TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

        conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_active ON profiles(is_active)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_age ON profiles(age)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_gender ON profiles(gender)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_filters ON profiles(min_age, max_age)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_updated ON profiles(updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_likes_from ON likes(from_user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_likes_to ON likes(to_user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_blocks_from ON blocks(from_user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_blocks_to ON blocks(to_user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_status_target ON reports(status, to_user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at)")


def upsert_profile(profile: Profile) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO profiles (
                user_id, username, display_name, age, city, bio,
                gender, looking_for, min_age, max_age, photo_file_id, updated_at, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                display_name=excluded.display_name,
                age=excluded.age,
                city=excluded.city,
                bio=excluded.bio,
                gender=excluded.gender,
                looking_for=excluded.looking_for,
                min_age=excluded.min_age,
                max_age=excluded.max_age,
                photo_file_id=excluded.photo_file_id,
                is_active=1,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                profile.user_id,
                profile.username,
                profile.display_name,
                profile.age,
                profile.city,
                profile.bio,
                profile.gender,
                profile.looking_for,
                profile.min_age,
                profile.max_age,
                profile.photo_file_id,
            ),
        )


def get_profile(user_id: int) -> Optional[Any]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()


def set_profile_active(user_id: int, is_active: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE profiles SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (1 if is_active else 0, user_id),
        )


def update_profile_bio(user_id: int, bio: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE profiles SET bio = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (bio, user_id),
        )


def update_profile_photo(user_id: int, photo_file_id: Optional[str]) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE profiles SET photo_file_id = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (photo_file_id, user_id),
        )


def update_profile_filters(user_id: int, looking_for: str, min_age: int, max_age: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE profiles
            SET looking_for = ?, min_age = ?, max_age = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (looking_for, min_age, max_age, user_id),
        )


def delete_profile(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM likes WHERE from_user_id = ? OR to_user_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM matches WHERE user1_id = ? OR user2_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM blocks WHERE from_user_id = ? OR to_user_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM views WHERE from_user_id = ? OR to_user_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM likes_daily WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM reports WHERE from_user_id = ? OR to_user_id = ?", (user_id, user_id))


def mark_view(from_user_id: int, to_user_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO views (from_user_id, to_user_id) VALUES (?, ?)",
            (from_user_id, to_user_id),
        )


def was_viewed(from_user_id: int, to_user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM views WHERE from_user_id = ? AND to_user_id = ?",
            (from_user_id, to_user_id),
        ).fetchone()
    return bool(row)


def has_block_between(user1_id: int, user2_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM blocks
            WHERE (from_user_id = ? AND to_user_id = ?)
               OR (from_user_id = ? AND to_user_id = ?)
            LIMIT 1
            """,
            (user1_id, user2_id, user2_id, user1_id),
        ).fetchone()
    return bool(row)


def can_interact(
    from_user_id: int,
    to_user_id: int,
    *,
    require_source_active: bool = False,
    require_target_active: bool = True,
) -> tuple[bool, str]:
    if from_user_id == to_user_id:
        return False, "self_action"

    from_profile = get_profile(from_user_id)
    to_profile = get_profile(to_user_id)
    if from_profile is None or to_profile is None:
        return False, "profile_not_found"

    if require_source_active and from_profile["is_active"] != 1:
        return False, "source_inactive"
    if require_target_active and to_profile["is_active"] != 1:
        return False, "target_inactive"
    if has_block_between(from_user_id, to_user_id):
        return False, "blocked"
    return True, "ok"


def pick_candidate_id(user_id: int) -> Optional[int]:
    me = get_profile(user_id)
    if me is None:
        return None

    with get_conn() as conn:
        if conn.backend == "postgres":
            score_expr = """
                CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM likes l
                        WHERE l.from_user_id = p.user_id AND l.to_user_id = ?
                    ) THEN 80
                    ELSE 0
                END
                + CASE WHEN lower(p.city) = lower(?) THEN 30 ELSE 0 END
                + CASE WHEN p.photo_file_id IS NOT NULL THEN 15 ELSE 0 END
                - abs(p.age - ?) * 0.4
                - (EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - COALESCE(p.updated_at, CURRENT_TIMESTAMP))) / 86400.0) * 0.8
            """
        else:
            score_expr = """
                CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM likes l
                        WHERE l.from_user_id = p.user_id AND l.to_user_id = ?
                    ) THEN 80
                    ELSE 0
                END
                + CASE WHEN lower(p.city) = lower(?) THEN 30 ELSE 0 END
                + CASE WHEN p.photo_file_id IS NOT NULL THEN 15 ELSE 0 END
                - abs(p.age - ?) * 0.4
                - (julianday('now') - julianday(COALESCE(p.updated_at, CURRENT_TIMESTAMP))) * 0.8
            """

        rows = conn.execute(
            f"""
            SELECT
                p.user_id,
                ({score_expr}) AS score
            FROM profiles p
            WHERE p.user_id != ?
              AND p.is_active = 1
              AND p.user_id NOT IN (SELECT to_user_id FROM views WHERE from_user_id = ?)
              AND p.user_id NOT IN (SELECT to_user_id FROM blocks WHERE from_user_id = ?)
              AND p.user_id NOT IN (SELECT from_user_id FROM blocks WHERE to_user_id = ?)
              AND p.age BETWEEN ? AND ?
              AND ? BETWEEN p.min_age AND p.max_age
              AND (? = 'any' OR p.gender = ?)
              AND (p.looking_for = 'any' OR p.looking_for = ?)
            ORDER BY score DESC
            LIMIT ?
            """,
            (
                user_id,
                me["city"],
                me["age"],
                user_id,
                user_id,
                user_id,
                user_id,
                me["min_age"],
                me["max_age"],
                me["age"],
                me["looking_for"],
                me["looking_for"],
                me["gender"],
                CANDIDATE_POOL_SIZE,
            ),
        ).fetchall()

    if not rows:
        return None

    top_pool = [row["user_id"] for row in rows[: min(5, len(rows))]]
    return random.choice(top_pool)


def get_next_candidate(user_id: int) -> Optional[Any]:
    candidate_id = pick_candidate_id(user_id)
    if candidate_id is None:
        return None

    with get_conn() as conn:
        candidate = conn.execute(
            "SELECT * FROM profiles WHERE user_id = ?",
            (candidate_id,),
        ).fetchone()

    if candidate is not None:
        mark_view(user_id, candidate_id)

    return candidate


def block_user(from_user_id: int, to_user_id: int) -> None:
    ok, _ = can_interact(from_user_id, to_user_id, require_target_active=False)
    if not ok:
        return
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO blocks (from_user_id, to_user_id) VALUES (?, ?)",
            (from_user_id, to_user_id),
        )
    log_moderation_event(from_user_id, to_user_id, "block", "inline_block")


def report_user(from_user_id: int, to_user_id: int, reason: str = "inline_report") -> tuple[bool, bool, int, str]:
    ok, check_reason = can_interact(from_user_id, to_user_id, require_target_active=False)
    if not ok:
        return False, False, 0, check_reason

    with get_conn() as conn:
        already_open = conn.execute(
            """
            SELECT 1
            FROM reports
            WHERE from_user_id = ? AND to_user_id = ? AND status = 'new'
            """,
            (from_user_id, to_user_id),
        ).fetchone()
        if already_open:
            pending_count = conn.execute(
                "SELECT COUNT(*) AS c FROM reports WHERE to_user_id = ? AND status = 'new'",
                (to_user_id,),
            ).fetchone()["c"]
            return True, False, pending_count, "already_reported"

        conn.execute(
            "INSERT INTO reports (from_user_id, to_user_id, reason, status) VALUES (?, ?, ?, 'new')",
            (from_user_id, to_user_id, reason),
        )
        pending_count = conn.execute(
            "SELECT COUNT(*) AS c FROM reports WHERE to_user_id = ? AND status = 'new'",
            (to_user_id,),
        ).fetchone()["c"]
        updated = conn.execute(
            """
            UPDATE profiles
            SET is_active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND is_active = 1 AND ? >= ?
            """,
            (to_user_id, pending_count, REPORT_AUTO_PAUSE_THRESHOLD),
        )
        auto_paused = updated.rowcount > 0

    log_moderation_event(from_user_id, to_user_id, "report", reason)
    if auto_paused:
        log_moderation_event(
            None,
            to_user_id,
            "auto_pause_by_reports",
            f"pending_reports={pending_count}",
        )
    return True, auto_paused, pending_count, "ok"


def log_moderation_event(
    actor_user_id: Optional[int],
    target_user_id: Optional[int],
    action: str,
    details: str = "",
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO moderation_audit_log (actor_user_id, target_user_id, action, details)
            VALUES (?, ?, ?, ?)
            """,
            (actor_user_id, target_user_id, action, details),
        )


def like_user(from_user_id: int, to_user_id: int) -> tuple[bool, bool, str]:
    """Returns (ok, is_match, reason)."""
    ok, reason = can_interact(from_user_id, to_user_id)
    if not ok:
        return False, False, reason

    with get_conn() as conn:
        if conn.backend == "sqlite":
            conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute(
            "SELECT 1 FROM likes WHERE from_user_id = ? AND to_user_id = ?",
            (from_user_id, to_user_id),
        ).fetchone()
        if existing:
            conn.commit()
            return True, False, "already_liked"

        conn.execute(
            """
            INSERT OR IGNORE INTO likes_daily (user_id, day, count)
            VALUES (?, date('now'), 0)
            """,
            (from_user_id,),
        )
        row = conn.execute(
            "SELECT count FROM likes_daily WHERE user_id = ? AND day = date('now')",
            (from_user_id,),
        ).fetchone()
        if row is None:
            conn.commit()
            return False, False, "quota_error"
        if row["count"] >= MAX_LIKES_PER_DAY:
            conn.commit()
            return False, False, "quota_exceeded"

        conn.execute(
            "INSERT INTO likes (from_user_id, to_user_id) VALUES (?, ?)",
            (from_user_id, to_user_id),
        )
        conn.execute(
            "UPDATE likes_daily SET count = count + 1 WHERE user_id = ? AND day = date('now')",
            (from_user_id,),
        )
        logging.info("User %s liked %s", from_user_id, to_user_id)

        reverse_like = conn.execute(
            "SELECT 1 FROM likes WHERE from_user_id = ? AND to_user_id = ?",
            (to_user_id, from_user_id),
        ).fetchone()
        if not reverse_like:
            conn.commit()
            return True, False, "ok"

        user1_id, user2_id = sorted((from_user_id, to_user_id))
        conn.execute(
            "INSERT OR IGNORE INTO matches (user1_id, user2_id) VALUES (?, ?)",
            (user1_id, user2_id),
        )
        conn.commit()
        logging.info("Match created between %s and %s", user1_id, user2_id)
        return True, True, "ok"


def get_stats(user_id: int) -> tuple[int, int, int]:
    with get_conn() as conn:
        sent = conn.execute(
            "SELECT COUNT(*) AS c FROM likes WHERE from_user_id = ?",
            (user_id,),
        ).fetchone()["c"]
        received = conn.execute(
            "SELECT COUNT(*) AS c FROM likes WHERE to_user_id = ?",
            (user_id,),
        ).fetchone()["c"]
        matches = conn.execute(
            "SELECT COUNT(*) AS c FROM matches WHERE user1_id = ? OR user2_id = ?",
            (user_id, user_id),
        ).fetchone()["c"]
    return sent, received, matches


def get_pending_likes(user_id: int) -> list[Any]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT p.*
            FROM likes l
            JOIN profiles p ON p.user_id = l.from_user_id
            WHERE l.to_user_id = ?
              AND p.is_active = 1
              AND NOT EXISTS (
                  SELECT 1
                  FROM blocks b
                  WHERE (b.from_user_id = ? AND b.to_user_id = p.user_id)
                     OR (b.from_user_id = p.user_id AND b.to_user_id = ?)
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM likes l2
                  WHERE l2.from_user_id = ? AND l2.to_user_id = l.from_user_id
              )
            ORDER BY l.created_at DESC
            LIMIT 20
            """,
            (user_id, user_id, user_id, user_id),
        ).fetchall()


def get_admin_stats() -> tuple[int, int, int, int]:
    with get_conn() as conn:
        users = conn.execute("SELECT COUNT(*) AS c FROM profiles").fetchone()["c"]
        active = conn.execute("SELECT COUNT(*) AS c FROM profiles WHERE is_active = 1").fetchone()["c"]
        likes = conn.execute("SELECT COUNT(*) AS c FROM likes").fetchone()["c"]
        matches = conn.execute("SELECT COUNT(*) AS c FROM matches").fetchone()["c"]
    return users, active, likes, matches


def get_reported_profiles(limit: int) -> list[Any]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT
                r.to_user_id,
                COUNT(*) AS pending_reports,
                MAX(r.created_at) AS last_report_at,
                p.username,
                p.display_name,
                p.is_active
            FROM reports r
            JOIN profiles p ON p.user_id = r.to_user_id
            WHERE r.status = 'new'
            GROUP BY r.to_user_id, p.username, p.display_name, p.is_active
            ORDER BY pending_reports DESC, last_report_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def admin_set_profile_active(
    admin_user_id: int,
    target_user_id: int,
    is_active: bool,
    reason: str = "",
) -> bool:
    with get_conn() as conn:
        updated = conn.execute(
            """
            UPDATE profiles
            SET is_active = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (1 if is_active else 0, target_user_id),
        )
    if updated.rowcount == 0:
        return False

    action = "admin_unban" if is_active else "admin_ban"
    log_moderation_event(admin_user_id, target_user_id, action, reason)
    return True


def resolve_reports_for_user(
    admin_user_id: int,
    target_user_id: int,
    resolution: str = "resolved",
) -> int:
    with get_conn() as conn:
        updated = conn.execute(
            """
            UPDATE reports
            SET status = ?, reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP
            WHERE to_user_id = ? AND status = 'new'
            """,
            (resolution, admin_user_id, target_user_id),
        )
    if updated.rowcount > 0:
        log_moderation_event(
            admin_user_id,
            target_user_id,
            "reports_resolved",
            f"resolution={resolution};count={updated.rowcount}",
        )
    return updated.rowcount
