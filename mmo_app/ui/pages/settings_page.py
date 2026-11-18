from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...application import Application
from ..components import show_toast


_DEFAULT_SPLIT_THRESHOLDS = (300, 480, 900, 1080)


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
        header_layout.addLayout(words, 1)
        header_layout.addStretch()
        save = QPushButton("Lưu cài đặt")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save)
        header_layout.addWidget(save)
        layout.addWidget(header)

        layout.addWidget(self._build_general())
        layout.addWidget(self._build_split())
        layout.addWidget(self._build_tracking())
        layout.addWidget(self._build_caption())
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
        self.notification_bell_seconds = QSpinBox()
        self.notification_bell_seconds.setRange(1, 300)
        self.notification_bell_seconds.setSuffix(" giây")
        self.notification_bell_seconds.setToolTip(
            "Chuông lặp trong khoảng thời gian này rồi tự tắt; nút Tắt chuông vẫn dừng ngay.")
        form.addRow("Thời gian chuông", self.notification_bell_seconds)
        form.addRow("Cách nhận video mới", QLabel("WebSub (YouTube Push)"))
        return card

    def _build_split(self) -> QFrame:
        card, form = self._section("Cắt video tự động")
        self.split_thresholds: list[QSpinBox] = []
        for index, default in enumerate(_DEFAULT_SPLIT_THRESHOLDS):
            box = QSpinBox()
            box.setRange(10, 21600)
            box.setSingleStep(30)
            box.setSuffix(" giây")
            box.setValue(default)
            self.split_thresholds.append(box)
            form.addRow(f"Chia {index + 2} phần từ", box)
        reset = QPushButton("Mặc định")
        reset.setToolTip("Đặt lại 4 mốc về 300 / 480 / 900 / 1080 giây")
        reset.clicked.connect(self._reset_split_thresholds)
        form.addRow("", reset)
        hint = QLabel(
            "Mốc tính trên thời lượng sau khi áp tốc độ của tài khoản "
            "(thời lượng gốc ÷ speed). Mặc định 300/480/900/1080 giây: dưới 300s "
            "thành 1 phần, từ 300s thành 2, từ 480s thành 3, từ 900s thành 4, "
            "từ 1080s thành 5 phần. Tắt chia phần trong cấu hình tài khoản thì "
            "luôn còn 1 phần."
        )
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        form.addRow("", hint)
        return card

    def _reset_split_thresholds(self) -> None:
        for box, value in zip(self.split_thresholds, _DEFAULT_SPLIT_THRESHOLDS):
            box.setValue(value)

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
        self.youtube_cookies = QLineEdit()
        self.youtube_cookies.setPlaceholderText("Đường dẫn youtube_cookies.txt (định dạng Netscape)")
        form.addRow("File cookie YouTube", self._file_row(self.youtube_cookies))
        cookie_hint = QLabel(
            "Dùng khi YouTube báo 'Sign in to confirm you're not a bot'. "
            "Để trống nếu không cần cookie."
        )
        cookie_hint.setWordWrap(True)
        cookie_hint.setObjectName("muted")
        form.addRow("", cookie_hint)
        self.download_quality = QComboBox()
        self.download_quality.addItem("Tốt nhất (mọi độ phân giải, file lớn nhất)", "best")
        self.download_quality.addItem("1080p AV1 (nhỏ nhất)", "av1_1080")
        self.download_quality.addItem("1080p H.264 (tương thích nhất)", "h264_1080")
        self.download_quality.addItem("720p AV1 (nhẹ nhất)", "av1_720")
        form.addRow("Chất lượng tải video", self.download_quality)
        quality_hint = QLabel(
            "Video luôn được xử lý về 1080×1920. AV1 cho file nhỏ nhất, H.264 "
            "tương thích rộng, Tốt nhất có thể tải 1440p/4K và file rất lớn."
        )
        quality_hint.setWordWrap(True)
        quality_hint.setObjectName("muted")
        form.addRow("", quality_hint)
        return card

    def _build_caption(self) -> QFrame:
        card, form = self._section("Caption mặc định")
        self.caption_mode = QComboBox()
        self.caption_mode.addItem("Caption cố định", "fixed")
        self.caption_mode.addItem("Sinh caption bằng AI", "ai")
        self.caption_mode.currentIndexChanged.connect(self._sync_caption_fields)
        form.addRow("Nguồn caption", self.caption_mode)
        self.caption_text = QPlainTextEdit()
        self.caption_text.setPlaceholderText(
            "Caption cố định, có thể nhiều dòng và kèm hashtag.")
        self.caption_text.setFixedHeight(80)
        form.addRow("Caption cố định", self.caption_text)
        self.ai_api_url = QLineEdit()
        self.ai_api_url.setPlaceholderText("https://api.deepseek.com/chat/completions")
        form.addRow("AI API URL", self.ai_api_url)
        self.ai_api_key = QLineEdit()
        self.ai_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_api_key.setPlaceholderText("API key dùng chung cho các tài khoản")
        form.addRow("AI API Key", self.ai_api_key)
        self.ai_model = QLineEdit()
        self.ai_model.setPlaceholderText("deepseek-chat")
        form.addRow("AI Model", self.ai_model)
        self.ai_system_prompt = QPlainTextEdit()
        self.ai_system_prompt.setPlaceholderText(
            "Bạn là trợ lý viết caption TikTok. Trả về caption hoàn chỉnh, có thể kèm hashtag.")
        self.ai_system_prompt.setFixedHeight(80)
        form.addRow("AI System prompt", self.ai_system_prompt)
        hint = QLabel(
            "Tài khoản TikTok chọn nguồn caption; caption cố định và cấu hình AI "
            "dùng chung ở đây. Tài khoản để trống nguồn caption sẽ theo mặc định này."
        )
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        form.addRow("", hint)
        return card

    def _sync_caption_fields(self) -> None:
        is_fixed = self.caption_mode.currentData() == "fixed"
        self.caption_text.setEnabled(is_fixed)
        for widget in (self.ai_api_url, self.ai_api_key, self.ai_model, self.ai_system_prompt):
            widget.setEnabled(not is_fixed)

    def _build_system(self) -> QFrame:
        card, form = self._section("Hệ thống")
        self.video_encoder = QComboBox()
        self.video_encoder.addItem("Tự động (GPU nếu có, fallback CPU)", "auto")
        self.video_encoder.addItem("NVIDIA — CUDA CQ23 (chất lượng cao)", "cuda")
        self.video_encoder.addItem("NVIDIA — CUDA CQ27 (file nhỏ hơn)", "cuda_cq27")
        self.video_encoder.addItem("NVIDIA — CUDA CQ27 + max 2.5M (nhỏ nhất)", "cuda_cq27_max")
        self.video_encoder.addItem("CPU — libx264", "cpu")
        self.video_encoder.addItem("Intel — Quick Sync (QSV)", "intel")
        self.video_encoder.addItem("AMD — AMF", "amd")
        form.addRow("Bộ mã hóa video", self.video_encoder)
        encoder_hint = QLabel(
            "3 mức CUDA dùng cùng pipeline GPU, khác CQ/bitrate: CQ23 đẹp nhất, "
            "CQ27 nhỏ hơn, CQ27 + max 2.5 Mbps nhỏ nhất cho upload chậm. "
            "GPU lỗi sẽ tự chạy lại bằng CPU."
        )
        encoder_hint.setWordWrap(True)
        encoder_hint.setObjectName("muted")
        form.addRow("", encoder_hint)
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
        self.failed_job_policy = QComboBox()
        self.failed_job_policy.addItem(
            "Tự thử lại job lỗi (tối đa 3 lần)", "retry")
        self.failed_job_policy.addItem(
            "Dừng job, chờ Thử lại/Bỏ qua", "manual")
        self.failed_job_policy.addItem(
            "Tự hủy job lỗi, chạy job tiếp theo", "skip")
        self.failed_job_policy.setToolTip(
            "Áp dụng khi job lỗi (ví dụ ffmpeg trả mã lỗi). Job lỗi luôn rời "
            "worker ngay; các job khác vẫn tiếp tục chạy.")
        form.addRow("Khi job lỗi", self.failed_job_policy)
        hint = QLabel(
            "Cấu hình xử lý video và đăng TikTok (tốc độ, zoom, cắt phần, "
            "quyền đăng, output, chuông) được quản lý trong từng tài khoản TikTok. "
            "Caption cố định và AI nằm ở mục Caption mặc định phía trên."
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

    def _file_row(self, edit: QLineEdit) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        browse = QPushButton("Chọn file…")
        browse.clicked.connect(lambda _=False, e=edit: self._browse_file(e))
        layout.addWidget(browse)
        return row

    def _browse(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục", edit.text())
        if path:
            edit.setText(path)

    def _browse_file(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file cookie YouTube", edit.text(), "Cookie (*.txt);;Tất cả (*)")
        if path:
            edit.setText(path)

    def _load(self) -> None:
        s = self.application.settings
        self.download_dir.setText(s.download_dir)
        self.output_dir.setText(s.output_dir)
        self.notification_bell_seconds.setValue(int(s.notification_bell_seconds))
        thresholds = list(getattr(s, "split_threshold_seconds", None) or [])
        for index, box in enumerate(self.split_thresholds):
            if index < len(thresholds):
                box.setValue(int(thresholds[index]))
        self.public_domain.setText(s.public_domain)
        self.webhook_host.setText(s.webhook_host)
        self.webhook_port.setValue(s.webhook_port)
        self.youtube_cookies.setText(s.youtube_cookies_path)
        self.download_quality.setCurrentIndex(
            max(0, self.download_quality.findData(s.download_quality)))
        self.caption_mode.setCurrentIndex(max(0, self.caption_mode.findData(s.caption_mode)))
        self.caption_text.setPlainText(s.caption_text)
        self.ai_api_url.setText(s.ai_api_url)
        self.ai_api_key.setText(s.ai_api_key)
        self.ai_model.setText(s.ai_model)
        self.ai_system_prompt.setPlainText(s.ai_system_prompt)
        self._sync_caption_fields()
        self.video_encoder.setCurrentIndex(max(0, self.video_encoder.findData(s.video_encoder)))
        self.gpm_api_url.setText(s.gpm_api_url)
        self.gpm_reclaim.setChecked(bool(s.gpm_reclaim_profile_in_use))
        self.max_download_workers.setValue(int(s.max_download_workers))
        self.max_process_workers.setValue(int(s.max_process_workers))
        self.max_upload_workers.setValue(s.max_upload_workers)
        self.failed_job_policy.setCurrentIndex(max(
            0, self.failed_job_policy.findData(s.failed_job_policy)))

    def _save(self) -> None:
        s = self.application.settings
        cookies_path = self.youtube_cookies.text().strip()
        if cookies_path and not Path(cookies_path).is_file():
            show_toast(self, "Không tìm thấy file cookie YouTube.", "error")
            return
        websub_changed = (
            s.public_domain != self.public_domain.text().strip()
            or s.webhook_host != self.webhook_host.text().strip()
            or s.webhook_port != self.webhook_port.value()
        )
        s.download_dir = self.download_dir.text().strip()
        s.output_dir = self.output_dir.text().strip()
        s.notification_bell_seconds = self.notification_bell_seconds.value()
        s.split_threshold_seconds = [box.value() for box in self.split_thresholds]
        s.public_domain = self.public_domain.text().strip().rstrip("/")
        s.webhook_host = self.webhook_host.text().strip() or "127.0.0.1"
        s.webhook_port = self.webhook_port.value()
        s.youtube_cookies_path = cookies_path
        s.download_quality = self.download_quality.currentData() or "best"
        s.caption_mode = self.caption_mode.currentData() or "fixed"
        s.caption_text = self.caption_text.toPlainText().strip()
        s.ai_api_url = self.ai_api_url.text().strip()
        s.ai_api_key = self.ai_api_key.text().strip()
        s.ai_model = self.ai_model.text().strip()
        s.ai_system_prompt = self.ai_system_prompt.toPlainText().strip()
        s.video_encoder = self.video_encoder.currentData()
        s.gpm_api_url = self.gpm_api_url.text().strip() or "http://127.0.0.1:9495/api/v1"
        s.gpm_reclaim_profile_in_use = self.gpm_reclaim.isChecked()
        s.max_download_workers = self.max_download_workers.value()
        s.max_process_workers = self.max_process_workers.value()
        s.max_upload_workers = self.max_upload_workers.value()
        s.failed_job_policy = self.failed_job_policy.currentData() or "retry"
        s.save()
        if websub_changed and self.application.started:
            self.application.restart_websub()
        show_toast(self, "Đã lưu cài đặt.", "success")
