from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class BarChart(QWidget):
    """Minimal dependency-free bar chart for the dashboard trend panel."""

    def __init__(self):
        super().__init__()
        self._labels: list[str] = []
        self._values: list[int] = []
        self._color = QColor("#2F6FED")
        self.setMinimumHeight(180)

    def set_data(self, labels: list[str], values: list[int], color: str = "#2F6FED") -> None:
        self._labels = labels
        self._values = values
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), Qt.transparent)
        if not self._labels:
            painter.setPen(QColor("#94A3B8"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Chưa có dữ liệu đăng bài")
            return

        margin_left, margin_right = 28, 16
        margin_top, margin_bottom = 12, 28
        plot_width = max(1, self.width() - margin_left - margin_right)
        plot_height = max(1, self.height() - margin_top - margin_bottom)
        max_value = max(self._values) if self._values else 1
        max_value = max(max_value, 1)
        slot = plot_width / max(1, len(self._labels))
        bar_width = max(8, slot * 0.55)

        axis_y = margin_top + plot_height
        painter.setPen(QPen(QColor("#94A3B8"), 1))
        painter.drawLine(margin_left, axis_y, margin_left + plot_width, axis_y)

        for index, (label, value) in enumerate(zip(self._labels, self._values)):
            bar_height = int(plot_height * (value / max_value)) if value else 0
            x = margin_left + int(slot * index + (slot - bar_width) / 2)
            y = axis_y - bar_height
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._color)
            painter.drawRoundedRect(x, y, int(bar_width), bar_height, 4, 4)

            painter.setPen(QColor("#94A3B8"))
            painter.drawText(
                int(x - slot / 2),
                axis_y + 16,
                int(slot),
                16,
                Qt.AlignHCenter | Qt.AlignTop,
                label,
            )
            if value:
                painter.setPen(QColor(self._color))
                painter.drawText(
                    int(x - slot / 2),
                    max(margin_top, y - 20),
                    int(slot),
                    16,
                    Qt.AlignHCenter | Qt.AlignBottom,
                    str(value),
                )
        painter.end()
