from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..components import EmptyState
from ..i18n import vi


class PlaceholderPage(QWidget):
    def __init__(self, title: str, description: str):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(EmptyState(title, description or vi.EMPTY_BODY))
