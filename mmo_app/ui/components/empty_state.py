from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout


class EmptyState(QFrame):
    def __init__(self, title: str, body: str, action_text: str | None = None):
        super().__init__()
        self.setObjectName("emptyState")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setAlignment(Qt.AlignCenter)
        icon = QLabel("◌")
        icon.setStyleSheet("font-size: 34pt;")
        icon.setAlignment(Qt.AlignCenter)
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 14pt; font-weight: 700;")
        heading.setAlignment(Qt.AlignCenter)
        detail = QLabel(body)
        detail.setObjectName("muted")
        detail.setWordWrap(True)
        detail.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon)
        layout.addWidget(heading)
        layout.addWidget(detail)
        if action_text:
            action = QPushButton(action_text)
            action.setObjectName("primaryButton")
            layout.addWidget(action, alignment=Qt.AlignCenter)
