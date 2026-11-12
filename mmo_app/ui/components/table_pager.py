from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)


class TablePager(QWidget):
    """Simple client-side pager used below data tables (20 rows by default)."""

    page_changed = Signal()

    def __init__(self, page_size: int = 20, page_sizes: tuple[int, ...] = (20, 50, 100), parent=None):
        super().__init__(parent)
        self.page_size = page_size
        self.page = 1
        self.total = 0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)
        layout.addStretch()
        layout.addWidget(QLabel("Số dòng/trang", objectName="muted"))
        self.size_box = QComboBox()
        for size in page_sizes:
            self.size_box.addItem(str(size), size)
        index = self.size_box.findData(page_size)
        if index >= 0:
            self.size_box.setCurrentIndex(index)
        self.size_box.currentIndexChanged.connect(self._change_page_size)
        layout.addWidget(self.size_box)
        self.previous = QPushButton("‹ Trước")
        self.previous.clicked.connect(lambda: self._go(-1))
        layout.addWidget(self.previous)
        self.label = QLabel("Trang 1/1")
        self.label.setObjectName("muted")
        layout.addWidget(self.label)
        self.next = QPushButton("Sau ›")
        self.next.clicked.connect(lambda: self._go(1))
        layout.addWidget(self.next)
        self._update()

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)

    def reset(self) -> None:
        self.page = 1

    def set_total(self, total: int) -> None:
        self.total = max(0, int(total))
        self.page = min(self.page, self.pages)
        self.page = max(1, self.page)
        self._update()

    def page_rows(self, rows: Sequence) -> list:
        start = (self.page - 1) * self.page_size
        return list(rows[start:start + self.page_size])

    def _go(self, delta: int) -> None:
        target = min(self.pages, max(1, self.page + delta))
        if target == self.page:
            return
        self.page = target
        self._update()
        self.page_changed.emit()

    def _change_page_size(self) -> None:
        self.page_size = int(self.size_box.currentData() or 20)
        self.page = 1
        self._update()
        self.page_changed.emit()

    def _update(self) -> None:
        self.label.setText(f"Trang {self.page}/{self.pages} · {self.total} dòng")
        self.previous.setEnabled(self.page > 1)
        self.next.setEnabled(self.page < self.pages)
