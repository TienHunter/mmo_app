from __future__ import annotations

from enum import Enum


class ThemeMode(str, Enum):
    LIGHT = "light"
    DARK = "dark"


TOKENS: dict[ThemeMode, dict[str, str]] = {
    ThemeMode.LIGHT: {
        "window": "#F7F8FA", "surface": "#FFFFFF", "surface_alt": "#F1F4F8",
        "sidebar": "#162338", "sidebar_hover": "#22344E", "sidebar_active": "#2F6FED",
        "text": "#172033", "muted": "#64748B", "border": "#E2E8F0", "primary": "#2F6FED",
        "primary_hover": "#245DCE", "success": "#16803C", "warning": "#B45309", "danger": "#C53030",
        "disabled": "#94A3B8",
    },
    ThemeMode.DARK: {
        "window": "#111827", "surface": "#182235", "surface_alt": "#222E42",
        "sidebar": "#0B1220", "sidebar_hover": "#18263C", "sidebar_active": "#3B82F6",
        "text": "#F1F5F9", "muted": "#A5B4C7", "border": "#2F3E55", "primary": "#60A5FA",
        "primary_hover": "#93C5FD", "success": "#4ADE80", "warning": "#FBBF24", "danger": "#FB7185",
        "disabled": "#64748B",
    },
}

SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"sm": 6, "md": 10, "lg": 14}


def stylesheet(mode: ThemeMode) -> str:
    t = TOKENS[mode]
    return f"""
    * {{ font-family: 'Segoe UI'; font-size: 10pt; color: {t['text']}; }}
    QMainWindow, QWidget#centralRoot {{ background: {t['window']}; }}
    QWidget#sidebar {{ background: {t['sidebar']}; }}
    QLabel#sidebarBrand, QLabel#sidebarBrandSub, QToolButton#navButton {{ color: #FFFFFF; }}
    QLabel#sidebarBrand {{ font-size: 18pt; font-weight: 700; }}
    QLabel#sidebarBrandSub {{ color: #B8C7DB; font-size: 9pt; }}
    QToolButton#navButton {{ border: none; border-radius: {RADIUS['sm']}px; padding: 10px 12px; text-align: left; }}
    QToolButton#navButton:hover {{ background: {t['sidebar_hover']}; }}
    QToolButton#navButton:checked {{ background: {t['sidebar_active']}; font-weight: 600; }}
    QWidget#pageHeader, QFrame#card, QFrame#statusPill {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: {RADIUS['md']}px; }}
    QLabel#pageTitle {{ font-size: 20pt; font-weight: 700; }}
    QLabel#pageSubtitle, QLabel#muted {{ color: {t['muted']}; }}
    QLabel#kpiValue {{ font-size: 22pt; font-weight: 700; }}
    QLabel#kpiLabel {{ color: {t['muted']}; font-size: 9pt; font-weight: 600; }}
    QPushButton {{ border: 1px solid {t['border']}; border-radius: {RADIUS['sm']}px; background: {t['surface']}; padding: 8px 12px; font-weight: 600; }}
    QPushButton:hover {{ background: {t['surface_alt']}; }}
    QPushButton#primaryButton {{ background: {t['primary']}; border-color: {t['primary']}; color: #FFFFFF; }}
    QPushButton#primaryButton:hover {{ background: {t['primary_hover']}; }}
    QPushButton#dangerButton {{ color: {t['danger']}; }}
    QPushButton#tableActionButton {{ padding: 3px 8px; font-size: 9pt; font-weight: 500; }}
    QPushButton#editActionButton, QPushButton#toggleActionButton, QPushButton#dangerActionButton {{ padding: 3px 10px; font-size: 9pt; font-weight: 700; color: #FFFFFF; }}
    QPushButton#editActionButton {{ background: #2563EB; border-color: #2563EB; }}
    QPushButton#editActionButton:hover {{ background: #1D4ED8; }}
    QPushButton#toggleActionButton {{ background: #D97706; border-color: #D97706; }}
    QPushButton#toggleActionButton:hover {{ background: #B45309; }}
    QPushButton#dangerActionButton {{ background: #DC2626; border-color: #DC2626; }}
    QPushButton#dangerActionButton:hover {{ background: #B91C1C; }}
    QToolButton#themeButton {{ border: 1px solid {t['border']}; border-radius: {RADIUS['sm']}px; background: {t['surface']}; padding: 7px; }}
    QStatusBar {{ background: {t['surface']}; border-top: 1px solid {t['border']}; color: {t['muted']}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QFrame#emptyState {{ background: {t['surface']}; border: 1px dashed {t['border']}; border-radius: {RADIUS['lg']}px; }}
    QFrame#attentionRow {{ background: {t['surface_alt']}; border-radius: {RADIUS['sm']}px; }}
    QTableView {{ background: {t['surface']}; alternate-background-color: {t['surface_alt']}; border: 1px solid {t['border']}; border-radius: {RADIUS['md']}px; gridline-color: {t['border']}; selection-background-color: {t['primary']}; selection-color: #FFFFFF; }}
    QHeaderView::section {{ background: {t['surface_alt']}; color: {t['muted']}; border: none; border-bottom: 1px solid {t['border']}; border-right: 1px solid {t['border']}; padding: 8px 10px; font-weight: 600; }}
    QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: {RADIUS['sm']}px; padding: 7px 9px; selection-background-color: {t['primary']}; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {t['primary']}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QCheckBox {{ spacing: 7px; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 5px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {t['muted']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {t['border']}; border-radius: 5px; min-width: 24px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QMenu {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: {RADIUS['sm']}px; padding: 4px; }}
    QMenu::item {{ padding: 7px 18px; border-radius: {RADIUS['sm']}px; }}
    QMenu::item:selected {{ background: {t['surface_alt']}; }}
    QToolTip {{ background: {t['sidebar']}; color: #FFFFFF; border: none; padding: 6px 8px; border-radius: {RADIUS['sm']}px; }}
    """
