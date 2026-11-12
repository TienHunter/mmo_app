from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...application import Application
from ..components import CopyableTable, TablePager
from ..services.repository import UIRepository, VIDEO_STATUS_LABELS, format_local_time


class VideosPage(QWidget):
    _PROGRESS = {
        "discovered": 10,
        "downloaded": 40,
        "processed": 80,
        "uploaded": 100,
        "skipped": 0,
    }

    def __init__(self, application: Application):
        super().__init__()
        self.application = application
        self.repository = UIRepository(application.database)
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        header = QFrame()
        header.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        words = QVBoxLayout()
        title = QLabel("Thư viện video")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Xem tiến độ từng video qua các bước tải, xử lý và đăng.")
        subtitle.setObjectName("pageSubtitle")
        words.addWidget(title)
        words.addWidget(subtitle)
        header_layout.addLayout(words)
        header_layout.addStretch()
        refresh = QPushButton("Làm mới")
        refresh.clicked.connect(self.refresh)
        header_layout.addWidget(refresh)
        layout.addWidget(header)

        filters = QHBoxLayout()
        filters.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm theo tiêu đề hoặc ID video…")
        self.search.returnPressed.connect(self.refresh)
        filters.addWidget(self.search, 2)

        self.status = QComboBox()
        self.status.addItem("Tất cả trạng thái", "")
        for key, label in VIDEO_STATUS_LABELS.items():
            self.status.addItem(label, key)
        self.status.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.status, 1)

        self.account = QComboBox()
        self.account.addItem("Tất cả tài khoản TikTok", "")
        self.account.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.account, 1)

        self.channel = QComboBox()
        self.channel.addItem("Tất cả kênh", "")
        self.channel.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.channel, 1)

        search_button = QPushButton("Tìm")
        search_button.clicked.connect(self.refresh)
        filters.addWidget(search_button)
        layout.addLayout(filters)

        self.table = CopyableTable(0, 5)
        self.table.setHorizontalHeaderLabels(["Tiêu đề", "Kênh", "Trạng thái", "Tiến độ", "Cập nhật"])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        for index, width in enumerate((360, 180, 120, 90, 150)):
            self.table.setColumnWidth(index, width)
        layout.addWidget(self.table, 1)
        self.pager = TablePager()
        self.pager.page_changed.connect(self._render_page)
        layout.addWidget(self.pager)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._reload_accounts()
        self._reload_channels()
        self.refresh()

    def _reload_accounts(self) -> None:
        current = self.account.currentData()
        self.account.blockSignals(True)
        self.account.clear()
        self.account.addItem("Tất cả tài khoản TikTok", "")
        for account in self.repository.list_accounts():
            self.account.addItem(account["name"], int(account["id"]))
        index = self.account.findData(current)
        if index >= 0:
            self.account.setCurrentIndex(index)
        self.account.blockSignals(False)

    def _reload_channels(self) -> None:
        current = self.channel.currentData()
        self.channel.blockSignals(True)
        self.channel.clear()
        self.channel.addItem("Tất cả kênh", "")
        for channel in self.repository.list_channels():
            label = channel["title"] or channel["channel_id"]
            self.channel.addItem(label, channel["channel_id"])
        index = self.channel.findData(current)
        if index >= 0:
            self.channel.setCurrentIndex(index)
        self.channel.blockSignals(False)

    def refresh(self) -> None:
        self._all_rows = self.repository.list_videos(
            status=self.status.currentData() or None,
            channel_id=self.channel.currentData() or None,
            account_id=self.account.currentData() or None,
            search=self.search.text().strip() or None,
        )
        self.pager.reset()
        self.pager.set_total(len(self._all_rows))
        self._render_page()

    def _render_page(self) -> None:
        self.table.setRowCount(0)
        for video in self.pager.page_rows(getattr(self, "_all_rows", [])):
            row = self.table.rowCount()
            self.table.insertRow(row)
            title_item = QTableWidgetItem(video["title"] or video["youtube_video_id"])
            title_item.setData(Qt.UserRole, video["id"])
            status = video["status"]
            progress = self._PROGRESS.get(status, 0)
            self.table.setItem(row, 0, title_item)
            self.table.setItem(row, 1, QTableWidgetItem(video["channel_title"] or video["channel_id"] or ""))
            self.table.setItem(row, 2, QTableWidgetItem(VIDEO_STATUS_LABELS.get(status, status)))
            self.table.setItem(row, 3, QTableWidgetItem(f"{progress}%"))
            self.table.setItem(row, 4, QTableWidgetItem(format_local_time(video["updated_at"])))
