from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QAction, QKeySequence, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QMenu,
    QTableView,
)

from .row_hover import install_row_hover


class DataTable(QTableView):
    """Thin model/view table wrapper used instead of one-widget-per-row layouts."""

    def __init__(self):
        super().__init__()
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(True)
        self.setSortingEnabled(True)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setHighlightSections(False)
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
            self.selectionModel().select(
                index, QItemSelectionModel.ClearAndSelect)
        menu = QMenu(self)
        copy_action = QAction("Sao chép", menu)
        copy_action.setShortcut(QKeySequence.Copy)
        copy_action.triggered.connect(self.copy_selection)
        copy_action.setEnabled(bool(self.selectionModel().selectedIndexes()))
        menu.addAction(copy_action)
        menu.exec(self.viewport().mapToGlobal(position))

    def selected_text(self) -> str:
        indexes = self.selectionModel().selectedIndexes()
        if not indexes:
            return ""
        selected = {(index.row(), index.column()) for index in indexes}
        rows = [row for row, _column in selected]
        columns = [column for _row, column in selected]
        lines = []
        for row in range(min(rows), max(rows) + 1):
            values = []
            for column in range(min(columns), max(columns) + 1):
                index = self._model.index(row, column)
                values.append(
                    str(index.data() or "") if (row, column) in selected else "")
            lines.append("\t".join(values))
        return "\n".join(lines)

    def copy_selection(self) -> None:
        text = self.selected_text()
        if text:
            QApplication.clipboard().setText(text)

    def set_columns(self, columns: list[str]) -> None:
        self._model.clear()
        self._model.setHorizontalHeaderLabels(columns)

    def add_row(self, values: list[str]) -> None:
        self._model.appendRow([QStandardItem(str(value)) for value in values])

    def set_rows(self, rows: list[list[str]]) -> None:
        self._model.removeRows(0, self._model.rowCount())
        for row in rows:
            self.add_row(row)

    def clear_rows(self) -> None:
        self._model.removeRows(0, self._model.rowCount())
