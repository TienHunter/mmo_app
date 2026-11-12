from __future__ import annotations

import csv

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
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
from ..components import CopyableTable, TablePager, show_toast
from ..services.repository import UIRepository, format_local_time


LEVEL_COLORS = {
    "INFO": QColor("#64748B"),
    "WARNING": QColor("#D97706"),
    "ERROR": QColor("#DC2626"),
}


class LogsPage(QWidget):
    def __init__(self, application: Application):
        super().__init__()
        self.application = application
        self.repository = UIRepository(application.database)
        self._build()
        self.refresh()
        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(3000)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        header = QFrame()
        header.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        words = QVBoxLayout()
        title = QLabel("Nhật ký")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Lịch sử hoạt động của hệ thống, cập nhật tự động.")
        subtitle.setObjectName("pageSubtitle")
        words.addWidget(title)
        words.addWidget(subtitle)
        header_layout.addLayout(words)
        header_layout.addStretch()
        export = QPushButton("Xuất ra file")
        export.clicked.connect(self._export)
        header_layout.addWidget(export)
        layout.addWidget(header)

        filters = QHBoxLayout()
        filters.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm trong nội dung nhật ký…")
        self.search.returnPressed.connect(self.refresh)
        filters.addWidget(self.search, 2)

        self.level = QComboBox()
        self.level.addItem("Tất cả mức độ", "")
        self.level.addItem("Thông tin", "INFO")
        self.level.addItem("Cảnh báo", "WARNING")
        self.level.addItem("Lỗi", "ERROR")
        self.level.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.level, 1)
        layout.addLayout(filters)

        self.table = CopyableTable(0, 3)
        self.table.setHorizontalHeaderLabels(["Thời gian", "Mức độ", "Nội dung"])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        for index, width in enumerate((160, 90, 700)):
            self.table.setColumnWidth(index, width)
        layout.addWidget(self.table, 1)
        self.pager = TablePager()
        self.pager.page_changed.connect(self._render_page)
        layout.addWidget(self.pager)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        self._all_rows = self.repository.list_events(
            level=self.level.currentData() or None,
            search=self.search.text().strip() or None,
        )
        self.pager.reset()
        self.pager.set_total(len(self._all_rows))
        self._render_page()

    def _render_page(self) -> None:
        self.table.setRowCount(0)
        for event in self.pager.page_rows(getattr(self, "_all_rows", [])):
            row = self.table.rowCount()
            self.table.insertRow(row)
            time_item = QTableWidgetItem(format_local_time(event["created_at"]))
            level = event["level"] or ""
            level_item = QTableWidgetItem(level)
            level_item.setForeground(LEVEL_COLORS.get(level, QColor("#64748B")))
            self.table.setItem(row, 0, time_item)
            self.table.setItem(row, 1, level_item)
            self.table.setItem(row, 2, QTableWidgetItem(event["message"] or ""))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Xuất nhật ký", "mmo_app_logs.csv", "CSV (*.csv)")
        if not path:
            return
        events = self.repository.list_events(
            level=self.level.currentData() or None,
            search=self.search.text().strip() or None,
            limit=10000,
        )
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)
                writer.writerow(["time", "level", "message"])
                for event in events:
                    writer.writerow([
                        format_local_time(event["created_at"]),
                        event["level"], event["message"],
                    ])
        except OSError as exc:
            show_toast(self, f"Không ghi được file: {exc}", "error")
            return
        show_toast(self, f"Đã xuất {len(events)} dòng nhật ký.", "success")
