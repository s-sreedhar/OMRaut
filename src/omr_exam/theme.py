"""Design tokens and the application stylesheet.

The theme is forced light so Windows dark mode never produces a half-dark window. Every
colour, radius, and type size used by the interface is defined here; widgets refer to
these tokens instead of hard-coding values.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QWidget

# ---- colour tokens ---------------------------------------------------------
ACCENT = "#0F766E"
ACCENT_DARK = "#0B5C56"
ACCENT_SOFT = "#E2F1EE"
ACCENT_BORDER = "#A6D3CC"

BG = "#F4F1EA"          # window canvas
CARD = "#FFFFFF"        # raised surface
SUBTLE = "#FAF8F3"      # table headers, inset areas
BORDER = "#E1DCCF"
BORDER_STRONG = "#C3BCA9"
GRID_LINE = "#BDB5A0"      # cell borders in spreadsheet-style tables
ROW_ACTIVE = "#EAF4F1"     # band behind the row that holds the cursor
TEXT = "#1D2420"
TEXT_2 = "#3A433E"
MUTED = "#656E68"
FAINT = "#979F99"
FIELD_BORDER = "#CBC5B3"
FIELD_HOVER = "#8FB0A6"

SIDEBAR = "#16201D"
SIDEBAR_HOVER = "#202C28"
SIDEBAR_ACTIVE = "#2B3D37"
SIDEBAR_TEXT = "#BAC5C0"
SIDEBAR_MUTED = "#80908A"

GOOD = "#3D7A1E"
WARN = "#B45309"
BAD = "#B42318"
GOOD_BG = "#E7F1DB"
WARN_BG = "#FCEFCB"
BAD_BG = "#FADFDB"
GOOD_BORDER = "#B4D697"
WARN_BORDER = "#EBCB6B"
BAD_BORDER = "#EFB0A7"

# tone -> (foreground, background, border); used by pills, banners and painted widgets.
TONES: dict[str, tuple[str, str, str]] = {
    "info": (ACCENT_DARK, ACCENT_SOFT, ACCENT_BORDER),
    "good": ("#2E5E14", GOOD_BG, GOOD_BORDER),
    "warn": ("#7A3E0A", WARN_BG, WARN_BORDER),
    "bad": ("#8E1B12", BAD_BG, BAD_BORDER),
    "neutral": (TEXT_2, "#ECE8DD", BORDER),
}

FONT_FAMILY = "Segoe UI"


def _tone_rules(selector: str, extra: str = "") -> str:
    return "\n".join(
        f'{selector}[tone="{tone}"] {{ color: {fg}; background: {bg}; border: 1px solid {border}; {extra} }}'
        for tone, (fg, bg, border) in TONES.items())


def _stylesheet(assets: dict[str, str]) -> str:
    return f"""
* {{ font-family: "{FONT_FAMILY}", "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif; font-size: 14px; color: {TEXT}; }}
QMainWindow, QDialog, QScrollArea, #page, QStackedWidget#pages {{ background: {BG}; }}
QStackedWidget {{ background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QToolTip {{ background: {TEXT}; color: white; border: none; padding: 6px 9px; border-radius: 4px; font-size: 13px; }}
QLabel {{ background: transparent; }}

/* ---- sidebar ---- */
#sidebar {{ background: {SIDEBAR}; }}
#sidebar QLabel {{ color: {SIDEBAR_TEXT}; }}
#sidebar QLabel#navBadge {{ color: #1F1300; }}
#brand {{ font-size: 16px; font-weight: 700; color: white; }}
#brandSub {{ color: {SIDEBAR_MUTED}; font-size: 12px; }}
#navGroup {{ color: {SIDEBAR_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 1px; padding: 14px 12px 4px 12px; }}
#sidebarFoot {{ color: {SIDEBAR_MUTED}; font-size: 12px; }}
QPushButton#nav {{ text-align: left; padding: 9px 12px; border: 1px solid transparent; border-radius: 8px;
    background: transparent; color: {SIDEBAR_TEXT}; font-weight: 600; }}
QPushButton#nav:hover {{ background: {SIDEBAR_HOVER}; color: white; }}
QPushButton#nav:checked {{ background: {SIDEBAR_ACTIVE}; color: white; }}
QPushButton#nav:focus {{ border: 1px solid #5FBFB3; }}
QPushButton#nav:disabled {{ color: #6C7B75; background: transparent; }}
#navBadge {{ background: #F59E0B; color: #1F1300; border-radius: 9px; font-size: 11px; font-weight: 700;
    padding: 0 6px; min-width: 8px; min-height: 18px; max-height: 18px; }}

/* ---- top bar ---- */
#topbar {{ background: {CARD}; border-bottom: 1px solid {BORDER}; }}
#topbarCaption {{ color: {MUTED}; font-size: 12px; font-weight: 600; }}

/* ---- type ---- */
#pageTitle {{ font-size: 22px; font-weight: 700; }}
#pageSub {{ color: {MUTED}; font-size: 14px; }}
#sectionTitle {{ font-size: 15px; font-weight: 700; }}
#cardTitle {{ font-size: 15px; font-weight: 700; }}
#eyebrow {{ color: {MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
#muted {{ color: {MUTED}; }}
#hint {{ color: {MUTED}; font-size: 13px; }}
#strong {{ font-weight: 600; }}
#mono {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: 13px; }}
#kbd {{ background: {SUBTLE}; border: 1px solid {BORDER_STRONG}; border-bottom-width: 2px; border-radius: 4px;
    padding: 0 5px; font-size: 12px; font-weight: 600; color: {TEXT_2}; }}

/* ---- surfaces ---- */
QFrame#card {{ background: {CARD}; border: 1px solid #E6E1D3; border-radius: 12px; }}
QFrame#inset {{ background: {SUBTLE}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
#statValue {{ font-size: 26px; font-weight: 700; }}
#statLabel {{ color: {MUTED}; font-size: 13px; }}
#statSub {{ color: {FAINT}; font-size: 12px; }}
QLabel#iconTile {{ background: {ACCENT_SOFT}; border-radius: 8px; }}
{_tone_rules("QLabel#iconTile", "border: none;")}

/* ---- status ---- */
QLabel#pill {{ border-radius: 10px; padding: 2px 9px; font-size: 12px; font-weight: 600; }}
{_tone_rules("QLabel#pill")}
QFrame#banner {{ border-radius: 8px; }}
{_tone_rules("QFrame#banner")}
QFrame#banner QLabel {{ background: transparent; border: none; }}
{chr(10).join(f'QFrame#banner[tone="{tone}"] QLabel {{ color: {fg}; }}' for tone, (fg, _bg, _b) in TONES.items())}
#emptyTitle {{ font-size: 16px; font-weight: 700; }}
#emptyBody {{ color: {MUTED}; }}
#toast {{ background: {TEXT}; border-radius: 8px; }}
#toast QLabel {{ color: white; font-weight: 600; }}

/* ---- buttons ---- */
QPushButton {{ background: {CARD}; border: 1px solid #D3CDBB; border-radius: 8px; padding: 7px 14px;
    font-weight: 600; color: {TEXT}; min-height: 20px; }}
QPushButton:hover {{ background: {SUBTLE}; border-color: {FAINT}; }}
QPushButton:pressed {{ background: #ECE8DD; }}
QPushButton:focus {{ border: 2px solid {ACCENT}; padding: 6px 13px; }}
QPushButton:disabled {{ color: #B3B8B1; background: {SUBTLE}; border-color: {BORDER}; }}
QPushButton::menu-indicator {{ image: url({assets["chevron-down"]}); subcontrol-position: right center;
    subcontrol-origin: padding; right: 8px; width: 14px; height: 14px; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; }}
QPushButton#primary:hover {{ background: {ACCENT_DARK}; border-color: {ACCENT_DARK}; }}
QPushButton#primary:focus {{ border: 2px solid #073F3B; }}
QPushButton#primary:disabled {{ background: #8FC4BD; border-color: #8FC4BD; color: white; }}
QPushButton#danger {{ color: {BAD}; }}
QPushButton#danger:hover {{ background: {BAD_BG}; border-color: {BAD_BORDER}; }}
QPushButton#danger:disabled {{ color: #B3B8B1; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {TEXT_2}; }}
QPushButton#ghost:hover {{ background: #ECE8DD; }}
QPushButton#ghost:focus {{ border: 2px solid {ACCENT}; }}
QPushButton#ghost:disabled {{ color: #B3B8B1; background: transparent; }}
QPushButton#link {{ background: transparent; border: none; color: {ACCENT}; padding: 2px 0; text-align: left; }}
QPushButton#link:hover {{ color: {ACCENT_DARK}; text-decoration: underline; }}
QPushButton#link:focus {{ border: none; text-decoration: underline; }}
QPushButton#tool {{ padding: 6px; border-radius: 6px; background: transparent; border: 1px solid transparent; }}
QPushButton#tool:hover {{ background: #ECE8DD; }}
QPushButton#tool:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT_BORDER}; }}
QPushButton#tool:focus {{ border: 2px solid {ACCENT}; padding: 5px; }}
QPushButton#templateCard {{ background: {CARD}; border: 1px solid #DAD4C3; border-radius: 12px; padding: 12px 14px;
    text-align: left; font-weight: 400; min-height: 50px; }}
QPushButton#templateCard:hover {{ border-color: {ACCENT}; background: {SUBTLE}; }}
QPushButton#templateCard:checked {{ border: 2px solid {ACCENT}; background: {ACCENT_SOFT}; padding: 11px 13px; }}
QPushButton#templateCard:focus {{ border: 2px solid {ACCENT}; padding: 11px 13px; }}

/* segmented control */
QFrame#segmented {{ background: #E9E5DA; border-radius: 8px; }}
QPushButton#segment {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 5px 12px;
    color: {MUTED}; font-weight: 600; }}
QPushButton#segment:hover {{ color: {TEXT}; }}
QPushButton#segment:checked {{ background: {CARD}; color: {TEXT}; border: 1px solid {BORDER}; }}
QPushButton#segment:focus {{ border: 2px solid {ACCENT}; padding: 4px 11px; }}

/* answer choices in the review inspector */
QPushButton#choice {{ min-width: 30px; padding: 6px 8px; border-radius: 6px; font-weight: 700; }}
QPushButton#choice:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
QPushButton#choiceBlank {{ padding: 6px 10px; border-radius: 6px; color: {MUTED}; }}
QPushButton#choiceBlank:checked {{ background: {TEXT_2}; border-color: {TEXT_2}; color: white; }}
QFrame#issue {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#issue[active="true"] {{ border: 2px solid {ACCENT}; }}
QFrame#issue[resolved="true"] {{ background: {SUBTLE}; }}
QPushButton#answerChip {{ padding: 3px 0; min-width: 52px; max-width: 52px; border-radius: 6px; font-weight: 600;
    font-size: 12px; border: 1px solid {BORDER}; background: {CARD}; }}
QPushButton#answerChip:hover {{ border-color: {ACCENT}; }}
QPushButton#answerChip[state="blank"] {{ background: {WARN_BG}; border-color: {WARN_BORDER}; color: #92400E; }}
QPushButton#answerChip[state="flagged"] {{ background: {BAD_BG}; border-color: {BAD_BORDER}; color: #991B1B; }}
QPushButton#answerChip[state="edited"] {{ background: {ACCENT_SOFT}; border-color: {ACCENT_BORDER}; color: {ACCENT_DARK}; }}

/* ---- inputs ---- */
QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {CARD}; border: 1px solid {FIELD_BORDER}; border-radius: 8px; padding: 8px 11px; min-height: 20px;
    selection-background-color: {ACCENT}; selection-color: white; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QDateEdit:hover, QComboBox:hover {{ border-color: {FIELD_HOVER}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border: 2px solid {ACCENT}; padding: 7px 10px; background: #FFFFFF; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ background: {SUBTLE}; color: {FAINT}; }}
QLineEdit[invalid="true"], QComboBox[invalid="true"], QDateEdit[invalid="true"] {{ border-color: {BAD}; background: #FEF6F4; }}
QLineEdit#search {{ padding-left: 6px; }}
QLineEdit#cellEditor {{ border: 2px solid {ACCENT}; border-radius: 4px; padding: 0 4px; min-height: 0; font-weight: 700;
    background: #FFFFFF; }}
QPushButton#chip {{ background: {SUBTLE}; border: 1px solid {BORDER}; border-radius: 11px; padding: 1px 10px; min-height: 18px;
    font-size: 12px; font-weight: 600; color: {TEXT_2}; }}
QPushButton#chip:hover {{ background: {ACCENT_SOFT}; border-color: {ACCENT_BORDER}; color: {ACCENT_DARK}; }}
QSpinBox, QDoubleSpinBox, QDateEdit {{ padding: 7px 26px 7px 11px; }}
QAbstractSpinBox QLineEdit, QComboBox QLineEdit {{ border: none; padding: 0; background: transparent; min-height: 0; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border-left: 1px solid {FIELD_BORDER}; width: 30px; margin: 2px 2px 2px 0;
    subcontrol-origin: padding; subcontrol-position: center right; border-top-right-radius: 6px; border-bottom-right-radius: 6px; }}
QComboBox::down-arrow {{ image: url({assets["chevron-down"]}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {FIELD_BORDER}; border-radius: 9px; outline: none; padding: 5px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; }}
QComboBox QAbstractItemView::item {{ padding: 7px 10px; min-height: 24px; border-radius: 5px; margin: 1px 2px; }}
QComboBox QAbstractItemView::item:hover {{ background: {SUBTLE}; }}
QComboBox QAbstractItemView::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QDateEdit::up-button {{ subcontrol-origin: border;
    subcontrol-position: top right; width: 22px; border: none; margin: 2px 2px 0 0; }}
QSpinBox::down-button, QDoubleSpinBox::down-button, QDateEdit::down-button {{ subcontrol-origin: border;
    subcontrol-position: bottom right; width: 22px; border: none; margin: 0 2px 2px 0; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateEdit::up-arrow {{ image: url({assets["chevron-up"]}); width: 11px; height: 11px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({assets["chevron-down"]}); width: 11px; height: 11px; }}
QDateEdit::drop-down {{ border: none; width: 28px; }}
QDateEdit::down-arrow {{ image: url({assets["chevron-down"]}); width: 14px; height: 14px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator, QTableView::indicator, QGroupBox::indicator {{ width: 16px; height: 16px;
    border: 1.5px solid {FAINT}; border-radius: 4px; background: white; }}
QCheckBox::indicator:hover, QTableView::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked, QTableView::indicator:checked, QGroupBox::indicator:checked {{
    background: {ACCENT}; border-color: {ACCENT}; image: url({assets["check"]}); }}
QCheckBox:focus {{ color: {ACCENT_DARK}; }}

/* ---- tables & lists ---- */
QTableWidget, QTableView {{ background: {CARD}; alternate-background-color: #FBF9F4; border: 1px solid {BORDER};
    border-radius: 8px; gridline-color: #ECE8DD; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT};
    outline: none; }}
QTableWidget::item {{ padding: 6px 10px; border: none; }}
QTableWidget#gridTable {{ gridline-color: {GRID_LINE}; border: 1px solid {BORDER_STRONG}; }}
QTableWidget#gridTable::item:selected {{ background: transparent; color: {TEXT}; }}
QTableWidget#gridTable:focus {{ border: 1px solid {ACCENT}; }}
QTableWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QTableWidget:focus {{ border: 1px solid {ACCENT}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {SUBTLE}; color: {MUTED}; font-size: 12px; font-weight: 700; border: none;
    border-bottom: 1px solid {BORDER}; padding: 8px 10px; }}
QTableCornerButton::section {{ background: {SUBTLE}; border: none; }}
QListWidget {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; outline: none; }}
QListWidget::item {{ padding: 8px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QListWidget::item:hover {{ background: {SUBTLE}; }}
QListWidget:focus {{ border: 1px solid {ACCENT}; }}
QListWidget#queue {{ padding: 0; border: none; border-radius: 0; background: {CARD}; }}
QListWidget#queue::item {{ padding: 0; border-radius: 0; border-bottom: 1px solid #ECE8DD; }}

QMenu {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 7px 22px 7px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 6px; }}
QMenu::section {{ color: {MUTED}; font-size: 11px; font-weight: 700; padding: 6px 12px 2px 12px; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; background: {CARD}; top: -1px; }}
QTabBar::tab {{ padding: 8px 16px; background: transparent; color: {MUTED}; font-weight: 600; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QSplitter::handle:vertical {{ height: 10px; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #CBC5B4; border-radius: 4px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {FAINT}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #CBC5B4; border-radius: 4px; min-width: 32px; }}
QScrollBar::handle:horizontal:hover {{ background: {FAINT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QProgressBar {{ border: none; border-radius: 4px; background: #E4DFD2; max-height: 8px; min-height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QProgressBar[tone="good"]::chunk {{ background: {GOOD}; }}
QProgressBar[tone="warn"]::chunk {{ background: #D97706; }}
QMessageBox {{ background: {CARD}; }}
QMessageBox QLabel {{ font-size: 14px; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 12px; padding: 14px 10px 10px 10px;
    background: {CARD}; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {MUTED}; }}
QGraphicsView#sheetViewer {{ background: #E3DED1; border: 1px solid {BORDER}; border-radius: 8px; }}
QGraphicsView#sheetViewer:focus {{ border: 1px solid {ACCENT}; }}
QCalendarWidget QWidget {{ alternate-background-color: {SUBTLE}; }}
"""


_ASSET_DIR: tempfile.TemporaryDirectory | None = None


def _write_assets() -> dict[str, str]:
    """Stylesheets can only reference image files, so render the few glyphs they need once."""
    global _ASSET_DIR
    from omr_exam.icons import pixmap

    if _ASSET_DIR is None:
        _ASSET_DIR = tempfile.TemporaryDirectory(prefix="omr-exam-ui-")
    directory = Path(_ASSET_DIR.name)
    assets: dict[str, str] = {}
    for name, color, stroke in (("chevron-down", MUTED, 2.4), ("chevron-up", MUTED, 2.4), ("check", "#FFFFFF", 3.2)):
        target = directory / f"{name}.png"
        if not target.is_file():
            pixmap(name, color, 16, 2.0, stroke).save(str(target), "PNG")
        assets[name] = target.as_posix()
    return assets


def repolish(widget: QWidget) -> None:
    """Re-evaluate stylesheet rules after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_property(widget: QWidget, name: str, value) -> None:
    if widget.property(name) != value:
        widget.setProperty(name, value)
        repolish(widget)


def apply_light_theme(app: QApplication) -> None:
    global FONT_FAMILY
    app.setStyle("Fusion")
    families = set(QFontDatabase.families())
    FONT_FAMILY = next((name for name in ("Segoe UI Variable Text", "Segoe UI", "Inter") if name in families), "Segoe UI")
    palette = QPalette()
    for role, color in (
        (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, CARD), (QPalette.AlternateBase, SUBTLE),
        (QPalette.ToolTipBase, TEXT), (QPalette.ToolTipText, "#FFFFFF"), (QPalette.Text, TEXT),
        (QPalette.Button, CARD), (QPalette.ButtonText, TEXT), (QPalette.BrightText, "#FFFFFF"),
        (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#FFFFFF"), (QPalette.PlaceholderText, FAINT),
        (QPalette.Link, ACCENT),
    ):
        palette.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor("#B3B8B1"))
    app.setPalette(palette)
    app.setFont(QFont(FONT_FAMILY, 10))
    app.setStyleSheet(_stylesheet(_write_assets()))
