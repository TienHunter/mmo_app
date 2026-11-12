from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...application import Application
from ..components import CopyableTable, EmptyState, TablePager, show_toast
from ..dialogs import ChannelDialog
from ..services.repository import UIRepository


class ChannelsPage(QWidget):
    def __init__(self, application: Application):
        super().__init__()
        self.application = application
        self.repository = UIRepository(application.database)
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        header = QFrame()
        header.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        words = QVBoxLayout()
        title = QLabel("Kênh theo dõi")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Thêm kênh YouTube và chọn tài khoản TikTok sẽ nhận video của kênh đó.")
        subtitle.setObjectName("pageSubtitle")
        words.addWidget(title)
        words.addWidget(subtitle)
        header_layout.addLayout(words)
        header_layout.addStretch()
        refresh = QPushButton("Làm mới")
        refresh.clicked.connect(self.refresh)
        header_layout.addWidget(refresh)
        add = QPushButton("Thêm kênh")
        add.setObjectName("primaryButton")
        add.clicked.connect(self._add_channel)
        header_layout.addWidget(add)
        layout.addWidget(header)

        self.table = CopyableTable(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Kênh", "Link", "Tài khoản TikTok", "Cấu hình xử lý",
            "Theo dõi", "Thao tác",
        ])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        for index, width in enumerate((190, 240, 140, 150, 100, 220)):
            self.table.setColumnWidth(index, width)
        layout.addWidget(self.table, 1)
        self.pager = TablePager()
        self.pager.page_changed.connect(self._render_page)
        layout.addWidget(self.pager)

    def refresh(self) -> None:
        self._all_rows = self.repository.list_channels()
        self.pager.reset()
        self.pager.set_total(len(self._all_rows))
        self._render_page()

    def _render_page(self) -> None:
        self.table.setRowCount(0)
        for channel in self.pager.page_rows(getattr(self, "_all_rows", [])):
            row = self.table.rowCount()
            self.table.insertRow(row)
            name_item = QTableWidgetItem(channel["title"] or channel["channel_id"])
            name_item.setData(Qt.UserRole, channel["channel_id"])
            active = bool(channel["active"])
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(channel["source_url"] or ""))
            self.table.setItem(row, 2, QTableWidgetItem(channel["account_name"] or "Chưa gán"))
            speed = float(channel.get("account_speed") or 1.0)
            zoom_width = channel.get("account_zoom_width") or 100
            zoom_height = channel.get("account_zoom_height") or 100
            processing = (
                f"{speed:g}x · {zoom_width}×{zoom_height}%"
                if channel["account_name"] else "Chưa gán"
            )
            self.table.setItem(row, 3, QTableWidgetItem(processing))
            self.table.setItem(row, 4, QTableWidgetItem("Đang theo dõi" if active else "Đã tạm dừng"))
            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(2, 2, 2, 2)
            actions_layout.setSpacing(4)
            edit_button = QPushButton("Sửa")
            edit_button.setObjectName("editActionButton")
            edit_button.clicked.connect(
                lambda _=False, c=channel: self._edit_channel(c))
            toggle_button = QPushButton("Tạm dừng" if active else "Bật")
            toggle_button.setObjectName("toggleActionButton")
            toggle_button.clicked.connect(
                lambda _=False, c=channel: self._toggle_channel(c))
            delete_button = QPushButton("Xóa")
            delete_button.setObjectName("dangerActionButton")
            delete_button.clicked.connect(
                lambda _=False, c=channel: self._delete_channel(c))
            actions_layout.addWidget(edit_button)
            actions_layout.addWidget(toggle_button)
            actions_layout.addWidget(delete_button)
            self.table.setItem(row, 5, QTableWidgetItem(""))
            self.table.setCellWidget(row, 5, actions)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.refresh()

    def _add_channel(self) -> None:
        accounts = self.repository.list_accounts()
        if not accounts:
            show_toast(self, "Hãy thêm tài khoản TikTok trước.", "warning")
            return
        dialog = ChannelDialog(accounts, self.application.youtube.resolve_channel_url, self)
        if dialog.exec() != ChannelDialog.Accepted:
            return
        values = dialog.result_values()
        self.repository.save_channel_route(
            values["channel_id"],
            values["title"],
            values["source_url"],
            values["account_id"],
            values["segment_seconds"],
        )
        self._sync_settings_channels()
        self.application.refresh_websub_subscriptions()
        self.refresh()
        show_toast(self, "Đã thêm kênh và gán vào tài khoản TikTok.", "success")

    def _edit_channel(self, channel: dict) -> None:
        accounts = self.repository.list_accounts()
        if not accounts:
            show_toast(self, "Chưa có tài khoản TikTok để gán.", "warning")
            return
        labels = [
            f"{account['name']}  (#{account['id']})" for account in accounts]
        current = next(
            (index for index, account in enumerate(accounts)
             if int(account["id"]) == int(channel.get("account_id") or 0)),
            0,
        )
        label = channel["title"] or channel["channel_id"]
        selected, accepted = QInputDialog.getItem(
            self, "Sửa kênh theo dõi",
            f"Tài khoản TikTok nhận video của {label}:",
            labels, current, False,
        )
        if not accepted:
            return
        account = accounts[labels.index(selected)]
        try:
            self.repository.update_channel_account(
                channel["channel_id"], int(account["id"]))
        except ValueError as exc:
            show_toast(self, str(exc), "error")
            return
        self.refresh()
        show_toast(self, f"Đã gán kênh {label} cho {account['name']}.", "success")

    def _delete_channel(self, channel: dict) -> None:
        label = channel["title"] or channel["channel_id"]
        if QMessageBox.question(
            self, "Xóa kênh theo dõi",
            f"Xóa theo dõi kênh {label}?\nVideo đã tải/xử lý vẫn được giữ lại.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.repository.delete_channel(channel["channel_id"])
        self._sync_settings_channels()
        self.refresh()
        show_toast(self, f"Đã xóa kênh {label}.", "success")

    def _toggle_channel(self, channel: dict) -> None:
        self.repository.set_channel_active(
            channel["channel_id"], not bool(channel["active"]))
        self._sync_settings_channels()
        self.application.refresh_websub_subscriptions()
        self.refresh()

    def _sync_settings_channels(self) -> None:
        rows = self.repository.active_channels()
        self.application.settings.channel_ids = [row["channel_id"] for row in rows]
        self.application.settings.youtube_channel_urls = [row["source_url"] for row in rows]
        self.application.settings.save()
