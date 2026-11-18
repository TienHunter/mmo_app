from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
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
from ..services.repository import (
    JOB_KIND_LABELS,
    JOB_STATUS_LABELS,
    UIRepository,
    format_local_time,
)


STATUS_COLORS = {
    "failed": QColor("#DC2626"),
    "manual_action": QColor("#D97706"),
    "cancelled": QColor("#64748B"),
    "completed": QColor("#15803D"),
}


class JobsPage(QWidget):
    """Read-only view of every pipeline job and its current status."""

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
        title = QLabel("Hàng đợi job")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Toàn bộ job tải, xử lý và đăng theo thời điểm cập nhật. "
            "Bấm Làm mới để cập nhật.")
        subtitle.setObjectName("pageSubtitle")
        words.addWidget(title)
        words.addWidget(subtitle)
        header_layout.addLayout(words, 1)
        header_layout.addStretch()
        refresh = QPushButton("Làm mới")
        refresh.clicked.connect(lambda: self.refresh(reset_page=False))
        header_layout.addWidget(refresh)
        layout.addWidget(header)

        self.table = CopyableTable(0, 8)
        self.table.setHorizontalHeaderLabels([
            "Mã", "Loại", "Video", "Trạng thái", "Lần thử",
            "Tạo lúc", "Cập nhật", "Lỗi",
        ])
        self.table.verticalHeader().setVisible(False)
        table_header = self.table.horizontalHeader()
        table_header.setSectionResizeMode(QHeaderView.Interactive)
        table_header.setStretchLastSection(False)
        for index, width in enumerate((60, 80, 260, 120, 80, 150, 150, 320)):
            self.table.setColumnWidth(index, width)
        layout.addWidget(self.table, 1)

        self.pager = TablePager()
        self.pager.page_changed.connect(self._render_page)
        layout.addWidget(self.pager)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.refresh(reset_page=False)

    def refresh(self, reset_page: bool = True) -> None:
        self._all_rows = self.repository.list_jobs()
        if reset_page:
            self.pager.reset()
        self.pager.set_total(len(self._all_rows))
        self._render_page()

    def _render_page(self) -> None:
        self.table.setRowCount(0)
        self._page_rows = self.pager.page_rows(getattr(self, "_all_rows", []))
        for job in self._page_rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            status = JOB_STATUS_LABELS.get(job["status"], job["status"])
            values = [
                str(job["id"]),
                JOB_KIND_LABELS.get(job["kind"], job["kind"]),
                job["video_title"] or "",
                status,
                f"{job['attempts']}/{job['max_attempts']}",
                format_local_time(job["created_at"]),
                format_local_time(job["updated_at"]),
                job["error_message"] or "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 3:
                    color = STATUS_COLORS.get(job["status"])
                    if color is not None:
                        item.setForeground(color)
                self.table.setItem(row, column, item)
