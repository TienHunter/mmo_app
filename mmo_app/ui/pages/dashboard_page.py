from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...application import Application
from ..components import BarChart, DataTable, EmptyState, KpiCard, StatusBadge, show_toast
from ..services.repository import JOB_KIND_LABELS, UIRepository, format_local_time


class DashboardPage(QWidget):
    """Overview screen backed by live database aggregates via UIRepository."""

    KPI_TARGETS = {
        "found": (2, "Thư viện video"),
        "downloading": (2, "Thư viện video"),
        "processing": (2, "Thư viện video"),
        "queued": (4, "Hàng đợi & lịch đăng"),
        "posted": (2, "Thư viện video"),
        "issues": (2, "Thư viện video"),
    }

    def __init__(
        self,
        application: Application,
        start_all: Callable[[], None],
        pause_all: Callable[[], None],
        navigate: Callable[[int], None],
    ):
        super().__init__()
        self.application = application
        self.repository = UIRepository(application.database)
        self._start_all = start_all
        self._pause_all = pause_all
        self._navigate = navigate
        self._build()
        self.refresh()
        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(5000)

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        scroll.setWidget(body)

        layout.addWidget(self._build_header())

        self.cards: dict[str, KpiCard] = {}
        grid = QGridLayout()
        grid.setSpacing(12)
        items = [
            ("found", "Video phát hiện hôm nay"),
            ("downloading", "Đang tải"),
            ("processing", "Đang xử lý"),
            ("queued", "Chờ đăng"),
            ("posted", "Đã đăng hôm nay"),
            ("issues", "Lỗi cần xử lý"),
        ]
        for index, (key, label) in enumerate(items):
            card = KpiCard(label, "—", on_click=lambda _key=key: self._open_kpi(_key))
            self.cards[key] = card
            grid.addWidget(card, index // 3, index % 3)
        layout.addLayout(grid)

        middle = QHBoxLayout()
        middle.setSpacing(16)
        middle.addWidget(self._build_attention(), 3)
        middle.addWidget(self._build_health(), 2)
        layout.addLayout(middle)

        layout.addWidget(self._build_trend())
        layout.addWidget(self._build_activity(), 1)

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        words = QVBoxLayout()
        title = QLabel("Tổng quan hệ thống")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Theo dõi tình trạng tải, xử lý và đăng video trong một nơi.")
        subtitle.setObjectName("pageSubtitle")
        words.addWidget(title)
        words.addWidget(subtitle)
        header_layout.addLayout(words)
        header_layout.addStretch()
        pause = QPushButton("Tạm dừng tất cả")
        pause.clicked.connect(self._pause_all)
        start = QPushButton("Bắt đầu tất cả")
        start.setObjectName("primaryButton")
        start.clicked.connect(self._start_all)
        header_layout.addWidget(pause)
        header_layout.addWidget(start)
        return header

    def _build_attention(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Cần bạn xử lý", objectName="pageTitle"))
        self.attention_box = QVBoxLayout()
        self.attention_box.setSpacing(8)
        layout.addLayout(self.attention_box)
        layout.addStretch()
        return card

    def _build_health(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        layout.addWidget(QLabel("Tình trạng hệ thống", objectName="pageTitle"))
        self.tracker = StatusBadge("Theo dõi: chưa bắt đầu", "neutral")
        self.processor = StatusBadge("Xử lý: đang chờ", "neutral")
        self.uploader = StatusBadge("Đăng bài: đang chờ", "neutral")
        for badge in (self.tracker, self.processor, self.uploader):
            layout.addWidget(badge)
        self.tunnel = QLabel("Kênh nhận thông báo: chưa cấu hình")
        self.tunnel.setObjectName("muted")
        self.tunnel.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(self.tunnel)
        layout.addStretch()
        return card

    def _build_trend(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(QLabel("Video đã đăng (7 ngày gần nhất)", objectName="pageTitle"))
        self.chart = BarChart()
        layout.addWidget(self.chart)
        return card

    def _build_activity(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(QLabel("Hoạt động gần đây", objectName="pageTitle"))
        self.activity_table = DataTable()
        self.activity_table.set_columns(["Thời gian", "Nội dung"])
        activity_font = self.activity_table.font()
        activity_font.setWeight(QFont.DemiBold)
        self.activity_table.setFont(activity_font)
        layout.addWidget(self.activity_table)
        return card

    def refresh(self) -> None:
        summary = self.repository.dashboard_summary()
        for key, value in summary.items():
            self.cards[key].set_value(value)

        running = self.application.is_running
        self.set_running(running)
        self._render_attention(self.repository.attention_items())
        trend = self.repository.posting_trend(7)
        self.chart.set_data([item["label"] for item in trend], [item["count"] for item in trend])
        self._render_events(self.repository.recent_events(15))
        self._render_tunnel()

    def set_running(self, running: bool) -> None:
        label, kind = ("Đang hoạt động", "good") if running else ("Đã tạm dừng", "neutral")
        self.tracker.set_status(f"Theo dõi: {label}", kind)
        self.processor.set_status(f"Xử lý: {label if running else 'đang chờ'}", kind)
        upload_label = label if running else "đang chờ"
        publish_enabled = (
            bool(self.application.settings.tiktok_publish_enabled)
            or self.repository.any_account_publish_enabled()
        )
        if running and not publish_enabled:
            self.uploader.set_status("Đăng bài: đang chờ bật quyền đăng cho tài khoản", "warning")
        else:
            self.uploader.set_status(f"Đăng bài: {upload_label}", kind)

    def _render_tunnel(self) -> None:
        domain = (self.application.settings.public_domain or "").strip()
        if not domain:
            self.tunnel.setText("Kênh nhận thông báo: chưa cấu hình (đang dùng RSS dự phòng)")
            return
        mode = self.application.settings.tracking_mode
        if mode == "rss":
            self.tunnel.setText("Kênh nhận thông báo: đang dùng RSS")
        else:
            self.tunnel.setText(f"Kênh nhận thông báo: {domain}")

    def _render_attention(self, items: list[dict]) -> None:
        while self.attention_box.count():
            item = self.attention_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not items:
            self.attention_box.addWidget(
                EmptyState(
                    "Chưa có việc cần can thiệp",
                    "Khi cần đăng nhập lại, xác minh hoặc thử lại, việc đó sẽ xuất hiện tại đây.",
                )
            )
            return
        for entry in items:
            self.attention_box.addWidget(self._attention_row(entry))

    def _attention_row(self, entry: dict) -> QFrame:
        row = QFrame()
        row.setObjectName("attentionRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        kind = JOB_KIND_LABELS.get(entry.get("kind"), entry.get("kind"))
        title = entry.get("title") or "Video"
        detail = entry.get("error_message") or entry.get("status")
        text = QLabel(f"{kind} · {title}\n{detail}")
        text.setWordWrap(True)
        text.setStyleSheet("font-size: 9pt;")
        layout.addWidget(text, 1)

        retry = QPushButton("Thử lại")
        retry.setObjectName("primaryButton")
        retry.clicked.connect(lambda _=False, job_id=entry["job_id"]: self._retry(job_id))
        skip = QPushButton("Bỏ qua")
        skip.setObjectName("dangerButton")
        skip.clicked.connect(lambda _=False, job_id=entry["job_id"]: self._skip(job_id))
        layout.addWidget(retry)
        layout.addWidget(skip)
        return row

    def _retry(self, job_id: int) -> None:
        self.repository.retry_job(job_id)
        self.refresh()
        show_toast(self, "Đã đưa việc trở lại hàng đợi.", "success")

    def _skip(self, job_id: int) -> None:
        self.repository.cancel_job(job_id)
        self.refresh()
        show_toast(self, "Đã bỏ qua việc này.", "info")

    def _render_events(self, events: list[dict]) -> None:
        rows = []
        for event in events:
            stamp = format_local_time(event.get("created_at"), include_date=False)
            rows.append([stamp, event.get("message") or ""])
        self.activity_table.set_rows(rows)

    def _open_kpi(self, key: str) -> None:
        target_index, target_name = self.KPI_TARGETS[key]
        self._navigate(target_index)
        show_toast(self, f"Đã chuyển đến {target_name}. Bộ lọc chi tiết sẽ được áp dụng ở giai đoạn sau.", "info")
