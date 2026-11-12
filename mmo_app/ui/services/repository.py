from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from ...database import Database


VIDEO_STATUS_LABELS = {
    "discovered": "Đã phát hiện",
    "downloading": "Đang tải",
    "downloaded": "Đã tải",
    "processing": "Đang xử lý",
    "processed": "Đã xử lý",
    "queued": "Chờ đăng",
    "uploading": "Đang đăng",
    "uploaded": "Đã đăng",
    "done": "Hoàn tất",
    "failed": "Lỗi",
    "skipped": "Đã bỏ qua",
}

JOB_KIND_LABELS = {"download": "Tải", "process": "Xử lý", "upload": "Đăng"}


LOCAL_TZ = timezone(timedelta(hours=7))


def _optional_flag(value: bool | None) -> int | None:
    return None if value is None else (1 if value else 0)


def format_local_time(value: Any, include_date: bool = True) -> str:
    """Convert a stored UTC ISO timestamp to Vietnam time (UTC+7)."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:19].replace("T", " ") if include_date else text[11:19]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone(LOCAL_TZ)
    return local.strftime("%Y-%m-%d %H:%M:%S" if include_date else "%H:%M:%S")


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso_day(days_ago: int = 0) -> str:
    """Return a ``YYYY-MM-DD`` date string in Vietnam time (UTC+7)."""
    return (datetime.now(LOCAL_TZ) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


class UIRepository:
    """Small, dependency-injected data facade used by every page.

    All SQL lives here, so pages never talk to the database directly. The core
    pipeline keeps using its own services and is not aware of this class.
    """

    def __init__(self, database: Database):
        self.database = database

    def dashboard_summary(self) -> dict[str, int]:
        today = _iso_day()
        with self.database.connect() as connection:
            found = connection.execute(
                """SELECT COUNT(*) AS c FROM media_items
                   WHERE substr(datetime(created_at,'+7 hours'),1,10)=?""",
                (today,),
            ).fetchone()["c"]
            downloading = connection.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE kind='download' AND status IN ('queued','leased','retry_wait')"
            ).fetchone()["c"]
            processing = connection.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE kind='process' AND status IN ('queued','leased','retry_wait')"
            ).fetchone()["c"]
            queued = connection.execute(
                "SELECT COUNT(*) AS c FROM upload_tasks WHERE status='pending'"
            ).fetchone()["c"]
            posted = connection.execute(
                """SELECT COUNT(*) AS c FROM upload_tasks
                   WHERE substr(datetime(posted_at,'+7 hours'),1,10)=?""",
                (today,),
            ).fetchone()["c"]
            issues = connection.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE status IN ('manual_action','failed')"
            ).fetchone()["c"]
        return {
            "found": int(found),
            "downloading": int(downloading),
            "processing": int(processing),
            "queued": int(queued),
            "posted": int(posted),
            "issues": int(issues),
        }

    def attention_items(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT j.id AS job_id, j.kind, j.status, j.error_message,
                          m.title, m.id AS media_id
                   FROM jobs j
                   LEFT JOIN media_items m ON m.id = j.media_item_id
                   WHERE j.status IN ('manual_action','failed')
                   ORDER BY j.updated_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT id, job_id, level, message, created_at
                   FROM job_events ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def pipeline_activity(self, event_limit: int = 8) -> dict[str, Any]:
        """Return live stage states plus the latest events for the side panel."""
        kinds = ("download", "process", "upload")
        labels = {"download": "Tải video", "process": "Xử lý video", "upload": "Đăng TikTok"}
        with self.database.connect() as connection:
            count_rows = connection.execute(
                """SELECT kind,status,COUNT(*) AS c FROM jobs
                   WHERE kind IN ('download','process','upload')
                   GROUP BY kind,status"""
            ).fetchall()
            latest_rows = connection.execute(
                """SELECT kind,status,error_message FROM jobs
                   WHERE kind IN ('download','process','upload')
                   ORDER BY updated_at DESC, id DESC LIMIT 60"""
            ).fetchall()
            events = connection.execute(
                """SELECT id,job_id,level,message,created_at FROM job_events
                   ORDER BY id DESC LIMIT ?""",
                (event_limit,),
            ).fetchall()
            pending_uploads = connection.execute(
                "SELECT COUNT(*) AS c FROM upload_tasks WHERE status IN ('pending','test_ready')"
            ).fetchone()["c"]
            now = datetime.now(timezone.utc)
            eta_seconds: dict[str, int | None] = {}
            running_tasks: dict[str, list[dict[str, Any]]] = {}
            for kind in kinds:
                history = connection.execute(
                    """SELECT j.id,
                         (SELECT e.created_at FROM job_events e
                          WHERE e.job_id=j.id AND e.message LIKE 'Bắt đầu %'
                          ORDER BY e.id DESC LIMIT 1) AS started,
                         (SELECT e.created_at FROM job_events e
                          WHERE e.job_id=j.id AND e.message LIKE 'Hoàn tất%'
                          ORDER BY e.id DESC LIMIT 1) AS finished
                       FROM jobs j
                       WHERE j.kind=? AND j.status='completed'
                       ORDER BY j.id DESC LIMIT 10""",
                    (kind,),
                ).fetchall()
                durations = []
                for row in history:
                    started = _parse_utc(row["started"])
                    finished = _parse_utc(row["finished"])
                    if started and finished and finished >= started:
                        durations.append((finished - started).total_seconds())
                estimate = statistics.median(durations) if durations else None
                rows = connection.execute(
                    """SELECT j.id,j.payload,m.title,m.youtube_video_id,
                              m.channel_id,c.title AS channel_title
                       FROM jobs j
                       LEFT JOIN media_items m ON m.id=j.media_item_id
                       LEFT JOIN channels c ON c.channel_id=m.channel_id
                       WHERE j.kind=? AND j.status='leased'
                       ORDER BY j.updated_at ASC, j.id ASC""",
                    (kind,),
                ).fetchall()
                tasks: list[dict[str, Any]] = []
                for row in rows:
                    started = connection.execute(
                        """SELECT created_at FROM job_events
                           WHERE job_id=? AND message LIKE 'Bắt đầu %'
                           ORDER BY id DESC LIMIT 1""",
                        (row["id"],),
                    ).fetchone()
                    started_at = _parse_utc(started["created_at"]) if started else None
                    eta = None
                    if started_at and estimate is not None:
                        elapsed = (now - started_at).total_seconds()
                        eta = max(0, int(estimate - elapsed))
                    title = row["title"] or row["youtube_video_id"] or "Không rõ video"
                    channel = row["channel_title"] or row["channel_id"] or ""
                    if str(row["channel_id"] or "").startswith("test_account_"):
                        channel = "Test video"
                    account = ""
                    if kind == "upload":
                        try:
                            payload = json.loads(row["payload"] or "{}")
                        except Exception:
                            payload = {}
                        task = connection.execute(
                            """SELECT a.name FROM upload_tasks u
                               JOIN tiktok_accounts a ON a.id=u.account_id
                               WHERE u.id=?""",
                            (payload.get("task_id"),),
                        ).fetchone()
                        if task:
                            account = task["name"] or ""
                    tasks.append({
                        "job_id": int(row["id"]),
                        "title": title,
                        "channel": channel,
                        "account": account,
                        "eta_seconds": eta,
                    })
                running_tasks[kind] = tasks
                eta_seconds[kind] = tasks[0]["eta_seconds"] if tasks else None
        counts: dict[str, dict[str, int]] = {kind: {} for kind in kinds}
        for row in count_rows:
            counts[row["kind"]][row["status"]] = int(row["c"])
        latest: dict[str, Any] = {}
        for row in latest_rows:
            latest.setdefault(row["kind"], row)
        stages = []
        for kind in kinds:
            active = counts[kind]
            leased = active.get("leased", 0)
            waiting = active.get("queued", 0) + active.get("retry_wait", 0)
            last = latest.get(kind)
            if leased:
                state, detail = "running", f"{leased} tác vụ đang chạy"
            elif waiting:
                state, detail = "waiting", f"{waiting} tác vụ đang chờ"
            elif last and last["status"] == "failed":
                state, detail = "failed", (last["error_message"] or "Thất bại")[:120]
            elif last and last["status"] == "manual_action":
                state, detail = "warning", (last["error_message"] or "Cần can thiệp")[:120]
            elif last and last["status"] == "completed":
                state, detail = "success", "Hoàn tất"
            else:
                state, detail = "idle", "Chưa có hoạt động"
            stages.append({
                "kind": kind,
                "label": labels[kind],
                "state": state,
                "detail": detail,
                "eta_seconds": eta_seconds.get(kind) if state == "running" else None,
                "tasks": running_tasks.get(kind, []) if state == "running" else [],
            })
        return {
            "stages": stages,
            "events": [dict(row) for row in events],
            "pending_uploads": int(pending_uploads),
        }

    def posting_trend(self, days: int = 7) -> list[dict[str, Any]]:
        start = _iso_day(days - 1)
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT substr(datetime(posted_at,'+7 hours'),1,10) AS day,
                          COUNT(*) AS count
                   FROM upload_tasks
                   WHERE posted_at IS NOT NULL
                     AND substr(datetime(posted_at,'+7 hours'),1,10) >= ?
                   GROUP BY day""",
                (start,),
            ).fetchall()
        counts = {row["day"]: int(row["count"]) for row in rows}
        result = []
        for index in range(days - 1, -1, -1):
            day = _iso_day(index)
            result.append({"day": day, "label": day[5:], "count": counts.get(day, 0)})
        return result

    def retry_job(self, job_id: int) -> None:
        self.database.retry_job(job_id)

    def cancel_job(self, job_id: int) -> None:
        self.database.cancel_job(job_id)

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tiktok_accounts ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def add_account(
        self,
        name: str,
        mode: str = "cdp",
        endpoint: str = "",
        gpm_profile_id: str = "",
        hashtags: str = "",
        daily_limit: int = 3,
        speed: float = 1.0,
        zoom_width_percent: int = 100,
        zoom_height_percent: int = 100,
        caption_mode: str = "fixed",
        caption_text: str = "",
        output_dir: str = "",
        notification_sound: str = "",
        split_enabled: bool | None = None,
        overlay_enabled: bool | None = None,
        background_path: str = "",
        publish_enabled: bool | None = None,
        ai_api_url: str = "",
        ai_api_key: str = "",
        ai_model: str = "",
        ai_system_prompt: str = "",
    ) -> int:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO tiktok_accounts(
                     name,mode,endpoint,gpm_profile_id,hashtags,daily_limit,
                     speed,zoom_width_percent,zoom_height_percent,
                     caption_mode,caption_text,output_dir,notification_sound,
                     split_enabled,overlay_enabled,background_path,publish_enabled,
                     ai_api_url,ai_api_key,ai_model,ai_system_prompt)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (name, mode, endpoint, gpm_profile_id, hashtags, daily_limit,
                 speed, zoom_width_percent, zoom_height_percent,
                 caption_mode, caption_text, output_dir, notification_sound,
                 _optional_flag(split_enabled), _optional_flag(overlay_enabled),
                 background_path, _optional_flag(publish_enabled),
                 ai_api_url, ai_api_key, ai_model, ai_system_prompt),
            )
            return int(cursor.lastrowid)

    def set_account_active(self, account_id: int, active: bool) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE tiktok_accounts SET active=? WHERE id=?",
                (1 if active else 0, account_id),
            )

    def update_account(
        self,
        account_id: int,
        name: str,
        mode: str,
        endpoint: str,
        gpm_profile_id: str,
        hashtags: str,
        daily_limit: int,
        speed: float = 1.0,
        zoom_width_percent: int = 100,
        zoom_height_percent: int = 100,
        caption_mode: str = "fixed",
        caption_text: str = "",
        output_dir: str = "",
        notification_sound: str = "",
        split_enabled: bool | None = None,
        overlay_enabled: bool | None = None,
        background_path: str = "",
        publish_enabled: bool | None = None,
        ai_api_url: str = "",
        ai_api_key: str = "",
        ai_model: str = "",
        ai_system_prompt: str = "",
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE tiktok_accounts
                   SET name=?,mode=?,endpoint=?,gpm_profile_id=?,hashtags=?,daily_limit=?,
                       speed=?,zoom_width_percent=?,zoom_height_percent=?,
                       caption_mode=?,caption_text=?,output_dir=?,notification_sound=?,
                       split_enabled=?,overlay_enabled=?,background_path=?,publish_enabled=?,
                       ai_api_url=?,ai_api_key=?,ai_model=?,ai_system_prompt=?
                   WHERE id=?""",
                (name, mode, endpoint, gpm_profile_id, hashtags, daily_limit,
                 speed, zoom_width_percent, zoom_height_percent,
                 caption_mode, caption_text, output_dir, notification_sound,
                 _optional_flag(split_enabled), _optional_flag(overlay_enabled),
                 background_path, _optional_flag(publish_enabled),
                 ai_api_url, ai_api_key, ai_model, ai_system_prompt, account_id),
            )

    def any_account_publish_enabled(self) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM tiktok_accounts WHERE active=1 AND publish_enabled=1 LIMIT 1"
            ).fetchone()
        return bool(row)

    def list_channels(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT c.id, c.channel_id, c.title, c.source_url, c.active,
                          c.subscription_status, c.last_checked_at,
                          s.segment_seconds, a.name AS account_name, a.id AS account_id,
                          a.speed AS account_speed, a.zoom_width_percent AS account_zoom_width,
                          a.zoom_height_percent AS account_zoom_height
                   FROM channels c
                   LEFT JOIN channel_route_sources s ON s.channel_id = c.channel_id
                   LEFT JOIN channel_routes r ON r.id = s.route_id
                   LEFT JOIN tiktok_accounts a ON a.id = r.account_id
                   ORDER BY c.id DESC""",
            ).fetchall()
        return [dict(row) for row in rows]

    def save_channel_route(
        self,
        channel_id: str,
        title: str,
        source_url: str,
        account_id: int,
        segment_seconds: int | None = None,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO channels(channel_id,title,source_url,active)
                   VALUES (?,?,?,1)
                   ON CONFLICT(channel_id) DO UPDATE SET
                     title=COALESCE(NULLIF(excluded.title,''),channels.title),
                     source_url=COALESCE(NULLIF(excluded.source_url,''),channels.source_url),
                     active=1""",
                (channel_id, title, source_url),
            )
            route = connection.execute(
                "SELECT id FROM channel_routes WHERE account_id=? AND active=1 ORDER BY id LIMIT 1",
                (account_id,),
            ).fetchone()
            if route:
                route_id = int(route["id"])
            else:
                account_name = connection.execute(
                    "SELECT name FROM tiktok_accounts WHERE id=?", (account_id,)
                ).fetchone()["name"]
                route_id = int(
                    connection.execute(
                        "INSERT INTO channel_routes(name,account_id,active) VALUES (?,?,1)",
                        (account_name, account_id),
                    ).lastrowid
                )
            connection.execute(
                """INSERT INTO channel_route_sources(route_id,channel_id,source_url,segment_seconds,zoom_percent,speed,background_path)
                   VALUES (?,?,?,?,100,1.0,'')
                   ON CONFLICT(channel_id) DO UPDATE SET
                     route_id=excluded.route_id,
                     source_url=excluded.source_url,
                     segment_seconds=COALESCE(excluded.segment_seconds, channel_route_sources.segment_seconds)""",
                (route_id, channel_id, source_url, segment_seconds),
            )

    def update_channel_account(self, channel_id: str, account_id: int) -> None:
        with self.database.connect() as connection:
            account = connection.execute(
                "SELECT name FROM tiktok_accounts WHERE id=?", (account_id,)).fetchone()
            if not account:
                raise ValueError("Tài khoản TikTok không tồn tại")
            channel = connection.execute(
                "SELECT source_url FROM channels WHERE channel_id=?", (channel_id,)).fetchone()
            if not channel:
                raise ValueError("Kênh YouTube không tồn tại")
            route = connection.execute(
                "SELECT id FROM channel_routes WHERE account_id=? AND active=1 ORDER BY id LIMIT 1",
                (account_id,),
            ).fetchone()
            if route:
                route_id = int(route["id"])
            else:
                route_id = int(connection.execute(
                    "INSERT INTO channel_routes(name,account_id,active) VALUES (?,?,1)",
                    (account["name"], account_id),
                ).lastrowid)
            old = connection.execute(
                "SELECT route_id FROM channel_route_sources WHERE channel_id=?", (channel_id,)
            ).fetchone()
            connection.execute(
                """INSERT INTO channel_route_sources(route_id,channel_id,source_url)
                   VALUES (?,?,?)
                   ON CONFLICT(channel_id) DO UPDATE SET route_id=excluded.route_id""",
                (route_id, channel_id, channel["source_url"] or ""),
            )
            if old:
                connection.execute(
                    """DELETE FROM channel_routes WHERE id=? AND NOT EXISTS (
                         SELECT 1 FROM channel_route_sources WHERE route_id=?)""",
                    (int(old["route_id"]), int(old["route_id"])),
                )

    def delete_channel(self, channel_id: str) -> None:
        with self.database.connect() as connection:
            routes = connection.execute(
                "SELECT route_id FROM channel_route_sources WHERE channel_id=?", (channel_id,)
            ).fetchall()
            connection.execute(
                "DELETE FROM channel_route_sources WHERE channel_id=?", (channel_id,))
            connection.execute("DELETE FROM channels WHERE channel_id=?", (channel_id,))
            for route in routes:
                connection.execute(
                    """DELETE FROM channel_routes WHERE id=? AND NOT EXISTS (
                         SELECT 1 FROM channel_route_sources WHERE route_id=?)""",
                    (int(route["route_id"]), int(route["route_id"])),
                )

    def set_channel_active(self, channel_id: str, active: bool) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE channels SET active=? WHERE channel_id=?",
                (1 if active else 0, channel_id),
            )

    def active_channels(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT s.channel_id, s.source_url FROM channel_route_sources s
                   JOIN channels c ON c.channel_id = s.channel_id AND c.active=1"""
            ).fetchall()
        return [dict(row) for row in rows]

    def list_videos(
        self,
        status: str | None = None,
        channel_id: str | None = None,
        account_id: int | None = None,
        search: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("m.status = ?")
            params.append(status)
        if channel_id:
            clauses.append("m.channel_id = ?")
            params.append(channel_id)
        if account_id:
            clauses.append("t.id = ?")
            params.append(account_id)
        if search:
            clauses.append("(m.title LIKE ? OR m.youtube_video_id LIKE ?)")
            term = f"%{search}%"
            params.extend([term, term])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""SELECT m.id, m.title, m.youtube_video_id, m.channel_id, m.status,
                          m.error_message, m.updated_at, c.title AS channel_title,
                          t.name AS account_name, t.id AS account_id
                   FROM media_items m
                   LEFT JOIN channels c ON c.channel_id = m.channel_id
                   LEFT JOIN channel_route_sources s ON s.channel_id = m.channel_id
                   LEFT JOIN channel_routes r ON r.id = s.route_id AND r.active=1
                   LEFT JOIN tiktok_accounts t ON t.id = r.account_id
                   {where}
                   ORDER BY m.id DESC LIMIT ?"""
        params.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_upload_tasks(self, account_id: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
        where = "WHERE u.account_id = ?" if account_id else ""
        params: list[Any] = [account_id] if account_id else []
        query = f"""SELECT u.id, u.status, u.caption, u.scheduled_at, u.posted_at,
                          u.error_message, u.is_test, a.path, a.segment_index,
                          m.title AS video_title, t.name AS account_name
                   FROM upload_tasks u
                   JOIN artifacts a ON a.id = u.artifact_id
                   JOIN media_items m ON m.id = u.media_item_id
                   JOIN tiktok_accounts t ON t.id = u.account_id
                   {where}
                   ORDER BY u.id DESC LIMIT ?"""
        params.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_events(
        self,
        level: str | None = None,
        search: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if level:
            clauses.append("level = ?")
            params.append(level.upper())
        if search:
            clauses.append("message LIKE ?")
            params.append(f"%{search}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""SELECT id, job_id, level, message, created_at
                   FROM job_events {where}
                   ORDER BY id DESC LIMIT ?"""
        params.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]
