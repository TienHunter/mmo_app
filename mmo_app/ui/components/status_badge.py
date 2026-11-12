from __future__ import annotations

from PySide6.QtWidgets import QLabel


class StatusBadge(QLabel):
    """Status always includes text, not color alone."""

    COLORS = {"good": "#16803C", "warning": "#B45309", "danger": "#C53030", "neutral": "#64748B"}

    def __init__(self, text: str, kind: str = "neutral"):
        super().__init__()
        self.set_status(text, kind)

    def set_status(self, text: str, kind: str = "neutral") -> None:
        color = self.COLORS.get(kind, self.COLORS["neutral"])
        self.setText(f"●  {text}")
        self.setStyleSheet(f"color: {color}; font-weight: 600; padding: 3px 0;")
