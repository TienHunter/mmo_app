from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, QSettings, Qt
from PySide6.QtGui import QAction, QBrush, QKeySequence, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QMenu,
    QStyledItemDelegate,
    QTableWidget,
    QTextEdit,
    QToolTip,
)

from ..theme import TOKENS, ThemeMode
from .row_hover import install_row_hover


class _ReadOnlyTextDelegate(QStyledItemDelegate):
    """Read-only cells; double-click opens a wrapped editor for text selection."""

    def __init__(self, view):
        super().__init__(view)
        self._view = view

    def createEditor(self, parent, option, index):  # type: ignore[override]
        text = str(index.data(Qt.DisplayRole) or "")
        tokens = self._theme_tokens()
        brush = index.data(Qt.ForegroundRole)
        color = brush.color().name() if isinstance(brush, QBrush) else tokens["text"]
        highlight = self._view.palette().highlight().color().name()
        highlighted = self._view.palette().highlightedText().color().name()
        background = tokens["surface_alt"] if index.row() % 2 else tokens["surface"]
        editor = QTextEdit(parent)
        editor.setReadOnly(True)
        editor.setFrameShape(QFrame.NoFrame)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        editor.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        editor.document().setDocumentMargin(0)
        wrap = self._view.wordWrap() or "\n" in text
        editor.setWordWrapMode(
            QTextOption.WrapAtWordBoundaryOrAnywhere if wrap else QTextOption.NoWrap)
        editor.setStyleSheet(
            f"QTextEdit {{ background: {background}; border: 1px solid {tokens['primary']};"
            f" border-radius: 4px; padding: 1px 8px; color: {color};"
            f" selection-background-color: {highlight}; selection-color: {highlighted}; }}")
        return editor

    def setEditorData(self, editor, index):  # type: ignore[override]
        font = index.data(Qt.FontRole)
        if font is not None:
            editor.setFont(font)
        editor.setPlainText(str(index.data() or ""))
        editor.selectAll()

    def setModelData(self, editor, model, index) -> None:  # type: ignore[override]
        return

    def helpEvent(self, event, view, option, index):  # type: ignore[override]
        """Hover shows the cell value instead of the old table-wide guide."""
        text = str(index.data(Qt.DisplayRole) or "")
        if text:
            QToolTip.showText(event.globalPos(), text, view)
            return True
        return super().helpEvent(event, view, option, index)

    @staticmethod
    def _theme_tokens() -> dict[str, str]:
        mode = QSettings("MMOApp", "MMOApp").value("ui/theme", ThemeMode.LIGHT.value)
        try:
            return TOKENS[ThemeMode(mode)]
        except ValueError:
            return TOKENS[ThemeMode.LIGHT]


class CopyableTable(QTableWidget):
    """TableWidget with cell/row selection and Ctrl+C copy support."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # Dragging stays cell-range selection; text selection opens explicitly
        # (double-click or context menu) in a wrapped read-only editor.
        self.setEditTriggers(QAbstractItemView.DoubleClicked)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setItemDelegate(_ReadOnlyTextDelegate(self))
        self.setShowGrid(True)
        self.setAlternatingRowColors(True)
        self.setWordWrap(True)
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
        select_action = QAction("Bôi đen chữ trong ô", menu)
        select_action.setEnabled(index.isValid())
        select_action.triggered.connect(
            lambda: self.editItem(self.item(index.row(), index.column())))
        menu.addAction(select_action)
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
