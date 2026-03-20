"""
state_store.py
--------------
SQLite-backed idempotency store.

Guarantees that if the orchestrator crashes mid-run (network drop,
laptop closes, API timeout), restarting it will NEVER re-dispatch
a Devin session for a file already in progress or completed.

This is standard distributed systems practice — treat Devin API
calls like idempotent remote procedure calls with persisted state.
"""

import sqlite3
import os
from enum import Enum
from datetime import datetime


class FileStatus(Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    DLQ = "DLQ"  # Dead Letter Queue — circuit breaker triggered


class StateStore:
    def __init__(self, db_path: str = "migration_state.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS file_migrations (
                filepath        TEXT PRIMARY KEY,
                filename        TEXT,
                batch_number    INTEGER DEFAULT 0,
                status          TEXT DEFAULT 'PENDING',
                session_id      TEXT,
                session_url     TEXT,
                pr_url          TEXT,
                attempts        INTEGER DEFAULT 0,
                error_reason    TEXT,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def initialize_files(self, batches: list[list[str]]):
        """
        Seed the database with all files.
        Uses INSERT OR IGNORE so re-running the script never
        overwrites existing state (idempotent initialization).
        """
        for batch_num, batch in enumerate(batches):
            for filepath in batch:
                filename = os.path.basename(filepath)
                self.conn.execute(
                    """INSERT OR IGNORE INTO file_migrations
                       (filepath, filename, batch_number, status)
                       VALUES (?, ?, ?, ?)""",
                    (filepath, filename, batch_num, FileStatus.PENDING.value),
                )
        self.conn.commit()

    def get_status(self, filepath: str) -> FileStatus | None:
        row = self.conn.execute(
            "SELECT status FROM file_migrations WHERE filepath=?", (filepath,)
        ).fetchone()
        return FileStatus(row[0]) if row else None

    def get_attempts(self, filepath: str) -> int:
        row = self.conn.execute(
            "SELECT attempts FROM file_migrations WHERE filepath=?", (filepath,)
        ).fetchone()
        return row[0] if row else 0

    def mark_in_progress(self, filepath: str, session_id: str, session_url: str = ""):
        self.conn.execute(
            """UPDATE file_migrations
               SET status=?, session_id=?, session_url=?,
                   attempts=attempts+1, updated_at=?
               WHERE filepath=?""",
            (
                FileStatus.IN_PROGRESS.value,
                session_id,
                session_url,
                datetime.now().isoformat(),
                filepath,
            ),
        )
        self.conn.commit()

    def mark_completed(self, filepath: str, pr_url: str = ""):
        self.conn.execute(
            """UPDATE file_migrations
               SET status=?, pr_url=?, updated_at=?
               WHERE filepath=?""",
            (FileStatus.COMPLETED.value, pr_url, datetime.now().isoformat(), filepath),
        )
        self.conn.commit()

    def mark_dlq(self, filepath: str, reason: str = ""):
        """Circuit breaker triggered — route to Dead Letter Queue."""
        self.conn.execute(
            """UPDATE file_migrations
               SET status=?, error_reason=?, updated_at=?
               WHERE filepath=?""",
            (FileStatus.DLQ.value, reason, datetime.now().isoformat(), filepath),
        )
        self.conn.commit()

    def get_all_rows(self) -> list[dict]:
        """Return all rows as dicts for the dashboard."""
        cursor = self.conn.execute(
            """SELECT filepath, filename, batch_number, status,
                      session_id, session_url, pr_url, attempts, error_reason
               FROM file_migrations
               ORDER BY batch_number, filepath"""
        )
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def get_summary(self) -> dict:
        """Return counts by status for the dashboard header."""
        cursor = self.conn.execute(
            "SELECT status, COUNT(*) FROM file_migrations GROUP BY status"
        )
        summary = {s.value: 0 for s in FileStatus}
        for status, count in cursor.fetchall():
            summary[status] = count
        return summary

    def get_pending_for_batch(self, batch_number: int) -> list[str]:
        """Return PENDING files for a given batch number."""
        cursor = self.conn.execute(
            """SELECT filepath FROM file_migrations
               WHERE batch_number=? AND status=?""",
            (batch_number, FileStatus.PENDING.value),
        )
        return [row[0] for row in cursor.fetchall()]

    def close(self):
        self.conn.close()
