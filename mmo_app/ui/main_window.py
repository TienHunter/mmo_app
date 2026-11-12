from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QMenu, QPushButton, QStackedWidget, QStatusBar,
    QStyle, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget,
)

from ..application import Application
from ..services import NOTIFICATION_BELL
from .components import ActivityPanel
from .controllers import RuntimeController
from .i18n import vi
from .pages.accounts_page import AccountsPage
from .pages.channels_page import ChannelsPage
from .pages.dashboard_page import DashboardPage
from .pages.logs_page import LogsPage
from .pages.queue_page import QueuePage
from .pages.settings_page import SettingsPage
from .pages.videos_page import VideosPage
from .theme import ThemeMode, stylesheet


class MainWindow(QMainWindow):
    """Phase-2 application shell. Business logic remains in Application/services."""

    pipeline_state_changed = Signal(bool)

    def __init__(self, application: Application):
        super().__init__()
        self.application = application
        self.controller = RuntimeController(application)
        self.preferences = QSettings("MMOApp", "MMOApp")
        self._theme_mode = ThemeMode(self.preferences.value("ui/theme", ThemeMode.LIGHT.value))
        self._nav: list[QToolButton] = []
        self.setWindowTitle("MMO App")
        self.setMinimumSize(QSize(1100, 680))
        self.resize(self.preferences.value("ui/size", QSize(1360, 820)))
        self.move(self.preferences.value("ui/position", self.pos()))
        self._build()
        self._apply_theme()
        self._restore_activity_panel()
        self.pipeline_state_changed.connect(self._set_pipeline_state)
        self.controller.state_changed.connect(self.pipeline_state_changed.emit)
        self.controller.operation_failed.connect(self._show_operation_error)
        self.controller.operation_busy.connect(self._set_operation_busy)

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("centralRoot")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        self.dashboard = DashboardPage(self.application, self._start_pipeline, self._pause_pipeline, self._navigate)
        self.stack.addWidget(self.dashboard)
        self.accounts = AccountsPage(self.application)
        self.stack.addWidget(self.accounts)
        self.videos = VideosPage(self.application)
        self.stack.addWidget(self.videos)
        self.channels = ChannelsPage(self.application)
        self.stack.addWidget(self.channels)
        self.queue = QueuePage(self.application)
        self.stack.addWidget(self.queue)
        self.logs = LogsPage(self.application)
        self.stack.addWidget(self.logs)
        self.settings_page = SettingsPage(self.application)
        self.stack.addWidget(self.settings_page)
        content_layout.addWidget(self.stack, 1)
        layout.addWidget(content, 1)
        self.activity_panel = ActivityPanel(self.application)
        layout.addWidget(self.activity_panel)
        self._build_statusbar()
        self._build_tray()

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(238)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 20, 12, 16)
        brand = QLabel("MMO App")
        brand.setObjectName("sidebarBrand")
        sub = QLabel("Video Pipeline")
        sub.setObjectName("sidebarBrandSub")
        layout.addWidget(brand)
        layout.addWidget(sub)
        layout.addSpacing(24)
        navigation = [
            ("⌂", vi.NAV_DASHBOARD), ("◉", vi.NAV_ACCOUNTS), ("▣", vi.NAV_VIDEOS),
            ("◌", vi.NAV_CHANNELS), ("◷", vi.NAV_QUEUE), ("≡", vi.NAV_LOGS), ("⚙", vi.NAV_SETTINGS),
        ]
        for index, (icon, label) in enumerate(navigation):
            button = QToolButton()
            button.setObjectName("navButton")
            button.setText(f"{icon}   {label}")
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(lambda checked=False, page=index: self._navigate(page))
            layout.addWidget(button)
            self._nav.append(button)
        layout.addStretch()
        version = QLabel("Phiên bản giao diện mới · Giai đoạn 2")
        version.setObjectName("sidebarBrandSub")
        version.setWordWrap(True)
        layout.addWidget(version)
        return sidebar

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("pageHeader")
        bar.setFixedHeight(64)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 10, 20, 10)
        self.page_name = QLabel(vi.NAV_DASHBOARD)
        self.page_name.setObjectName("pageTitle")
        self.page_name.setStyleSheet("font-size: 15pt;")
        layout.addWidget(self.page_name)
        layout.addStretch()
        self.activity_button = QToolButton()
        self.activity_button.setObjectName("themeButton")
        self.activity_button.setText("Hoạt động")
        self.activity_button.setCheckable(True)
        self.activity_button.setToolTip("Ẩn/hiện bảng trạng thái hoạt động")
        self.activity_button.toggled.connect(self._toggle_activity_panel)
        layout.addWidget(self.activity_button)
        self.theme_button = QToolButton()
        self.theme_button.setObjectName("themeButton")
        self.theme_button.setToolTip("Chuyển giao diện sáng/tối")
        self.theme_button.clicked.connect(self._toggle_theme)
        layout.addWidget(self.theme_button)
        return bar

    def _build_statusbar(self) -> None:
        status = QStatusBar()
        self.setStatusBar(status)
        self.tunnel_status = QLabel(vi.TUNNEL_UNKNOWN)
        self.task_status = QLabel(vi.TASKS_IDLE)
        self.disk_status = QLabel("Dung lượng ổ đĩa: sẽ kiểm tra khi kết nối dữ liệu")
        self.stop_bell_button = QPushButton("Tắt chuông")
        self.stop_bell_button.setObjectName("primaryButton")
        self.stop_bell_button.setToolTip("Dừng chuông video mới")
        self.stop_bell_button.setVisible(False)
        self.stop_bell_button.clicked.connect(NOTIFICATION_BELL.stop)
        status.addWidget(self.tunnel_status, 1)
        status.addPermanentWidget(self.stop_bell_button)
        status.addPermanentWidget(self.task_status)
        status.addPermanentWidget(self.disk_status)
        self.bell_timer = QTimer(self)
        self.bell_timer.setInterval(500)
        self.bell_timer.timeout.connect(self._sync_bell_button)
        self.bell_timer.start()

    def _sync_bell_button(self) -> None:
        self.stop_bell_button.setVisible(NOTIFICATION_BELL.is_playing)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.SP_ComputerIcon), self)
        menu = QMenu(self)
        show_action = QAction("Mở MMO App", self)
        show_action.triggered.connect(self._show_window)
        exit_action = QAction("Thoát hoàn toàn", self)
        exit_action.triggered.connect(self._exit_from_tray)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self._show_window() if reason == QSystemTrayIcon.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _navigate(self, index: int) -> None:
        for nav_index, button in enumerate(self._nav):
            button.setChecked(nav_index == index)
        self.stack.setCurrentIndex(index)
        self.page_name.setText(self._nav[index].text().split("   ", 1)[-1])

    def _restore_activity_panel(self) -> None:
        visible = bool(self.preferences.value("ui/activity_panel", True, type=bool))
        self.activity_button.blockSignals(True)
        self.activity_button.setChecked(visible)
        self.activity_button.blockSignals(False)
        self.activity_panel.setVisible(visible)

    def _toggle_activity_panel(self, visible: bool) -> None:
        self.activity_panel.setVisible(bool(visible))
        self.preferences.setValue("ui/activity_panel", bool(visible))

    def _toggle_theme(self) -> None:
        self._theme_mode = ThemeMode.DARK if self._theme_mode == ThemeMode.LIGHT else ThemeMode.LIGHT
        self.preferences.setValue("ui/theme", self._theme_mode.value)
        self._apply_theme()

    def _apply_theme(self) -> None:
        QApplication.instance().setStyleSheet(stylesheet(self._theme_mode))
        self.theme_button.setText("☀" if self._theme_mode == ThemeMode.DARK else "◐")
        self.theme_button.setToolTip("Chuyển sang giao diện sáng" if self._theme_mode == ThemeMode.DARK else "Chuyển sang giao diện tối")

    def _start_pipeline(self) -> None:
        self.controller.start_all()

    def _pause_pipeline(self) -> None:
        if QMessageBox.question(self, "Tạm dừng toàn bộ", "Tất cả tác vụ mới sẽ tạm dừng. Bạn có muốn tiếp tục?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.controller.pause_all()

    def _set_pipeline_state(self, running: bool) -> None:
        self.dashboard.set_running(running)
        self.task_status.setText("Hệ thống đang hoạt động" if running else vi.TASKS_IDLE)

    def _set_operation_busy(self, busy: bool) -> None:
        if busy:
            self.task_status.setText("Đang thực hiện yêu cầu…")

    def _show_operation_error(self, message: str) -> None:
        QMessageBox.warning(self, "Chưa thể hoàn thành", f"{message}\n\nHãy kiểm tra cấu hình và thử lại.")

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _exit_from_tray(self) -> None:
        self.tray.hide()
        self.application.shutdown()
        QApplication.quit()

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        self.preferences.setValue("ui/size", self.size())
        self.preferences.setValue("ui/position", self.pos())
        answer = QMessageBox.question(
            self, "Thoát MMO App", "Bạn có muốn dừng hoàn toàn ứng dụng?\n\nChọn Không để tiếp tục chạy dưới khay hệ thống.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.No:
            if self.tray.isVisible():
                self.hide()
                self.tray.showMessage("MMO App", "Ứng dụng vẫn đang chạy trong khay hệ thống.")
            else:
                QMessageBox.information(self, "Khay hệ thống không khả dụng", "Không thể ẩn ứng dụng vì máy không có khay hệ thống khả dụng.")
            event.ignore()
            return
        self.tray.hide()
        self.application.shutdown()
        event.accept()


class _WheelValueGuard(QObject):
    """Prevent the mouse wheel from changing spin boxes and combo boxes."""

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Wheel and isinstance(
                obj, (QAbstractSpinBox, QComboBox)):
            return True
        return False


def run() -> None:
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("MMOApp")
    qt_app.setOrganizationName("MMOApp")
    qt_app._wheel_guard = _WheelValueGuard(qt_app)
    qt_app.installEventFilter(qt_app._wheel_guard)
    window = MainWindow(Application())
    window.show()
    sys.exit(qt_app.exec())
