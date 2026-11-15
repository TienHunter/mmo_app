from __future__ import annotations

from PySide6.QtWidgets import QLabel


class StatusBadge(QLabel):
    """Status always includes text, not color alone. Colors come from theme QSS."""

    KINDS = {"good", "warning", "danger", "neutral"}

    def __init__(self, text: str, kind: str = "neutral"):
        super().__init__()
        self.set_status(text, kind)

    def set_status(self, text: str, kind: str = "neutral") -> None:
        self.setText(f"●  {text}")
        self.setProperty("statusKind", kind if kind in self.KINDS else "neutral")
        self.style().unpolish(self)
        self.style().polish(self)
