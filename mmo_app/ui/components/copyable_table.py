from __future__ import annotations

from PySide6.QtCore import (
    QEvent,
    QItemSelectionModel,
    QPointF,
    QRect,
    QSettings,
    Qt,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QFontMetrics,
    QKeySequence,
    QTextCursor,
    QTextOption,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QLineEdit,
    QMenu,
    QStyledItemDelegate,
    QTableWidget,
    QTextEdit,
)

from ..theme import TOKENS, ThemeMode
from .row_hover import install_row_hover


class _ReadOnlyTextDelegate(QStyledItemDelegate):
    """Read-only cells with card-like text selection: drag inside a cell, Ctrl+C."""

    def __init__(self, view):
        super().__init__(view)
        self._view = view
        self._overlay: QTextEdit | None = None
        self._dragging = False
        self._anchor = 0
        viewport = view.viewport()
        viewport.installEventFilter(self)
        view.verticalScrollBar().valueChanged.connect(self.hide_text_overlay)
        view.horizontalScrollBar().valueChanged.connect(self.hide_text_overlay)
        view.horizontalHeader().sectionResized.connect(self.hide_text_overlay)
        model = view.model()
        if model is not None:
            model.modelReset.connect(self.hide_text_overlay)
            model.rowsRemoved.connect(self.hide_text_overlay)
            model.layoutChanged.connect(self.hide_text_overlay)

    def createEditor(self, parent, option, index):  # type: ignore[override]
        editor = QLineEdit(parent)
        editor.setReadOnly(True)
        editor.setFrame(False)
        editor.setStyleSheet(
            "QLineEdit { border: 0; background: transparent; padding: 0 4px; }")
        return editor

    def setEditorData(self, editor, index):  # type: ignore[override]
        editor.setText(str(index.data() or ""))
        editor.selectAll()

    def setModelData(self, editor, model, index) -> None:  # type: ignore[override]
        return

    @staticmethod
    def _theme_tokens() -> dict[str, str]:
        mode = QSettings("MMOApp", "MMOApp").value("ui/theme", ThemeMode.LIGHT.value)
        try:
            return TOKENS[ThemeMode(mode)]
        except ValueError:
            return TOKENS[ThemeMode.LIGHT]

    def show_text_overlay(self, index, point: QPointF) -> bool:
        """Cover the cell with a selectable text edit so text can be dragged like a label."""
        text = str(index.data(Qt.DisplayRole) or "")
        if not text:
            return False
        view = self._view
        self.hide_text_overlay()
        view.setCurrentIndex(index)
        view.selectionModel().select(index, QItemSelectionModel.ClearAndSelect)
        rect = view.visualRect(index)
        tokens = self._theme_tokens()
        background = tokens["surface_alt"] if index.row() % 2 else tokens["surface"]
        highlight = view.palette().highlight().color().name()
        highlighted = view.palette().highlightedText().color().name()
        font = index.data(Qt.FontRole) or view.font()
        brush = index.data(Qt.ForegroundRole)
        color = brush.color().name() if isinstance(brush, QBrush) else tokens["text"]

        overlay = QTextEdit(view.viewport())
        overlay.setReadOnly(True)
        overlay.setFrameShape(QFrame.NoFrame)
        overlay.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        overlay.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        overlay.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        overlay.document().setDocumentMargin(0)
        overlay.setFont(font)
        wrap = view.wordWrap() or "\n" in text
        overlay.setWordWrapMode(
            QTextOption.WrapAtWordBoundaryOrAnywhere if wrap else QTextOption.NoWrap)
        overlay.setPlainText(text)
        overlay.setStyleSheet(
            f"QTextEdit {{ background: {background}; border: 1px solid {tokens['primary']};"
            f" border-radius: 4px; padding: 1px 6px; color: {color};"
            f" selection-background-color: {highlight}; selection-color: {highlighted}; }}")
        if wrap:
            geometry = rect.adjusted(0, 0, -1, 0)
        else:
            height = QFontMetrics(font).height() + 4
            geometry = QRect(
                rect.left(),
                rect.top() + max(0, (rect.height() - height) // 2),
                rect.width() - 1,
                min(height + 4, rect.height()))
        overlay.setGeometry(geometry)
        overlay.installEventFilter(self)
        overlay.show()
        overlay.setFocus(Qt.MouseFocusReason)
        self._overlay = overlay
        self._dragging = True
        local = QPointF(point.x() - geometry.left(), point.y() - geometry.top())
        cursor = overlay.cursorForPosition(local.toPoint())
        overlay.setTextCursor(cursor)
        self._anchor = cursor.position()
        return True

    def _extend_selection(self, overlay: QTextEdit, point: QPointF) -> None:
        local = QPointF(point) - QPointF(overlay.geometry().topLeft())
        cursor = overlay.cursorForPosition(local.toPoint())
        cursor.setPosition(self._anchor, QTextCursor.KeepAnchor)
        overlay.setTextCursor(cursor)

    def hide_text_overlay(self, *args) -> None:
        overlay = self._overlay
        self._overlay = None
        self._dragging = False
        if overlay is not None:
            try:
                overlay.hide()
                overlay.deleteLater()
            except RuntimeError:
                pass

    def eventFilter(self, obj, event) -> bool:
        try:
            viewport = self._view.viewport()
        except RuntimeError:
            return False
        overlay = self._overlay
        if overlay is not None and obj is overlay:
            if event.type() == QEvent.Wheel:
                self.hide_text_overlay()
                QApplication.sendEvent(viewport, event)
                return True
            if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
                self.hide_text_overlay()
                return True
            if event.type() == QEvent.KeyPress and event.matches(QKeySequence.Copy):
                if not overlay.textCursor().hasSelection():
                    QApplication.clipboard().setText(overlay.toPlainText())
                    return True
                return False
            if event.type() == QEvent.FocusOut and self._overlay is overlay:
                self.hide_text_overlay()
            return False
        if obj is viewport:
            etype = event.type()
            if etype == QEvent.Resize:
                self.hide_text_overlay()
            elif etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                if not (event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)):
                    index = self._view.indexAt(event.position().toPoint())
                    if index.isValid() and self.show_text_overlay(index, event.position()):
                        return True
                self.hide_text_overlay()
            elif overlay is not None and etype == QEvent.MouseMove:
                if self._dragging:
                    self._extend_selection(overlay, event.position())
                    return True
            elif overlay is not None and etype == QEvent.MouseButtonRelease:
                self._dragging = False
                return True
            elif etype in (QEvent.MouseButtonDblClick, QEvent.Wheel):
                self.hide_text_overlay()
        return super().eventFilter(obj, event)


class CopyableTable(QTableWidget):
    """TableWidget with cell/row selection and Ctrl+C copy support."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(
            QAbstractItemView.SelectedClicked
            | QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed)
        self.setItemDelegate(_ReadOnlyTextDelegate(self))
        self.setToolTip(
            "Kéo chuột trong ô để bôi đen chữ rồi Ctrl+C sao chép phần bôi đen (Esc để thoát). Không bôi đen thì Ctrl+C sao chép ô/vùng chọn.")
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
