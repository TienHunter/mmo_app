from __future__ import annotations

import uuid

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
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


class AccountDialog(QDialog):
    """Collect one TikTok account, including its processing and posting rules."""

    def __init__(self, account: dict | None = None, defaults: dict | None = None, parent=None):
        super().__init__(parent)
        defaults = defaults or {}
        self._account = account or {}
        self.setWindowTitle("Sửa tài khoản TikTok" if account else "Thêm tài khoản TikTok")
        self.setMinimumSize(620, 680)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        intro = QLabel(
            "Mỗi tài khoản TikTok có cấu hình xử lý video và đăng bài riêng. "
            "Các ô đã được điền sẵn giá trị mặc định từ Cài đặt chung."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 8, 0)
        body_layout.setSpacing(14)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        def section(title: str) -> QFormLayout:
            card = QFrame()
            card.setObjectName("card")
            box = QVBoxLayout(card)
            box.setContentsMargins(16, 14, 16, 14)
            box.addWidget(QLabel(title, objectName="pageTitle"))
            form = QFormLayout()
            form.setSpacing(10)
            box.addLayout(form)
            body_layout.addWidget(card)
            return form

        account_form = section("Tài khoản")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Ví dụ: TikTok Review Phim")
        account_form.addRow("Tên kênh TikTok", self.name)

        self.gpm_profile_id = QLineEdit()
        self.gpm_profile_id.setPlaceholderText("ID profile trong GPM Login")
        account_form.addRow("GPM Profile ID", self.gpm_profile_id)

        self.hashtags = QLineEdit()
        self.hashtags.setPlaceholderText("#review #viral")
        account_form.addRow("Hashtag caption", self.hashtags)

        self.caption_mode = QComboBox()
        self.caption_mode.addItem("Theo Cài đặt chung", "")
        self.caption_mode.addItem("Caption cố định", "fixed")
        self.caption_mode.addItem("Sinh caption bằng AI", "ai")
        account_form.addRow("Nguồn caption", self.caption_mode)
        caption_hint = QLabel(
            "Caption cố định và cấu hình AI dùng chung trong Cài đặt. "
            "Chọn \"Theo Cài đặt chung\" để dùng nguồn caption mặc định."
        )
        caption_hint.setWordWrap(True)
        caption_hint.setObjectName("muted")
        account_form.addRow("", caption_hint)

        self.daily_limit = QSpinBox()
        self.daily_limit.setRange(0, 999)
        self.daily_limit.setValue(3)
        self.daily_limit.setToolTip("0 nghĩa là không giới hạn số video đăng mỗi ngày.")
        account_form.addRow("Giới hạn đăng/ngày", self.daily_limit)

        processing_form = section("Xử lý video")
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.25, 4.0)
        self.speed.setSingleStep(0.05)
        self.speed.setDecimals(2)
        self.speed.setValue(float(defaults.get("speed") or 1.0))
        self.speed.setSuffix("x")
        processing_form.addRow("Tốc độ video", self.speed)

        zoom_row = QWidget()
        zoom_layout = QHBoxLayout(zoom_row)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        self.zoom_width = QSpinBox()
        self.zoom_width.setRange(0, 300)
        self.zoom_width.setValue(int(defaults.get("zoom_width_percent") or 100))
        self.zoom_width.setSuffix(" %")
        self.zoom_height = QSpinBox()
        self.zoom_height.setRange(0, 300)
        self.zoom_height.setValue(int(defaults.get("zoom_height_percent") or 100))
        self.zoom_height.setSuffix(" %")
        zoom_layout.addWidget(QLabel("Rộng"))
        zoom_layout.addWidget(self.zoom_width)
        zoom_layout.addWidget(QLabel("Cao"))
        zoom_layout.addWidget(self.zoom_height)
        zoom_layout.addStretch()
        processing_form.addRow("Zoom", zoom_row)

        self.split_enabled = QCheckBox("Cắt video thành nhiều phần theo mốc thời lượng")
        self.split_enabled.setChecked(bool(defaults.get("split_enabled", True)))
        processing_form.addRow("", self.split_enabled)

        self.overlay_enabled = QCheckBox("Chèn caption (title + Phần) lên video")
        self.overlay_enabled.setChecked(bool(defaults.get("overlay_enabled", False)))
        processing_form.addRow("", self.overlay_enabled)

        self.background_path = QLineEdit()
        default_background = str(defaults.get("background_path") or "")
        self.background_path.setPlaceholderText(
            default_background or "Để trống để dùng nền đen")
        processing_form.addRow(
            "Video/ảnh nền", self._path_row(self.background_path, "media"))

        posting_form = section("Đăng TikTok")
        self.publish_enabled = QCheckBox("Cho phép app bấm Đăng cho tài khoản này")
        self.publish_enabled.setChecked(bool(defaults.get("publish_enabled", False)))
        posting_form.addRow("", self.publish_enabled)

        storage_form = section("Lưu trữ & thông báo")
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText(
            "Để trống để dùng thư mục chung"
            + (f": {defaults['output_dir']}" if defaults.get("output_dir") else ""))
        storage_form.addRow(
            "Thư mục video đã cắt", self._path_row(self.output_dir, "dir"))

        self.notification_sound = QLineEdit()
        self.notification_sound.setPlaceholderText("Để trống để dùng âm thanh mặc định của Windows")
        self.notification_sound.setToolTip("Hỗ trợ WAV và MP3; chuông lặp theo thời gian cấu hình ở Cài đặt chung và có nút tắt.")
        storage_form.addRow(
            "Âm thanh chuông", self._path_row(self.notification_sound, "sound"))

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if account:
            self.name.setText(account.get("name") or "")
            self.gpm_profile_id.setText(account.get("gpm_profile_id") or "")
            self.hashtags.setText(account.get("hashtags") or "")
            self.daily_limit.setValue(int(account.get("daily_limit") or 0))
            self.speed.setValue(float(account.get("speed") or 1.0))
            self.zoom_width.setValue(int(account.get("zoom_width_percent") or 100))
            self.zoom_height.setValue(int(account.get("zoom_height_percent") or 100))
            split_enabled = account.get("split_enabled")
            self.split_enabled.setChecked(
                bool(split_enabled) if split_enabled is not None
                else bool(defaults.get("split_enabled", True)))
            overlay_enabled = account.get("overlay_enabled")
            self.overlay_enabled.setChecked(
                bool(overlay_enabled) if overlay_enabled is not None
                else bool(defaults.get("overlay_enabled", False)))
            self.background_path.setText(account.get("background_path") or "")
            publish_enabled = account.get("publish_enabled")
            self.publish_enabled.setChecked(
                bool(publish_enabled) if publish_enabled is not None
                else bool(defaults.get("publish_enabled", False)))
            self.caption_mode.setCurrentIndex(
                max(0, self.caption_mode.findData(account.get("caption_mode") or "")))
            self.output_dir.setText(account.get("output_dir") or "")
            self.notification_sound.setText(account.get("notification_sound") or "")

        # Add/duplicate starts with a fresh GPM profile id; edit keeps the saved one.
        if not self.gpm_profile_id.text().strip():
            self.gpm_profile_id.setText(str(uuid.uuid4()))

    def _path_row(self, edit: QLineEdit, kind: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        browse = QPushButton("Chọn…")
        browse.clicked.connect(lambda _=False: self._browse(edit, kind))
        layout.addWidget(browse)
        return row

    def _browse(self, edit: QLineEdit, kind: str) -> None:
        if kind == "sound":
            path, _ = QFileDialog.getOpenFileName(
                self, "Chọn âm thanh chuông", edit.text(),
                "Âm thanh (*.wav *.mp3);;Tất cả file (*.*)")
        elif kind == "media":
            path, _ = QFileDialog.getOpenFileName(
                self, "Chọn video/ảnh nền", edit.text(),
                "Video/Ảnh (*.mp4 *.mov *.mkv *.webm *.jpg *.jpeg *.png *.webp *.bmp);;Tất cả file (*.*)")
        else:
            path = QFileDialog.getExistingDirectory(self, "Chọn thư mục", edit.text())
        if path:
            edit.setText(path)

    def _validate_and_accept(self) -> None:
        name = self.name.text().strip()
        if not name:
            self.name.setFocus()
            return
        if not self.gpm_profile_id.text().strip():
            self.gpm_profile_id.setFocus()
            return
        self.accept()

    def values(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "mode": "gpm",
            "endpoint": "",
            "gpm_profile_id": self.gpm_profile_id.text().strip(),
            "hashtags": self.hashtags.text().strip(),
            "daily_limit": self.daily_limit.value(),
            "speed": self.speed.value(),
            "zoom_width_percent": self.zoom_width.value(),
            "zoom_height_percent": self.zoom_height.value(),
            "split_enabled": self.split_enabled.isChecked(),
            "overlay_enabled": self.overlay_enabled.isChecked(),
            "background_path": self.background_path.text().strip(),
            "publish_enabled": self.publish_enabled.isChecked(),
            "caption_mode": self.caption_mode.currentData() or "",
            "caption_text": str(self._account.get("caption_text") or ""),
            "ai_api_url": str(self._account.get("ai_api_url") or ""),
            "ai_api_key": str(self._account.get("ai_api_key") or ""),
            "ai_model": str(self._account.get("ai_model") or ""),
            "ai_system_prompt": str(self._account.get("ai_system_prompt") or ""),
            "output_dir": self.output_dir.text().strip(),
            "notification_sound": self.notification_sound.text().strip(),
        }
