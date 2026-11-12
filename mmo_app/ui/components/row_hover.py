from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QBrush, QColor, QPalette


class _RowHoverFilter(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.MouseMove:
            index = self.view.indexAt(event.position().toPoint())
            row = index.row() if index.isValid() else -1
            self._set_row(row)
        elif event.type() == QEvent.Leave:
            self._set_row(-1)
        return False

    def _set_row(self, row: int) -> None:
        if self.view._hover_row == row:
            return
        model = self.view.model()
        previous = self.view._hover_row
        self.view._hover_row = row
        if model is None:
            return
        base = self.view.palette().color(QPalette.Base)
        color = QColor("#E6EEFF" if base.lightness() > 128 else "#2B3E5C")
        for target in (previous, row):
            if target is None or target < 0:
                continue
            brush = QBrush(color) if target == row else QBrush()
            for column in range(model.columnCount()):
                model.setData(
                    model.index(target, column), brush, Qt.BackgroundRole)


def install_row_hover(view) -> None:
    """Highlight the whole table row under the mouse."""
    view._hover_row = -1
    view._row_hover_filter = _RowHoverFilter(view)
    view.viewport().setMouseTracking(True)
    view.viewport().installEventFilter(view._row_hover_filter)
