from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...application import Application
from ..services.repository import UIRepository, format_local_time


_STAGE_STYLES = {
    "running": ("●", "#2563EB", "Đang chạy"),
    "waiting": ("●", "#D97706", "Đang chờ"),
    "success": ("✓", "#16A34A", "Hoàn tất"),
    "failed": ("✕", "#DC2626", "Lỗi"),
    "warning": ("!", "#D97706", "Cần xử lý"),
    "idle": ("○", "#64748B", "Trống"),
}

_LEVEL_COLORS = {
    "ERROR": "#DC2626",
    "WARNING": "#D97706",
}


def _format_eta(seconds: int) -> str:
    if seconds <= 0:
        return "sắp xong"
    if seconds < 60:
        return f"~{seconds} giây"
    if seconds < 3600:
        return f"~{max(1, seconds // 60)} phút"
    return f"~{seconds // 3600} giờ {(seconds % 3600) // 60} phút"


class _StageRow(QFrame):
    def __init__(self, label: str):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 6)
        layout.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.dot = QLabel("○")
        self.dot.setFixedWidth(18)
        self.dot.setStyleSheet("font-size: 14pt; color: #64748B;")
        self.title = QLabel(label)
        self.title.setStyleSheet("font-weight: 700;")
        self.state = QLabel("Trống")
        self.state.setObjectName("muted")
        top.addWidget(self.dot)
        top.addWidget(self.title)
        top.addStretch()
        top.addWidget(self.state)
        layout.addLayout(top)
        self.details = QWidget()
        self.details_layout = QVBoxLayout(self.details)
        self.details_layout.setContentsMargins(0, 0, 0, 0)
        self.details_layout.setSpacing(2)
        layout.addWidget(self.details)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

    def _clear_details(self) -> None:
        while self.details_layout.count():
            item = self.details_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_state(self, state: str, detail: str,
                  eta_seconds: int | None = None,
                  tasks: list[dict] | None = None) -> None:
        symbol, color, label = _STAGE_STYLES.get(state, _STAGE_STYLES["idle"])
        self.dot.setText(symbol)
        self.dot.setStyleSheet(f"font-size: 14pt; color: {color};")
        self.state.setStyleSheet(f"color: {color}; font-weight: 600;")
        self._clear_details()
        if state == "running" and tasks:
            self.state.setText(
                f"{label} ({len(tasks)})" if len(tasks) > 1 else label)
            for task in tasks:
                parts = [str(task.get("title") or "Không rõ video")]
                if task.get("channel"):
                    parts.append(f"Kênh: {task['channel']}")
                if task.get("account"):
                    parts.append(f"TK: {task['account']}")
                if task.get("eta_seconds") is not None:
                    parts.append(f"còn {_format_eta(task['eta_seconds'])}")
                line = QLabel(" · ".join(parts))
                line.setObjectName("muted")
                line.setWordWrap(True)
                line.setStyleSheet("font-size: 9pt;")
                line.setToolTip(" · ".join(parts))
                self.details_layout.addWidget(line)
        else:
            self.state.setText(label)
            if state == "running" and eta_seconds is not None:
                detail = f"{detail} · dự kiến còn {_format_eta(eta_seconds)}"
            line = QLabel(detail or "Chưa có hoạt động")
            line.setObjectName("muted")
            line.setWordWrap(True)
            self.details_layout.addWidget(line)
        self.progress.setVisible(state == "running")


class ActivityPanel(QFrame):
    """Right-side live status panel for the download/process/upload stages."""

    def __init__(self, application: Application):
        super().__init__()
        self.application = application
        self.repository = UIRepository(application.database)
        self.setObjectName("card")
        self.setFixedWidth(320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        title = QLabel("Trạng thái hoạt động")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        self.pipeline = QLabel("Pipeline: đang kiểm tra…")
        self.pipeline.setObjectName("muted")
        layout.addWidget(self.pipeline)

        self.stage_rows: dict[str, _StageRow] = {}
        for key, label in (
            ("download", "Tải video"),
            ("process", "Xử lý video"),
            ("upload", "Đăng TikTok"),
        ):
            row = _StageRow(label)
            self.stage_rows[key] = row
            layout.addWidget(row)

        self.queue = QLabel("Hàng đợi đăng: 0 bản ghi")
        self.queue.setObjectName("muted")
        layout.addWidget(self.queue)
        layout.addWidget(QLabel("Hoạt động gần đây", objectName="pageTitle"))
        self.events_scroll = QScrollArea()
        self.events_scroll.setWidgetResizable(True)
        self.events_scroll.setFrameShape(QFrame.NoFrame)
        self.events_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.events_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }")
        self.events_body = QWidget()
        self.events_layout = QVBoxLayout(self.events_body)
        self.events_layout.setContentsMargins(0, 0, 0, 0)
        self.events_layout.setSpacing(6)
        self.events_scroll.setWidget(self.events_body)
        layout.addWidget(self.events_scroll, 1)

        self._last_events_signature: tuple = ()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        try:
            activity = self.repository.pipeline_activity(20)
        except Exception:
            self.pipeline.setText("Pipeline: không đọc được trạng thái")
            return
        running = self.application.is_running
        self.pipeline.setText(
            "Pipeline: Đang chạy" if running else "Pipeline: Đã dừng")
        self.pipeline.setStyleSheet(
            "color: #16A34A; font-weight: 600;" if running
            else "color: #D97706; font-weight: 600;")
        for stage in activity["stages"]:
            row = self.stage_rows.get(stage["kind"])
            if row is not None:
                row.set_state(
                    stage["state"], stage["detail"], stage.get("eta_seconds"),
                    stage.get("tasks"))
        self.queue.setText(f"Hàng đợi đăng: {activity['pending_uploads']} bản ghi")
        events = activity["events"]
        signature = tuple(
            (event["id"], event["level"], event["message"]) for event in events)
        if signature == self._last_events_signature:
            return
        self._last_events_signature = signature
        while self.events_layout.count():
            item = self.events_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for event in events:
            card = QFrame()
            card.setObjectName("eventCard")
            card.setStyleSheet(
                "QFrame#eventCard { background: rgba(148,163,184,0.16);"
                " border-radius: 8px; }")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            card_layout.setSpacing(3)
            clock_text = format_local_time(
                event["created_at"], include_date=False)
            clock = QLabel(clock_text)
            clock.setTextInteractionFlags(Qt.TextSelectableByMouse)
            clock.setStyleSheet(
                "font-weight: 700; font-size: 9pt; color: #2563EB;")
            message = QLabel(event["message"])
            message.setWordWrap(True)
            message.setTextInteractionFlags(
                Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            color = _LEVEL_COLORS.get(event["level"])
            weight_style = "font-weight: 600;"
            if color:
                weight_style += f" color: {color};"
            message.setStyleSheet(weight_style)
            message.setToolTip(event["message"])
            card_layout.addWidget(clock)
            card_layout.addWidget(message)
            card_text = f"{clock_text}  {event['message']}"
            card.setContextMenuPolicy(Qt.CustomContextMenu)
            card.customContextMenuRequested.connect(
                lambda position, widget=card, text=card_text:
                self._copy_event_menu(widget, position, text))
            self.events_layout.addWidget(card)
        self.events_layout.addStretch()

    def _copy_event_menu(self, widget: QWidget, position, text: str) -> None:
        menu = QMenu(widget)
        action = QAction("Sao chép hoạt động", menu)
        action.triggered.connect(
            lambda: QApplication.clipboard().setText(text))
        menu.addAction(action)
        menu.exec(widget.mapToGlobal(position))
