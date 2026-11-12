from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from .database import Database, utc_now
from .settings import DEFAULT_GPM_API_URL, Settings

LOGGER = logging.getLogger(__name__)
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{20,30}$")

# Split-length thresholds applied to the already speed-adjusted duration.
SPLIT_THRESHOLDS_SECONDS = (300.0, 480.0, 900.0, 1080.0)
MAX_VIDEO_PARTS = 5

ROUTE_ACCOUNT_SQL = """
SELECT a.*, s.background_path AS route_background_path
FROM channel_route_sources s
JOIN channel_routes r ON r.id=s.route_id AND r.active=1
JOIN tiktok_accounts a ON a.id=r.account_id
WHERE s.channel_id=? ORDER BY r.id LIMIT 1
"""


def channel_account(connection, channel_id: str | None):
    """Return the active TikTok account routed to a YouTube channel, if any."""
    if not channel_id:
        return None
    return connection.execute(ROUTE_ACCOUNT_SQL, (channel_id,)).fetchone()


class NotificationBell:
    """Loop a notification sound for a fixed duration with a manual stop."""

    def __init__(self, duration_seconds: int = 30):
        self.duration_seconds = max(1, int(duration_seconds))
        self._lock = threading.Lock()
        self._generation = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._playing = False

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    def play(self, sound_path: str = "", ffmpeg_binary: str = "ffmpeg") -> bool:
        """Start the bell loop. Returns False on non-Windows systems."""
        if os.name != "nt":
            return False
        with self._lock:
            self._generation += 1
            generation = self._generation
            previous_event = self._stop_event
            self._stop_event = threading.Event()
            event = self._stop_event
            self._playing = True
        previous_event.set()
        thread = threading.Thread(
            target=self._run,
            args=(generation, event, str(sound_path or ""), str(ffmpeg_binary or "ffmpeg")),
            name="notification-bell",
            daemon=True,
        )
        with self._lock:
            self._thread = thread
        thread.start()
        return True

    def stop(self) -> None:
        """Stop the bell immediately, if it is ringing."""
        with self._lock:
            self._stop_event.set()
            self._playing = False
        if os.name == "nt":
            try:
                import winsound
                winsound.PlaySound(None, 0)
            except Exception:
                pass

    def _run(self, generation: int, event: threading.Event,
             sound_path: str, ffmpeg_binary: str) -> None:
        import winsound
        temporary: Path | None = None
        try:
            sound = Path(sound_path) if sound_path else None
            playable: Path | None = None
            if sound and sound.is_file():
                if sound.suffix.lower() == ".wav":
                    playable = sound
                else:
                    try:
                        temporary = self._convert_to_wav(sound, ffmpeg_binary)
                        playable = temporary
                    except Exception:
                        LOGGER.exception("Cannot convert notification sound to WAV: %s", sound)
            if event.is_set():
                return
            if playable:
                winsound.PlaySound(
                    str(playable),
                    winsound.SND_FILENAME | winsound.SND_ASYNC
                    | winsound.SND_LOOP | winsound.SND_NODEFAULT,
                )
                event.wait(self.duration_seconds)
            else:
                deadline = time.monotonic() + self.duration_seconds
                while not event.is_set() and time.monotonic() < deadline:
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
                    event.wait(1.0)
        except Exception:
            LOGGER.exception("Cannot play notification bell")
        finally:
            with self._lock:
                is_current = generation == self._generation
                if is_current:
                    self._playing = False
            if is_current:
                try:
                    winsound.PlaySound(None, 0)
                except Exception:
                    pass
            if temporary:
                temporary.unlink(missing_ok=True)

    def _convert_to_wav(self, source: Path, ffmpeg_binary: str) -> Path:
        import uuid
        temporary = Path(tempfile.gettempdir()) / f"mmo_bell_{uuid.uuid4().hex}.wav"
        command = [
            ffmpeg_binary or "ffmpeg", "-y", "-i", str(source),
            "-t", str(self.duration_seconds), "-vn", "-ac", "1", "-ar", "44100",
            str(temporary),
        ]
        try:
            subprocess.run(
                command, capture_output=True, check=True, timeout=120,
                **_hidden_subprocess_kwargs(),
            )
            if not temporary.is_file() or temporary.stat().st_size <= 0:
                raise RuntimeError(f"Không chuyển được âm thanh sang WAV: {source}")
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return temporary


NOTIFICATION_BELL = NotificationBell()


def play_notification_bell(sound_path: str = "", ffmpeg_binary: str = "ffmpeg") -> bool:
    """Play the per-account notification sound, looping for up to 30 seconds."""
    return NOTIFICATION_BELL.play(sound_path, ffmpeg_binary)


def local_today() -> str:
    """Current date in Vietnam time (UTC+7), used for daily counters."""
    return datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d")


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
        kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | int(
            hidden["creationflags"] or 0)
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
        direct = re.search(
            r"/channel/(UC[A-Za-z0-9_-]{20,30})(?:/|$)", urllib.parse.urlparse(url).path)
        if direct:
            return direct.group(1), "", url
        try:
            import yt_dlp
            options = {"quiet": True, "no_warnings": True,
                       "extract_flat": True, "playlistend": 1, "skip_download": True}
            with _hide_child_console_windows():
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(url, download=False)
            channel_id = (info or {}).get(
                "channel_id") or (info or {}).get("id")
            title = (info or {}).get("channel") or (info or {}).get(
                "uploader") or (info or {}).get("title") or ""
            if channel_id and YOUTUBE_CHANNEL_ID.fullmatch(str(channel_id)):
                return str(channel_id), str(title), url
        except Exception as exc:
            raise ValueError(
                f"Không lấy được Channel ID từ {url}: {exc}") from exc
        raise ValueError(f"Không tìm thấy Channel ID trong URL: {url}")

    def resolve_channel_urls(self, values: list[str]) -> list[tuple[str, str, str]]:
        resolved, seen = [], set()
        for value in values:
            channel_id, title, url = self.resolve_channel_url(value)
            if channel_id not in seen:
                resolved.append((channel_id, title, url))
                seen.add(channel_id)
        return resolved

    @staticmethod
    def video_id_from_url(value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("URL video YouTube đang trống")
        if "://" not in value:
            value = "https://" + value
        parsed = urllib.parse.urlparse(value)
        host = (parsed.hostname or "").lower()
        parts = [part for part in parsed.path.split("/") if part]
        if host in {"youtu.be", "www.youtu.be"}:
            candidate = parts[0] if parts else ""
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            query = urllib.parse.parse_qs(parsed.query)
            if "v" in query:
                candidate = (query.get("v") or [""])[0]
            elif len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"}:
                candidate = parts[1]
            else:
                candidate = ""
        else:
            raise ValueError(f"Không phải URL video YouTube: {value}")
        if not candidate or not YOUTUBE_ID.fullmatch(candidate):
            raise ValueError(f"Không tìm thấy ID video YouTube trong URL: {value}")
        return candidate

    def resolve_video_url(self, value: str) -> tuple[str, str, str]:
        video_id = self.video_id_from_url(value)
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            import yt_dlp
            options = {"quiet": True, "no_warnings": True, "skip_download": True}
            with _hide_child_console_windows():
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(url, download=False)
        except Exception as exc:
            raise ValueError(f"Không lấy được thông tin video {url}: {exc}") from exc
        title = str((info or {}).get("title") or video_id)
        channel_id = str((info or {}).get("channel_id") or "")
        return video_id, title, channel_id

    def queue_test_video(self, value: str, account_id: int) -> int:
        """Queue a one-off video for one TikTok account without channel routing."""
        video_id, title, _channel_id = self.resolve_video_url(value)
        with self.db.connect() as connection:
            account = connection.execute(
                "SELECT id,name FROM tiktok_accounts WHERE id=?", (account_id,)).fetchone()
            if not account:
                raise ValueError("Tài khoản TikTok không tồn tại")
            existing = connection.execute(
                "SELECT id FROM media_items WHERE youtube_video_id=?", (video_id,)).fetchone()
            if existing:
                media_id = int(existing["id"])
            else:
                media_id = int(connection.execute(
                    """INSERT INTO media_items(youtube_video_id,channel_id,title,source_url)
                       VALUES (?,?,?,?)""",
                    (video_id, f"test_account_{int(account_id)}", title,
                     f"https://www.youtube.com/watch?v={video_id}"),
                ).lastrowid)
        self.db.enqueue(
            "download", media_id,
            f"test:{int(account_id)}:{video_id}:{time.time_ns()}",
            json.dumps({"test_account_id": int(account_id), "is_test": 1}),
        )
        self.db.event(
            None, "INFO",
            f"Test video: đã xếp hàng {video_id} cho tài khoản {account['name']}")
        return media_id

    def sync_channels(self, resolved: list[tuple[str, str, str]] | None = None) -> None:
        values = [value.strip()
                  for value in self.settings.channel_ids if value.strip()]
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
                connection.execute(
                    f"UPDATE channels SET active=(channel_id IN ({marks}))", values)

    def poll_rss(self) -> int:
        self.sync_channels()
        with self.db.connect() as connection:
            channels = connection.execute(
                "SELECT * FROM channels WHERE active=1").fetchall()
        found = 0
        namespaces = {"atom": "http://www.w3.org/2005/Atom",
                      "yt": "http://www.youtube.com/xml/schemas/2015"}
        for channel in channels:
            url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['channel_id']}"
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    root = ET.fromstring(response.read())
                for entry in root.findall("atom:entry", namespaces):
                    video_id = entry.findtext(
                        "yt:videoId", namespaces=namespaces)
                    if not video_id or not YOUTUBE_ID.match(video_id):
                        continue
                    title = entry.findtext(
                        "atom:title", default="", namespaces=namespaces)
                    published = entry.findtext(
                        "atom:published", default="", namespaces=namespaces)
                    if self.discover(video_id, channel["channel_id"], title, published):
                        found += 1
                with self.db.connect() as connection:
                    connection.execute(
                        "UPDATE channels SET last_checked_at=? WHERE id=?", (utc_now(), channel["id"]))
            except Exception as exc:
                LOGGER.exception("RSS channel %s failed: %s",
                                 channel["channel_id"], exc)
        return found

    def discover(self, video_id: str, channel_id: str, title: str, published_at: str = "") -> bool:
        with self.db.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM media_items WHERE youtube_video_id=?", (video_id,)).fetchone()
            if existing:
                return False
            cursor = connection.execute(
                """INSERT INTO media_items(youtube_video_id,channel_id,title,source_url,published_at)
                   VALUES (?,?,?,?,?)""",
                (video_id, channel_id, title,
                 f"https://www.youtube.com/watch?v={video_id}", published_at),
            )
            media_id = int(cursor.lastrowid)
        self.db.enqueue("download", media_id, f"download:{video_id}")
        return True


class DownloadHandler:
    def __init__(self, database: Database, settings: Settings):
        self.db, self.settings = database, settings

    def run(self, job) -> None:
        with self.db.connect() as connection:
            media = connection.execute(
                "SELECT * FROM media_items WHERE id=?", (job["media_item_id"],)).fetchone()
        if not media:
            raise RuntimeError("Media item no longer exists")
        try:
            job_payload = json.loads(job["payload"] or "{}")
        except Exception:
            job_payload = {}
        self._notify_download_started(
            job["id"] if job and "id" in job.keys() else None,
            media["channel_id"],
            media["title"] or "",
            account_id=job_payload.get("test_account_id"),
        )
        target = Path(self.settings.download_dir) / \
            (media["channel_id"] or "unknown")
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
        if bool(getattr(self.settings, "use_vpn_for_youtube", False)):
            cookies_path = str(getattr(self.settings, "youtube_cookies_path", "") or "").strip()
            if not cookies_path:
                raise RuntimeError("Đã bật VPN nhưng chưa cấu hình cookies YouTube")
            if not Path(cookies_path).is_file():
                raise RuntimeError(f"Không tìm thấy file cookies YouTube: {cookies_path}")
            options["cookiefile"] = cookies_path
        ffmpeg_binary = self.settings.media_binary("ffmpeg")
        if ffmpeg_binary and (ffmpeg_binary != "ffmpeg" or Path(ffmpeg_binary).exists()):
            options["ffmpeg_location"] = ffmpeg_binary
        orientation_filter = self._source_orientation_filter()
        with _hide_child_console_windows():
            with yt_dlp.YoutubeDL(options) as ydl:
                if orientation_filter != "both":
                    metadata = ydl.extract_info(
                        media["source_url"], download=False)
                    metadata_dimensions = self._metadata_dimensions(metadata)
                    if metadata_dimensions and not self._orientation_allowed(metadata_dimensions, orientation_filter):
                        self._skip_for_orientation(
                            job, media, metadata_dimensions, orientation_filter, "metadata trước khi tải")
                        return
                    if not metadata_dimensions:
                        self.db.event(
                            job["id"] if job and "id" in job.keys() else None,
                            "INFO",
                            "Download: chưa lấy được hướng video từ metadata YouTube, sẽ tải rồi kiểm tra bằng ffprobe",
                        )
                info = ydl.extract_info(media["source_url"], download=True)
                path = Path(ydl.prepare_filename(info))
        if not path.exists() and path.suffix.lower() != ".mp4":
            path = path.with_suffix(".mp4")
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(
                "yt-dlp kết thúc nhưng không tìm thấy output hợp lệ")
        if orientation_filter != "both":
            dimensions = self._dimensions(path)
            if not self._orientation_allowed(dimensions, orientation_filter):
                self._skip_for_orientation(
                    job, media, dimensions, orientation_filter, "file đã tải")
                return
            self.db.event(
                job["id"] if job and "id" in job.keys() else None,
                "INFO",
                f"Download: video {self._orientation_name(dimensions)} khớp bộ lọc {self._orientation_filter_label(orientation_filter)}",
            )
        with self.db.connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO artifacts(media_item_id,kind,path,size_bytes) VALUES (?,?,?,?)",
                (media["id"], "download", str(path), path.stat().st_size),
            )
            artifact = connection.execute(
                "SELECT id FROM artifacts WHERE media_item_id=? AND kind='download' AND path=?",
                (media["id"], str(path)),
            ).fetchone()
            connection.execute(
                "UPDATE media_items SET status='downloaded',updated_at=? WHERE id=?", (utc_now(), media["id"]))
        process_payload = {"artifact_id": artifact["id"]}
        process_key = f"process:{media['id']}:{artifact['id']}"
        if job_payload.get("is_test"):
            process_payload["is_test"] = 1
            process_payload["test_account_id"] = int(job_payload["test_account_id"])
            process_key += f":test:{time.time_ns()}"
        self.db.enqueue(
            "process", media["id"], process_key, json.dumps(process_payload))

    def _notify_download_started(self, job_id: int | None, channel_id: str | None,
                                 title: str, account_id: int | None = None) -> None:
        """Ring the desktop bell as soon as the download starts."""
        sound_path = ""
        with self.db.connect() as connection:
            account = (
                connection.execute(
                    "SELECT * FROM tiktok_accounts WHERE id=?", (int(account_id),)
                ).fetchone()
                if account_id else channel_account(connection, channel_id)
            )
            if account and "notification_sound" in account.keys():
                sound_path = str(account["notification_sound"] or "").strip()
        if play_notification_bell(sound_path, self.settings.media_binary("ffmpeg")):
            self.db.event(
                job_id, "INFO",
                f"Thông báo: đã phát chuông bắt đầu tải video: {title}")
        else:
            self.db.event(
                job_id, "WARNING",
                "Thông báo: không phát được chuông bắt đầu tải video")

    def _source_orientation_filter(self) -> str:
        value = str(getattr(self.settings, "source_orientation_filter",
                    "both") or "both").strip().lower()
        return value if value in {"both", "landscape", "portrait"} else "both"

    @classmethod
    def _metadata_dimensions(cls, info: dict | None) -> tuple[int, int] | None:
        if not isinstance(info, dict):
            return None
        direct = cls._dimension_pair(info)
        if direct:
            return direct
        for key in ("requested_formats", "formats"):
            candidates: list[tuple[int, int]] = []
            for item in info.get(key) or []:
                pair = cls._dimension_pair(item)
                if pair:
                    candidates.append(pair)
            if candidates:
                return max(candidates, key=lambda pair: pair[0] * pair[1])
        return None

    @staticmethod
    def _dimension_pair(value: dict | None) -> tuple[int, int] | None:
        if not isinstance(value, dict):
            return None
        try:
            width = int(value.get("width") or 0)
            height = int(value.get("height") or 0)
        except (TypeError, ValueError):
            return None
        if width > 0 and height > 0:
            return width, height
        return None

    def _skip_for_orientation(self, job, media, dimensions: tuple[int, int] | None,
                              orientation_filter: str, source: str) -> None:
        orientation = self._orientation_name(dimensions)
        message = (
            f"Download: bỏ qua video vì hướng {orientation} từ {source} không khớp bộ lọc "
            f"{self._orientation_filter_label(orientation_filter)}"
        )
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE media_items SET status='skipped',error_message=?,updated_at=? WHERE id=?",
                (message, utc_now(), media["id"]),
            )
        self.db.event(job["id"] if job and "id" in job.keys()
                      else None, "INFO", message)

    def _dimensions(self, source: Path) -> tuple[int, int] | None:
        try:
            result = subprocess.run(
                [
                    self.settings.media_binary("ffprobe"),
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=s=x:p=0",
                    str(source),
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
                **_hidden_subprocess_kwargs(),
            )
            width_text, height_text = result.stdout.strip().split("x", 1)
            width, height = int(width_text), int(height_text)
            if width > 0 and height > 0:
                return width, height
        except Exception:
            return None
        return None

    @staticmethod
    def _orientation_allowed(dimensions: tuple[int, int] | None, orientation_filter: str) -> bool:
        if orientation_filter == "both" or not dimensions:
            return True
        width, height = dimensions
        if orientation_filter == "landscape":
            return width > height
        if orientation_filter == "portrait":
            return height > width
        return True

    @staticmethod
    def _orientation_name(dimensions: tuple[int, int] | None) -> str:
        if not dimensions:
            return "không xác định"
        width, height = dimensions
        if width > height:
            return f"ngang ({width}x{height})"
        if height > width:
            return f"dọc ({width}x{height})"
        return f"vuông ({width}x{height})"

    @staticmethod
    def _orientation_filter_label(orientation_filter: str) -> str:
        return {
            "landscape": "chỉ video ngang",
            "portrait": "chỉ video dọc",
            "both": "cả ngang và dọc",
        }.get(orientation_filter, "cả ngang và dọc")


@dataclass(frozen=True)
class ProcessingOptions:
    segment_seconds: int
    aspect_ratio: str
    zoom_percent: int
    speed: float
    background_path: str = ""
    zoom_width_percent: int | None = None
    zoom_height_percent: int | None = None
    account_id: int | None = None
    output_dir: str = ""
    caption_mode: str = "fixed"
    caption_text: str = ""
    notification_sound: str = ""
    split_enabled: bool | None = None
    overlay_enabled: bool | None = None

    def zoom_pair(self) -> tuple[int, int]:
        width = self.zoom_width_percent if self.zoom_width_percent is not None else self.zoom_percent
        height = self.zoom_height_percent if self.zoom_height_percent is not None else self.zoom_percent
        return (
            max(0, min(300, int(width if width is not None else 100))),
            max(0, min(300, int(height if height is not None else 100))),
        )


@dataclass(frozen=True)
class VideoBox:
    canvas_width: int
    canvas_height: int
    video_width: int
    video_height: int
    video_x: int
    video_y: int

    @property
    def video_bottom(self) -> int:
        return self.video_y + self.video_height


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
            source_row = connection.execute(
                "SELECT * FROM artifacts WHERE id=?", (payload["artifact_id"],)).fetchone()
            media = connection.execute(
                "SELECT * FROM media_items WHERE id=?", (job["media_item_id"],)).fetchone()
        if not source_row or not Path(source_row["path"]).is_file():
            raise RuntimeError("Download artifact bị thiếu")
        source = Path(source_row["path"])
        test_account_id = payload.get("test_account_id")
        is_test = bool(payload.get("is_test"))
        options = self._processing_options(
            media["channel_id"] if media else None,
            account_id=test_account_id,
        )
        output = Path(options.output_dir or self.settings.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        duration = self._duration(source)
        segments = []
        effective_duration = duration / options.speed
        split_enabled = (
            self.settings.split_enabled
            if options.split_enabled is None else bool(options.split_enabled)
        )
        total_parts = self.part_count_for_duration(
            effective_duration) if split_enabled else 1
        segment_ranges = self.segment_ranges_for_parts(
            effective_duration, total_parts)
        self.db.event(
            job["id"],
            "INFO",
            f"Process: duration gốc={duration:.1f}s, speed={options.speed:.3g}x, duration sau speed={effective_duration:.1f}s, parts={total_parts}, output={output}",
        )
        for index, (output_start, output_length) in enumerate(segment_ranges, start=1):
            destination = self.output_path(
                output, media["title"] or "", index)
            self._render(
                source,
                destination,
                output_start * options.speed,
                output_length * options.speed if total_parts > 1 else None,
                media["title"] or "",
                index,
                total_parts,
                options,
            )
            segments.append((index, destination))
        with self.db.connect() as connection:
            for index, path in segments:
                connection.execute(
                    """INSERT OR IGNORE INTO artifacts(media_item_id,kind,path,segment_index,size_bytes)
                    VALUES (?,?,?,?,?)""", (media["id"], "rendered", str(path), index, path.stat().st_size)
                )
            connection.execute(
                "UPDATE media_items SET status='processed',updated_at=? WHERE id=?", (utc_now(), media["id"]))
        self._route_uploads(
            media["id"], media["title"] or "",
            account_id=test_account_id, is_test=is_test)

    def _processing_options(self, channel_id: str | None,
                            account_id: int | None = None) -> ProcessingOptions:
        row = None
        with self.db.connect() as connection:
            if account_id:
                row = connection.execute(
                    """SELECT a.*, '' AS route_background_path
                       FROM tiktok_accounts a WHERE a.id=?""",
                    (int(account_id),),
                ).fetchone()
                if not row:
                    raise RuntimeError("Tài khoản TikTok của video test không tồn tại")
            else:
                row = channel_account(connection, channel_id)
        default_zoom = max(
            0, min(300, int(self.settings.video_zoom_percent if self.settings.video_zoom_percent is not None else 100)))
        default_speed = max(0.25, min(4.0, float(self.settings.video_speed or 1.0)))
        if row:
            speed = float(row["speed"] if row["speed"] is not None else default_speed)
            zoom_width = int(
                row["zoom_width_percent"] if row["zoom_width_percent"] is not None else default_zoom)
            zoom_height = int(
                row["zoom_height_percent"] if row["zoom_height_percent"] is not None else default_zoom)
            background_path = str(
                row["background_path"] or row["route_background_path"]
                or self.settings.video_background_path or "").strip()
            output_dir = str(row["output_dir"] or self.settings.output_dir)
            split_enabled = (
                self.settings.split_enabled if row["split_enabled"] is None
                else bool(row["split_enabled"])
            )
            overlay_enabled = (
                self.settings.overlay_text_enabled if row["overlay_enabled"] is None
                else bool(row["overlay_enabled"])
            )
            return ProcessingOptions(
                segment_seconds=max(5, int(self.settings.segment_seconds or 60)),
                aspect_ratio="9:16",
                zoom_percent=max(0, min(300, zoom_width)),
                speed=max(0.25, min(4.0, speed)),
                background_path=background_path,
                zoom_width_percent=max(0, min(300, zoom_width)),
                zoom_height_percent=max(0, min(300, zoom_height)),
                account_id=int(row["id"]),
                output_dir=output_dir,
                caption_mode=str(row["caption_mode"] or "fixed"),
                caption_text=str(row["caption_text"] or ""),
                notification_sound=str(row["notification_sound"] or ""),
                split_enabled=split_enabled,
                overlay_enabled=overlay_enabled,
            )
        return ProcessingOptions(
            segment_seconds=max(5, int(self.settings.segment_seconds or 60)),
            aspect_ratio="9:16",
            zoom_percent=default_zoom,
            speed=default_speed,
            background_path=str(self.settings.video_background_path or "").strip(),
            zoom_width_percent=default_zoom,
            zoom_height_percent=default_zoom,
            output_dir=self.settings.output_dir,
            split_enabled=bool(self.settings.split_enabled),
            overlay_enabled=bool(self.settings.overlay_text_enabled),
        )

    @staticmethod
    def part_count_for_duration(duration: float) -> int:
        """Map the speed-adjusted duration to the configured number of parts."""
        if duration < SPLIT_THRESHOLDS_SECONDS[0]:
            return 1
        for index, threshold in enumerate(SPLIT_THRESHOLDS_SECONDS[1:], start=2):
            if duration < threshold:
                return index
        return MAX_VIDEO_PARTS

    @staticmethod
    def segment_ranges_for_parts(duration: float, parts: int) -> list[tuple[float, float]]:
        """Split into near-equal parts with a small deliberate length difference.

        Offsets stay within roughly 5% of the average part; the last part
        absorbs the remainder so the total still matches the source duration.
        """
        parts = max(1, min(MAX_VIDEO_PARTS, int(parts)))
        if duration <= 0 or parts == 1:
            return [(0.0, max(0.0, duration))]
        base = duration / parts
        offsets = (0.05, -0.03, 0.04, -0.02)
        ranges: list[tuple[float, float]] = []
        start = 0.0
        for index in range(parts - 1):
            length = base * (1.0 + offsets[index])
            ranges.append((start, length))
            start += length
        ranges.append((start, duration - start))
        return ranges

    @staticmethod
    def safe_filename(value: str) -> str:
        value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "").strip().rstrip(" .")
        return value[:180] or "video"

    @classmethod
    def output_path(cls, output_dir: Path, title: str, part: int) -> Path:
        return Path(output_dir) / f"{cls.safe_filename(title)}_{part}.mp4"

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
        value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_",
                       value).strip().rstrip(".")
        return value or "unknown"

    def _duration(self, source: Path) -> float:
        result = subprocess.run(
            [self.settings.media_binary("ffprobe"), "-v", "error", "-show_entries",
             "format=duration", "-of", "default=nw=1:nk=1", str(source)],
            capture_output=True, text=True, check=True, timeout=60, **_hidden_subprocess_kwargs(),
        )
        return float(result.stdout.strip())

    def _dimensions(self, source: Path) -> tuple[int, int] | None:
        try:
            result = subprocess.run(
                [
                    self.settings.media_binary("ffprobe"),
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=s=x:p=0",
                    str(source),
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
                **_hidden_subprocess_kwargs(),
            )
            width_text, height_text = result.stdout.strip().split("x", 1)
            width, height = int(width_text), int(height_text)
            if width > 0 and height > 0:
                return width, height
        except Exception:
            return None
        return None

    def _render(self, source: Path, destination: Path, start: float, length: float | None,
                title: str = "", part: int = 1, total_parts: int = 1,
                options: ProcessingOptions | None = None) -> None:
        options = options or ProcessingOptions(
            max(5, int(self.settings.segment_seconds)),
            "9:16",
            max(0, min(300, int(getattr(self.settings, "video_zoom_percent", 100)))),
            max(0.25, min(4.0, float(getattr(self.settings, "video_speed", 1.0) or 1.0))),
            str(getattr(self.settings, "video_background_path", "") or ""),
        )
        zoom_width, zoom_height = options.zoom_pair()
        overlay_enabled = (
            bool(self.settings.overlay_text_enabled)
            if options.overlay_enabled is None else bool(options.overlay_enabled)
        )
        source_dimensions = (
            self._dimensions(source)
            if overlay_enabled and options.aspect_ratio in self.ASPECT_SIZES
            else None
        )
        video_box = self.scaled_video_box(
            source_dimensions, options.aspect_ratio, zoom_width, zoom_height)
        base_video_filter = self.video_filter(
            options.aspect_ratio, zoom_width, zoom_height)
        speed_changed = abs(options.speed - 1.0) > 0.001
        video_filters: list[str] = []
        if speed_changed:
            video_filters.append(f"setpts=PTS/{options.speed:.6g}")
        if base_video_filter:
            video_filters.append(base_video_filter)
        temporary_files: list[Path] = []
        caption_image: Path | None = None
        caption_x = caption_y = 0
        if overlay_enabled:
            caption_text = self.caption_overlay_text(title, part, total_parts)
            try:
                caption_image, caption_x, caption_y = self.caption_overlay_image(
                    destination, caption_text, options.aspect_ratio, video_box)
                temporary_files.append(caption_image)
            except Exception as exc:
                LOGGER.exception("Cannot render caption overlay image: %s", exc)
                overlay_filters, text_files = self.overlay_filters(
                    destination, title, part, total_parts, video_box)
                video_filters.extend(overlay_filters)
                temporary_files.extend(text_files)
        background = Path(
            options.background_path) if options.background_path else None
        use_background = bool(background and background.is_file(
        ) and self.ASPECT_SIZES.get(options.aspect_ratio))
        if destination.is_file() and destination.stat().st_size > 0 and not video_filters and not use_background and not caption_image:
            return
        command = [self.settings.media_binary(
            "ffmpeg"), "-y", "-ss", str(start)]
        if length is not None:
            command += ["-t", str(length)]
        command += ["-i", str(source)]
        if use_background:
            if background.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
                command += ["-loop", "1", "-i", str(background)]
            else:
                command += ["-stream_loop", "-1", "-i", str(background)]
        if caption_image:
            command += ["-i", str(caption_image)]
            caption_index = 2 if use_background else 1
            if use_background:
                graph = self.background_video_filter(
                    options, [], output_label="base")
            else:
                graph = f"[0:v]{','.join(video_filters) or 'null'}[base]"
            graph += f";[base][{caption_index}:v]overlay={caption_x}:{caption_y}[v]"
            command += ["-filter_complex", graph, "-map", "[v]", "-map", "0:a?"]
        elif use_background:
            command += ["-filter_complex", self.background_video_filter(
                options, []), "-map", "[v]", "-map", "0:a?"]
        elif video_filters:
            command += ["-vf", ",".join(video_filters)]
        if speed_changed:
            command += ["-af", self.audio_speed_filter(options.speed)]
        command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(destination)]
        try:
            subprocess.run(command, capture_output=True, check=True,
                           timeout=7200, **_hidden_subprocess_kwargs())
        finally:
            for path in temporary_files:
                path.unlink(missing_ok=True)

    @classmethod
    def caption_overlay_text(cls, title: str, part: int, total_parts: int) -> str:
        title = (title or "").strip() or " "
        if total_parts > 1:
            return f"{title} ({part})"
        return title

    def overlay_font_path(self) -> Path:
        if self.settings.overlay_font_path:
            return Path(self.settings.overlay_font_path)
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("arialbd.ttf", "arial.ttf"):
            candidate = fonts / name
            if candidate.is_file():
                return candidate
        return fonts / "arial.ttf"

    def caption_font(self, text: str, size: int):
        """Pick the rounded caption font when it covers the title glyphs."""
        from PIL import ImageFont

        if self.settings.overlay_font_path:
            configured = Path(self.settings.overlay_font_path)
            if configured.is_file():
                try:
                    return ImageFont.truetype(str(configured), size)
                except Exception:
                    pass
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        candidates = [
            fonts / name for name in ("arialbd.ttf", "arial.ttf")
        ]
        for path in candidates:
            if not path.is_file():
                continue
            try:
                font = ImageFont.truetype(str(path), size)
            except Exception:
                continue
            if self.font_covers_text(font, text):
                return font
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    @staticmethod
    def font_covers_text(font, text: str) -> bool:
        try:
            missing = font.getmask("\uFFFF")
            missing_signature = (missing.size, bytes(missing))
            for character in set(text or ""):
                if character in "\n\r\t ":
                    continue
                mask = font.getmask(character)
                if (mask.size, bytes(mask)) == missing_signature:
                    return False
            return True
        except Exception:
            return False

    @staticmethod
    def wrap_caption_lines(text: str, font, draw, max_width: int, max_lines: int = 3) -> list[str]:
        """Wrap caption text to the canvas width, keeping at most max_lines rows."""
        lines: list[str] = []
        for paragraph in (text or " ").split("\n"):
            words = paragraph.split()
            if not words:
                lines.append("")
                continue
            current = ""
            for word in words:
                if not current and draw.textlength(word, font=font) > max_width:
                    chunk = ""
                    for character in word:
                        if chunk and draw.textlength(chunk + character, font=font) > max_width:
                            lines.append(chunk)
                            chunk = character
                        else:
                            chunk += character
                    if chunk:
                        current = chunk
                    continue
                candidate = f"{current} {word}".strip()
                if current and draw.textlength(candidate, font=font) > max_width:
                    lines.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > max_width:
                lines[-1] = lines[-1][:-1]
            lines[-1] = lines[-1].rstrip() + "…"
        return lines or [" "]

    def caption_overlay_image(self, destination: Path, text: str, aspect_ratio: str,
                              video_box: VideoBox | None = None) -> tuple[Path, int, int]:
        """Render the white rounded caption plate as a transparent PNG."""
        from PIL import Image, ImageDraw, ImageFont

        canvas_width, canvas_height = (
            (video_box.canvas_width, video_box.canvas_height) if video_box
            else self.ASPECT_SIZES.get(aspect_ratio, (1080, 1920))
        )
        font_size = max(16, int(self.settings.overlay_font_size or 56))
        font = self.caption_font(text, font_size)
        padding = 10
        measuring = Image.new("RGBA", (1, 1))
        draw = ImageDraw.Draw(measuring)
        lines = self.wrap_caption_lines(
            text, font, draw, max(64, int(canvas_width * 0.9)))
        block = "\n".join(lines)
        bbox = draw.multiline_textbbox((0, 0), block, font=font, spacing=10, align="center")
        width = max(2, int(bbox[2] - bbox[0] + 2 * padding))
        height = max(2, int(bbox[3] - bbox[1] + 2 * padding))
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        painter = ImageDraw.Draw(image)
        painter.rounded_rectangle(
            (0, 0, width - 1, height - 1), radius=18, fill=(255, 255, 255, 255))
        painter.multiline_text(
            (padding - bbox[0], padding - bbox[1]), block, font=font,
            fill=(0, 0, 0, 255), spacing=10, align="center",
        )
        path = destination.with_name(destination.stem + ".caption.png")
        image.save(path)
        x = max(0, (canvas_width - width) // 2)
        # Keep the caption below TikTok's top navigation ("For You" header).
        safe_top = int(canvas_height * 0.16)
        y = (video_box.video_y - height - 8) if video_box else safe_top
        y = max(safe_top, y)
        return path, x, y

    def overlay_filters(self, destination: Path, title: str, part: int, total_parts: int,
                        video_box: VideoBox | None = None) -> tuple[list[str], list[Path]]:
        """Fallback drawtext overlay used only when Pillow cannot render the plate."""
        font = self.overlay_font_path()
        common = [
            "fontcolor=black",
            f"fontsize={max(16, int(self.settings.overlay_font_size or 56))}",
            "box=1",
            "boxcolor=white",
            "boxborderw=8",
        ]
        if font.is_file():
            common.insert(0, f"fontfile='{self.escape_filter_path(font)}'")
        text_file = destination.with_name(destination.stem + ".caption.txt")
        text = self.caption_overlay_text(title, part, total_parts)
        text_file.write_text(text, encoding="utf-8")
        safe_top = int((video_box.canvas_height if video_box else 1920) * 0.16)
        y = f"max({safe_top}\\,{video_box.video_y}-text_h-8)" if video_box else str(safe_top)
        filter_text = "drawtext=" + ":".join(
            [f"textfile='{self.escape_filter_path(text_file)}'", *common,
             "x=(w-text_w)/2", f"y={y}"])
        return [filter_text], [text_file]

    @staticmethod
    def escape_filter_path(path: Path) -> str:
        return path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")

    @classmethod
    def video_filter(cls, aspect_ratio: str, zoom_percent: int = 100,
                     zoom_height_percent: int | None = None) -> str:
        size = cls.ASPECT_SIZES.get(aspect_ratio)
        if not size:
            return ""
        width, height = size
        zoom_width = max(
            0, min(300, int(zoom_percent if zoom_percent is not None else 100))) / 100
        zoom_height = max(0, min(300, int(
            zoom_height_percent if zoom_height_percent is not None else zoom_percent))) / 100
        if abs(zoom_width - zoom_height) < 0.0001:
            target_width = max(2, int(width * zoom_width) // 2 * 2)
            target_height = max(2, int(height * zoom_height) // 2 * 2)
            if zoom_width <= 1.0:
                return (
                    f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
                )
            return (
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                f"crop=min(iw\\,{width}):min(ih\\,{height}):(iw-ow)/2:(ih-oh)/2,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
            )
        zoom_width_expr = (
            "2" if zoom_width == 0 else f"trunc(iw*{zoom_width:.6g}/2)*2")
        zoom_height_expr = (
            "2" if zoom_height == 0 else f"trunc(ih*{zoom_height:.6g}/2)*2")
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"scale={zoom_width_expr}:{zoom_height_expr},"
            f"crop=min(iw\\,{width}):min(ih\\,{height}):(iw-ow)/2:(ih-oh)/2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )

    @classmethod
    def scaled_video_box(cls, source_dimensions: tuple[int, int] | None, aspect_ratio: str,
                         zoom_percent: int = 100,
                         zoom_height_percent: int | None = None) -> VideoBox | None:
        size = cls.ASPECT_SIZES.get(aspect_ratio)
        if not size or not source_dimensions:
            return None
        canvas_width, canvas_height = size
        source_width, source_height = source_dimensions
        if source_width <= 0 or source_height <= 0:
            return None
        zoom_width = max(
            0, min(300, int(zoom_percent if zoom_percent is not None else 100))) / 100
        zoom_height = max(0, min(300, int(
            zoom_height_percent if zoom_height_percent is not None else zoom_percent))) / 100
        if abs(zoom_width - zoom_height) < 0.0001:
            fit_width = max(2, int(canvas_width * zoom_width) // 2 * 2)
            fit_height = max(2, int(canvas_height * zoom_height) // 2 * 2)
            scale = min(fit_width / source_width, fit_height / source_height)
            video_width = min(canvas_width, max(
                2, int(source_width * scale) // 2 * 2))
            video_height = min(canvas_height, max(
                2, int(source_height * scale) // 2 * 2))
        else:
            fit_scale = min(canvas_width / source_width,
                            canvas_height / source_height)
            base_width = source_width * fit_scale * zoom_width
            base_height = source_height * fit_scale * zoom_height
            video_width = min(canvas_width, max(
                2, int(base_width) // 2 * 2))
            video_height = min(canvas_height, max(
                2, int(base_height) // 2 * 2))
        return VideoBox(
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            video_width=video_width,
            video_height=video_height,
            video_x=(canvas_width - video_width) // 2,
            video_y=(canvas_height - video_height) // 2,
        )

    @classmethod
    def background_video_filter(cls, options: ProcessingOptions, tail_filters: list[str],
                                output_label: str = "v") -> str:
        width, height = cls.ASPECT_SIZES[options.aspect_ratio]
        zoom_width, zoom_height = options.zoom_pair()
        source_filters = []
        if abs(options.speed - 1.0) > 0.001:
            source_filters.append(f"setpts=PTS/{options.speed:.6g}")
        if zoom_width == zoom_height:
            target_width = max(2, int(width * zoom_width / 100) // 2 * 2)
            target_height = max(2, int(height * zoom_height / 100) // 2 * 2)
            source_filters.append(
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease")
            if zoom_width > 100 or zoom_height > 100:
                source_filters.append(
                    f"crop=min(iw\\,{width}):min(ih\\,{height}):(iw-ow)/2:(ih-oh)/2")
        else:
            zoom_width_expr = (
                "2" if zoom_width == 0 else f"trunc(iw*{zoom_width / 100:.6g}/2)*2")
            zoom_height_expr = (
                "2" if zoom_height == 0 else f"trunc(ih*{zoom_height / 100:.6g}/2)*2")
            source_filters.append(
                f"scale={width}:{height}:force_original_aspect_ratio=decrease")
            source_filters.append(f"scale={zoom_width_expr}:{zoom_height_expr}")
            source_filters.append(
                f"crop=min(iw\\,{width}):min(ih\\,{height}):(iw-ow)/2:(ih-oh)/2")
        source_filters.append("setsar=1")
        merged = (
            f"[1:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1[bg];"
            f"[0:v]{','.join(source_filters)}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1"
        )
        if tail_filters:
            merged += "," + ",".join(tail_filters)
        return merged + f"[{output_label}]"

    @staticmethod
    def audio_speed_filter(speed: float) -> str:
        speed = max(0.25, min(4.0, float(speed or 1.0)))
        parts: list[float] = []
        while speed > 2.0:
            parts.append(2.0)
            speed /= 2.0
        while speed < 0.5:
            parts.append(0.5)
            speed /= 0.5
        parts.append(speed)
        return ",".join(f"atempo={part:.6g}" for part in parts)

    def _route_uploads(self, media_id: int, caption: str,
                       account_id: int | None = None, is_test: bool = False) -> None:
        queued: list[tuple[int, int]] = []
        test_message = ""
        with self.db.connect() as connection:
            media = connection.execute(
                "SELECT channel_id FROM media_items WHERE id=?", (media_id,)).fetchone()
            if account_id:
                account = connection.execute(
                    "SELECT * FROM tiktok_accounts WHERE id=?",
                    (int(account_id),),
                ).fetchone()
                if not account:
                    raise RuntimeError("Tài khoản TikTok của video test không tồn tại")
            else:
                account = connection.execute(
                    """SELECT a.* FROM channel_route_sources s
                    JOIN channel_routes r ON r.id=s.route_id AND r.active=1
                    JOIN tiktok_accounts a ON a.id=r.account_id
                    WHERE s.channel_id=? ORDER BY r.id LIMIT 1""",
                    (media["channel_id"],),
                ).fetchone() if media else None
                if account and not bool(account["active"]):
                    # Account is paused: still process locally, but never post.
                    return
                if not account:
                    account = connection.execute(
                        "SELECT * FROM tiktok_accounts WHERE active=1 ORDER BY id LIMIT 1").fetchone()
            artifacts = connection.execute(
                """SELECT * FROM artifacts WHERE media_item_id=? AND kind='rendered'
                ORDER BY COALESCE(segment_index,1), id""",
                (media_id,),
            ).fetchall()
            if not account:
                return
            total_parts = len(artifacts)
            for artifact in artifacts:
                part = int(artifact["segment_index"] or 1)
                upload_caption = self._upload_caption(
                    caption, part, account, total_parts)
                connection.execute(
                    """INSERT INTO upload_tasks(
                         media_item_id,artifact_id,account_id,caption,status,is_test)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(artifact_id,account_id) DO UPDATE SET
                         caption=excluded.caption,
                         is_test=excluded.is_test,
                         status=CASE
                           WHEN excluded.is_test=1 THEN 'test_ready'
                           WHEN upload_tasks.status='test_ready' THEN 'pending'
                           ELSE upload_tasks.status END""",
                    (media_id, artifact["id"], account["id"], upload_caption,
                     "test_ready" if is_test else "pending", 1 if is_test else 0),
                )
                task = connection.execute(
                    "SELECT id FROM upload_tasks WHERE artifact_id=? AND account_id=?",
                    (artifact["id"], account["id"]),
                ).fetchone()
                if not is_test:
                    queued.append((int(task["id"]), media_id))
            if is_test:
                test_message = (
                    f"Test video: đã tạo {len(artifacts)} bản ghi hàng đợi "
                    f"cho tài khoản {account['name']}")
        if test_message:
            self.db.event(None, "INFO", test_message)
        for task_id, item_id in queued:
            self.db.enqueue(
                "upload", item_id, f"upload:{task_id}", json.dumps({"task_id": task_id}))

    def _upload_caption(self, title: str, part: int, account,
                        total_parts: int = 1) -> str:
        """Compose the TikTok description from the account caption settings."""
        caption_text = str(account["caption_text"] or "").strip()
        caption_mode = str(account["caption_mode"] or "fixed").lower()
        if caption_mode == "ai":
            try:
                generated = self._generate_caption(
                    title, part, account, total_parts)
                if generated:
                    return self._with_part_suffix(generated, part, total_parts)
            except Exception as exc:
                LOGGER.exception("AI caption generation failed")
                self.db.event(
                    None,
                    "WARNING",
                    f"Generate caption bằng AI thất bại, dùng caption cố định: {exc}",
                )
        if caption_text:
            return self._with_part_suffix(caption_text, part, total_parts)
        title_line = (title or "").strip()
        if title_line and total_parts > 1:
            title_line = f"{title_line} ({part})"
        lines = [title_line]
        hashtags = self.normalize_hashtag_caption(str(account["hashtags"] or ""))
        if hashtags:
            lines.append(hashtags)
        return "\n".join(line for line in lines if line).strip()

    @staticmethod
    def _with_part_suffix(caption: str, part: int, total_parts: int = 1) -> str:
        caption = caption.strip()
        if caption and total_parts > 1 and part > 0:
            return f"{caption} ({part})"
        return caption

    def _ai_value(self, account, key: str, fallback: str = "") -> str:
        if account is not None and key in account.keys():
            value = str(account[key] or "").strip()
            if value:
                return value
        return str(getattr(self.settings, key, "") or "").strip() or fallback

    def _generate_caption(self, title: str, part: int = 1, account=None,
                          total_parts: int = 1) -> str:
        system_prompt = self._ai_value(account, "ai_system_prompt") or (
            "Bạn là trợ lý viết caption TikTok. Trả về caption hoàn chỉnh, có thể kèm hashtag."
        )
        user_prompt = title
        if total_parts > 1:
            user_prompt = f"{title} ({part})"
        return self._ai_chat(system_prompt, user_prompt, account).strip()

    def _ai_chat(self, system_prompt: str, user_prompt: str, account=None) -> str:
        url = self._ai_value(account, "ai_api_url")
        if not url:
            raise RuntimeError("Chưa cấu hình AI API URL")
        model = self._ai_value(account, "ai_model") or "gpt-4o-mini"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
        }
        headers = {"Content-Type": "application/json"}
        api_key = self._ai_value(account, "ai_api_key")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
        return str((data.get("choices") or [{}])[0].get("message", {}).get("content", ""))

    @staticmethod
    def normalize_hashtag_caption(value: str) -> str:
        tags = []
        for token in re.split(r"[\s,;]+", value or ""):
            token = token.strip()
            if not token:
                continue
            token = token if token.startswith("#") else "#" + token
            token = re.sub(r"[^\w#\u00C0-\u1EF9]", "", token, flags=re.UNICODE)
            if len(token) > 1 and token not in tags:
                tags.append(token)
        return " ".join(tags)


class TikTokBrowserOutOfMemory(RuntimeError):
    pass


class UploadHandler:
    TIKTOK_UPLOAD_TIMEOUT_MS = 150_000
    TIKTOK_UPLOAD_MAX_ATTEMPTS = 2
    TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
    _account_locks: dict[int, threading.Lock] = {}
    _account_locks_guard = threading.Lock()

    def __init__(self, database: Database, settings: Settings):
        self.db, self.settings = database, settings

    @classmethod
    def _account_lock(cls, account_id: int) -> threading.Lock:
        with cls._account_locks_guard:
            return cls._account_locks.setdefault(int(account_id), threading.Lock())

    def manual_upload_file(
        self,
        path: Path,
        caption: str,
        mode: str,
        endpoint: str = "",
        gpm_profile_id: str = "",
        account_id: int | None = None,
        job_id: int | None = None,
    ) -> None:
        lock = self._account_lock(account_id) if account_id else None
        if lock:
            lock.acquire()
        try:
            self._manual_upload_locked(
                path, caption, mode, endpoint, gpm_profile_id, account_id, job_id)
        finally:
            if lock:
                lock.release()

    def _manual_upload_locked(
        self,
        path: Path,
        caption: str,
        mode: str,
        endpoint: str,
        gpm_profile_id: str,
        account_id: int | None,
        job_id: int | None,
    ) -> None:
        """Run the real TikTok upload flow for a user-selected local file.

        This is intentionally not queued as a normal pipeline job: it is a
        deliberate UI action for testing or one-off manual posting from a
        specific TikTok layout. It still writes full logs to job_events so the
        Log tab shows the same TikTok/GPM detail as automatic uploads.
        """
        path = Path(path)
        today = local_today()
        caption = self.normalize_manual_caption(caption)
        self.db.event(job_id, "INFO",
                      f"TikTok manual upload: bắt đầu file {path}")
        cdp_url = self._start_gpm_profile(
            gpm_profile_id, account_id or 0, job_id)
        self._playwright_upload(
            path,
            caption,
            cdp_url,
            log=lambda message: self.db.event(job_id, "INFO", message),
        )
        if account_id:
            with self.db.connect() as connection:
                connection.execute(
                    "UPDATE tiktok_accounts SET posts_today=posts_today+1,counter_date=?,status='idle' WHERE id=?",
                    (today, account_id),
                )
        self.db.event(job_id, "INFO", "TikTok manual upload: hoàn tất")

    @staticmethod
    def normalize_manual_caption(value: str) -> str:
        return ProcessHandler.normalize_hashtag_caption(value or "")

    def run(self, job) -> None:
        payload = json.loads(job["payload"])
        with self.db.connect() as connection:
            task = connection.execute(
                """SELECT u.*,a.path,t.endpoint,t.mode,t.gpm_profile_id,t.daily_limit,t.posts_today,
                          t.counter_date,t.publish_enabled,t.active
                FROM upload_tasks u JOIN artifacts a ON a.id=u.artifact_id
                JOIN tiktok_accounts t ON t.id=u.account_id WHERE u.id=?""", (payload["task_id"],)
            ).fetchone()
        if not task:
            raise RuntimeError("Upload task không tồn tại")
        if bool(task["is_test"]):
            with self.db.connect() as connection:
                connection.execute(
                    "UPDATE upload_tasks SET status='test_ready' WHERE id=?",
                    (task["id"],))
            return
        if not bool(task["active"]):
            raise ManualAction(
                "Tài khoản TikTok đang tắt; bật lại để mở profile và đăng bài.")
        lock = (
            self._account_lock(int(task["account_id"]))
            if task["account_id"] is not None else None
        )
        if lock:
            lock.acquire()
        try:
            self._run_locked(task, job)
        finally:
            if lock:
                lock.release()

    def _run_locked(self, task, job) -> None:
        today = local_today()
        posts_today = int(task["posts_today"] or 0)
        daily_limit = int(task["daily_limit"] or 0)
        if task["counter_date"] != today:
            posts_today = 0
            with self.db.connect() as connection:
                connection.execute(
                    "UPDATE tiktok_accounts SET posts_today=0,counter_date=? WHERE id=?", (today, task["account_id"]))
        if daily_limit > 0 and posts_today >= daily_limit:
            raise ManualAction("Tài khoản đã đạt giới hạn đăng trong ngày")
        publish_enabled = (
            self.settings.tiktok_publish_enabled if task["publish_enabled"] is None
            else bool(task["publish_enabled"])
        )
        if not publish_enabled:
            raise ManualAction(
                "Đã xử lý xong; bật 'Cho phép đăng TikTok' cho tài khoản này để publish")
        job_id = job["id"] if "id" in job.keys() else None
        cdp_url = self._start_gpm_profile(
            task["gpm_profile_id"], task["account_id"], job_id)
        self._playwright_upload(
            Path(task["path"]),
            task["caption"],
            cdp_url,
            log=lambda message: self.db.event(job_id, "INFO", message),
        )
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE upload_tasks SET status='completed',posted_at=? WHERE id=?", (utc_now(), task["id"]))
            connection.execute(
                "UPDATE tiktok_accounts SET posts_today=posts_today+1,counter_date=?,status='idle' WHERE id=?", (today, task["account_id"]))
            connection.execute(
                "UPDATE media_items SET status='uploaded',updated_at=? WHERE id=?", (utc_now(), task["media_item_id"]))

    def _start_gpm_profile(self, profile_id: str, account_id: int, job_id: int | None = None) -> str:
        if not profile_id:
            raise ManualAction(
                "Chế độ GPM cần GPM Profile ID cho kênh TikTok này")
        try:
            self.db.event(job_id, "INFO", f"GPM: đang mở profile {profile_id}")
            if self.settings.gpm_reclaim_profile_in_use:
                self.db.event(
                    job_id, "INFO", "GPM: nếu profile đang mở nhưng không lấy được CDP, app sẽ đóng profile và mở lại")
            cdp_url = GpmLoginClient(
                self.settings.gpm_api_url,
                reclaim_profile_in_use=self.settings.gpm_reclaim_profile_in_use,
                event_callback=lambda message: self.db.event(
                    job_id, "INFO", message),
            ).start_profile(profile_id)
        except ManualAction:
            raise
        except Exception as exc:
            raise ManualAction(
                f"Không mở được GPM profile {profile_id}: {exc}") from exc
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE tiktok_accounts SET endpoint=?,status='uploading' WHERE id=?",
                (cdp_url, account_id),
            )
        self.db.event(
            job_id, "INFO", f"GPM: profile {profile_id} đã sẵn sàng qua CDP {cdp_url}")
        return cdp_url

    @staticmethod
    def _playwright_upload(path: Path, caption: str, cdp_url: str, log: Callable[[str], None] | None = None) -> None:
        if not cdp_url:
            raise ManualAction(
                "Chưa cấu hình CDP URL hoặc GPM profile cho TikTok")
        normalized_cdp_url = GpmLoginClient._normalize_cdp_url(
            cdp_url) or cdp_url
        emit = log or (lambda _message: None)
        if normalized_cdp_url != cdp_url:
            emit(
                f"TikTok: chuyển CDP local sang HTTP endpoint {normalized_cdp_url} để upload file lớn ổn định hơn")
            cdp_url = normalized_cdp_url
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Thiếu dependency Playwright") from exc
        with sync_playwright() as playwright:
            upload_timeout = UploadHandler.TIKTOK_UPLOAD_TIMEOUT_MS
            emit("TikTok: đang kết nối Chrome/CDP")
            browser = UploadHandler._connect_over_cdp_with_retry(
                playwright, cdp_url)
            if not browser.contexts:
                raise ManualAction("Browser CDP không có context")
            context = browser.contexts[0]
            last_oom_error: TikTokBrowserOutOfMemory | None = None
            for attempt in range(1, UploadHandler.TIKTOK_UPLOAD_MAX_ATTEMPTS + 1):
                UploadHandler._close_stale_tiktok_upload_pages(context, log=emit)
                page = context.new_page()
                crash_state = UploadHandler._attach_tiktok_crash_monitor(page, log=emit)
                emit("TikTok: đã mở tab upload mới để tránh giữ preview video cũ trong bộ nhớ")
                try:
                    emit("TikTok: đang mở trang upload")
                    page.goto(UploadHandler.TIKTOK_UPLOAD_URL,
                              wait_until="domcontentloaded")
                    UploadHandler._raise_if_tiktok_browser_oom(page, crash_state)
                    emit("TikTok: đã mở trang upload")
                    try:
                        UploadHandler._select_tiktok_upload_file(
                            page, path, timeout=upload_timeout, log=emit, crash_state=crash_state)
                    except ManualAction:
                        raise
                    except Exception as exc:
                        if UploadHandler._is_tiktok_browser_oom_error(exc) or UploadHandler._page_has_tiktok_browser_oom(page, crash_state):
                            raise TikTokBrowserOutOfMemory(str(exc)) from exc
                        diagnostic = UploadHandler._diagnose_tiktok_upload_page(page)
                        raise RuntimeError(f"{exc}\n{diagnostic}") from exc
                    UploadHandler._raise_if_tiktok_browser_oom(page, crash_state)
                    emit("TikTok: đang chờ ô caption/hashtag")
                    editor = UploadHandler._find_tiktok_caption_editor(
                        page, timeout=upload_timeout, crash_state=crash_state)
                    emit(
                        "TikTok: đã thấy ô caption, bắt đầu nhập hashtag trong khi TikTok xử lý video")
                    UploadHandler._human_click(page, editor)
                    UploadHandler._fill_tiktok_caption(page, editor, caption, log=emit)
                    emit(
                        "TikTok: đã nhập hashtag, đang chờ video xử lý xong hoặc nút Post sẵn sàng")
                    post = UploadHandler._find_tiktok_post_button(page)
                    UploadHandler._wait_for_tiktok_video_ready(
                        page, post, timeout=upload_timeout, log=emit, crash_state=crash_state)
                    if not UploadHandler._is_tiktok_post_button_ready(post, timeout=10_000):
                        raise RuntimeError("TikTok: nút Đăng chưa sẵn sàng sau khi video xử lý xong")
                    UploadHandler._raise_if_tiktok_browser_oom(page, crash_state)
                    emit("TikTok: đang bấm Post")
                    UploadHandler._human_click(page, post)
                    UploadHandler._confirm_tiktok_post_if_needed(page)
                    try:
                        page.wait_for_url(re.compile(
                            r"^(?!.*\/upload).*$"), timeout=upload_timeout)
                    except Exception as exc:
                        if UploadHandler._is_tiktok_browser_oom_error(exc) or UploadHandler._page_has_tiktok_browser_oom(page, crash_state):
                            raise TikTokBrowserOutOfMemory(str(exc)) from exc
                        raise
                    emit("TikTok: đăng video hoàn tất")
                    return
                except TikTokBrowserOutOfMemory as exc:
                    last_oom_error = exc
                    emit(f"TikTok: phát hiện Chrome/TikTok upload bị out of memory/crash ở lần {attempt}")
                    if attempt >= UploadHandler.TIKTOK_UPLOAD_MAX_ATTEMPTS:
                        raise RuntimeError(
                            "TikTok upload bị out of memory/crash sau khi đã reload tab upload. "
                            "App đã đóng tab lỗi; hãy thử lại job hoặc giảm dung lượng/thời lượng video."
                        ) from exc
                    emit("TikTok: đang đóng tab lỗi và reload luồng upload mới")
                finally:
                    UploadHandler._release_tiktok_upload_page(page, log=emit)
            if last_oom_error:
                raise last_oom_error

    @staticmethod
    def _is_tiktok_upload_page(page) -> bool:
        try:
            url = str(page.url or "")
        except Exception:
            return False
        return "tiktok.com/upload" in url or "tiktok.com/tiktokstudio/upload" in url or "/tiktokstudio/upload" in url

    @staticmethod
    def _close_stale_tiktok_upload_pages(context, log: Callable[[str], None] | None = None) -> int:
        emit = log or (lambda _message: None)
        closed = 0
        for page in list(getattr(context, "pages", []) or []):
            if not UploadHandler._is_tiktok_upload_page(page):
                continue
            try:
                UploadHandler._release_tiktok_upload_page(page)
                closed += 1
            except Exception:
                continue
        if closed:
            emit(f"TikTok: đã dọn {closed} tab upload cũ để tránh lỗi out of memory")
        return closed

    @staticmethod
    def _release_tiktok_upload_page(page, log: Callable[[str], None] | None = None) -> None:
        emit = log or (lambda _message: None)
        try:
            if hasattr(page, "is_closed") and page.is_closed():
                return
        except Exception:
            pass
        try:
            page.goto("about:blank", wait_until="commit", timeout=5_000)
        except Exception:
            pass
        try:
            page.close()
            emit("TikTok: đã đóng tab upload để giải phóng preview video")
        except Exception:
            pass

    @staticmethod
    def _attach_tiktok_crash_monitor(page, log: Callable[[str], None] | None = None) -> dict:
        state = {"crashed": False}
        emit = log or (lambda _message: None)

        def mark_crashed(*_args):
            state["crashed"] = True
            emit("TikTok: Chrome báo page crash, app sẽ reload tab upload")

        try:
            page.on("crash", mark_crashed)
        except Exception:
            pass
        return state

    @staticmethod
    def _is_tiktok_browser_oom_error(error: Exception | str) -> bool:
        text = str(error or "").lower()
        patterns = (
            "out of memory",
            "not enough memory",
            "aw, snap",
            "page crashed",
            "target closed",
            "browser has been closed",
            "browser context closed",
            "renderer process crashed",
            "chrome-error://chromewebdata",
        )
        return any(pattern in text for pattern in patterns)

    @staticmethod
    def _page_has_tiktok_browser_oom(page, crash_state: dict | None = None) -> bool:
        if crash_state and crash_state.get("crashed"):
            return True
        try:
            url = str(page.url or "").lower()
            if "chrome-error://chromewebdata" in url:
                return True
        except Exception as exc:
            return UploadHandler._is_tiktok_browser_oom_error(exc)
        try:
            body_text = page.locator("body").inner_text(timeout=500)
            return bool(re.search(
                r"(out of memory|not enough memory|aw,\s*snap|this page isn.t working|ran out of memory|không đủ bộ nhớ|hết bộ nhớ)",
                body_text,
                re.IGNORECASE,
            ))
        except Exception as exc:
            return UploadHandler._is_tiktok_browser_oom_error(exc)

    @staticmethod
    def _raise_if_tiktok_browser_oom(page, crash_state: dict | None = None) -> None:
        if UploadHandler._page_has_tiktok_browser_oom(page, crash_state):
            raise TikTokBrowserOutOfMemory("Chrome/TikTok upload page crashed or out of memory")

    @staticmethod
    def _connect_over_cdp_with_retry(playwright, cdp_url: str, timeout: int = 60_000):
        deadline = time.monotonic() + timeout / 1000
        last_error: Exception | None = None
        while True:
            try:
                return playwright.chromium.connect_over_cdp(cdp_url)
            except Exception as exc:
                last_error = exc
                if time.monotonic() >= deadline:
                    break
                time.sleep(1)
        raise last_error or RuntimeError(f"Không kết nối được CDP {cdp_url}")

    @staticmethod
    def _human_click(page, locator, timeout: int = 10_000) -> None:
        """Click a DOM-selected element with visible mouse movement when possible."""
        locator.wait_for(state="visible", timeout=timeout)
        try:
            locator.scroll_into_view_if_needed(timeout=timeout)
        except Exception:
            pass
        try:
            box = locator.bounding_box(timeout=timeout)
        except TypeError:
            try:
                box = locator.bounding_box()
            except Exception:
                box = None
        except Exception:
            box = None
        if not box:
            locator.click()
            return
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        try:
            page.mouse.move(x, y, steps=8)
            page.wait_for_timeout(120)
            page.mouse.click(x, y)
        except Exception:
            locator.click()

    @staticmethod
    def _type_caption_text(editor, text: str) -> None:
        lines = (text or "").split("\n")
        for index, line in enumerate(lines):
            if line:
                editor.type(line, delay=25)
            if index < len(lines) - 1:
                editor.press("Enter")

    @staticmethod
    def _fill_tiktok_caption(page, editor, caption: str, log: Callable[[str], None] | None = None) -> None:
        emit = log or (lambda _message: None)
        editor.press("Control+A")
        editor.press("Backspace")
        caption = (caption or "").strip()
        if not caption:
            emit("TikTok: không có nội dung caption để nhập")
            return
        pattern = re.compile(r"#[^\s#]+")
        position = 0
        text_announced = False
        for match in pattern.finditer(caption):
            literal = caption[position:match.start()]
            if literal:
                UploadHandler._type_caption_text(editor, literal)
                if literal.strip() and not text_announced:
                    emit("TikTok: đã nhập nội dung caption")
                    text_announced = True
            tag = match.group(0)
            emit(f"TikTok: đang nhập hashtag {tag}")
            editor.type(tag, delay=35)
            if UploadHandler._select_first_hashtag_suggestion(page):
                emit(f"TikTok: đã chọn hashtag {tag}")
            else:
                emit(
                    f"TikTok: đã nhập hashtag {tag} nhưng không thấy popup gợi ý")
            position = match.end()
        tail = caption[position:]
        if tail:
            UploadHandler._type_caption_text(editor, tail)
            if tail.strip() and not text_announced:
                emit("TikTok: đã nhập nội dung caption")

    @staticmethod
    def _select_first_hashtag_suggestion(page, timeout: int = 5_000) -> bool:
        candidates = []
        for selector in (
            '.mention-list-popover [role="option"].focused',
            '.mention-list-popover [role="option"]',
            '.hashtag-suggestion-item.focused',
            '.hashtag-suggestion-item',
            'xpath=//div[contains(@class,"mention-list-popover")]//*[@role="option"][1]',
        ):
            try:
                candidates.append(page.locator(selector).first)
            except Exception:
                pass
        for candidate in candidates:
            try:
                candidate.wait_for(state="visible", timeout=timeout)
                UploadHandler._human_click(page, candidate, timeout=timeout)
                return True
            except Exception:
                continue
        try:
            page.keyboard.press("Enter")
            return True
        except Exception:
            return False

    @staticmethod
    def _find_tiktok_post_button(page):
        candidates = []
        for selector in (
            'button[data-e2e="post_video_button"][data-disabled="false"]',
            'button[data-e2e="post_video_button"][aria-disabled="false"]',
            'button[data-e2e="post_video_button"]',
            'xpath=//button[@data-e2e="post_video_button"]',
            'xpath=//button[@role="button" and @data-e2e="post_video_button"]',
            'button:has-text("Đăng")',
            'button:has-text("Post")',
        ):
            try:
                candidates.append(page.locator(selector).first)
            except Exception:
                pass
        try:
            candidates.append(page.get_by_role("button", name=re.compile(r"^(Post|Đăng)$")).first)
        except Exception:
            pass
        for candidate in candidates:
            try:
                candidate.wait_for(state="attached", timeout=1_000)
                return candidate
            except Exception:
                continue
        return page.locator('button[data-e2e="post_video_button"]').first

    @staticmethod
    def _is_tiktok_post_button_ready(post_locator, timeout: int = 1_000) -> bool:
        try:
            post_locator.wait_for(state="visible", timeout=timeout)
        except Exception:
            return False
        try:
            if hasattr(post_locator, "is_enabled") and not post_locator.is_enabled(timeout=timeout):
                return False
        except TypeError:
            try:
                if hasattr(post_locator, "is_enabled") and not post_locator.is_enabled():
                    return False
            except Exception:
                pass
        except Exception:
            pass
        for attr in ("aria-disabled", "data-disabled", "disabled", "data-loading"):
            try:
                value = post_locator.get_attribute(attr, timeout=timeout)
            except TypeError:
                try:
                    value = post_locator.get_attribute(attr)
                except Exception:
                    value = None
            except Exception:
                value = None
            if attr in {"aria-disabled", "data-disabled", "disabled"} and str(value).lower() == "true":
                return False
            if attr == "data-loading" and str(value).lower() == "true":
                return False
        return True

    @staticmethod
    def _wait_for_tiktok_video_ready(page, post_locator, timeout: int = 150_000, log: Callable[[str], None] | None = None, crash_state: dict | None = None) -> bool:
        """Wait until TikTok indicates the uploaded video is ready to post.

        TikTok can show the caption editor before the video finishes processing,
        so the uploader fills hashtags first and then waits for either the
        success check icon or the Post button to become available.
        """
        emit = log or (lambda _message: None)
        ready_selectors = (
            '[data-testid="CheckCircleFill"]',
            'span[role="img"][data-icon="CheckCircleFill"]',
            'span[data-icon="CheckCircleFill"]',
            'svg[data-icon="check-circle-fill"]',
            '.px-icon[data-testid="CheckCircleFill"]',
        )
        deadline = time.time() + timeout / 1000
        last_error = None
        video_ready_seen = False
        while time.time() < deadline:
            UploadHandler._raise_if_tiktok_browser_oom(page, crash_state)
            remaining = int(max(1_000, (deadline - time.time()) * 1000))
            if UploadHandler._is_tiktok_post_button_ready(post_locator, timeout=min(1_000, remaining)):
                if video_ready_seen:
                    emit("TikTok: video đã xử lý xong và nút Post đã sẵn sàng")
                else:
                    emit("TikTok: nút Post đã visible và sẵn sàng")
                return True
            for selector in ready_selectors:
                try:
                    page.locator(selector).first.wait_for(
                        state="visible", timeout=500)
                    if not video_ready_seen:
                        emit("TikTok: video đã xử lý xong (CheckCircleFill), đang chờ nút Post sẵn sàng")
                    video_ready_seen = True
                    break
                except Exception as exc:
                    last_error = exc
            try:
                page.wait_for_timeout(500)
            except Exception:
                time.sleep(0.5)
        if last_error:
            raise last_error
        raise RuntimeError(
            "TikTok chưa báo video xử lý xong và nút Post chưa sẵn sàng")

    @staticmethod
    def _select_tiktok_upload_file(page, path: Path, timeout: int = 45_000, log: Callable[[str], None] | None = None, crash_state: dict | None = None) -> None:
        """Select a video file using a direct file input or TikTok's select-video button."""
        emit = log or (lambda _message: None)
        try:
            emit("TikTok: đang tìm input[type=file]")
            file_input = UploadHandler._find_tiktok_file_input(
                page, timeout=timeout)
            emit("TikTok: đã tìm thấy input[type=file]")
        except Exception as first_error:
            last_error = first_error
        else:
            if path.is_absolute() and not path.is_file():
                raise RuntimeError(
                    f"TikTok: không tìm thấy file video trên máy để upload: {path}")
            emit(f"TikTok: đang set file {path}")
            try:
                file_input.set_input_files(str(path))
            except Exception as exc:
                raise RuntimeError(
                    f"TikTok: đã tìm thấy input[type=file] nhưng set file thất bại cho {path}: {exc}") from exc
            emit(f"TikTok: đã set file {path.name}")
            emit("TikTok: file đã được gửi vào TikTok, tiếp tục chờ ô caption để nhập song song lúc video xử lý")
            return
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
            UploadHandler._raise_if_tiktok_browser_oom(page, crash_state)
            try:
                remaining = int(max(1_000, (deadline - time.time()) * 1000))
                button = page.locator(selector).first
                button.wait_for(state="visible",
                                timeout=min(10_000, remaining))
                with page.expect_file_chooser(timeout=min(10_000, remaining)) as chooser:
                    UploadHandler._human_click(
                        page, button, timeout=min(10_000, remaining))
                chooser.value.set_files(str(path))
                emit(f"TikTok: đã set file {path.name} qua file chooser")
                emit(
                    "TikTok: file đã được gửi vào TikTok, tiếp tục chờ ô caption để nhập song song lúc video xử lý")
                return
            except Exception as exc:
                last_error = exc
                if UploadHandler._has_tiktok_upload_form(page, timeout=1_000):
                    emit("TikTok: đã thấy form caption/upload details")
                    return
        raise last_error

    @staticmethod
    def _wait_for_tiktok_upload_form(page, timeout: int = 45_000) -> bool:
        """Wait until TikTok leaves the select-file state and shows the upload details form."""
        deadline = time.time() + timeout / 1000
        last_error = None
        while time.time() < deadline:
            if UploadHandler._has_tiktok_upload_form(page, timeout=1_000):
                return True
            try:
                body_text = page.locator("body").inner_text(timeout=1_000)
                if re.search(r"(Uploading|Processing|Đang tải|Đang xử lý|Caption|Description|Mô tả|Hashtag)", body_text, re.IGNORECASE):
                    return True
            except Exception as exc:
                last_error = exc
            try:
                page.wait_for_timeout(500)
            except Exception:
                time.sleep(0.5)
        if last_error:
            raise last_error
        raise RuntimeError(
            "TikTok đã nhận file nhưng chưa hiển thị form caption/upload details")

    @staticmethod
    def _has_tiktok_upload_form(page, timeout: int = 1_000) -> bool:
        try:
            UploadHandler._find_tiktok_caption_editor(page, timeout=timeout)
            return True
        except Exception:
            return False

    @staticmethod
    def _find_tiktok_caption_editor(page, timeout: int = 20 * 60_000, crash_state: dict | None = None):
        selectors = (
            '[contenteditable="true"][role="combobox"]',
            '[contenteditable="true"]',
            '[contenteditable="true"][role="textbox"]',
            '[contenteditable="true"][aria-label*="caption" i]',
            '[contenteditable="true"][aria-label*="description" i]',
            '[contenteditable="true"][data-e2e*="caption" i]',
            'textarea[placeholder*="caption" i]',
            'textarea[placeholder*="description" i]',
        )
        deadline = time.time() + timeout / 1000
        last_error = None
        for selector in selectors:
            UploadHandler._raise_if_tiktok_browser_oom(page, crash_state)
            try:
                remaining = int(max(1_000, (deadline - time.time()) * 1000))
                editor = page.locator(selector).first
                editor.wait_for(state="visible",
                                timeout=min(10_000, remaining))
                return editor
            except Exception as exc:
                last_error = exc
        for selector in ("iframe", 'iframe[src*="creator"]', 'iframe[src*="upload"]', 'iframe[src*="studio"]'):
            UploadHandler._raise_if_tiktok_browser_oom(page, crash_state)
            try:
                frame = page.frame_locator(selector).first
                for editor_selector in selectors:
                    remaining = int(
                        max(1_000, (deadline - time.time()) * 1000))
                    editor = frame.locator(editor_selector).first
                    editor.wait_for(state="visible",
                                    timeout=min(10_000, remaining))
                    return editor
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise RuntimeError("Không tìm thấy ô caption/description TikTok")

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
                candidates.append(frame.locator(
                    'input[accept*="video"]').first)
            except Exception:
                pass
        last_error = None
        per_candidate_timeout = max(3_000, min(timeout, 10_000))
        deadline = time.time() + (timeout / 1000)
        for candidate in candidates:
            try:
                remaining = int(max(1_000, (deadline - time.time()) * 1000))
                candidate.wait_for(state="attached", timeout=min(
                    per_candidate_timeout, remaining))
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
            (r"(Log in|Login|Sign up|Đăng nhập|Đăng ký)",
             "TikTok đang mở màn hình đăng nhập. Hãy đăng nhập đúng Chrome profile/CDP port rồi chạy lại job."),
            (r"(captcha|verification|verify|xác minh|checkpoint|security check)",
             "TikTok đang yêu cầu xác minh/captcha trên Chrome profile này. Hãy xử lý thủ công trong Chrome rồi chạy lại job."),
            (r"(Something went wrong|Try again|Không thể|Lỗi)",
             "Trang upload TikTok đang báo lỗi. Hãy tải lại trang trong Chrome CDP rồi chạy lại job."),
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
                    name=re.compile(
                        r"^(Post now|Đăng ngay|Tiếp tục đăng)$", re.IGNORECASE),
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
                UploadHandler._human_click(page, candidate, timeout=3_000)
                return True
            except Exception:
                continue
        return False


class ManualAction(RuntimeError):
    pass


class GpmLoginClient:
    """Small adapter for GPM Login API v3.

    GPM opens a real Chrome profile and returns a remote-debugging address.
    The TikTok uploader then connects to that address through Playwright CDP.
    """

    def __init__(
        self,
        api_url: str,
        timeout: int = 60,
        reclaim_profile_in_use: bool = True,
        reclaim_wait_seconds: float = 2.0,
        event_callback: Callable[[str], None] | None = None,
    ):
        self.api_url = (api_url or DEFAULT_GPM_API_URL).strip().rstrip("/")
        self.timeout = timeout
        self.reclaim_profile_in_use = reclaim_profile_in_use
        self.reclaim_wait_seconds = reclaim_wait_seconds
        self.event_callback = event_callback

    def start_profile(self, profile_id: str) -> str:
        profile_id = str(profile_id or "").strip()
        if not profile_id:
            raise ManualAction("GPM Profile ID đang trống")
        quoted_profile_id = urllib.parse.quote(profile_id, safe="")
        start_path = f"/profiles/start/{quoted_profile_id}"
        response = self._get_json(start_path)
        cdp_url = self._extract_cdp_url(response)
        if cdp_url:
            try:
                return self._wait_for_cdp_endpoint(cdp_url)
            except ManualAction as exc:
                if self.reclaim_profile_in_use:
                    self._event(
                        f"GPM: CDP {cdp_url} chưa sẵn sàng, app sẽ đóng profile và mở lại")
                    return self._reclaim_profile_and_start(profile_id, start_path, str(exc))
                raise
        if isinstance(response, dict) and response.get("success") is False:
            message = response.get("message") or response.get("msg") or response.get(
                "error") or "GPM API báo lỗi khi start profile"
            if "ProfileInUse" in str(message):
                running_cdp_url = self.find_running_profile_cdp(profile_id)
                if running_cdp_url:
                    return self._wait_for_cdp_endpoint(running_cdp_url)
                if self.reclaim_profile_in_use:
                    self._event(
                        "GPM: ProfileInUse, app đang đóng profile và mở lại")
                    return self._reclaim_profile_and_start(profile_id, start_path, "ProfileInUse")
                raise ManualAction(
                    "GPM báo ProfileInUse: profile đang mở ở nơi khác nhưng app không tìm thấy CDP port để dùng lại. "
                    "Hãy đóng profile trong GPM rồi chạy lại, bật option tự đóng/mở lại profile, hoặc mở profile với remote debugging/CDP và điền endpoint thủ công."
                )
            raise ManualAction(str(message))
        raise ManualAction(
            "GPM đã trả response nhưng không có remote_debugging_address/CDP endpoint. "
            "Hãy kiểm tra GPM Login API URL và phiên bản GPM."
        )

    def _reclaim_profile_and_start(self, profile_id: str, start_path: str, reason: str = "") -> str:
        self.stop_profile(profile_id)
        self._event(f"GPM: đã gửi lệnh stop profile {profile_id}, đang mở lại")
        if self.reclaim_wait_seconds > 0:
            time.sleep(self.reclaim_wait_seconds)
        retry_response = self._get_json(start_path)
        retry_cdp_url = self._extract_cdp_url(retry_response)
        if retry_cdp_url:
            self._event(
                f"GPM: đã lấy CDP mới {retry_cdp_url}, đang chờ Chrome sẵn sàng")
            return self._wait_for_cdp_endpoint(retry_cdp_url)
        retry_message = ""
        if isinstance(retry_response, dict):
            retry_message = str(retry_response.get("message") or retry_response.get(
                "msg") or retry_response.get("error") or "")
        raise ManualAction(
            "GPM cần reclaim profile nhưng sau khi đóng/mở lại vẫn không lấy được CDP endpoint. "
            f"Lý do ban đầu: {reason}. Phản hồi GPM sau khi mở lại: {retry_message or retry_response}"
        )

    def _event(self, message: str) -> None:
        if self.event_callback:
            try:
                self.event_callback(message)
            except Exception:
                LOGGER.exception("Could not write GPM event")

    def _get_json(self, path: str) -> object:
        url = self.api_url + path
        request = urllib.request.Request(
            url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise ManualAction(
                f"Không gọi được GPM API tại {url}. Hãy mở GPM Login và kiểm tra GPM API URL. Chi tiết: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ManualAction(
                f"GPM API trả dữ liệu không phải JSON tại {url}: {raw[:300]}") from exc

    def stop_profile(self, profile_id: str) -> object:
        profile_id = str(profile_id or "").strip()
        if not profile_id:
            raise ManualAction("GPM Profile ID đang trống")
        response = self._get_json(
            f"/profiles/stop/{urllib.parse.quote(profile_id, safe='')}")
        if isinstance(response, dict) and response.get("success") is False:
            message = response.get("message") or response.get(
                "msg") or response.get("error") or "GPM API báo lỗi khi stop profile"
            if str(message).strip().lower() in {"ok", "success", "successful", "profile stopped", "stopped"}:
                return response
            raise ManualAction(
                f"Không đóng được GPM profile {profile_id}: {message}")
        return response

    @classmethod
    def _extract_cdp_url(cls, payload: object) -> str:
        candidates: list[object] = []
        if isinstance(payload, dict):
            candidates.append(payload)
            data = payload.get("data")
            if isinstance(data, dict):
                candidates.append(data)
            elif isinstance(data, list):
                candidates.extend(
                    item for item in data if isinstance(item, dict))
        for item in candidates:
            if not isinstance(item, dict):
                continue
            for key in (
                "remote_debugging_address",
                "remoteDebuggingAddress",
                "debugging_address",
                "debuggerAddress",
                "cdp_url",
                "cdpUrl",
                "endpoint",
                "browser_endpoint",
                "browserWSEndpoint",
                "wsEndpoint",
                "websocket_debugging_url",
                "websocketDebuggingUrl",
                "webSocketDebuggerUrl",
            ):
                value = item.get(key)
                cdp_url = cls._normalize_cdp_url(value)
                if cdp_url:
                    return cdp_url
            port = item.get("remote_debugging_port") or item.get(
                "remoteDebuggingPort") or item.get("debugging_port") or item.get("port")
            if port:
                host = item.get("host") or item.get("ip") or "127.0.0.1"
                cdp_url = cls._normalize_cdp_url(f"{host}:{port}")
                if cdp_url:
                    return cdp_url
        return ""

    @staticmethod
    def _normalize_cdp_url(value: object) -> str:
        if value is None:
            return ""
        text = str(value).strip().strip('"').strip("'")
        if not text:
            return ""
        if text.startswith(("ws://", "wss://")):
            try:
                parsed = urllib.parse.urlparse(text)
                host = (parsed.hostname or "").lower()
                if parsed.port and host in {"localhost", "127.0.0.1", "::1"}:
                    return f"http://127.0.0.1:{parsed.port}"
            except Exception:
                pass
            return text
        if text.startswith(("http://", "https://")):
            return text
        if re.fullmatch(r"\d{2,5}", text):
            return f"http://127.0.0.1:{text}"
        if re.fullmatch(r"[^/:]+:\d{2,5}", text):
            return f"http://{text}"
        return ""

    def _wait_for_cdp_endpoint(self, cdp_url: str, timeout: int = 60) -> str:
        if cdp_url.startswith(("ws://", "wss://")):
            return cdp_url
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_cdp_alive(cdp_url):
                return cdp_url
            time.sleep(1)
        raise ManualAction(
            f"GPM đã trả CDP {cdp_url} nhưng Chrome chưa mở cổng remote debugging sau {timeout}s. "
            "Hãy kiểm tra profile GPM có bật remote debugging và thử đóng/mở lại profile."
        )

    @classmethod
    def find_running_profile_cdp(cls, profile_id: str) -> str:
        """Best-effort Windows fallback for GPM versions that return ProfileInUse.

        Recent GPM API versions are documented to return the cached CDP endpoint
        when a profile is already running. Some local installs instead return
        ProfileInUse. In that case we inspect local Chrome process command lines,
        match the GPM profile id, and reuse its --remote-debugging-port.
        """
        if os.name != "nt" or not profile_id:
            return ""
        powershell = Path(os.environ.get("WINDIR", "C:/Windows")) / \
            "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        command = (
            "$profileId=$args[0]; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($profileId) -and $_.CommandLine -match '--remote-debugging-port[= ](\\d+)' } | "
            "Select-Object -First 1 -ExpandProperty CommandLine"
        )
        try:
            result = subprocess.run(
                [str(powershell), "-NoProfile", "-ExecutionPolicy",
                 "Bypass", "-Command", command, profile_id],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                **_hidden_subprocess_kwargs(),
            )
        except Exception:
            LOGGER.exception(
                "Could not inspect running GPM/Chrome process for profile=%s", profile_id)
            return ""
        if result.returncode != 0:
            LOGGER.warning("Inspect running GPM profile failed: %s",
                           (result.stderr or result.stdout or "").strip()[:500])
            return ""
        match = re.search(
            r"--remote-debugging-port[= ](\d+)", result.stdout or "")
        if not match:
            return ""
        cdp_url = f"http://127.0.0.1:{match.group(1)}"
        if cls._is_cdp_alive(cdp_url):
            return cdp_url
        LOGGER.warning(
            "Found running GPM CDP port but /json/version did not respond: %s", cdp_url)
        return ""

    @staticmethod
    def _is_cdp_alive(cdp_url: str) -> bool:
        try:
            with urllib.request.urlopen(cdp_url.rstrip("/") + "/json/version", timeout=3) as response:
                return 200 <= response.status < 300
        except Exception:
            return False


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
        self.threads: list[threading.Thread] = []
        # Kept for compatibility with older callers.
        self.thread: threading.Thread | None = None

    @property
    def is_alive(self) -> bool:
        return any(thread.is_alive() for thread in self.threads)

    def _worker_counts(self) -> dict[str, int]:
        def clamp(value) -> int:
            return max(1, min(10, int(value or 1)))
        return {
            "download": clamp(getattr(self.settings, "max_download_workers", 2)),
            "process": clamp(getattr(self.settings, "max_process_workers", 1)),
            "upload": clamp(getattr(self.settings, "max_upload_workers", 1)),
        }

    def start(self) -> None:
        self.pause_event.clear()
        if self.is_alive:
            return
        self.stop_event.clear()
        self.db.recover_expired_leases()
        self.threads = []
        for kind, count in self._worker_counts().items():
            for index in range(count):
                thread = threading.Thread(
                    target=self._loop,
                    args=(kind, f"desktop-{kind}-{index + 1}"),
                    name=f"pipeline-{kind}-{index + 1}",
                    daemon=True,
                )
                self.threads.append(thread)
                thread.start()
        self.thread = self.threads[0] if self.threads else None

    def pause(self) -> None:
        self.pause_event.set()

    def stop(self, timeout: float = 10) -> None:
        self.stop_event.set()
        deadline = time.monotonic() + timeout
        for thread in list(self.threads):
            thread.join(max(0.0, deadline - time.monotonic()))
        if not any(thread.is_alive() for thread in self.threads):
            self.threads = []
            self.thread = None

    KIND_LABELS = {
        "download": "tải video",
        "process": "xử lý video",
        "upload": "đăng TikTok",
    }

    def _job_details(self, job) -> str:
        """Build a readable log line with video title, channel and account."""
        kind = job["kind"]
        label = self.KIND_LABELS.get(kind, kind)
        title = ""
        channel = ""
        account = ""
        download_link = ""
        try:
            with self.db.connect() as connection:
                media = connection.execute(
                    """SELECT m.title,m.youtube_video_id,m.channel_id,m.source_url,
                              c.title AS channel_title
                       FROM media_items m
                       LEFT JOIN channels c ON c.channel_id=m.channel_id
                       WHERE m.id=?""",
                    (job["media_item_id"],),
                ).fetchone()
                if media:
                    title = media["title"] or media["youtube_video_id"] or ""
                    if kind == "download" and media["source_url"]:
                        download_link = str(media["source_url"])
                    if media["channel_title"]:
                        channel = media["channel_title"]
                    elif str(media["channel_id"] or "").startswith("test_account_"):
                        channel = "Test video"
                    else:
                        channel = media["channel_id"] or "không rõ kênh"
                if kind == "upload":
                    try:
                        payload = json.loads(job["payload"] or "{}")
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
        except Exception:
            LOGGER.exception("Cannot build job log details for job %s", job["id"])
        parts = [f"{label}: {title or 'không rõ video'}"]
        if channel:
            parts.append(f"Kênh: {channel}")
        if download_link:
            parts.append(f"Link: {download_link}")
        if account:
            parts.append(f"Tài khoản: {account}")
        return " | ".join(parts)

    def _loop(self, kind: str, worker_name: str) -> None:
        while not self.stop_event.is_set():
            if self.pause_event.is_set():
                self.stop_event.wait(0.5)
                continue
            try:
                job = self.db.claim(worker_name, (kind,))
            except Exception:
                LOGGER.exception("Cannot claim %s job", kind)
                self.stop_event.wait(1.0)
                continue
            if not job:
                self.stop_event.wait(self.settings.worker_poll_seconds)
                continue
            details = self._job_details(job)
            try:
                self.db.event(job["id"], "INFO", f"Bắt đầu {details}")
                self.handlers[job["kind"]].run(job)
                self.db.complete(job["id"])
                self.db.event(job["id"], "INFO", f"Hoàn tất {details}")
            except ManualAction as exc:
                self.db.manual_action(job["id"], str(exc))
                self.db.event(job["id"], "WARNING", str(exc))
            except Exception as exc:
                LOGGER.exception("Job %s failed", job["id"])
                self.db.fail(job["id"], str(exc))
                self.db.event(job["id"], "ERROR", str(exc))
