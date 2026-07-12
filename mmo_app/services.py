from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
import textwrap
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

from .database import Database, utc_now
from .settings import Settings

LOGGER = logging.getLogger(__name__)
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{20,30}$")


def _hidden_subprocess_kwargs() -> dict:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


@contextmanager
def _hide_child_console_windows():
    """Prevent short-lived console windows from ffmpeg/ffprobe/yt-dlp children on Windows."""
    if os.name != "nt":
        yield
        return
    original_popen = subprocess.Popen

    def hidden_popen(*args, **kwargs):
        hidden = _hidden_subprocess_kwargs()
        if "startupinfo" not in kwargs:
            kwargs["startupinfo"] = hidden["startupinfo"]
        kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | int(hidden["creationflags"] or 0)
        return original_popen(*args, **kwargs)

    subprocess.Popen = hidden_popen
    try:
        yield
    finally:
        subprocess.Popen = original_popen


class YouTubeService:
    def __init__(self, database: Database, settings: Settings):
        self.db, self.settings = database, settings

    @staticmethod
    def normalize_channel_url(value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("URL kênh YouTube đang trống")
        if "://" not in value:
            value = "https://" + value
        parsed = urllib.parse.urlparse(value)
        host = (parsed.hostname or "").lower()
        if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            raise ValueError(f"Không phải URL kênh YouTube: {value}")
        if not parsed.path or parsed.path == "/":
            raise ValueError(f"URL chưa trỏ tới một kênh YouTube: {value}")
        return urllib.parse.urlunparse(("https", "www.youtube.com", parsed.path.rstrip("/"), "", "", ""))

    def resolve_channel_url(self, value: str) -> tuple[str, str, str]:
        url = self.normalize_channel_url(value)
        direct = re.search(r"/channel/(UC[A-Za-z0-9_-]{20,30})(?:/|$)", urllib.parse.urlparse(url).path)
        if direct:
            return direct.group(1), "", url
        try:
            import yt_dlp
            options = {"quiet": True, "no_warnings": True, "extract_flat": True, "playlistend": 1, "skip_download": True}
            with _hide_child_console_windows():
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(url, download=False)
            channel_id = (info or {}).get("channel_id") or (info or {}).get("id")
            title = (info or {}).get("channel") or (info or {}).get("uploader") or (info or {}).get("title") or ""
            if channel_id and YOUTUBE_CHANNEL_ID.fullmatch(str(channel_id)):
                return str(channel_id), str(title), url
        except Exception as exc:
            raise ValueError(f"Không lấy được Channel ID từ {url}: {exc}") from exc
        raise ValueError(f"Không tìm thấy Channel ID trong URL: {url}")

    def resolve_channel_urls(self, values: list[str]) -> list[tuple[str, str, str]]:
        resolved, seen = [], set()
        for value in values:
            channel_id, title, url = self.resolve_channel_url(value)
            if channel_id not in seen:
                resolved.append((channel_id, title, url)); seen.add(channel_id)
        return resolved

    def sync_channels(self, resolved: list[tuple[str, str, str]] | None = None) -> None:
        values = [value.strip() for value in self.settings.channel_ids if value.strip()]
        if resolved is not None:
            values = [item[0] for item in resolved]
            self.settings.channel_ids = values
        with self.db.connect() as connection:
            for channel_id, title, source_url in (resolved or [(item, "", "") for item in values]):
                connection.execute(
                    """INSERT INTO channels(channel_id,title,source_url) VALUES (?,?,?)
                    ON CONFLICT(channel_id) DO UPDATE SET title=COALESCE(NULLIF(excluded.title,''),channels.title),
                    source_url=COALESCE(NULLIF(excluded.source_url,''),channels.source_url)""",
                    (channel_id, title, source_url),
                )
            if values:
                marks = ",".join("?" for _ in values)
                connection.execute(f"UPDATE channels SET active=(channel_id IN ({marks}))", values)

    def poll_rss(self) -> int:
        self.sync_channels()
        with self.db.connect() as connection:
            channels = connection.execute("SELECT * FROM channels WHERE active=1").fetchall()
        found = 0
        namespaces = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
        for channel in channels:
            url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['channel_id']}"
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    root = ET.fromstring(response.read())
                for entry in root.findall("atom:entry", namespaces):
                    video_id = entry.findtext("yt:videoId", namespaces=namespaces)
                    if not video_id or not YOUTUBE_ID.match(video_id):
                        continue
                    title = entry.findtext("atom:title", default="", namespaces=namespaces)
                    published = entry.findtext("atom:published", default="", namespaces=namespaces)
                    if self.discover(video_id, channel["channel_id"], title, published):
                        found += 1
                with self.db.connect() as connection:
                    connection.execute("UPDATE channels SET last_checked_at=? WHERE id=?", (utc_now(), channel["id"]))
            except Exception as exc:
                LOGGER.exception("RSS channel %s failed: %s", channel["channel_id"], exc)
        return found

    def discover(self, video_id: str, channel_id: str, title: str, published_at: str = "") -> bool:
        with self.db.connect() as connection:
            existing = connection.execute("SELECT id FROM media_items WHERE youtube_video_id=?", (video_id,)).fetchone()
            if existing:
                return False
            cursor = connection.execute(
                """INSERT INTO media_items(youtube_video_id,channel_id,title,source_url,published_at)
                   VALUES (?,?,?,?,?)""",
                (video_id, channel_id, title, f"https://www.youtube.com/watch?v={video_id}", published_at),
            )
            media_id = int(cursor.lastrowid)
        self.db.enqueue("download", media_id, f"download:{video_id}")
        return True


class DownloadHandler:
    def __init__(self, database: Database, settings: Settings):
        self.db, self.settings = database, settings

    def run(self, job) -> None:
        with self.db.connect() as connection:
            media = connection.execute("SELECT * FROM media_items WHERE id=?", (job["media_item_id"],)).fetchone()
        if not media:
            raise RuntimeError("Media item no longer exists")
        target = Path(self.settings.download_dir) / (media["channel_id"] or "unknown")
        target.mkdir(parents=True, exist_ok=True)
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("Thiếu dependency yt-dlp") from exc
        options = {
            "outtmpl": str(target / "%(title).180B [%(id)s].%(ext)s"),
            "format": "bv*+ba/b[ext=mp4]/b",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
        }
        with _hide_child_console_windows():
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(media["source_url"], download=True)
                path = Path(ydl.prepare_filename(info))
        if not path.exists() and path.suffix.lower() != ".mp4":
            path = path.with_suffix(".mp4")
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError("yt-dlp kết thúc nhưng không tìm thấy output hợp lệ")
        with self.db.connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO artifacts(media_item_id,kind,path,size_bytes) VALUES (?,?,?,?)",
                (media["id"], "download", str(path), path.stat().st_size),
            )
            artifact = connection.execute(
                "SELECT id FROM artifacts WHERE media_item_id=? AND kind='download' AND path=?",
                (media["id"], str(path)),
            ).fetchone()
            connection.execute("UPDATE media_items SET status='downloaded',updated_at=? WHERE id=?", (utc_now(), media["id"]))
        self.db.enqueue("process", media["id"], f"process:{media['id']}:{artifact['id']}", json.dumps({"artifact_id": artifact["id"]}))


class ProcessHandler:
    ASPECT_SIZES = {
        "9:16": (1080, 1920),
        "4:5": (1080, 1350),
        "1:1": (1080, 1080),
        "16:9": (1920, 1080),
    }

    def __init__(self, database: Database, settings: Settings):
        self.db, self.settings = database, settings

    def run(self, job) -> None:
        payload = json.loads(job["payload"])
        with self.db.connect() as connection:
            source_row = connection.execute("SELECT * FROM artifacts WHERE id=?", (payload["artifact_id"],)).fetchone()
            media = connection.execute("SELECT * FROM media_items WHERE id=?", (job["media_item_id"],)).fetchone()
        if not source_row or not Path(source_row["path"]).is_file():
            raise RuntimeError("Download artifact bị thiếu")
        source = Path(source_row["path"])
        output_group = self._output_group(media["channel_id"])
        output = Path(self.settings.output_dir) / output_group / (media["youtube_video_id"] or str(media["id"]))
        output.mkdir(parents=True, exist_ok=True)
        duration = self._duration(source)
        segments = []
        if self.settings.split_enabled and duration > self.settings.segment_seconds:
            segment_ranges = self.segment_ranges(duration, self.settings.segment_seconds)
            total_parts = len(segment_ranges)
            for index, (start, length) in enumerate(segment_ranges, start=1):
                destination = output / f"{index:03d}.mp4"
                self._render(source, destination, start, length, media["title"] or "", index, total_parts)
                segments.append((index, destination))
        else:
            destination = output / "001.mp4"
            self._render(source, destination, 0, None, media["title"] or "", 1, 1)
            segments.append((1, destination))
        with self.db.connect() as connection:
            for index, path in segments:
                connection.execute(
                    """INSERT OR IGNORE INTO artifacts(media_item_id,kind,path,segment_index,size_bytes)
                    VALUES (?,?,?,?,?)""", (media["id"], "rendered", str(path), index, path.stat().st_size)
                )
            connection.execute("UPDATE media_items SET status='processed',updated_at=? WHERE id=?", (utc_now(), media["id"]))
        self._route_uploads(media["id"], media["title"] or "")

    def _output_group(self, channel_id: str | None) -> str:
        with self.db.connect() as connection:
            row = connection.execute(
                """SELECT a.name FROM channel_route_sources s
                JOIN channel_routes r ON r.id=s.route_id AND r.active=1
                JOIN tiktok_accounts a ON a.id=r.account_id AND a.active=1
                WHERE s.channel_id=? ORDER BY r.id LIMIT 1""",
                (channel_id,),
            ).fetchone()
        return self.safe_folder_name(row["name"] if row else (channel_id or "unknown"))

    @staticmethod
    def safe_folder_name(value: str) -> str:
        value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip().rstrip(".")
        return value or "unknown"

    def _duration(self, source: Path) -> float:
        result = subprocess.run(
            [self.settings.media_binary("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(source)],
            capture_output=True, text=True, check=True, timeout=60, **_hidden_subprocess_kwargs(),
        )
        return float(result.stdout.strip())

    @staticmethod
    def segment_ranges(duration: float, target_seconds: int | float) -> list[tuple[float, float]]:
        target = float(target_seconds)
        if duration <= 0:
            return []
        if target <= 0 or duration <= target:
            return [(0.0, duration)]
        ranges: list[tuple[float, float]] = []
        start = 0.0
        while start < duration:
            length = min(target, duration - start)
            ranges.append((start, length))
            start += length
        if len(ranges) > 1 and ranges[-1][1] < target / 2:
            previous_start, previous_length = ranges[-2]
            _, last_length = ranges[-1]
            ranges[-2] = (previous_start, previous_length + last_length)
            ranges.pop()
        return ranges

    def _render(self, source: Path, destination: Path, start: float, length: float | None,
                title: str = "", part: int = 1, total_parts: int = 1) -> None:
        filters = [item for item in [self.video_filter(self.settings.output_aspect_ratio)] if item]
        temporary_text = []
        if self.settings.overlay_text_enabled:
            overlay_filters, temporary_text = self.overlay_filters(destination, title, part, total_parts)
            filters.extend(overlay_filters)
        if destination.is_file() and destination.stat().st_size > 0 and not filters:
            return
        command = [self.settings.media_binary("ffmpeg"), "-y", "-ss", str(start), "-i", str(source)]
        if length is not None:
            command += ["-t", str(length)]
        if filters:
            command += ["-vf", ",".join(filters)]
        command += ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", str(destination)]
        try:
            subprocess.run(command, capture_output=True, check=True, timeout=7200, **_hidden_subprocess_kwargs())
        finally:
            for path in temporary_text:
                path.unlink(missing_ok=True)

    def overlay_filters(self, destination: Path, title: str, part: int, total_parts: int) -> tuple[list[str], list[Path]]:
        font = Path(self.settings.overlay_font_path) if self.settings.overlay_font_path else Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
        common = [f"fontcolor={self.settings.overlay_font_color}", f"fontsize={self.settings.overlay_font_size}", "borderw=2", "bordercolor=black"]
        if font.is_file(): common.insert(0, f"fontfile='{self.escape_filter_path(font)}'")
        paths, filters = [], []
        title_file = destination.with_name(destination.stem + ".title.txt")
        wrapped = "\n".join(textwrap.wrap(title.strip(), width=36, break_long_words=False, break_on_hyphens=False)[:3]) or " "
        title_file.write_text(wrapped, encoding="utf-8")
        paths.append(title_file)
        filters.append("drawtext=" + ":".join([f"textfile='{self.escape_filter_path(title_file)}'", *common, "x=(w-text_w)/2", "y=40"]))
        if total_parts > 1:
            part_file = destination.with_name(destination.stem + ".part.txt")
            part_file.write_text(f"Part {part}", encoding="utf-8")
            paths.append(part_file)
            filters.append("drawtext=" + ":".join([f"textfile='{self.escape_filter_path(part_file)}'", *common, "x=(w-text_w)/2", "y=h-text_h-50"]))
        return filters, paths

    @staticmethod
    def escape_filter_path(path: Path) -> str:
        return path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")

    @classmethod
    def video_filter(cls, aspect_ratio: str) -> str:
        size = cls.ASPECT_SIZES.get(aspect_ratio)
        if not size:
            return ""
        width, height = size
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )

    def _route_uploads(self, media_id: int, caption: str) -> None:
        queued: list[tuple[int, int]] = []
        with self.db.connect() as connection:
            media = connection.execute("SELECT channel_id FROM media_items WHERE id=?", (media_id,)).fetchone()
            account = connection.execute(
                """SELECT a.* FROM channel_route_sources s
                JOIN channel_routes r ON r.id=s.route_id AND r.active=1
                JOIN tiktok_accounts a ON a.id=r.account_id AND a.active=1
                WHERE s.channel_id=? ORDER BY r.id LIMIT 1""",
                (media["channel_id"],),
            ).fetchone() if media else None
            if not account:
                account = connection.execute("SELECT * FROM tiktok_accounts WHERE active=1 ORDER BY id LIMIT 1").fetchone()
            artifacts = connection.execute(
                """SELECT * FROM artifacts WHERE media_item_id=? AND kind='rendered'
                ORDER BY COALESCE(segment_index,1), id""",
                (media_id,),
            ).fetchall()
            if not account:
                return
            multiple_parts = len(artifacts) > 1
            for artifact in artifacts:
                upload_caption = caption
                if multiple_parts:
                    upload_caption = f"{caption} - Part {artifact['segment_index'] or 1}"
                connection.execute(
                    """INSERT INTO upload_tasks(media_item_id,artifact_id,account_id,caption) VALUES (?,?,?,?)
                    ON CONFLICT(artifact_id,account_id) DO UPDATE SET caption=excluded.caption""",
                    (media_id, artifact["id"], account["id"], upload_caption),
                )
                task = connection.execute("SELECT id FROM upload_tasks WHERE artifact_id=? AND account_id=?", (artifact["id"], account["id"])).fetchone()
                queued.append((int(task["id"]), media_id))
        for task_id, item_id in queued:
            self.db.enqueue("upload", item_id, f"upload:{task_id}", json.dumps({"task_id": task_id}))


class UploadHandler:
    def __init__(self, database: Database, settings: Settings):
        self.db, self.settings = database, settings

    def run(self, job) -> None:
        payload = json.loads(job["payload"])
        with self.db.connect() as connection:
            task = connection.execute(
                """SELECT u.*,a.path,t.endpoint,t.mode,t.gpm_profile_id,t.daily_limit,t.posts_today,t.counter_date
                FROM upload_tasks u JOIN artifacts a ON a.id=u.artifact_id
                JOIN tiktok_accounts t ON t.id=u.account_id WHERE u.id=?""", (payload["task_id"],)
            ).fetchone()
        if not task:
            raise RuntimeError("Upload task không tồn tại")
        today = utc_now()[:10]
        posts_today = int(task["posts_today"] or 0)
        daily_limit = int(task["daily_limit"] or 0)
        if task["counter_date"] != today:
            posts_today = 0
            with self.db.connect() as connection:
                connection.execute("UPDATE tiktok_accounts SET posts_today=0,counter_date=? WHERE id=?", (today, task["account_id"]))
        if daily_limit > 0 and posts_today >= daily_limit:
            raise ManualAction("Tài khoản đã đạt giới hạn đăng trong ngày")
        if not self.settings.tiktok_publish_enabled:
            raise ManualAction("Đã xử lý xong; bật 'Cho phép đăng TikTok' để publish")
        mode = task["mode"] or "cdp"
        if mode == "gpm":
            raise ManualAction("Tài khoản đang chọn chế độ GPM Automation. Cấu hình đã được lưu, nhưng adapter upload GPM chưa được triển khai production.")
        self._playwright_upload(Path(task["path"]), task["caption"], task["endpoint"] or self.settings.tiktok_cdp_url)
        with self.db.connect() as connection:
            connection.execute("UPDATE upload_tasks SET status='completed',posted_at=? WHERE id=?", (utc_now(), task["id"]))
            connection.execute("UPDATE tiktok_accounts SET posts_today=posts_today+1,counter_date=?,status='idle' WHERE id=?", (today, task["account_id"]))
            connection.execute("UPDATE media_items SET status='uploaded',updated_at=? WHERE id=?", (utc_now(), task["media_item_id"]))

    @staticmethod
    def _playwright_upload(path: Path, caption: str, cdp_url: str) -> None:
        if not cdp_url:
            raise ManualAction("Chưa cấu hình CDP URL hoặc GPM profile cho TikTok")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Thiếu dependency Playwright") from exc
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            if not browser.contexts:
                raise ManualAction("Browser CDP không có context")
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://www.tiktok.com/upload?lang=en", wait_until="domcontentloaded")
            try:
                UploadHandler._select_tiktok_upload_file(page, path, timeout=45_000)
            except Exception as exc:
                raise ManualAction(UploadHandler._diagnose_tiktok_upload_page(page)) from exc
            editor = page.locator('[contenteditable="true"][role="textbox"], [contenteditable="true"]').first
            editor.wait_for(state="visible", timeout=20 * 60_000)
            editor.click(); editor.press("Control+A"); editor.type(caption, delay=20)
            post = page.get_by_role("button", name=re.compile(r"^(Post|Đăng)$")).first
            post.wait_for(state="visible", timeout=20 * 60_000)
            post.click()
            UploadHandler._confirm_tiktok_post_if_needed(page)
            page.wait_for_url(re.compile(r"^(?!.*\/upload).*$"), timeout=20 * 60_000)

    @staticmethod
    def _select_tiktok_upload_file(page, path: Path, timeout: int = 45_000) -> None:
        """Select a video file using a direct file input or TikTok's select-video button."""
        try:
            file_input = UploadHandler._find_tiktok_file_input(page, timeout=timeout)
            file_input.set_input_files(str(path))
            return
        except Exception as first_error:
            last_error = first_error
        button_selectors = (
            'button[aria-label="Select video"]',
            'button:has-text("Select video")',
            'button:has-text("Chọn video")',
            'button:has-text("Tải video lên")',
            '[data-e2e="select_video_button"]',
            '[data-e2e="select_video_container"] button',
        )
        deadline = time.time() + (timeout / 1000)
        for selector in button_selectors:
            try:
                remaining = int(max(1_000, (deadline - time.time()) * 1000))
                button = page.locator(selector).first
                button.wait_for(state="visible", timeout=min(10_000, remaining))
                with page.expect_file_chooser(timeout=min(10_000, remaining)) as chooser:
                    button.click()
                chooser.value.set_files(str(path))
                return
            except Exception as exc:
                last_error = exc
        raise last_error

    @staticmethod
    def _find_tiktok_file_input(page, timeout: int = 45_000):
        """Return the upload file input from TikTok page or embedded Studio iframe."""
        candidates = []
        for selector in (
            'input[type="file"][accept*="video"]',
            'input[accept*="video"]',
            'input[type="file"]',
        ):
            try:
                candidates.append(page.locator(selector).first)
            except Exception:
                pass
        for selector in ("iframe", 'iframe[src*="creator"]', 'iframe[src*="upload"]', 'iframe[src*="studio"]'):
            try:
                frame = page.frame_locator(selector).first
                candidates.append(frame.locator('input[type="file"]').first)
                candidates.append(frame.locator('input[accept*="video"]').first)
            except Exception:
                pass
        last_error = None
        per_candidate_timeout = max(3_000, min(timeout, 10_000))
        deadline = time.time() + (timeout / 1000)
        for candidate in candidates:
            try:
                remaining = int(max(1_000, (deadline - time.time()) * 1000))
                candidate.wait_for(state="attached", timeout=min(per_candidate_timeout, remaining))
                return candidate
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise RuntimeError("Không tìm thấy ô chọn file upload TikTok")

    @staticmethod
    def _diagnose_tiktok_upload_page(page) -> str:
        """Build a clearer manual-action message when TikTok upload input is not available."""
        url = ""
        try:
            url = page.url or ""
        except Exception:
            pass
        checks = (
            (r"(Log in|Login|Sign up|Đăng nhập|Đăng ký)", "TikTok đang mở màn hình đăng nhập. Hãy đăng nhập đúng Chrome profile/CDP port rồi chạy lại job."),
            (r"(captcha|verification|verify|xác minh|checkpoint|security check)", "TikTok đang yêu cầu xác minh/captcha trên Chrome profile này. Hãy xử lý thủ công trong Chrome rồi chạy lại job."),
            (r"(Something went wrong|Try again|Không thể|Lỗi)", "Trang upload TikTok đang báo lỗi. Hãy tải lại trang trong Chrome CDP rồi chạy lại job."),
        )
        for pattern, message in checks:
            try:
                if page.locator(f"body:has-text('{pattern}')").first.is_visible(timeout=1_000):
                    return message
            except Exception:
                pass
            try:
                body_text = page.locator("body").inner_text(timeout=1_000)
                if re.search(pattern, body_text, re.IGNORECASE):
                    return message
            except Exception:
                pass
        if "/login" in url or "login" in url:
            return "TikTok đang redirect sang trang đăng nhập. Có thể app đang kết nối nhầm CDP port hoặc nhầm Chrome profile chưa đăng nhập."
        if "/upload" in url:
            return "TikTok đã mở trang upload nhưng app không tìm thấy ô chọn file. Có thể TikTok đổi giao diện hoặc ô upload nằm trong iframe chưa tải xong; hãy thử reload Chrome/CDP rồi chạy lại."
        return f"TikTok chưa sẵn sàng upload tại URL hiện tại: {url or 'không đọc được URL'}. Kiểm tra đúng CDP URL/profile và trạng thái đăng nhập."

    @staticmethod
    def _confirm_tiktok_post_if_needed(page) -> bool:
        """Click TikTok's secondary safety confirmation if it appears after Post."""
        candidates = []
        try:
            candidates.append(
                page.get_by_role(
                    "button",
                    name=re.compile(r"^(Post now|Đăng ngay|Tiếp tục đăng)$", re.IGNORECASE),
                ).first
            )
        except Exception:
            pass
        for selector in (
            'xpath=//div[contains(@class,"TUXModal") and @role="dialog" and @title="Continue to post?"]//button[.//*[normalize-space()="Post now"]]',
            'xpath=//div[contains(@class,"common-modal-confirm-modal") and .//*[normalize-space()="Continue to post?"]]//button[.//*[normalize-space()="Post now"]]',
            'div.TUXModal[role="dialog"]:has-text("Continue to post?") button.TUXButton--primary:has-text("Post now")',
            'button:has-text("Post now")',
        ):
            try:
                candidates.append(page.locator(selector).first)
            except Exception:
                pass
        for candidate in candidates:
            try:
                candidate.wait_for(state="visible", timeout=3_000)
                candidate.click()
                return True
            except Exception:
                continue
        return False


class ManualAction(RuntimeError):
    pass


class PipelineOrchestrator:
    def __init__(self, database: Database, settings: Settings):
        self.db, self.settings = database, settings
        self.handlers = {
            "download": DownloadHandler(database, settings),
            "process": ProcessHandler(database, settings),
            "upload": UploadHandler(database, settings),
        }
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.pause_event.clear()
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.db.recover_expired_leases()
        self.thread = threading.Thread(target=self._loop, name="pipeline-worker", daemon=True)
        self.thread.start()

    def pause(self) -> None:
        self.pause_event.set()

    def stop(self, timeout: float = 10) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout)

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            if self.pause_event.is_set():
                self.stop_event.wait(0.5); continue
            job = self.db.claim("desktop", tuple(self.handlers))
            if not job:
                self.stop_event.wait(self.settings.worker_poll_seconds); continue
            try:
                self.db.event(job["id"], "INFO", f"Bắt đầu {job['kind']}")
                self.handlers[job["kind"]].run(job)
                self.db.complete(job["id"])
                self.db.event(job["id"], "INFO", "Hoàn tất")
            except ManualAction as exc:
                self.db.manual_action(job["id"], str(exc)); self.db.event(job["id"], "WARNING", str(exc))
            except Exception as exc:
                LOGGER.exception("Job %s failed", job["id"])
                self.db.fail(job["id"], str(exc)); self.db.event(job["id"], "ERROR", str(exc))
