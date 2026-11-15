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
        self.websub_scheduler_thread: threading.Thread | None = None
        self.websub_subscribe_thread: threading.Thread | None = None
        self._websub_thread_lock = threading.Lock()
        self.started = False

    def start(self) -> bool:
        was_running = self.is_running
        self._validate_ready_to_start()
        self.started = True
        self.stop_event.clear()
        self.websub.start()
        self.pipeline.start()
        if not self.websub_scheduler_thread or not self.websub_scheduler_thread.is_alive():
            self.websub_scheduler_thread = threading.Thread(
                target=self._websub_loop, name="websub-scheduler", daemon=True)
            self.websub_scheduler_thread.start()
        return not was_running

    def pause(self) -> None:
        self.started = False
        self.stop_event.set()
        self.websub.stop()
        self.pipeline.pause()
        if self.websub_scheduler_thread:
            self.websub_scheduler_thread.join(3)
        if self.websub_subscribe_thread:
            self.websub_subscribe_thread.join(3)

    @property
    def is_running(self) -> bool:
        alive = getattr(self.pipeline, "is_alive", None)
        if alive is None:
            alive = bool(
                getattr(self.pipeline, "thread", None)
                and self.pipeline.thread.is_alive())
        elif callable(alive):
            alive = alive()
        return bool(
            self.started and alive
            and not self.pipeline.pause_event.is_set()
        )

    def shutdown(self) -> None:
        self.started = False
        self.stop_event.set()
        self.websub.stop()
        self.pipeline.stop()
        if self.websub_scheduler_thread:
            self.websub_scheduler_thread.join(3)
        if self.websub_subscribe_thread:
            self.websub_subscribe_thread.join(3)

    def _websub_loop(self) -> None:
        next_renew = 0.0
        force = True
        while not self.stop_event.is_set():
            if time.monotonic() >= next_renew:
                try:
                    self.websub.renew_due(force=force)
                except Exception:
                    logging.getLogger(__name__).exception("WebSub renew failed")
                force = False
                next_renew = time.monotonic() + max(60, self.settings.websub_renew_interval_seconds)
            self.stop_event.wait(5)

    def restart_websub(self) -> None:
        if not self.started:
            return
        self.websub.stop()
        self.websub.start()
        self._start_websub_subscribe_thread(force=True)

    def refresh_websub_subscriptions(self, force: bool = False) -> None:
        """Subscribe newly added/reactivated channels without restarting the app."""
        if self.started:
            self._start_websub_subscribe_thread(force=force)

    def _start_websub_subscribe_thread(self, force: bool = False) -> None:
        with self._websub_thread_lock:
            if self.websub_subscribe_thread and self.websub_subscribe_thread.is_alive():
                return
            thread = threading.Thread(
                target=lambda: self.websub.renew_due(force=force),
                name="websub-subscribe",
                daemon=True,
            )
            self.websub_subscribe_thread = thread
            thread.start()

    def _validate_ready_to_start(self) -> None:
        with self.database.connect() as connection:
            has_channels = connection.execute("SELECT 1 FROM channels WHERE active=1 LIMIT 1").fetchone()
            has_routes = connection.execute("SELECT 1 FROM channel_routes WHERE active=1 LIMIT 1").fetchone()
        if not has_routes or not has_channels:
            raise RuntimeError("Chưa có layout/kênh YouTube. Hãy nhập cấu hình trong tab 'Luồng YouTube → TikTok', bấm lưu rồi mới bấm Bắt đầu.")
        if not self.settings.public_domain:
            raise RuntimeError("WebSub cần Public domain HTTPS. Hãy nhập domain trong Cài đặt.")


def configure_logging(settings: Settings) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(settings.log_dir / "mmo_app.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(threadName)s | %(name)s | %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(item, RotatingFileHandler) for item in root.handlers):
        root.addHandler(handler)
