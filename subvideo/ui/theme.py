"""Visual theme (dark / light) for the whole application."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

DARK = {
    "bg": "#16181d",
    "panel": "#1f2229",
    "panel2": "#262a33",
    "border": "#323744",
    "text": "#e7e9ee",
    "muted": "#9aa3b2",
    "accent": "#4f8cff",
    "accent2": "#3a6fd8",
    "ok": "#3ecf8e",
    "warn": "#f5b94a",
    "err": "#ff6b6b",
    "sel": "#2d4a7a",
    "input": "#14161b",
}
LIGHT = {
    "bg": "#f3f5f9",
    "panel": "#ffffff",
    "panel2": "#eef1f6",
    "border": "#d5dae3",
    "text": "#1d2330",
    "muted": "#5f6b7d",
    "accent": "#2f6fed",
    "accent2": "#2459c4",
    "ok": "#1f9d63",
    "warn": "#c98a12",
    "err": "#d64545",
    "sel": "#cfe0ff",
    "input": "#ffffff",
}

C = dict(DARK)  # current colours (used by custom-painted widgets)


def qss(c: dict) -> str:
    return f"""
* {{ font-family: "Segoe UI", "Noto Sans", "Inter", Arial; font-size: 10pt; }}
QMainWindow, QWidget#Page, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c['bg']}; color: {c['text']}; }}
QWidget {{ color: {c['text']}; }}
QToolTip {{ background: {c['panel2']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
QLabel#PageTitle {{ font-size: 17pt; font-weight: 600; }}
QLabel#PageSubtitle, QLabel.hint, QLabel#Hint {{ color: {c['muted']}; }}
QLabel#Badge {{ background: {c['panel2']}; border: 1px solid {c['border']}; border-radius: 9px; padding: 2px 8px; color: {c['muted']}; }}
QLabel#Stat {{ font-size: 13pt; font-weight: 600; }}
QFrame#Card, QGroupBox {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QGroupBox {{ margin-top: 14px; padding: 12px 10px 10px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {c['muted']}; }}
QFrame#TopBar {{ background: {c['panel']}; border-bottom: 1px solid {c['border']}; }}
QListWidget#Sidebar {{ background: {c['panel']}; border: none; border-right: 1px solid {c['border']}; outline: none; padding: 6px 4px; }}
QListWidget#Sidebar::item {{ padding: 8px 10px; border-radius: 7px; margin: 1px 4px; color: {c['text']}; }}
QListWidget#Sidebar::item:selected {{ background: {c['sel']}; color: {c['text']}; }}
QListWidget#Sidebar::item:hover:!selected {{ background: {c['panel2']}; }}
QPushButton {{ background: {c['panel2']}; border: 1px solid {c['border']}; border-radius: 7px; padding: 6px 12px; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: {c['panel']}; border-color: {c['panel2']}; }}
QPushButton#Primary {{ background: {c['accent']}; border: 1px solid {c['accent2']}; color: white; font-weight: 600; padding: 8px 16px; }}
QPushButton#Primary:hover {{ background: {c['accent2']}; }}
QPushButton#Primary:disabled {{ background: {c['panel2']}; color: {c['muted']}; }}
QPushButton#Danger {{ color: {c['err']}; }}
QPushButton#Flat {{ background: transparent; border: none; padding: 4px 6px; }}
QPushButton#Flat:hover {{ background: {c['panel2']}; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px; }}
QToolButton:hover {{ background: {c['panel2']}; border-color: {c['border']}; }}
QToolButton:checked {{ background: {c['sel']}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTimeEdit {{
    background: {c['input']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 4px 6px;
    selection-background-color: {c['accent']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c['panel']}; border: 1px solid {c['border']}; selection-background-color: {c['sel']}; }}
QCheckBox, QRadioButton {{ spacing: 6px; }}
QSlider::groove:horizontal {{ height: 6px; background: {c['panel2']}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {c['text']}; width: 14px; margin: -5px 0; border-radius: 7px; }}
QProgressBar {{ background: {c['panel2']}; border: 1px solid {c['border']}; border-radius: 6px; text-align: center; height: 16px; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 5px; }}
QTableView, QTableWidget, QListWidget, QTreeWidget {{ background: {c['panel']}; alternate-background-color: {c['panel2']};
    border: 1px solid {c['border']}; border-radius: 8px; gridline-color: {c['border']}; selection-background-color: {c['sel']}; selection-color: {c['text']}; }}
QListWidget::item {{ padding: 4px 6px; }}
QHeaderView::section {{ background: {c['panel2']}; color: {c['muted']}; border: none; border-right: 1px solid {c['border']}; padding: 5px; font-weight: 600; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 8px; background: {c['panel']}; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 7px 14px; border: none; color: {c['muted']}; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; }}
QSplitter::handle {{ background: {c['border']}; }}
QSplitter::handle:horizontal {{ width: 2px; }}
QSplitter::handle:vertical {{ height: 2px; }}
QScrollBar:vertical {{ background: transparent; width: 11px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {c['panel']}; padding: 5px; color: {c['muted']}; }}
QStatusBar {{ background: {c['panel']}; color: {c['muted']}; }}
QMenu {{ background: {c['panel']}; border: 1px solid {c['border']}; }}
QMenu::item:selected {{ background: {c['sel']}; }}
"""


def apply_theme(app: QApplication, name: str = "dark") -> None:
    c = DARK if name != "light" else LIGHT
    C.clear()
    C.update(c)
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c["bg"]))
    pal.setColor(QPalette.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.Base, QColor(c["input"]))
    pal.setColor(QPalette.AlternateBase, QColor(c["panel2"]))
    pal.setColor(QPalette.Text, QColor(c["text"]))
    pal.setColor(QPalette.Button, QColor(c["panel2"]))
    pal.setColor(QPalette.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.Highlight, QColor(c["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, QColor(c["panel2"]))
    pal.setColor(QPalette.ToolTipText, QColor(c["text"]))
    pal.setColor(QPalette.PlaceholderText, QColor(c["muted"]))
    app.setPalette(pal)
    f = QFont("Segoe UI", 10)
    app.setFont(f)
    app.setStyleSheet(qss(c))
