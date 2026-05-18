from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .config import settings


def _connect() -> sqlite3.Connection:
    Path(settings.state_db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        settings.state_db_path,
        check_same_thread=False,
        isolation_level=None,
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS thread_sessions (
            thread_id   TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            last_seq    INTEGER NOT NULL DEFAULT 0,
            created_at  INTEGER NOT NULL DEFAULT 0,
            updated_at  INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # Migrate the pre-refactor schema (which lacked the timestamp columns)
    # so existing Railway volumes don't trip on the INSERT below.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(thread_sessions)")}
    if "created_at" not in cols:
        conn.execute("ALTER TABLE thread_sessions ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0")
    if "updated_at" not in cols:
        conn.execute("ALTER TABLE thread_sessions ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_thread_sessions_updated_at "
        "ON thread_sessions(updated_at)"
    )
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


db = _connect()


def load(thread_id: str) -> tuple[str, int] | None:
    row = db.execute(
        "SELECT session_id, last_seq FROM thread_sessions WHERE thread_id = ?",
        (thread_id,),
    ).fetchone()
    return (row[0], int(row[1])) if row else None


def save(thread_id: str, session_id: str, last_seq: int) -> None:
    now = int(time.time())
    db.execute(
        """
        INSERT INTO thread_sessions(thread_id, session_id, last_seq, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(thread_id) DO UPDATE SET
            session_id = excluded.session_id,
            last_seq   = excluded.last_seq,
            updated_at = excluded.updated_at
        """,
        (thread_id, session_id, last_seq, now, now),
    )


def update_seq(thread_id: str, last_seq: int) -> None:
    db.execute(
        "UPDATE thread_sessions SET last_seq = ?, updated_at = ? WHERE thread_id = ?",
        (last_seq, int(time.time()), thread_id),
    )


def delete(thread_id: str) -> None:
    db.execute("DELETE FROM thread_sessions WHERE thread_id = ?", (thread_id,))
