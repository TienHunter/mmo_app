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
from ..components import CopyableTable, TablePager, show_toast
from ..services.repository import UIRepository, format_local_time


UPLOAD_STATUS_LABELS = {
    "pending": "Chờ đăng",
    "completed": "Đã đăng",
    "test_ready": "Test xong (không đăng)",
    "no_task": "Chưa xếp lịch",
}


class QueuePage(QWidget):
    def __init__(self, application: Application):
        super().__init__()
        self.application = application
        self.repository = UIRepository(application.database)
        self._page_rows: list[dict] = []
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
        subtitle = QLabel(
            "Video đã xử lý xong, kèm lịch đăng TikTok. Video chưa có lịch "
            "(tài khoản tắt) vẫn hiện với trạng thái Chưa xếp lịch.")
        subtitle.setObjectName("pageSubtitle")
        words.addWidget(title)
        words.addWidget(subtitle)
        header_layout.addLayout(words, 1)
        header_layout.addStretch()
        mark = QPushButton("Đánh dấu đã đăng")
        mark.setToolTip(
            "Đánh dấu các dòng đang chọn là đã đăng thủ công ra TikTok.")
        mark.clicked.connect(self._mark_posted)
        header_layout.addWidget(mark)
        revert = QPushButton("Thu hồi đã đăng")
        revert.setToolTip(
            "Đưa các dòng đang chọn về Chờ đăng (hoặc Chưa xếp lịch nếu task "
            "được tạo bởi Đánh dấu đã đăng).")
        revert.clicked.connect(self._revert_posted)
        header_layout.addWidget(revert)
        refresh = QPushButton("Làm mới")
        refresh.clicked.connect(lambda: self.refresh())
        header_layout.addWidget(refresh)
        layout.addWidget(header)

        filters = QHBoxLayout()
        filters.setSpacing(10)
        self.account = QComboBox()
        self.account.addItem("Tất cả tài khoản TikTok", "")
        self.account.currentIndexChanged.connect(lambda: self.refresh())
        filters.addWidget(self.account, 1)
        filters.addStretch()
        layout.addLayout(filters)

        self.table = CopyableTable(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Video", "Tạo lúc", "Tài khoản TikTok", "Trạng thái",
            "Lịch đăng", "Đã đăng", "Đường dẫn",
        ])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        for index, width in enumerate((200, 170, 145, 140, 120, 120, 360)):
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

    def refresh(self, keep_page: bool = False) -> None:
        self._all_rows = self.repository.list_upload_tasks(
            account_id=self.account.currentData() or None)
        if not keep_page:
            self.pager.reset()
        self.pager.set_total(len(self._all_rows))
        self._render_page()

    def _mark_posted(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        marked = 0
        for row in rows:
            if row >= len(self._page_rows):
                continue
            task = self._page_rows[row]
            if task["is_test"] or task["status"] == "completed":
                continue
            if self.repository.mark_upload_task_posted(task):
                marked += 1
        if not marked:
            show_toast(
                self,
                "Chọn dòng video đã đăng thủ công (bỏ qua video test và dòng Chưa gán).",
                "warning")
            return
        self.refresh(keep_page=True)
        show_toast(
            self, f"Đã cập nhật {marked} video sang trạng thái Đã đăng.", "success")

    def _revert_posted(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        reverted = 0
        for row in rows:
            if row >= len(self._page_rows):
                continue
            if self.repository.revert_upload_task_posted(self._page_rows[row]):
                reverted += 1
        if not reverted:
            show_toast(self, "Chọn dòng đang ở trạng thái Đã đăng để thu hồi.", "warning")
            return
        self.refresh(keep_page=True)
        show_toast(
            self, f"Đã thu hồi {reverted} video về Chờ đăng.", "success")

    def _render_page(self) -> None:
        self.table.setRowCount(0)
        self._page_rows = self.pager.page_rows(getattr(self, "_all_rows", []))
        for task in self._page_rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(task["video_title"] or ""))
            self.table.setItem(row, 1, QTableWidgetItem(format_local_time(task["created_at"])))
            self.table.setItem(row, 2, QTableWidgetItem(task["account_name"] or "Chưa gán"))
            self.table.setItem(row, 3, QTableWidgetItem(UPLOAD_STATUS_LABELS.get(task["status"], task["status"])))
            self.table.setItem(row, 4, QTableWidgetItem(format_local_time(task["scheduled_at"])))
            self.table.setItem(row, 5, QTableWidgetItem(format_local_time(task["posted_at"])))
            self.table.setItem(row, 6, QTableWidgetItem(task["path"] or ""))
        # Fit the two-line caption (title + hashtags) and long paths.
        self.table.resizeRowsToContents()
