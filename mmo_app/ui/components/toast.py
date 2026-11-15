from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)


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
    label.setMaximumWidth(480)
    label.setStyleSheet("color: #FFFFFF; font-weight: 600; background: transparent;")
    layout.addWidget(label)
    toast.setStyleSheet(
        f"QFrame#toast {{ background: {color}; border-radius: 10px; }}")
    toast.adjustSize()
    toast.resize(max(toast.width(), 240), toast.height())

    active = []
    for item in getattr(window, "_active_toasts", []):
        try:
            if item.isVisible():
                active.append(item)
        except RuntimeError:
            pass
    window._active_toasts = active
    offset = sum(item.height() + 8 for item in active)
    toast.move((window.width() - toast.width()) // 2,
               max(8, window.height() - toast.height() - 48 - offset))
    window._active_toasts.append(toast)

    effect = QGraphicsOpacityEffect(toast)
    toast.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", toast)
    animation.setDuration(160)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.OutCubic)
    animation.start()

    toast.raise_()
    toast.show()
    QTimer.singleShot(duration_ms, toast.close)
