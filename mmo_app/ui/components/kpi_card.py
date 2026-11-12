from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout


class KpiCard(QFrame):
    """Reusable click target. It will receive a data-backed filter in phase 3."""

    def __init__(self, label: str, value: str = "—", on_click: Callable[[], None] | None = None):
        super().__init__()
        self.setObjectName("card")
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor if on_click else Qt.ArrowCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        self.label = QLabel(label)
        self.label.setObjectName("kpiLabel")
        self.value = QLabel(value)
        self.value.setObjectName("kpiValue")
        layout.addWidget(self.label)
        layout.addSpacing(5)
        layout.addWidget(self.value)

    def set_value(self, value: str | int) -> None:
        self.value.setText(str(value))

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._on_click and event.button() == Qt.LeftButton:
            self._on_click()
        super().mouseReleaseEvent(event)
