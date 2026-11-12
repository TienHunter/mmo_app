from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...application import Application
from ..components import show_toast


class SettingsPage(QWidget):
    def __init__(self, application: Application):
        super().__init__()
        self.application = application
        self._build()
        self._load()

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
        title = QLabel("Cài đặt")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Tuỳ chọn chung cho theo dõi và hệ thống. Cấu hình xử lý video/đăng TikTok "
            "nằm trong từng tài khoản TikTok.")
        subtitle.setObjectName("pageSubtitle")
        words.addWidget(title)
        words.addWidget(subtitle)
        header_layout.addLayout(words)
        header_layout.addStretch()
        save = QPushButton("Lưu cài đặt")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save)
        header_layout.addWidget(save)
        layout.addWidget(header)

        layout.addWidget(self._build_general())
        layout.addWidget(self._build_tracking())
        layout.addWidget(self._build_system())
        layout.addStretch()

    def _section(self, title: str) -> tuple[QFrame, QFormLayout]:
        card = QFrame()
        card.setObjectName("card")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 18, 20, 18)
        box.addWidget(QLabel(title, objectName="pageTitle"))
        form = QFormLayout()
        form.setSpacing(10)
        box.addLayout(form)
        return card, form

    def _build_general(self) -> QFrame:
        card, form = self._section("Chung")
        self.download_dir = QLineEdit()
        download_row = self._path_row(self.download_dir)
        form.addRow("Thư mục tải video", download_row)
        self.output_dir = QLineEdit()
        output_row = self._path_row(self.output_dir)
        form.addRow("Thư mục video mặc định", output_row)
        self.tracking_mode = QComboBox()
        self.tracking_mode.addItem("WebSub + RSS dự phòng", "both")
        self.tracking_mode.addItem("Chỉ WebSub", "websub")
        self.tracking_mode.addItem("Chỉ RSS", "rss")
        form.addRow("Cách nhận video mới", self.tracking_mode)
        return card

    def _build_tracking(self) -> QFrame:
        card, form = self._section("Theo dõi YouTube")
        self.public_domain = QLineEdit()
        self.public_domain.setPlaceholderText("https://mmo-webhook.example.com")
        form.addRow("Domain công khai (WebSub)", self.public_domain)
        self.webhook_host = QLineEdit()
        form.addRow("WebSub host", self.webhook_host)
        self.webhook_port = QSpinBox()
        self.webhook_port.setRange(1, 65535)
        form.addRow("WebSub port", self.webhook_port)
        self.rss_interval = QSpinBox()
        self.rss_interval.setRange(60, 86400)
        self.rss_interval.setSuffix(" giây")
        form.addRow("Chu kỳ RSS dự phòng", self.rss_interval)
        return card

    def _build_system(self) -> QFrame:
        card, form = self._section("Hệ thống")
        self.gpm_api_url = QLineEdit()
        form.addRow("GPM API URL", self.gpm_api_url)
        self.gpm_reclaim = QCheckBox("Tự đóng/mở lại profile GPM khi đang bận")
        form.addRow("", self.gpm_reclaim)
        self.max_download_workers = QSpinBox()
        self.max_download_workers.setRange(1, 10)
        form.addRow("Số video tải đồng thời", self.max_download_workers)
        self.max_process_workers = QSpinBox()
        self.max_process_workers.setRange(1, 10)
        form.addRow("Số video xử lý đồng thời", self.max_process_workers)
        self.max_upload_workers = QSpinBox()
        self.max_upload_workers.setRange(1, 10)
        form.addRow("Số tài khoản đăng đồng thời", self.max_upload_workers)
        hint = QLabel(
            "Cấu hình xử lý video và đăng TikTok (tốc độ, zoom, cắt phần, caption, "
            "AI, quyền đăng, output, chuông) được quản lý trong từng tài khoản TikTok."
        )
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        form.addRow("", hint)
        return card

    def _path_row(self, edit: QLineEdit) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        browse = QPushButton("Chọn…")
        browse.clicked.connect(lambda _=False, e=edit: self._browse(e))
        layout.addWidget(browse)
        return row

    def _browse(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục", edit.text())
        if path:
            edit.setText(path)

    def _load(self) -> None:
        s = self.application.settings
        self.download_dir.setText(s.download_dir)
        self.output_dir.setText(s.output_dir)
        self.tracking_mode.setCurrentIndex(max(0, self.tracking_mode.findData(s.tracking_mode)))
        self.public_domain.setText(s.public_domain)
        self.webhook_host.setText(s.webhook_host)
        self.webhook_port.setValue(s.webhook_port)
        self.rss_interval.setValue(s.rss_interval_seconds)
        self.gpm_api_url.setText(s.gpm_api_url)
        self.gpm_reclaim.setChecked(bool(s.gpm_reclaim_profile_in_use))
        self.max_download_workers.setValue(int(s.max_download_workers))
        self.max_process_workers.setValue(int(s.max_process_workers))
        self.max_upload_workers.setValue(s.max_upload_workers)

    def _save(self) -> None:
        s = self.application.settings
        websub_changed = (
            s.tracking_mode != self.tracking_mode.currentData()
            or s.public_domain != self.public_domain.text().strip()
            or s.webhook_host != self.webhook_host.text().strip()
            or s.webhook_port != self.webhook_port.value()
        )
        s.download_dir = self.download_dir.text().strip()
        s.output_dir = self.output_dir.text().strip()
        s.tracking_mode = self.tracking_mode.currentData()
        s.public_domain = self.public_domain.text().strip().rstrip("/")
        s.webhook_host = self.webhook_host.text().strip() or "127.0.0.1"
        s.webhook_port = self.webhook_port.value()
        s.rss_interval_seconds = self.rss_interval.value()
        s.gpm_api_url = self.gpm_api_url.text().strip() or "http://127.0.0.1:9495/api/v1"
        s.gpm_reclaim_profile_in_use = self.gpm_reclaim.isChecked()
        s.max_download_workers = self.max_download_workers.value()
        s.max_process_workers = self.max_process_workers.value()
        s.max_upload_workers = self.max_upload_workers.value()
        s.save()
        if websub_changed and self.application.started:
            self.application.restart_websub()
        show_toast(self, "Đã lưu cài đặt.", "success")
