from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)


class _WorkerSignals(QObject):
    resolved = Signal(str, str, str)
    failed = Signal(str)


class _ResolveWorker(QRunnable):
    def __init__(self, resolver: Callable[[str], tuple[str, str, str]], url: str):
        super().__init__()
        self.resolver = resolver
        self.url = url
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            channel_id, title, normalized_url = self.resolver(self.url)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        else:
            self.signals.resolved.emit(channel_id, title, normalized_url)


class ChannelDialog(QDialog):
    """Paste a YouTube channel URL, resolve it, and link it to a TikTok account."""

    def __init__(self, accounts: list[dict], resolver: Callable[[str], tuple[str, str, str]], parent=None):
        super().__init__(parent)
        self.resolver = resolver
        self._account_id: int | None = None
        self._segment_seconds = None
        self.setWindowTitle("Thêm kênh YouTube")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        intro = QLabel(
            "Dán link kênh YouTube (dạng @tên hoặc /channel/UC...). "
            "App sẽ tự tìm Channel ID và gán kênh này vào tài khoản TikTok bạn chọn."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(10)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://www.youtube.com/@tien5140")
        form.addRow("Link kênh YouTube", self.url)

        self.account = QComboBox()
        for account in accounts:
            self.account.addItem(account["name"], int(account["id"]))
        form.addRow("Tài khoản TikTok đăng", self.account)
        layout.addLayout(form)

        self.status = QLabel("")
        self.status.setObjectName("muted")
        layout.addWidget(self.status)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._submit)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        if not accounts:
            self.status.setText("Cần tạo ít nhất một tài khoản TikTok trước khi thêm kênh.")
            self.buttons.button(QDialogButtonBox.Save).setEnabled(False)

    @Slot()
    def _submit(self) -> None:
        url = self.url.text().strip()
        if not url:
            self.url.setFocus()
            return
        if self.account.count() == 0:
            return
        self._account_id = int(self.account.currentData())
        self._segment_seconds = None
        self.status.setText("Đang lấy thông tin kênh…")
        self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        worker = _ResolveWorker(self.resolver, url)
        worker.signals.resolved.connect(self._on_resolved)
        worker.signals.failed.connect(self._on_failed)
        QThreadPool.globalInstance().start(worker)

    @Slot(str, str, str)
    def _on_resolved(self, channel_id: str, title: str, normalized_url: str) -> None:
        self._result = (channel_id, title, normalized_url)
        self.accept()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.status.setText(message)
        self.buttons.button(QDialogButtonBox.Save).setEnabled(True)

    def result_values(self) -> dict:
        channel_id, title, normalized_url = self._result
        return {
            "channel_id": channel_id,
            "title": title,
            "source_url": normalized_url,
            "account_id": self._account_id,
            "segment_seconds": self._segment_seconds,
        }
