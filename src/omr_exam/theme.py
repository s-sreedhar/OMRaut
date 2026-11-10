"""Light, high-contrast visual theme. Forced so Windows dark mode never makes the app dark."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#2563EB"
ACCENT_DARK = "#1D4ED8"
ACCENT_SOFT = "#EFF4FF"
BG = "#F4F6FA"
CARD = "#FFFFFF"
BORDER = "#E2E8F0"
TEXT = "#0F172A"
MUTED = "#64748B"
GOOD = "#16A34A"
WARN = "#D97706"
BAD = "#DC2626"
WARN_BG = "#FEF3C7"
BAD_BG = "#FEE2E2"
GOOD_BG = "#DCFCE7"

STYLESHEET = f"""
* {{ font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif; font-size: 14px; color: {TEXT}; }}
QMainWindow, QDialog, QStackedWidget, QScrollArea, #page {{ background: {BG}; }}
QToolTip {{ background: {TEXT}; color: white; border: none; padding: 6px 8px; }}

#sidebar {{ background: {CARD}; border-right: 1px solid {BORDER}; }}
#brand {{ font-size: 18px; font-weight: 700; color: {TEXT}; padding: 4px 8px 0 8px; }}
#brandSub {{ color: {MUTED}; font-size: 12px; padding: 0 8px 12px 8px; }}
QPushButton#nav {{ text-align: left; padding: 10px 14px; border: none; border-radius: 8px;
    background: transparent; color: {MUTED}; font-weight: 600; }}
QPushButton#nav:hover {{ background: {BG}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}

#topbar {{ background: {CARD}; border-bottom: 1px solid {BORDER}; }}
#pageTitle {{ font-size: 24px; font-weight: 700; }}
#pageSub {{ color: {MUTED}; font-size: 14px; }}
#sectionTitle {{ font-size: 16px; font-weight: 700; }}
#muted {{ color: {MUTED}; }}
#hint {{ color: {MUTED}; font-size: 13px; }}

#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
#statValue {{ font-size: 28px; font-weight: 700; }}
#statLabel {{ color: {MUTED}; font-size: 13px; }}
#banner {{ background: {ACCENT_SOFT}; border: 1px solid #BFD3FF; border-radius: 10px; color: {ACCENT_DARK}; padding: 10px 14px; }}
#warnBanner {{ background: {WARN_BG}; border: 1px solid #FCD34D; border-radius: 10px; color: #92400E; padding: 10px 14px; }}
#goodBanner {{ background: {GOOD_BG}; border: 1px solid #86EFAC; border-radius: 10px; color: #166534; padding: 10px 14px; }}
#stepNumber {{ background: {ACCENT}; color: white; border-radius: 13px; font-weight: 700; min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; qproperty-alignment: AlignCenter; }}

QPushButton {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 16px; font-weight: 600; }}
QPushButton:hover {{ background: {BG}; border-color: #CBD5E1; }}
QPushButton:pressed {{ background: #E8EDF5; }}
QPushButton:disabled {{ color: #A0AEC0; background: #F8FAFC; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; }}
QPushButton#primary:hover {{ background: {ACCENT_DARK}; }}
QPushButton#primary:disabled {{ background: #93B4F5; border-color: #93B4F5; color: white; }}
QPushButton#danger {{ color: {BAD}; }}
QPushButton#danger:hover {{ background: {BAD_BG}; border-color: #FCA5A5; }}
QPushButton#big {{ padding: 16px 20px; font-size: 15px; border-radius: 12px; text-align: left; }}
QPushButton#templateCard {{ background: {CARD}; border: 2px solid {BORDER}; border-radius: 12px; padding: 12px; text-align: left; font-weight: 400; }}
QPushButton#templateCard:hover {{ border-color: #93B4F5; background: {ACCENT_SOFT}; }}
QPushButton#templateCard:checked {{ border-color: {ACCENT}; background: {ACCENT_SOFT}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {CARD}; border: 1px solid #CBD5E1; border-radius: 8px; padding: 7px 10px;
    selection-background-color: {ACCENT}; selection-color: white; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 2px solid {ACCENT}; padding: 6px 9px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1.5px solid #94A3B8; border-radius: 4px; background: white; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: none; }}

QTableWidget, QTableView {{ background: {CARD}; alternate-background-color: #F8FAFC; border: 1px solid {BORDER}; border-radius: 10px;
    gridline-color: #EEF2F7; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; }}
QTableWidget::item {{ padding: 4px 8px; }}
QHeaderView::section {{ background: #F8FAFC; color: {MUTED}; font-weight: 600; border: none; border-bottom: 1px solid {BORDER}; padding: 8px; }}
QTableCornerButton::section {{ background: #F8FAFC; border: none; }}

QListWidget {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px; outline: none; }}
QListWidget::item {{ padding: 10px; border-radius: 8px; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QListWidget::item:hover {{ background: {BG}; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 10px; background: {CARD}; top: -1px; }}
QTabBar::tab {{ padding: 8px 16px; background: transparent; color: {MUTED}; font-weight: 600; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #CBD5E1; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #94A3B8; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #CBD5E1; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QProgressBar {{ border: 1px solid {BORDER}; border-radius: 6px; text-align: center; background: white; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QMessageBox {{ background: {CARD}; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 10px; margin-top: 12px; padding: 14px 10px 10px 10px; background: {CARD}; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {MUTED}; }}
"""


def apply_light_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, CARD), (QPalette.AlternateBase, "#F8FAFC"),
        (QPalette.ToolTipBase, TEXT), (QPalette.ToolTipText, "#FFFFFF"), (QPalette.Text, TEXT),
        (QPalette.Button, CARD), (QPalette.ButtonText, TEXT), (QPalette.BrightText, "#FFFFFF"),
        (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#FFFFFF"), (QPalette.PlaceholderText, "#94A3B8"),
        (QPalette.Link, ACCENT),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLESHEET)
