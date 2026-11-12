from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 8


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(schema)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(channels)")}
            additions = {
                "subscription_status": "TEXT NOT NULL DEFAULT 'not_subscribed'",
                "subscribed_at": "TEXT",
                "subscription_expires_at": "TEXT",
                "subscription_error": "TEXT",
                "source_url": "TEXT",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE channels ADD COLUMN {name} {declaration}")
            account_columns = {row["name"] for row in connection.execute("PRAGMA table_info(tiktok_accounts)")}
            account_additions = {
                "mode": "TEXT NOT NULL DEFAULT 'cdp'",
                "endpoint": "TEXT",
                "gpm_profile_id": "TEXT",
                "active": "INTEGER NOT NULL DEFAULT 1",
                "daily_limit": "INTEGER NOT NULL DEFAULT 3",
                "posts_today": "INTEGER NOT NULL DEFAULT 0",
                "counter_date": "TEXT",
                "status": "TEXT NOT NULL DEFAULT 'idle'",
                "hashtags": "TEXT NOT NULL DEFAULT ''",
                "speed": "REAL NOT NULL DEFAULT 1.0",
                "zoom_width_percent": "INTEGER NOT NULL DEFAULT 100",
                "zoom_height_percent": "INTEGER NOT NULL DEFAULT 100",
                "caption_mode": "TEXT NOT NULL DEFAULT 'fixed'",
                "caption_text": "TEXT NOT NULL DEFAULT ''",
                "output_dir": "TEXT NOT NULL DEFAULT ''",
                "notification_sound": "TEXT NOT NULL DEFAULT ''",
                "split_enabled": "INTEGER",
                "overlay_enabled": "INTEGER",
                "background_path": "TEXT NOT NULL DEFAULT ''",
                "publish_enabled": "INTEGER",
                "ai_api_url": "TEXT NOT NULL DEFAULT ''",
                "ai_api_key": "TEXT NOT NULL DEFAULT ''",
                "ai_model": "TEXT NOT NULL DEFAULT ''",
                "ai_system_prompt": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in account_additions.items():
                if name not in account_columns:
                    connection.execute(f"ALTER TABLE tiktok_accounts ADD COLUMN {name} {declaration}")
            source_columns = {row["name"] for row in connection.execute("PRAGMA table_info(channel_route_sources)")}
            source_additions = {
                "segment_seconds": "INTEGER",
                "zoom_percent": "INTEGER NOT NULL DEFAULT 100",
                "speed": "REAL NOT NULL DEFAULT 1.0",
                "background_path": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in source_additions.items():
                if name not in source_columns:
                    connection.execute(f"ALTER TABLE channel_route_sources ADD COLUMN {name} {declaration}")
            task_columns = {row["name"] for row in connection.execute("PRAGMA table_info(upload_tasks)")}
            if "is_test" not in task_columns:
                connection.execute(
                    "ALTER TABLE upload_tasks ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0")
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (SCHEMA_VERSION,)
            )

    def recover_expired_leases(self) -> int:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE jobs SET status='queued', lease_owner=NULL, lease_expires_at=NULL,
                   updated_at=? WHERE status='leased' AND lease_expires_at < ?""",
                (now, now),
            )
            return cursor.rowcount

    def enqueue(self, kind: str, media_item_id: int, idempotency_key: str, payload: str = "{}") -> int:
        with self.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO jobs(kind, media_item_id, idempotency_key, payload)
                   VALUES (?, ?, ?, ?)""",
                (kind, media_item_id, idempotency_key, payload),
            )
            row = connection.execute(
                "SELECT id FROM jobs WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            return int(row["id"])

    def claim(self, worker: str, kinds: tuple[str, ...], lease_seconds: int = 1800):
        if not kinds:
            return None
        now = utc_now()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
        placeholders = ",".join("?" for _ in kinds)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"""SELECT * FROM jobs WHERE kind IN ({placeholders})
                AND status IN ('queued','retry_wait') AND available_at <= ?
                ORDER BY created_at, id LIMIT 1""",
                (*kinds, now),
            ).fetchone()
            if not row:
                return None
            connection.execute(
                """UPDATE jobs SET status='leased', lease_owner=?, lease_expires_at=?,
                attempts=attempts+1, updated_at=? WHERE id=?""",
                (worker, expires, now, row["id"]),
            )
            return connection.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()

    def complete(self, job_id: int) -> None:
        self._finish(job_id, "completed", None)

    def fail(self, job_id: int, message: str, retry: bool = True) -> None:
        with self.connect() as connection:
            row = connection.execute("SELECT attempts,max_attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
            can_retry = bool(retry and row and row["attempts"] < row["max_attempts"])
            status = "retry_wait" if can_retry else "failed"
            delay = min(3600, 30 * (2 ** max(0, int(row["attempts"]) - 1))) if row else 30
            available = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            connection.execute(
                """UPDATE jobs SET status=?, error_message=?, available_at=?, lease_owner=NULL,
                lease_expires_at=NULL, updated_at=? WHERE id=?""",
                (status, message[:4000], available, utc_now(), job_id),
            )

    def manual_action(self, job_id: int, message: str) -> None:
        self._finish(job_id, "manual_action", message)

    def retry_job(self, job_id: int) -> None:
        """Requeue a failed/manual-action job so the pipeline can claim it again."""
        with self.connect() as connection:
            connection.execute(
                """UPDATE jobs SET status='queued', error_message=NULL, lease_owner=NULL,
                lease_expires_at=NULL, available_at=?, updated_at=? WHERE id=?""",
                (utc_now(), utc_now(), job_id),
            )

    def cancel_job(self, job_id: int, message: str = "Đã bỏ qua bởi người dùng") -> None:
        """Cancel a job from the UI without deleting audit history."""
        with self.connect() as connection:
            connection.execute(
                """UPDATE jobs SET status='cancelled', error_message=?, lease_owner=NULL,
                lease_expires_at=NULL, updated_at=? WHERE id=?""",
                (message[:4000], utc_now(), job_id),
            )

    def event(self, job_id: int | None, level: str, message: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO job_events(job_id,level,message,created_at) VALUES (?,?,?,?)",
                (job_id, level, message, utc_now()),
            )

    def _finish(self, job_id: int, status: str, message: str | None) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE jobs SET status=?, error_message=?, lease_owner=NULL,
                lease_expires_at=NULL, updated_at=? WHERE id=?""",
                (status, message, utc_now(), job_id),
            )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
