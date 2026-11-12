from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


_KINDS = {
    "success": "#16A34A",
    "error": "#DC2626",
    "info": "#2563EB",
    "warning": "#D97706",
}


def show_toast(parent: QWidget, message: str, kind: str = "success", duration_ms: int = 3200) -> None:
    """Show a lightweight, self-dismissing notification anchored to the window."""
    window = parent.window()
    toast = QFrame(window)
    toast.setAttribute(Qt.WA_DeleteOnClose)
    toast.setObjectName("toast")
    color = _KINDS.get(kind, _KINDS["info"])
    layout = QHBoxLayout(toast)
    layout.setContentsMargins(14, 10, 14, 10)
    label = QLabel(message)
    label.setWordWrap(True)
    label.setStyleSheet("color: #FFFFFF; font-weight: 600; background: transparent;")
    layout.addWidget(label)
    toast.setStyleSheet(f"QFrame#toast {{ background: {color}; border-radius: 8px; }}")
    toast.adjustSize()
    toast.resize(max(toast.width(), 240), toast.height())
    toast.move((window.width() - toast.width()) // 2, window.height() - toast.height() - 48)
    toast.raise_()
    toast.show()
    QTimer.singleShot(duration_ms, toast.close)
