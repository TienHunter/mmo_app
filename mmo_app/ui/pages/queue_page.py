from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...application import Application
from ..components import CopyableTable, TablePager
from ..services.repository import UIRepository, format_local_time


UPLOAD_STATUS_LABELS = {
    "pending": "Chờ đăng",
    "completed": "Đã đăng",
    "test_ready": "Test xong (không đăng)",
}


class QueuePage(QWidget):
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
        title = QLabel("Hàng đợi & lịch đăng")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Danh sách video đã xử lý xong và đang chờ đăng lên TikTok.")
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
        self.account = QComboBox()
        self.account.addItem("Tất cả tài khoản TikTok", "")
        self.account.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.account, 1)
        filters.addStretch()
        layout.addLayout(filters)

        self.table = CopyableTable(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Video", "Tài khoản TikTok", "Test", "Caption", "Trạng thái",
            "Lịch đăng", "Đã đăng",
        ])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        for index, width in enumerate((300, 150, 60, 300, 150, 140, 140)):
            self.table.setColumnWidth(index, width)
        layout.addWidget(self.table, 1)
        self.pager = TablePager()
        self.pager.page_changed.connect(self._render_page)
        layout.addWidget(self.pager)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._reload_accounts()
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

    def refresh(self) -> None:
        self._all_rows = self.repository.list_upload_tasks(
            account_id=self.account.currentData() or None)
        self.pager.reset()
        self.pager.set_total(len(self._all_rows))
        self._render_page()

    def _render_page(self) -> None:
        self.table.setRowCount(0)
        for task in self.pager.page_rows(getattr(self, "_all_rows", [])):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(task["video_title"] or ""))
            self.table.setItem(row, 1, QTableWidgetItem(task["account_name"] or ""))
            self.table.setItem(row, 2, QTableWidgetItem("Test" if task["is_test"] else ""))
            self.table.setItem(row, 3, QTableWidgetItem(task["caption"] or ""))
            self.table.setItem(row, 4, QTableWidgetItem(UPLOAD_STATUS_LABELS.get(task["status"], task["status"])))
            self.table.setItem(row, 5, QTableWidgetItem(format_local_time(task["scheduled_at"])))
            self.table.setItem(row, 6, QTableWidgetItem(format_local_time(task["posted_at"])))
