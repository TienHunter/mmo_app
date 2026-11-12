from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QMenu,
    QTableWidget,
)

from .row_hover import install_row_hover


class CopyableTable(QTableWidget):
    """TableWidget with cell/row selection and Ctrl+C copy support."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setShowGrid(True)
        self.setAlternatingRowColors(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_copy_menu)
        install_row_hover(self)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.Copy):
            self.copy_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def _show_copy_menu(self, position) -> None:
        index = self.indexAt(position)
        if index.isValid() and not self.selectionModel().isSelected(index):
            self.setCurrentCell(
                index.row(), index.column(), QItemSelectionModel.ClearAndSelect)
        menu = QMenu(self)
        copy_action = QAction("Sao chép", menu)
        copy_action.setShortcut(QKeySequence.Copy)
        copy_action.triggered.connect(self.copy_selection)
        copy_action.setEnabled(bool(self.selectedRanges()))
        menu.addAction(copy_action)
        menu.exec(self.viewport().mapToGlobal(position))

    def selected_text(self) -> str:
        ranges = self.selectedRanges()
        if not ranges:
            item = self.currentItem()
            return item.text() if item else ""
        blocks = []
        for cell_range in sorted(
                ranges, key=lambda item: (item.topRow(), item.leftColumn())):
            lines = []
            for row in range(cell_range.topRow(), cell_range.bottomRow() + 1):
                values = []
                for column in range(cell_range.leftColumn(), cell_range.rightColumn() + 1):
                    item = self.item(row, column)
                    values.append(item.text() if item else "")
                lines.append("\t".join(values))
            blocks.append("\n".join(lines))
        return "\n".join(blocks)

    def copy_selection(self) -> None:
        text = self.selected_text()
        if text:
            QApplication.clipboard().setText(text)
