from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def app_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "MMOApp"


def bundled_path(*parts: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root.joinpath(*parts)


@dataclass(slots=True)
class Settings:
    data_dir: str = field(default_factory=lambda: str(app_data_dir()))
    download_dir: str = field(default_factory=lambda: str(Path.home() / "Videos" / "MMOApp" / "downloads"))
    output_dir: str = field(default_factory=lambda: str(Path.home() / "Videos" / "MMOApp" / "processed"))
    channel_ids: list[str] = field(default_factory=list)
    youtube_channel_urls: list[str] = field(default_factory=list)
    tracking_mode: str = "both"
    public_domain: str = ""
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 8000
    websub_renew_interval_seconds: int = 3600
    subscription_lease_seconds: int = 864000
    rss_interval_seconds: int = 1800
    worker_poll_seconds: int = 3
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    segment_seconds: int = 60
    split_enabled: bool = True
    output_aspect_ratio: str = "original"
    overlay_text_enabled: bool = False
    overlay_font_path: str = ""
    overlay_font_color: str = "white"
    overlay_font_size: int = 56
    tiktok_publish_enabled: bool = False
    tiktok_cdp_url: str = ""
    gpm_api_url: str = "http://127.0.0.1:19995/api/v3"
    max_upload_workers: int = 1

    @property
    def database_path(self) -> Path:
        return Path(self.data_dir) / "mmo_app.db"

    @property
    def log_dir(self) -> Path:
        return Path(self.data_dir) / "logs"

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or app_data_dir() / "settings.json"
        if not path.exists():
            value = cls()
            value.save(path)
            return value
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = cls.__dataclass_fields__.keys()
        value = cls(**{key: raw[key] for key in allowed if key in raw})
        value.ensure_dirs()
        return value

    def save(self, path: Path | None = None) -> Path:
        path = path or Path(self.data_dir) / "settings.json"
        self.ensure_dirs()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return path

    def ensure_dirs(self) -> None:
        for path in (Path(self.data_dir), Path(self.download_dir), Path(self.output_dir), self.log_dir):
            path.mkdir(parents=True, exist_ok=True)

    def media_binary(self, name: str) -> str:
        configured = self.ffmpeg_path if name == "ffmpeg" else self.ffprobe_path
        if configured:
            return configured
        bundled = bundled_path("tools", "ffmpeg", "bin", f"{name}.exe")
        return str(bundled) if bundled.exists() else name
