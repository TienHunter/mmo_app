from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...application import Application
from ...services import YouTubeService, local_today
from ..components import EmptyState, StatusBadge, show_toast
from ..dialogs import AccountDialog
from ..services.repository import UIRepository


def _flag(value, fallback: bool) -> bool:
    return fallback if value is None else bool(value)


class _TestVideoSignals(QObject):
    finished = Signal(int, str)
    failed = Signal(str)


class _TestVideoWorker(QRunnable):
    def __init__(self, youtube: YouTubeService, url: str, account_id: int, account_name: str):
        super().__init__()
        self.youtube = youtube
        self.url = url
        self.account_id = account_id
        self.account_name = account_name
        self.signals = _TestVideoSignals()

    def run(self) -> None:
        try:
            media_id = self.youtube.queue_test_video(self.url, self.account_id)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        else:
            self.signals.finished.emit(media_id, self.account_name)


class AccountsPage(QWidget):
    def __init__(self, application: Application):
        super().__init__()
        self.application = application
        self.repository = UIRepository(application.database)
        self._test_workers: list[_TestVideoWorker] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        scroll.setWidget(body)

        header = QFrame()
        header.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        words = QVBoxLayout()
        title = QLabel("Tài khoản TikTok")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Mỗi kênh TikTok có riêng profile, cấu hình xử lý video và quy tắc đăng bài.")
        subtitle.setObjectName("pageSubtitle")
        words.addWidget(title)
        words.addWidget(subtitle)
        header_layout.addLayout(words)
        header_layout.addStretch()
        add = QPushButton("Thêm tài khoản")
        add.setObjectName("primaryButton")
        add.clicked.connect(self._add_account)
        header_layout.addWidget(add)
        layout.addWidget(header)

        self.cards_layout = QVBoxLayout()
        self.cards_layout.setSpacing(12)
        layout.addLayout(self.cards_layout)
        layout.addStretch()

    def refresh(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        accounts = self.repository.list_accounts()
        if not accounts:
            self.cards_layout.addWidget(
                EmptyState(
                    "Chưa có tài khoản TikTok",
                    "Thêm tài khoản đầu tiên để app biết đăng video vào kênh nào.",
                    action_text="Thêm tài khoản",
                )
            )
            self.cards_layout.itemAt(self.cards_layout.count() - 1).widget().findChild(QPushButton).clicked.connect(
                self._add_account
            )
            return
        for account in accounts:
            self.cards_layout.addWidget(self._account_card(account))

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.refresh()

    def _account_card(self, account: dict) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        name = QLabel(account["name"] or "Chưa đặt tên")
        name.setStyleSheet("font-size: 12pt; font-weight: 700;")
        top.addWidget(name)
        top.addStretch()
        active = bool(account["active"])
        top.addWidget(StatusBadge("Đang bật" if active else "Đã tạm dừng", "good" if active else "neutral"))
        layout.addLayout(top)

        posts_today = (
            int(account["posts_today"] or 0)
            if account.get("counter_date") == local_today() else 0
        )
        detail = QLabel(
            f"GPM Profile: {account['gpm_profile_id'] or 'Chưa đặt'}  ·  "
            f"Đã đăng hôm nay: {posts_today}/{account['daily_limit'] or '∞'}"
        )
        detail.setObjectName("muted")
        layout.addWidget(detail)

        processing = QLabel(
            f"Tốc độ {float(account.get('speed') or 1.0):g}x  ·  "
            f"Zoom {account.get('zoom_width_percent', 100)}×{account.get('zoom_height_percent', 100)}%  ·  "
            f"Caption {'AI' if (account.get('caption_mode') or 'fixed') == 'ai' else 'cố định'}  ·  "
            f"Output: {account.get('output_dir') or 'thư mục chung'}"
        )
        processing.setObjectName("muted")
        layout.addWidget(processing)

        flags = QLabel(
            f"Cắt phần: {'Bật' if _flag(account.get('split_enabled'), True) else 'Tắt'}  ·  "
            f"Caption trên video: {'Bật' if _flag(account.get('overlay_enabled'), False) else 'Tắt'}  ·  "
            f"Quyền đăng: {'Bật' if _flag(account.get('publish_enabled'), False) else 'Tắt'}"
        )
        flags.setObjectName("muted")
        layout.addWidget(flags)

        if account["hashtags"] or account.get("caption_text"):
            caption = QLabel(account.get("caption_text") or account["hashtags"])
            caption.setObjectName("muted")
            layout.addWidget(caption)

        controls = QHBoxLayout()
        controls.addStretch()
        test = QPushButton("Test video")
        test.setToolTip("Nhập link YouTube để cắt thử vào đúng thư mục của tài khoản này.")
        test.clicked.connect(lambda _=False, a=account: self._test_video(a))
        controls.addWidget(test)
        edit = QPushButton("Sửa")
        edit.clicked.connect(lambda _=False, a=account: self._edit_account(a))
        controls.addWidget(edit)
        toggle = QPushButton("Tạm dừng" if active else "Bật lại")
        toggle.clicked.connect(lambda _=False, a=account: self._toggle_account(a))
        controls.addWidget(toggle)
        layout.addLayout(controls)
        return card

    def _account_defaults(self) -> dict:
        settings = self.application.settings
        return {
            "speed": float(settings.video_speed or 1.0),
            "zoom_width_percent": int(settings.video_zoom_percent or 100),
            "zoom_height_percent": int(settings.video_zoom_percent or 100),
            "split_enabled": bool(settings.split_enabled),
            "overlay_enabled": bool(settings.overlay_text_enabled),
            "background_path": settings.video_background_path,
            "publish_enabled": bool(settings.tiktok_publish_enabled),
            "caption_mode": "ai" if (settings.hashtag_source or "fixed") == "ai" else "fixed",
            "ai_api_url": settings.ai_api_url,
            "ai_api_key": settings.ai_api_key,
            "ai_model": settings.ai_model,
            "ai_system_prompt": settings.ai_system_prompt,
            "output_dir": settings.output_dir,
        }

    def _add_account(self) -> None:
        dialog = AccountDialog(defaults=self._account_defaults(), parent=self)
        if dialog.exec() != AccountDialog.Accepted:
            return
        values = dialog.values()
        self.repository.add_account(
            values["name"],
            values["mode"],
            values["endpoint"],
            values["gpm_profile_id"],
            values["hashtags"],
            values["daily_limit"],
            values["speed"],
            values["zoom_width_percent"],
            values["zoom_height_percent"],
            values["caption_mode"],
            values["caption_text"],
            values["output_dir"],
            values["notification_sound"],
            values["split_enabled"],
            values["overlay_enabled"],
            values["background_path"],
            values["publish_enabled"],
            values["ai_api_url"],
            values["ai_api_key"],
            values["ai_model"],
            values["ai_system_prompt"],
        )
        self.refresh()
        show_toast(self, f"Đã thêm tài khoản {values['name']}.", "success")

    def _edit_account(self, account: dict) -> None:
        dialog = AccountDialog(account, self._account_defaults(), self)
        if dialog.exec() != AccountDialog.Accepted:
            return
        values = dialog.values()
        self.repository.update_account(
            int(account["id"]),
            values["name"],
            values["mode"],
            values["endpoint"],
            values["gpm_profile_id"],
            values["hashtags"],
            values["daily_limit"],
            values["speed"],
            values["zoom_width_percent"],
            values["zoom_height_percent"],
            values["caption_mode"],
            values["caption_text"],
            values["output_dir"],
            values["notification_sound"],
            values["split_enabled"],
            values["overlay_enabled"],
            values["background_path"],
            values["publish_enabled"],
            values["ai_api_url"],
            values["ai_api_key"],
            values["ai_model"],
            values["ai_system_prompt"],
        )
        self.refresh()
        show_toast(self, f"Đã cập nhật tài khoản {values['name']}.", "success")

    def _test_video(self, account: dict) -> None:
        url, accepted = QInputDialog.getText(
            self,
            f"Test video – {account['name']}",
            "Dán link video YouTube:",
        )
        if not accepted or not url.strip():
            return
        try:
            YouTubeService.video_id_from_url(url)
        except ValueError as exc:
            show_toast(self, str(exc), "warning")
            return
        worker = _TestVideoWorker(
            self.application.youtube, url.strip(), int(account["id"]),
            account["name"] or "")
        worker.signals.finished.connect(self._test_video_queued)
        worker.signals.failed.connect(self._test_video_failed)
        self._test_workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    @Slot(int, str)
    def _test_video_queued(self, _media_id: int, account_name: str) -> None:
        message = f"Đã thêm video test cho {account_name} vào hàng đợi."
        if not self.application.is_running:
            message += " Bấm Bắt đầu để pipeline xử lý."
        show_toast(self, message, "success")

    @Slot(str)
    def _test_video_failed(self, message: str) -> None:
        show_toast(self, message, "error")

    def _toggle_account(self, account: dict) -> None:
        self.repository.set_account_active(int(account["id"]), not bool(account["active"]))
        self.refresh()
