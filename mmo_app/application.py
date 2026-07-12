from __future__ import annotations

import logging
import os
import threading
import time
from logging.handlers import RotatingFileHandler

from .database import Database
from .services import PipelineOrchestrator, YouTubeService
from .settings import Settings
from .settings import bundled_path
from .websub import WebSubService


class Application:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()
        self.settings.ensure_dirs()
        runtime = bundled_path("tools", "runtime")
        if runtime.exists():
            os.environ["PATH"] = str(runtime) + os.pathsep + os.environ.get("PATH", "")
        configure_logging(self.settings)
        self.database = Database(self.settings.database_path)
        self.database.migrate()
        self.youtube = YouTubeService(self.database, self.settings)
        self.websub = WebSubService(self.database, self.settings, self.youtube)
        self.pipeline = PipelineOrchestrator(self.database, self.settings)
        self.stop_event = threading.Event()
        self.rss_thread: threading.Thread | None = None
        self.started = False

    def start(self) -> bool:
        was_running = self.is_running
        self._validate_ready_to_start()
        self.started = True
        self.stop_event.clear()
        if self.settings.tracking_mode in {"websub", "both"}:
            self.websub.start()
            threading.Thread(target=lambda: self.websub.renew_due(force=True), name="websub-subscribe", daemon=True).start()
        self.pipeline.start()
        if not self.rss_thread or not self.rss_thread.is_alive():
            self.rss_thread = threading.Thread(target=self._rss_loop, name="rss-scheduler", daemon=True)
            self.rss_thread.start()
        return not was_running

    def pause(self) -> None:
        self.started = False
        self.stop_event.set()
        self.websub.stop()
        self.pipeline.pause()
        if self.rss_thread:
            self.rss_thread.join(3)

    @property
    def is_running(self) -> bool:
        return bool(
            self.started
            and self.pipeline.thread
            and self.pipeline.thread.is_alive()
            and not self.pipeline.pause_event.is_set()
        )

    def shutdown(self) -> None:
        self.started = False
        self.stop_event.set()
        self.websub.stop()
        self.pipeline.stop()
        if self.rss_thread:
            self.rss_thread.join(3)

    def _rss_loop(self) -> None:
        next_rss = 0.0
        next_renew = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if self.settings.tracking_mode in {"websub", "both"} and now >= next_renew:
                self.websub.renew_due()
                next_renew = now + max(60, self.settings.websub_renew_interval_seconds)
            if self.settings.tracking_mode in {"rss", "both"} and self.settings.channel_ids and now >= next_rss:
                self.youtube.poll_rss()
                next_rss = now + max(60, self.settings.rss_interval_seconds)
            self.stop_event.wait(5)

    def restart_websub(self) -> None:
        if not self.started:
            return
        self.websub.stop()
        if self.settings.tracking_mode in {"websub", "both"}:
            self.websub.start()
            threading.Thread(target=lambda: self.websub.renew_due(force=True), name="websub-subscribe", daemon=True).start()

    def _validate_ready_to_start(self) -> None:
        with self.database.connect() as connection:
            has_channels = connection.execute("SELECT 1 FROM channels WHERE active=1 LIMIT 1").fetchone()
            has_routes = connection.execute("SELECT 1 FROM channel_routes WHERE active=1 LIMIT 1").fetchone()
        if not has_routes or not has_channels:
            raise RuntimeError("Chưa có layout/kênh YouTube. Hãy nhập cấu hình trong tab 'Luồng YouTube → TikTok', bấm lưu rồi mới bấm Bắt đầu.")
        if self.settings.tracking_mode == "websub" and not self.settings.public_domain:
            raise RuntimeError("Chế độ WebSub cần Public domain HTTPS. Hãy nhập domain hoặc đổi sang chế độ 'both'/'rss'.")


def configure_logging(settings: Settings) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(settings.log_dir / "mmo_app.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(threadName)s | %(name)s | %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(item, RotatingFileHandler) for item in root.handlers):
        root.addHandler(handler)
