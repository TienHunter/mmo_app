from __future__ import annotations

import re
from enum import Enum


class ThemeMode(str, Enum):
    LIGHT = "light"
    DARK = "dark"


DEFAULT_FONT_PT = 10.0

TOKENS: dict[ThemeMode, dict[str, str]] = {
    ThemeMode.LIGHT: {
        "window": "#F4F6FA", "surface": "#FFFFFF", "surface_alt": "#EDF1F7",
        "sidebar": "#0E1A2B", "sidebar_hover": "#1A2A42", "sidebar_active": "#2F6FED",
        "text": "#111C2E", "muted": "#5C6B80", "border": "#DCE3EE",
        "primary": "#2F6FED", "primary_hover": "#1F5AD4", "primary_soft": "#E8F0FE",
        "success": "#15803D", "success_soft": "#E7F6EC",
        "warning": "#B45309", "warning_soft": "#FCF0DE",
        "danger": "#C53030", "danger_soft": "#FCECEC",
        "disabled": "#9AA8BB",
    },
    ThemeMode.DARK: {
        "window": "#0B1220", "surface": "#131D2E", "surface_alt": "#1B2739",
        "sidebar": "#08111F", "sidebar_hover": "#15233A", "sidebar_active": "#2F6FED",
        "text": "#E7EDF6", "muted": "#9DAEC4", "border": "#27364D",
        "primary": "#5B95FF", "primary_hover": "#7AA9FF", "primary_soft": "#1C2F4F",
        "success": "#4ADE80", "success_soft": "#123827",
        "warning": "#FBBF24", "warning_soft": "#3A2A12",
        "danger": "#FB7185", "danger_soft": "#3B1D25",
        "disabled": "#5A6B82",
    },
}

SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"sm": 8, "md": 12, "lg": 16}


def stylesheet(mode: ThemeMode, font_pt: float = DEFAULT_FONT_PT) -> str:
    t = TOKENS[mode]
    css = f"""
    * {{ font-family: 'Segoe UI'; font-size: 10pt; color: {t['text']}; }}
    QMainWindow, QWidget#centralRoot, QDialog {{ background: {t['window']}; }}
    QWidget#sidebar {{ background: {t['sidebar']}; border-right: 1px solid rgba(255,255,255,0.07); }}
    QLabel#sidebarBrand {{ color: #FFFFFF; font-size: 17pt; font-weight: 700; letter-spacing: 0.5px; }}
    QLabel#sidebarBrandSub {{ color: #AEBDD3; font-size: 9pt; }}
    QToolButton#navButton {{ color: #C9D6E8; border: none; border-left: 3px solid transparent; border-radius: {RADIUS['sm']}px; padding: 10px 12px 10px 9px; text-align: left; font-weight: 500; }}
    QToolButton#navButton:hover {{ background: {t['sidebar_hover']}; color: #FFFFFF; }}
    QToolButton#navButton:checked {{ background: {t['sidebar_active']}; border-left-color: #A9C8FF; color: #FFFFFF; font-weight: 600; }}
    #pageHeader, #card, #statusPill {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: {RADIUS['md']}px; }}
    QLabel#pageTitle {{ font-size: 20pt; font-weight: 700; letter-spacing: -0.3px; }}
    QFrame#card QLabel#pageTitle {{ font-size: 12pt; font-weight: 700; letter-spacing: 0; }}
    QLabel#topbarTitle {{ font-size: 15pt; font-weight: 700; }}
    QLabel#pageSubtitle, QLabel#muted {{ color: {t['muted']}; }}
    QLabel#kpiLabel {{ color: {t['muted']}; font-size: 9pt; font-weight: 600; letter-spacing: 0.3px; }}
    QLabel#kpiValue {{ font-size: 26pt; font-weight: 700; }}
    QLabel#eventClock {{ font-weight: 700; font-size: 9pt; color: {t['primary']}; }}
    QLabel[statusKind="good"] {{ color: {t['success']}; }}
    QLabel[statusKind="warning"] {{ color: {t['warning']}; }}
    QLabel[statusKind="danger"] {{ color: {t['danger']}; }}
    QLabel[statusKind="neutral"] {{ color: {t['muted']}; }}
    QLabel[statusKind] {{ font-weight: 600; padding: 3px 0; }}
    QLabel#stageDot {{ font-size: 14pt; }}
    QLabel[stageState] {{ font-weight: 600; }}
    QLabel[stageState="running"] {{ color: {t['primary']}; }}
    QLabel[stageState="waiting"], QLabel[stageState="warning"] {{ color: {t['warning']}; }}
    QLabel[stageState="success"] {{ color: {t['success']}; }}
    QLabel[stageState="failed"] {{ color: {t['danger']}; }}
    QLabel[stageState="idle"] {{ color: {t['muted']}; }}
    QPushButton {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: {RADIUS['sm']}px; padding: 7px 14px; font-weight: 600; color: {t['text']}; }}
    QPushButton:hover {{ background: {t['surface_alt']}; }}
    QPushButton:pressed {{ background: {t['border']}; }}
    QPushButton:disabled {{ color: {t['disabled']}; background: {t['surface_alt']}; }}
    QPushButton#primaryButton {{ background: {t['primary']}; border-color: {t['primary']}; color: #FFFFFF; }}
    QPushButton#primaryButton:hover {{ background: {t['primary_hover']}; border-color: {t['primary_hover']}; }}
    QPushButton#primaryButton:pressed {{ background: {t['primary']}; }}
    QPushButton#primaryButton:disabled {{ background: {t['disabled']}; border-color: {t['disabled']}; color: #FFFFFF; }}
    QPushButton#dangerButton {{ color: {t['danger']}; border-color: {t['danger']}; background: {t['surface']}; }}
    QPushButton#dangerButton:hover {{ background: {t['danger_soft']}; }}
    QPushButton#tableActionButton {{ padding: 3px 9px; font-size: 9pt; font-weight: 600; }}
    QPushButton#editActionButton, QPushButton#toggleActionButton, QPushButton#dangerActionButton {{ padding: 3px 10px; font-size: 9pt; font-weight: 700; color: #FFFFFF; border: none; border-radius: 6px; }}
    QPushButton#editActionButton {{ background: #2563EB; }}
    QPushButton#editActionButton:hover {{ background: #1D4ED8; }}
    QPushButton#toggleActionButton {{ background: #D97706; }}
    QPushButton#toggleActionButton:hover {{ background: #B45309; }}
    QPushButton#dangerActionButton {{ background: #DC2626; }}
    QPushButton#dangerActionButton:hover {{ background: #B91C1C; }}
    QToolButton#themeButton {{ border: 1px solid {t['border']}; border-radius: {RADIUS['sm']}px; background: {t['surface']}; padding: 7px 10px; font-weight: 600; color: {t['text']}; }}
    QToolButton#themeButton:hover {{ background: {t['surface_alt']}; }}
    QToolButton#themeButton:checked {{ background: {t['primary_soft']}; border-color: {t['primary']}; color: {t['primary']}; }}
    QStatusBar {{ background: {t['surface']}; border-top: 1px solid {t['border']}; color: {t['muted']}; }}
    QStatusBar::item {{ border: none; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget, QStackedWidget {{ background: transparent; }}
    QFrame#emptyState {{ background: {t['surface_alt']}; border: 1px dashed {t['border']}; border-radius: {RADIUS['lg']}px; }}
    QFrame#attentionRow {{ background: {t['surface_alt']}; border-radius: {RADIUS['sm']}px; }}
    QTableView, QTableWidget {{ background: {t['surface']}; alternate-background-color: {t['surface_alt']}; border: 1px solid {t['border']}; border-radius: {RADIUS['md']}px; gridline-color: {t['border']}; selection-background-color: {t['primary']}; selection-color: #FFFFFF; }}
    QTableView::item, QTableWidget::item {{ padding: 0 8px; }}
    QHeaderView::section {{ background: {t['surface_alt']}; color: {t['muted']}; border: none; border-bottom: 1px solid {t['border']}; border-right: 1px solid {t['border']}; padding: 9px 10px; font-weight: 600; }}
    QHeaderView::section:hover {{ background: {t['border']}; }}
    QTableCornerButton::section {{ background: {t['surface_alt']}; border: none; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: {RADIUS['sm']}px; padding: 7px 10px; selection-background-color: {t['primary']}; selection-color: #FFFFFF; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {t['primary']}; }}
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{ color: {t['disabled']}; background: {t['surface_alt']}; }}
    QComboBox {{ padding-right: 26px; }}
    QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: {RADIUS['sm']}px; padding: 4px; outline: 0; selection-background-color: {t['primary_soft']}; selection-color: {t['text']}; }}
    QCheckBox {{ spacing: 8px; }}
    QProgressBar {{ background: {t['surface_alt']}; border: none; border-radius: 2px; }}
    QProgressBar::chunk {{ background: {t['primary']}; border-radius: 2px; }}
    QScrollBar:vertical {{ background: transparent; width: 16px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 6px; min-height: 32px; }}
    QScrollBar::handle:vertical:hover {{ background: {t['muted']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 16px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {t['border']}; border-radius: 6px; min-width: 32px; }}
    QScrollBar::handle:horizontal:hover {{ background: {t['muted']}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QMenu {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: {RADIUS['md']}px; padding: 6px; }}
    QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {t['primary_soft']}; color: {t['text']}; }}
    QMenu::separator {{ height: 1px; background: {t['border']}; margin: 4px 8px; }}
    QToolTip {{ background: {t['sidebar']}; color: #FFFFFF; border: none; padding: 6px 9px; border-radius: 6px; }}
    QSplitter::handle:horizontal {{ background: {t['border']}; }}
    QSplitter::handle:horizontal:hover {{ background: {t['primary']}; }}
    """
    scale = float(font_pt) / DEFAULT_FONT_PT
    if scale == 1:
        return css
    return re.sub(
        r"font-size:\s*([\d.]+)pt",
        lambda match: f"font-size: {max(6.0, float(match.group(1)) * scale):.2f}pt",
        css,
    )
