"""Operator desktop workflow: tests, keys, sheet PDF, scan batches, review, export."""
from __future__ import annotations

import base64
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMenu, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QStackedWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from omr_exam import __version__, theme
from omr_exam.answer_keys import validate_answer_key
from omr_exam.config import load_config
from omr_exam.database import Repository
from omr_exam.dialogs import AnswerKeyDialog, BatchLogDialog, BatchPickerDialog, NewTestDialog, StudentDialog
from omr_exam.icons import icon, icon_size, pixmap
from omr_exam.ingest import SUPPORTED_SCAN_SUFFIXES, count_scan_pages, scan_files_in_folder
from omr_exam.reports import (class_results, export_csv, export_item_analysis_csv, export_report_pdf,
                              export_student_scorecards_pdf, export_subject_analysis_csv,
                              export_topic_analysis_csv, export_xlsx, item_analysis)
from omr_exam.review import ReviewPage
from omr_exam.scan_job import ScanOutcome, ScanProgress, ScanWorker, process_scan_batch
from omr_exam.sheets import generate_sheet_pdf
from omr_exam.templates import TEMPLATES, build_from_template
from omr_exam.updater import (UPDATE_SOURCE_URL, check_for_update, download_and_apply,
                              should_check_automatically)
from omr_exam.widgets import (Banner, BusyDialog, DropZone, EmptyState, Histogram, Pill, PillDelegate, StatTile,
                              Stepper, Toast, alert, button, card, combo, confirm, divider, icon_tile, label, table)

IMAGE_TYPES = "Scanned sheets (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.pdf);;All files (*.*)"

NAV_GROUPS = (
    ("", (("dashboard", "Home", "home"),)),
    ("PREPARE", (("tests", "Tests", "tests"), ("students", "Students", "students"))),
    ("PROCESS", (("scan", "Scan sheets", "scan"), ("review", "Review", "review"))),
    ("REPORT", (("results", "Results", "results"),)),
)
NAV_ITEMS = tuple((key, text) for _group, items in NAV_GROUPS for key, text, _icon in items) + (("settings", "Settings"),)


class _UpdateCheckWorker(QThread):
    checked = Signal(object, bool)
    failed = Signal(str, bool)

    def __init__(self, automatic: bool = False, parent=None):
        super().__init__(parent)
        self.automatic = automatic

    def run(self):
        try:
            self.checked.emit(check_for_update(), self.automatic)
        except Exception as exc:
            self.failed.emit(str(exc), self.automatic)


class _UpdateApplyWorker(QThread):
    applied = Signal()
    failed = Signal(str)

    def __init__(self, manager, update, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.update = update

    def run(self):
        try:
            download_and_apply(self.manager, self.update)
            self.applied.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


# ---------------------------------------------------------------- helpers
def _when(stamp: str) -> str:
    """SQLite UTC timestamp -> short local date and time."""
    try:
        moment = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone()
    except (TypeError, ValueError):
        return str(stamp or "")
    return f"{moment.day} {moment:%b %Y}, {moment:%H:%M}"


def _size(byte_count: int) -> str:
    return f"{byte_count / 1_048_576:.1f} MB" if byte_count >= 1_048_576 else f"{max(1, round(byte_count / 1024))} KB"


def _item(text: Any, data: Any = None, align: Qt.AlignmentFlag | None = None) -> QTableWidgetItem:
    cell = QTableWidgetItem(str(text))
    if data is not None:
        cell.setData(Qt.UserRole, data)
    if align is not None:
        cell.setTextAlignment(align | Qt.AlignVCenter)
    return cell


def _page(title: str, subtitle: str, actions: tuple[QWidget, ...] = ()) -> tuple[QWidget, QVBoxLayout]:
    page = QWidget()
    page.setObjectName("page")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(28, 22, 28, 22)
    layout.setSpacing(14)
    head = QHBoxLayout()
    head.setSpacing(10)
    titles = QVBoxLayout()
    titles.setSpacing(2)
    titles.addWidget(label(title, "pageTitle"))
    if subtitle:
        titles.addWidget(label(subtitle, "pageSub"))
    head.addLayout(titles, 1)
    for action in actions:
        head.addWidget(action, 0, Qt.AlignBottom)
    layout.addLayout(head)
    return page, layout


def _scrollable(page: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(page)
    return scroll


def _batch_state(batch: dict[str, Any], pending: int) -> str:
    if batch["status"] == "processing":
        return "Reading"
    if batch["status"] != "complete":
        return "Incomplete"
    return f"Needs review · {pending}" if pending else "Ready"


def _batch_tone(text: str) -> str:
    return "good" if text == "Ready" else "bad" if text == "Incomplete" else "info" if text == "Reading" else "warn"


def _key_tone(text: str) -> str:
    return "good" if text == "Complete" else "bad" if text == "Unreadable" else "warn"


def _outcome_tone(text: str) -> str:
    return {"Correct": "good", "Incorrect": "bad", "Blank": "warn"}.get(text, "neutral")


class MainWindow(QMainWindow):
    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.db_path = Path(db_path)
        self.repo = Repository(db_path)
        self.active_test_id: int | None = None
        self.config: dict[str, Any] | None = None
        self.batch_id: int | None = None
        self.result_records: list[dict[str, Any]] = []
        self.dashboard_batches: list[dict[str, Any]] = []
        self.staged_scans: list[tuple[Path, int | None]] = []
        self._scan_worker: ScanWorker | None = None
        self._result_rows: list[dict[str, Any]] = []
        self._current_page = "dashboard"
        self.setWindowTitle("OMRaut")
        from omr_exam.icons import app_icon
        self.setWindowIcon(app_icon())
        self.resize(1400, 860)
        self.setMinimumSize(1120, 700)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        self.page_index: dict[str, int] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        self.test_combo = combo(340)
        self.test_combo.setAccessibleName("Active test")
        self.test_combo.currentIndexChanged.connect(self.select_test)

        shell = QWidget()
        outer = QHBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())
        right = QVBoxLayout()
        right.setSpacing(0)
        right.addWidget(self._build_topbar())
        self.update_notice = Banner("", "info")
        self.update_notice.set_action("Open Settings", lambda: self.show_page("settings"))
        self.update_notice.hide()
        right.addWidget(self.update_notice)
        right.addWidget(self.pages, 1)
        outer.addLayout(right, 1)
        self.setCentralWidget(shell)
        self._toast = Toast(shell)

        self.review_page = ReviewPage(self)
        for key, builder in (("dashboard", self._build_dashboard_page), ("tests", self._build_tests_page),
                             ("students", self._build_students_page), ("scan", self._build_scan_page),
                             ("review", lambda: self.review_page), ("results", self._build_results_page),
                             ("settings", self._build_settings_page)):
            self.page_index[key] = self.pages.addWidget(builder())
        # Names kept for callers that predate the review workspace.
        self.review_table = self.review_page.table
        self.review_summary = self.review_page.summary

        for number, (key, _text) in enumerate(NAV_ITEMS, 1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{number}"), self)
            shortcut.activated.connect(lambda k=key: self.show_page(k))
            self.nav_buttons[key].setToolTip(f"{self.nav_buttons[key].toolTip()} (Ctrl+{number})".strip())
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self.new_test)

        self._restore_geometry()
        last_test = self.repo.get_setting("last_test_id", "")
        self.refresh_tests(int(last_test) if last_test.isdigit() else None)
        self.refresh_students()
        self.show_page("dashboard")

    # ------------------------------------------------------------ chrome
    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(228)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(12, 18, 12, 14)
        layout.setSpacing(2)
        brand = QHBoxLayout()
        brand.setContentsMargins(8, 0, 8, 10)
        brand.setSpacing(10)
        from omr_exam.icons import app_logo
        mark = QLabel()
        mark.setFixedSize(34, 34)
        mark.setAlignment(Qt.AlignCenter)
        mark.setPixmap(app_logo(34, self.devicePixelRatioF()))
        brand.addWidget(mark)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label("OMRaut", "brand", wrap=False))
        names.addWidget(label("Scan · score · report", "brandSub", wrap=False))
        brand.addLayout(names, 1)
        layout.addLayout(brand)
        for group, items in NAV_GROUPS:
            if group:
                layout.addWidget(label(group, "navGroup", wrap=False))
            for key, text, icon_name in items:
                layout.addWidget(self._nav_button(key, text, icon_name))
        layout.addStretch(1)
        layout.addWidget(self._nav_button("settings", "Settings", "settings"))
        foot = QHBoxLayout()
        foot.setContentsMargins(12, 10, 8, 0)
        foot.setSpacing(8)
        lock = QLabel()
        lock.setPixmap(pixmap("lock", theme.SIDEBAR_MUTED, 14))
        foot.addWidget(lock, 0, Qt.AlignTop)
        foot.addWidget(label("Works offline. All data stays on this computer.", "sidebarFoot"), 1)
        layout.addLayout(foot)
        return side

    def _nav_button(self, key: str, text: str, icon_name: str) -> QPushButton:
        nav = QPushButton(f"  {text}")
        nav.setObjectName("nav")
        nav.setCheckable(True)
        nav.setCursor(Qt.PointingHandCursor)
        glyph = QIcon()
        for scale in (1.0, 2.0, 3.0):
            glyph.addPixmap(pixmap(icon_name, theme.SIDEBAR_TEXT, 18, scale), QIcon.Normal, QIcon.Off)
            glyph.addPixmap(pixmap(icon_name, "#FFFFFF", 18, scale), QIcon.Normal, QIcon.On)
            glyph.addPixmap(pixmap(icon_name, "#FFFFFF", 18, scale), QIcon.Active, QIcon.Off)
            glyph.addPixmap(pixmap(icon_name, "#6C7B75", 18, scale), QIcon.Disabled, QIcon.Off)
        nav.setIcon(glyph)
        nav.setIconSize(icon_size(18))
        nav.clicked.connect(lambda _c=False, k=key: self.show_page(k))
        self.nav_buttons[key] = nav
        if key == "review":
            row = QHBoxLayout(nav)
            row.setContentsMargins(0, 0, 10, 0)
            row.addStretch(1)
            self.review_badge = label("", "navBadge", wrap=False)
            self.review_badge.setAlignment(Qt.AlignCenter)
            self.review_badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.review_badge.hide()
            row.addWidget(self.review_badge, 0, Qt.AlignVCenter)
        return nav

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topbar")
        bar.setFixedHeight(64)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(28, 8, 28, 8)
        layout.setSpacing(12)
        layout.addWidget(label("ACTIVE TEST", "topbarCaption", wrap=False))
        layout.addWidget(self.test_combo)
        self.topbar_key_pill = Pill("", "neutral")
        self.topbar_key_pill.hide()
        layout.addWidget(self.topbar_key_pill)
        layout.addStretch(1)
        self.new_test_button = button("New test", self.new_test, "primary", "plus", "Create a new test (Ctrl+N)")
        layout.addWidget(self.new_test_button)
        return bar

    def toast(self, text: str, tone: str = "good") -> None:
        self._toast.show_message(text, tone)

    def show_page(self, key: str):
        if key in ("review", "results") and self.batch_id is None:
            self._open_latest_batch()
        self._current_page = key
        self.pages.setCurrentIndex(self.page_index[key])
        for name, nav in self.nav_buttons.items():
            nav.setChecked(name == key)
        if key == "dashboard":
            self.refresh_dashboard()
        elif key == "students":
            self.refresh_students()
        elif key == "scan":
            self.refresh_scan_page()
        elif key == "results":
            self.refresh_results_summary()

    def update_review_badge(self) -> None:
        if not hasattr(self, "review_badge"):
            return
        pending = 0
        if self.active_test_id is not None:
            counts = self.repo.pending_review_counts()
            pending = sum(counts.get(int(batch["id"]), 0) for batch in self.repo.batches(self.active_test_id))
        self.review_badge.setText(str(pending))
        self.review_badge.setVisible(pending > 0)
        self.nav_buttons["review"].setAccessibleDescription(f"{pending} sheets need review" if pending else "")

    def _set_busy(self, busy: bool) -> None:
        """Lock navigation while a batch is being read so the test cannot change underneath it."""
        for key, nav in self.nav_buttons.items():
            nav.setEnabled(not busy or key == "scan")
        self.test_combo.setEnabled(not busy)
        self.new_test_button.setEnabled(not busy)

    # ------------------------------------------------------------ dashboard
    def _build_dashboard_page(self) -> QWidget:
        page, layout = _page("Home", "Where you are with the active test, and what to do next.")

        flow, fl = card((20, 18, 20, 16), 12)
        head = QHBoxLayout()
        head.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("ACTIVE TEST", "eyebrow"))
        self.flow_title = label("No test yet", "cardTitle")
        self.flow_hint = label("", "muted")
        titles.addWidget(self.flow_title)
        titles.addWidget(self.flow_hint)
        head.addLayout(titles, 1)
        self.next_button = button("Create a test", self.new_test, "primary", "arrow-right")
        self.next_button.setLayoutDirection(Qt.RightToLeft)
        head.addWidget(self.next_button, 0, Qt.AlignVCenter)
        fl.addLayout(head)
        fl.addWidget(divider())
        self.stepper = Stepper()
        self.stepper.clicked.connect(self._workflow_step_clicked)
        fl.addWidget(self.stepper)
        layout.addWidget(flow)

        stats = QHBoxLayout()
        stats.setSpacing(14)
        self.stat_labels: dict[str, StatTile] = {}
        for key, caption, icon_name in (("tests", "Tests", "tests"), ("students", "Students on roster", "students"),
                                        ("sheets", "Sheets scanned", "sheet"), ("pending", "Sheets awaiting review", "review")):
            tile = StatTile(caption, icon_name)
            self.stat_labels[key] = tile
            stats.addWidget(tile)
        self.stat_labels["batches"] = self.stat_labels["sheets"]
        layout.addLayout(stats)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        batches_card, bl = card((1, 14, 1, 6), 10)
        bl.addLayout(self._card_header("Recent scan batches", "Double-click a batch to review it"))
        self.dashboard_batches_table = table(["Batch", "Test", "Scanned", "Sheets", "Pages", "Status"], 1)
        self.dashboard_batches_table.setFrameShape(QFrame.NoFrame)
        self.dashboard_batches_table.setItemDelegateForColumn(5, PillDelegate(_batch_tone, self.dashboard_batches_table))
        self.dashboard_batches_table.cellDoubleClicked.connect(self.open_dashboard_batch)
        self.dashboard_batches_table.setAccessibleName("Recent scan batches")
        self.batches_empty = EmptyState("scan", "No sheets scanned yet",
                                        "Scanned batches appear here with their review status.")
        self.batches_stack = QStackedWidget()
        self.batches_stack.addWidget(self.dashboard_batches_table)
        self.batches_stack.addWidget(self.batches_empty)
        bl.addWidget(self.batches_stack, 1)
        columns.addWidget(batches_card, 3)

        tests_card, tl = card((1, 14, 1, 6), 10)
        tl.addLayout(self._card_header("Your tests", "Double-click to open"))
        self.dashboard_tests_table = table(["Test", "Created", "ID"], 0)
        self.dashboard_tests_table.setFrameShape(QFrame.NoFrame)
        self.dashboard_tests_table.setColumnHidden(2, True)
        self.dashboard_tests_table.cellDoubleClicked.connect(self.open_dashboard_test)
        self.dashboard_tests_table.setAccessibleName("Your tests")
        self.tests_empty = EmptyState("tests", "No tests yet", "Create your first test from a ready-made template.",
                                      (button("New test", self.new_test, "primary", "plus"),))
        self.dash_tests_stack = QStackedWidget()
        self.dash_tests_stack.addWidget(self.dashboard_tests_table)
        self.dash_tests_stack.addWidget(self.tests_empty)
        tl.addWidget(self.dash_tests_stack, 1)
        columns.addWidget(tests_card, 2)
        layout.addLayout(columns, 1)
        return page

    @staticmethod
    def _card_header(title: str, hint: str = "") -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(18, 0, 18, 0)
        row.addWidget(label(title, "cardTitle", wrap=False))
        row.addStretch(1)
        if hint:
            row.addWidget(label(hint, "hint", wrap=False))
        return row

    def _workflow(self) -> tuple[list[dict[str, str]], str, str]:
        """Workflow steps for the active test, plus the label and step key of the next action."""
        config = self.config
        steps = [{"key": key, "title": title, "state": "todo", "caption": caption} for key, title, caption in (
            ("create", "Create test", "Pick a template"), ("key", "Answer key", "Enter correct answers"),
            ("print", "Print sheets", "PDF at 100% scale"), ("scan", "Scan sheets", "Upload the scans"),
            ("review", "Review", "Check unclear marks"), ("results", "Results", "Export marks"))]
        if not config:
            steps[0]["state"] = "current"
            return steps, "Create a test", "create"
        by_key = {step["key"]: step for step in steps}
        by_key["create"].update(state="done", caption=f"{len(config['questions'])} questions")
        complete, text = self._key_status(config)
        batches = self.repo.batches(self.active_test_id)
        counts = self.repo.pending_review_counts()
        pending = sum(counts.get(int(batch["id"]), 0) for batch in batches)
        sheets = sum(int(batch["sheet_count"]) for batch in batches)
        if complete:
            by_key["key"].update(state="done", caption="Complete")
        else:
            by_key["key"].update(state="current", caption=text.replace("Incomplete - ", ""))
            return steps, "Enter the answer key", "key"
        printed = bool(batches) or self.repo.get_setting(f"sheet_pdf_made_{self.active_test_id}", "") == "1"
        if printed:
            by_key["print"].update(state="done", caption="Sheet PDF created")
        else:
            by_key["print"]["state"] = "current"
            return steps, "Print answer sheets", "print"
        if not batches:
            by_key["scan"]["state"] = "current"
            return steps, "Scan sheets", "scan"
        by_key["scan"].update(state="done", caption=f"{sheets} sheet{'s' if sheets != 1 else ''} in {len(batches)} batch{'es' if len(batches) != 1 else ''}")
        if pending:
            by_key["review"].update(state="attention", caption=f"{pending} to review")
            return steps, f"Review {pending} sheet{'s' if pending != 1 else ''}", "review"
        by_key["review"].update(state="done", caption="All reviewed")
        by_key["results"].update(state="current", caption="Ready to export")
        return steps, "View results", "results"

    def _run_workflow_step(self, key: str) -> None:
        if key == "create":
            self.new_test() if not self.config else self.show_page("tests")
        elif key == "key":
            self.edit_keys()
        elif key == "print":
            self.make_sheets()
        else:
            self.show_page(key)

    def _workflow_step_clicked(self, index: int) -> None:
        steps = self.stepper.steps()
        if 0 <= index < len(steps):
            if not self.config and steps[index]["key"] != "create":
                self.toast("Create a test first", "info")
                return
            self._run_workflow_step(steps[index]["key"])

    def refresh_dashboard(self):
        tests = self.repo.tests()
        all_batches = self.repo.batches()
        counts = self.repo.pending_review_counts()
        batches = all_batches[:10]
        pending_total = sum(counts.values())
        self.stat_labels["tests"].set_value(str(len(tests)))
        self.stat_labels["students"].set_value(str(len(self.repo.students())))
        self.stat_labels["sheets"].set_value(str(sum(int(b["sheet_count"]) for b in all_batches)),
                                             f"in {len(all_batches)} batch{'es' if len(all_batches) != 1 else ''}")
        self.stat_labels["pending"].set_value(str(pending_total), "", "warn" if pending_total else "good")

        steps, next_text, next_key = self._workflow()
        self.stepper.set_steps(steps)
        self.flow_title.setText(self.config["test"]["title"] if self.config else "No test yet")
        self.flow_hint.setText(f"Next step: {next_text.lower()}." if self.config else
                               "Create a test from a template to get started. It takes about a minute.")
        self.next_button.setText(next_text)
        try:
            self.next_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.next_button.clicked.connect(lambda _c=False, k=next_key: self._run_workflow_step(k))

        self.dashboard_tests_table.setRowCount(len(tests))
        for row, test in enumerate(tests):
            self.dashboard_tests_table.setItem(row, 0, _item(test["title"], test["id"]))
            self.dashboard_tests_table.setItem(row, 1, _item(_when(test["created_at"])))
            self.dashboard_tests_table.setItem(row, 2, _item(test["id"]))
        self.dash_tests_stack.setCurrentIndex(0 if tests else 1)
        self.dashboard_batches = batches
        self.dashboard_batches_table.setRowCount(len(batches))
        for row, batch in enumerate(batches):
            values = (f"#{batch['id']}", batch["title"], _when(batch["created_at"]), str(batch["sheet_count"]),
                      f"{batch['processed_pages']}/{batch['expected_pages']}", _batch_state(batch, counts.get(int(batch["id"]), 0)))
            for column, value in enumerate(values):
                self.dashboard_batches_table.setItem(row, column, _item(value))
        self.batches_stack.setCurrentIndex(0 if batches else 1)
        self.update_review_badge()

    def open_dashboard_batch(self, row: int, _column: int = 0):
        if row < 0 or row >= len(self.dashboard_batches):
            return
        self._open_batch(self.dashboard_batches[row])

    def open_dashboard_test(self, row: int, _column: int = 0):
        item = self.dashboard_tests_table.item(row, 2)
        if item is None:
            return
        index = self.test_combo.findData(int(item.text()))
        if index >= 0:
            self.test_combo.setCurrentIndex(index)
            self.show_page("tests")

    def _open_batch(self, batch: dict[str, Any], navigate: bool = True):
        test_index = self.test_combo.findData(batch["test_id"])
        if test_index >= 0:
            self.test_combo.setCurrentIndex(test_index)
        self.batch_id = int(batch["id"])
        self.repo.set_setting("last_batch_id", str(self.batch_id))
        self.log_button.setEnabled(True)
        self.repo.reconcile_batch_roster_flags(self.batch_id)
        self.result_records = self.repo.results_for_batch(self.batch_id)
        self.batch_label.setText(f"Batch #{self.batch_id} · {len(self.result_records)} sheet(s) · {batch['status']}")
        self.load_review_table()
        if navigate:
            self.show_page("review")

    def _open_latest_batch(self) -> None:
        """Open the batch last worked on (or the newest) for the active test, without changing page."""
        if self.active_test_id is None:
            return
        batches = self.repo.batches(self.active_test_id)
        if not batches:
            return
        last = self.repo.get_setting("last_batch_id", "")
        chosen = next((batch for batch in batches if str(batch["id"]) == last), batches[0])
        self._open_batch(chosen, navigate=False)

    def load_review_table(self):
        self.review_page.load()

    def save_reviews(self):
        self.review_page.save_all()

    # ------------------------------------------------------------ tests & keys
    def _build_tests_page(self) -> QWidget:
        page, layout = _page("Tests", "Each test has its own answer sheet, answer key, and scan batches.",
                             (button("JSON example", self.download_test_json_example, "ghost", "download",
                                     "Save a valid test JSON file, then edit and import it"),
                              button("Import exam file", self.import_config, "", "upload", "Import a test saved as JSON"),
                              button("New test", self.new_test, "primary", "plus")))
        split = QHBoxLayout()
        split.setSpacing(14)

        list_card, ll = card((1, 4, 1, 6), 0)
        self.tests_table = table(["Test", "Class", "Subject", "Questions", "Sets", "Answer key"], 0)
        self.tests_table.setFrameShape(QFrame.NoFrame)
        self.tests_table.setItemDelegateForColumn(5, PillDelegate(lambda text: _key_tone(text.split(" ")[0]), self.tests_table))
        self.tests_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Interactive)
        self.tests_table.setColumnWidth(5, 190)
        self.tests_table.cellClicked.connect(self._tests_table_clicked)
        self.tests_table.currentCellChanged.connect(lambda row, column, _r, _c: self._tests_table_clicked(row, column))
        self.tests_table.setAccessibleName("Tests")
        self.tests_page_empty = EmptyState("tests", "Create your first test",
                                           "Pick a ready-made template, name the test, and enter the answer key. "
                                           "The app then gives you a printable answer sheet.",
                                           (button("New test", self.new_test, "primary", "plus"),
                                            button("Import exam file", self.import_config, "", "upload")))
        self.tests_stack = QStackedWidget()
        self.tests_stack.addWidget(self.tests_table)
        self.tests_stack.addWidget(self.tests_page_empty)
        ll.addWidget(self.tests_stack)
        split.addWidget(list_card, 1)

        detail, dl = card((20, 18, 20, 18), 12)
        detail.setFixedWidth(400)
        dl.addWidget(label("SELECTED TEST", "eyebrow"))
        self.test_title_label = label("No test selected", "cardTitle")
        self.test_info = label("Create a test or import an exam file to get started.", "muted")
        dl.addWidget(self.test_title_label)
        dl.addWidget(self.test_info)
        self.key_banner = Banner("", "info")
        dl.addWidget(self.key_banner)
        self.test_facts = QGridLayout()
        self.test_facts.setHorizontalSpacing(16)
        self.test_facts.setVerticalSpacing(8)
        self.fact_labels: dict[str, QLabel] = {}
        for row, (key, caption) in enumerate((("questions", "Questions"), ("options", "Answer options"), ("sets", "Sets"),
                                              ("marking", "Marking"), ("date", "Test date"), ("roll", "Roll number"))):
            self.test_facts.addWidget(label(caption, "hint", wrap=False), row, 0)
            value = label("—", "strong")
            self.fact_labels[key] = value
            self.test_facts.addWidget(value, row, 1)
        self.test_facts.setColumnStretch(1, 1)
        dl.addLayout(self.test_facts)
        dl.addWidget(divider())
        self.key_button = button("Enter answer key", self.edit_keys, "primary", "key")
        self.sheet_button = button("Print answer sheets (PDF)", self.make_sheets, "", "printer")
        self.scan_button = button("Scan sheets for this test", lambda: self.show_page("scan"), "", "scan")
        self.export_config_button = button("Export exam file", self.export_config, "ghost", "download",
                                           "Save this test as JSON to reuse it on another computer")
        for action in (self.key_button, self.sheet_button, self.scan_button, self.export_config_button):
            dl.addWidget(action)
        dl.addStretch(1)
        split.addWidget(detail)
        layout.addLayout(split, 1)
        return page

    def _tests_table_clicked(self, row: int, _column: int):
        item = self.tests_table.item(row, 0) if row >= 0 else None
        if item is not None:
            index = self.test_combo.findData(item.data(Qt.UserRole))
            if index >= 0 and index != self.test_combo.currentIndex():
                self.test_combo.setCurrentIndex(index)

    def _key_status(self, config: dict[str, Any]) -> tuple[bool, str]:
        problems = []
        for code in config["sheet"]["set_codes"]:
            try:
                validate_answer_key(config, config["sets"][code]["answer_key"])
            except ValueError:
                filled = len(config["sets"][code]["answer_key"])
                problems.append(f"{filled}/{len(config['questions'])} answers" if len(config["sheet"]["set_codes"]) == 1
                                else f"Set {code}: {filled}/{len(config['questions'])}")
        if problems:
            return False, "Incomplete - " + ", ".join(problems)
        return True, "Complete"

    def refresh_tests(self, select_id: int | None = None):
        tests = self.repo.tests()
        self.test_combo.blockSignals(True)
        self.test_combo.clear()
        for item in tests:
            self.test_combo.addItem(item["title"], item["id"])
        if select_id is not None:
            index = self.test_combo.findData(select_id)
            if index >= 0:
                self.test_combo.setCurrentIndex(index)
        self.test_combo.blockSignals(False)
        self.test_combo.setEnabled(bool(tests) and self._scan_worker is None)
        if not tests:
            self.test_combo.setPlaceholderText("No tests yet")
        self.tests_table.blockSignals(True)
        self.tests_table.setRowCount(len(tests))
        for row, item in enumerate(tests):
            try:
                config = self.repo.config_for_test(item["id"])
                values = [item["title"], config["test"]["class"], config["test"]["subject"], str(len(config["questions"])),
                          ", ".join(config["sheet"]["set_codes"]), self._key_status(config)[1]]
            except Exception:
                values = [item["title"], "", "", "", "", "Unreadable"]
            for column, value in enumerate(values):
                self.tests_table.setItem(row, column, _item(value, item["id"] if column == 0 else None))
            if self.test_combo.itemData(self.test_combo.currentIndex()) == item["id"]:
                self.tests_table.selectRow(row)
        self.tests_table.blockSignals(False)
        self.tests_stack.setCurrentIndex(0 if tests else 1)
        self.select_test(self.test_combo.currentIndex())
        self.refresh_dashboard()

    def select_test(self, index: int):
        test_id = self.test_combo.itemData(index) if index >= 0 else None
        if test_id != self.active_test_id:
            self.batch_id = None
            self.result_records = []
            self.batch_label.setText("No batch open")
            self.log_button.setEnabled(False)
            self.staged_scans = []
        self.active_test_id = test_id
        try:
            self.config = self.repo.config_for_test(test_id) if test_id is not None else None
        except Exception:
            self.config = None
        if test_id is not None:
            self.repo.set_setting("last_test_id", str(test_id))
        if self.batch_id is None:
            self.review_page.clear_batch()
        has_test = self.config is not None
        for action in (self.key_button, self.sheet_button, self.scan_button, self.export_config_button):
            action.setEnabled(has_test)
        if self.config:
            c = self.config
            scoring = c["scoring"]
            self.test_title_label.setText(c["test"]["title"])
            self.test_info.setText(f"Class {c['test']['class']} · {c['test']['subject']}")
            self.fact_labels["questions"].setText(str(len(c["questions"])))
            self.fact_labels["options"].setText(", ".join(map(str, c["sheet"]["options"])))
            self.fact_labels["sets"].setText(", ".join(c["sheet"]["set_codes"]) if len(c["sheet"]["set_codes"]) > 1 else "One paper")
            self.fact_labels["marking"].setText(f"+{scoring['correct']:g} correct · {scoring['incorrect']:g} wrong · {scoring['blank']:g} blank")
            self.fact_labels["date"].setText(str(c["test"].get("date", "—")))
            self.fact_labels["roll"].setText(f"{c['sheet']['roll_number_digits']} digits")
            complete, text = self._key_status(c)
            if complete:
                self.key_banner.set("<b>Answer key complete.</b> You can print sheets and scan.", "good")
            else:
                self.key_banner.set(f"<b>Answer key incomplete.</b> {text.replace('Incomplete - ', '')} answers entered. "
                                    f"Finish it before printing or scanning.", "warn")
            self.key_button.setText("Edit answer key" if complete else "Enter answer key")
            self.key_button.setObjectName("" if complete else "primary")
            self.sheet_button.setObjectName("primary" if complete else "")
            for action in (self.key_button, self.sheet_button):
                theme.repolish(action)
            self.key_button.setIcon(icon("key", theme.TEXT_2 if complete else "#FFFFFF", 16))
            self.sheet_button.setIcon(icon("printer", "#FFFFFF" if complete else theme.TEXT_2, 16))
            self.topbar_key_pill.set("Answer key complete" if complete else "Answer key incomplete", "good" if complete else "warn")
            self.topbar_key_pill.show()
        else:
            self.test_title_label.setText("No test selected")
            self.test_info.setText("Create a test or import an exam file to get started.")
            for value in self.fact_labels.values():
                value.setText("—")
            self.key_banner.set("Choose <b>New test</b> and pick a template. It takes about a minute.", "info")
            self.topbar_key_pill.hide()
        for row in range(self.tests_table.rowCount()):
            item = self.tests_table.item(row, 0)
            if item is not None and item.data(Qt.UserRole) == test_id and self.tests_table.currentRow() != row:
                self.tests_table.blockSignals(True)
                self.tests_table.selectRow(row)
                self.tests_table.blockSignals(False)
        self.refresh_scan_page()
        self.update_review_badge()
        if self._current_page in ("review", "results") and self.batch_id is None:
            self._open_latest_batch()
        if self._current_page == "results":
            self.refresh_results_summary()
        elif self._current_page == "dashboard":
            self.refresh_dashboard()

    def _known_classes(self) -> list[str]:
        values = {str(student.get("class_name") or "").strip() for student in self.repo.students()}
        for item in self.repo.tests():
            try:
                values.add(str(self.repo.config_for_test(item["id"])["test"]["class"]).strip())
            except Exception:
                continue
        return sorted({value for value in values if value}, key=str.casefold)

    def new_test(self):
        if self._scan_worker is not None:
            return
        classes = set(self._known_classes())
        subjects = set()
        for item in self.repo.tests():
            try:
                existing_config = self.repo.config_for_test(item["id"])
                existing_class = str(existing_config["test"]["class"]).strip()
                if existing_class: classes.add(existing_class)
                subject = str(existing_config["test"]["subject"]).strip()
                if subject: subjects.add(subject)
            except Exception:
                continue
        dialog = NewTestDialog(self, sorted(classes, key=str.casefold), sorted(subjects, key=str.casefold))
        dialog.institute.setText(self.repo.get_setting("institute_name", ""))
        if not dialog.exec():
            return
        try:
            test_id = self.repo.save_test(dialog.result_config)
            self.refresh_tests(test_id)
            self.show_page("tests")
            self.toast("Test created. Now enter the answer key.")
            self.edit_keys()
        except Exception as exc:
            alert(self, "Could not create test", str(exc), "bad")

    def import_config(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "Choose a test JSON file", "",
            "Test configuration JSON (*.json);;All files (*.*)")
        if not file:
            return
        try:
            test_id = self.repo.save_test(load_config(file))
            self.refresh_tests(test_id)
            self.show_page("tests")
            self.toast("Test imported")
        except Exception as exc:
            alert(self, "This file could not be imported", str(exc), "bad")

    def download_test_json_example(self):
        config = build_from_template(
            TEMPLATES[0], title="Example Test", class_name="10A", subject="Science",
            question_count=10, columns=1, set_codes=["A"], institute="QualifyIAS")
        path, _ = QFileDialog.getSaveFileName(self, "Save test JSON example", "test-config.json", "Test configuration (*.json)")
        if not path:
            return
        try:
            target = Path(path)
            if target.suffix.lower() != ".json":
                target = target.with_suffix(".json")
            target.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
            self.toast("Valid test JSON saved. Edit the test details, questions, topics, sets, and scoring before importing.")
        except Exception as exc:
            alert(self, "Could not save the JSON example", str(exc), "bad")

    def edit_keys(self) -> bool:
        if not self.config or self.active_test_id is None:
            alert(self, "No test selected", "Create or select a test first.")
            return False
        dialog = AnswerKeyDialog(self.config, self)
        if not dialog.exec():
            return False
        try:
            candidate_config = dialog.result_config
            updated = self.repo.update_test_config_and_rescore(self.active_test_id, candidate_config)
            self.config = candidate_config
            keep_batch = self.batch_id
            self.refresh_tests(self.active_test_id)
            if keep_batch is not None:
                self.result_records = self.repo.results_for_batch(keep_batch)
                self.load_review_table()
            complete = self._key_status(candidate_config)[0]
            self.toast(("Answer key saved" if complete else "Answer key saved as a draft")
                       + (f" · {updated} sheet(s) re-scored" if updated else ""), "good" if complete else "warn")
            return True
        except Exception as exc:
            alert(self, "The answer key could not be saved", str(exc), "bad")
            return False

    def export_config(self):
        if not self.config:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export exam file", "test-config.json", "Exam file (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(self.config, indent=2, ensure_ascii=False), encoding="utf-8")
                self.toast("Exam file exported")
            except Exception as exc:
                alert(self, "Export failed", str(exc), "bad")

    def _require_complete_keys(self):
        if not self.config:
            raise ValueError("Select a test first")
        for code in self.config["sheet"]["set_codes"]:
            validate_answer_key(self.config, self.config["sets"][code]["answer_key"])

    def make_sheets(self):
        try:
            self._require_complete_keys()
        except Exception as exc:
            if confirm(self, "Finish the answer key first", f"{exc}\n\nSheets can be printed once every set has a complete key.",
                       "Open answer key", "Not now"):
                self.edit_keys()
            return
        try:
            path, _ = QFileDialog.getSaveFileName(self, "Save printable answer sheet", "answer-sheets.pdf", "PDF (*.pdf)")
            if path:
                institute, logo = self.sheet_branding()
                generate_sheet_pdf(self.config, path, institute=institute, logo=logo)
                self.repo.set_setting(f"sheet_pdf_made_{self.active_test_id}", "1")
                self.refresh_dashboard()
                if confirm(self, "Answer sheet PDF saved",
                           f"<b>{Path(path).name}</b> is ready.\n\nPrint at <b>100% scale</b> (turn off “fit to page”) and "
                           f"keep the four corner squares on the paper.", "Open PDF", "Close"):
                    QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as exc:
            alert(self, "The sheet could not be created", str(exc), "bad")

    # ------------------------------------------------------------ students
    def _build_students_page(self) -> QWidget:
        more = button("More", None, "", "more")
        menu = QMenu(more)
        menu.addAction(icon("download", theme.TEXT_2, 16), "Download example roster", self.download_roster_template)
        menu.addAction(icon("file-export", theme.TEXT_2, 16), "Export roster as JSON", self.export_students_json)
        more.setMenu(menu)
        page, layout = _page("Students", "The roster links roll numbers on the sheets to student names. It is optional but recommended.",
                             (more, button("Import roster", self.import_students, "", "upload", "Import a CSV, Excel, or JSON roster"),
                              button("Add student", self.add_student, "primary", "user-plus")))
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.student_search = QLineEdit()
        self.student_search.setObjectName("search")
        self.student_search.setPlaceholderText("Search by roll number, name, or class")
        self.student_search.setClearButtonEnabled(True)
        self.student_search.addAction(icon("search", theme.MUTED, 16), QLineEdit.LeadingPosition)
        self.student_search.textChanged.connect(self.refresh_students)
        self.student_search.setMaximumWidth(380)
        self.student_search.setAccessibleName("Search students")
        bar.addWidget(self.student_search, 1)
        self.students_hint = label("", "hint", wrap=False)
        bar.addWidget(self.students_hint)
        bar.addStretch(1)
        self.delete_students_button = button("Delete selected", self.delete_students, "danger", "trash")
        self.delete_students_button.setEnabled(False)
        bar.addWidget(self.delete_students_button)
        layout.addLayout(bar)
        self.students_table = table(["Roll number", "Name", "Class"], 1)
        self.students_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.students_table.setColumnWidth(0, 160)
        self.students_table.cellDoubleClicked.connect(self.edit_student)
        self.students_table.itemSelectionChanged.connect(
            lambda: self.delete_students_button.setEnabled(bool(self.students_table.selectedIndexes())))
        self.students_table.setAccessibleName("Student roster")
        self.students_table.setToolTip("Double-click a student to edit")
        self.students_empty = EmptyState("students", "No students yet",
                                         "Import a class list from Excel or CSV with the columns roll_number, name and "
                                         "class_name, or add students one at a time.",
                                         (button("Import roster", self.import_students, "primary", "upload"),
                                          button("Add student", self.add_student, "", "user-plus"),
                                          button("Download example", self.download_roster_template, "ghost", "download")))
        empty_card, el = card((0, 0, 0, 0), 0)
        el.addWidget(self.students_empty)
        self.students_stack = QStackedWidget()
        self.students_stack.addWidget(self.students_table)
        self.students_stack.addWidget(empty_card)
        layout.addWidget(self.students_stack, 1)
        return page

    def refresh_students(self, *_args):
        needle = self.student_search.text().strip().lower()
        everyone = self.repo.students()
        students = [s for s in everyone if not needle or needle in f"{s['roll_number']} {s['name']} {s['class_name']}".lower()]
        self.students_table.setRowCount(len(students))
        for row, student in enumerate(students):
            for column, key in enumerate(("roll_number", "name", "class_name")):
                self.students_table.setItem(row, column, _item(student[key]))
        total = len(everyone)
        self.students_stack.setCurrentIndex(0 if total else 1)
        self.students_hint.setText((f"{len(students)} of {total} shown" if needle else f"{total} student{'s' if total != 1 else ''}") if total else "")
        self.delete_students_button.setVisible(bool(total))

    def add_student(self):
        dialog = StudentDialog(self, classes=self._known_classes())
        if dialog.exec():
            self._save_student(*dialog.values())

    def edit_student(self, row: int, _column: int = 0):
        roll, name, cls = (self.students_table.item(row, c).text() for c in range(3))
        dialog = StudentDialog(self, roll, name, cls, editing=True, classes=self._known_classes())
        if dialog.exec():
            self._save_student(*dialog.values())

    def _save_student(self, roll: str, name: str, cls: str):
        try:
            self.repo.save_student(roll, name, cls)
            self._after_roster_change()
            self.toast(f"Saved {name}")
        except Exception as exc:
            alert(self, "Could not save student", str(exc), "bad")

    def delete_students(self):
        rolls = sorted({self.students_table.item(i.row(), 0).text() for i in self.students_table.selectedIndexes()})
        if not rolls:
            return
        if not confirm(self, f"Delete {len(rolls)} student{'s' if len(rolls) != 1 else ''}?",
                       "They are removed from the roster only. Saved scan results are kept.", "Delete", danger=True):
            return
        for roll in rolls:
            self.repo.delete_student(roll)
        self._after_roster_change()
        self.toast(f"Deleted {len(rolls)} student{'s' if len(rolls) != 1 else ''}")

    def _after_roster_change(self):
        if self.batch_id is not None:
            self.repo.reconcile_batch_roster_flags(self.batch_id)
            self.result_records = self.repo.results_for_batch(self.batch_id)
            self.load_review_table()
        self.refresh_students()
        self.refresh_dashboard()

    def import_students(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import student roster", "", "Roster files (*.csv *.xlsx *.json);;JSON (*.json);;CSV (*.csv);;Excel workbook (*.xlsx)")
        if not path:
            return
        try:
            suffix = Path(path).suffix.lower()
            count = (self.repo.import_students_json(path) if suffix == ".json" else
                     self.repo.import_students_excel(path) if suffix == ".xlsx" else self.repo.import_students_csv(path))
            self._after_roster_change()
            self.toast(f"Imported or updated {count} student{'s' if count != 1 else ''}")
        except Exception as exc:
            alert(self, "The roster could not be imported", str(exc), "bad")

    def download_roster_template(self):
        path, selected_filter = QFileDialog.getSaveFileName(self, "Save roster example", "students-template.csv", "CSV (*.csv);;JSON (*.json)")
        if not path:
            return
        try:
            if Path(path).suffix.lower() == ".json" or (not Path(path).suffix and "JSON" in selected_filter):
                if Path(path).suffix.lower() != ".json":
                    path += ".json"
                payload = {"students": [{"roll_number": "000001", "name": "Example Student", "class_name": "10A"}]}
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2, ensure_ascii=False)
            else:
                if not Path(path).suffix:
                    path += ".csv"
                with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["roll_number", "name", "class_name"])
                    writer.writerow(["000001", "Example Student", "10A"])
            self.toast("Example saved. Fill it in, then choose Import roster.")
        except Exception as exc:
            alert(self, "The example could not be saved", str(exc), "bad")

    def export_students_json(self):
        students = self.repo.students()
        if not students:
            alert(self, "No students yet", "Add a student or import a roster first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export student roster", "students.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"students": students}, handle, indent=2, ensure_ascii=False)
            self.toast(f"Exported {len(students)} student{'s' if len(students) != 1 else ''}")
        except Exception as exc:
            alert(self, "Roster export failed", str(exc), "bad")

    # ------------------------------------------------------------ scan
    def _build_scan_page(self) -> QWidget:
        page, layout = _page("Scan sheets", "Add the scanned or photographed answer sheets. Each one is straightened, read, "
                                            "scored, and saved. Anything unclear is held for your review.")
        self.scan_key_banner = Banner("", "warn")
        layout.addWidget(self.scan_key_banner)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(14)

        # -- stage 1: collect files
        self.drop_zone = DropZone()
        self.drop_zone.dropped.connect(self.add_scan_paths)
        dz = self.drop_zone.layout_
        dz.addWidget(icon_tile("upload", "info", 48), 0, Qt.AlignHCenter)
        heading = label("Drop scanned sheets here", "emptyTitle")
        heading.setAlignment(Qt.AlignHCenter)
        dz.addWidget(heading)
        sub = label("Add as many files and folders as you like: PNG, JPG, TIFF, BMP, or multi-page PDF.", "emptyBody")
        sub.setAlignment(Qt.AlignHCenter)
        dz.addWidget(sub)
        pick = QHBoxLayout()
        pick.setSpacing(10)
        pick.addStretch(1)
        self.upload_button = button("Add files", self.scan_images, "primary", "plus", "Choose one or many scan files")
        self.folder_button = button("Add a folder", self.choose_scan_folder, "", "folder", "Add every scan inside a folder")
        pick.addWidget(self.upload_button)
        pick.addWidget(self.folder_button)
        pick.addStretch(1)
        dz.addSpacing(4)
        dz.addLayout(pick)
        left.addWidget(self.drop_zone)

        self.queue_card, qc = card((1, 14, 1, 12), 10)
        self.queue_title = label("Upload queue", "cardTitle", wrap=False)
        queue_head = QHBoxLayout()
        queue_head.setContentsMargins(18, 0, 18, 0)
        queue_head.addWidget(self.queue_title)
        queue_head.addStretch(1)
        queue_head.addWidget(button("Clear all", self.clear_staged_scans, "ghost", "x"))
        qc.addLayout(queue_head)
        self.staged_table = table(["File", "Folder", "Pages", "Size", ""], 1)
        self.staged_table.setFrameShape(QFrame.NoFrame)
        self.staged_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.staged_table.setTextElideMode(Qt.ElideMiddle)
        self.staged_table.setMinimumHeight(150)
        self.staged_table.setMaximumHeight(260)
        self.staged_table.setAccessibleName("Files waiting to be read")
        qc.addWidget(self.staged_table)
        queue_foot = QHBoxLayout()
        queue_foot.setContentsMargins(18, 0, 18, 0)
        self.staged_summary = label("", "hint", wrap=False)
        queue_foot.addWidget(self.staged_summary)
        queue_foot.addStretch(1)
        self.start_scan_button = button("Start reading", self.start_scan, "primary", "scan")
        queue_foot.addWidget(self.start_scan_button)
        qc.addLayout(queue_foot)
        left.addWidget(self.queue_card)

        # -- stage 2: reading
        self.progress_card, pc = card((20, 18, 20, 18), 12)
        self.progress_title = label("Reading sheets…", "cardTitle")
        self.progress_detail = label("", "muted")
        self.scan_progress = QProgressBar()
        self.scan_progress.setTextVisible(False)
        pc.addWidget(self.progress_title)
        pc.addWidget(self.scan_progress)
        pc.addWidget(self.progress_detail)
        counters = QHBoxLayout()
        counters.setSpacing(12)
        self.scan_counters: dict[str, QLabel] = {}
        for key, caption, tone in (("read", "Sheets read", "info"), ("review", "Need review", "warn"), ("errors", "Errors", "bad")):
            box = QFrame()
            box.setObjectName("inset")
            inner = QVBoxLayout(box)
            inner.setContentsMargins(14, 10, 14, 10)
            inner.setSpacing(0)
            value = label("0", "statValue", wrap=False)
            self.scan_counters[key] = value
            inner.addWidget(value)
            inner.addWidget(label(caption, "statLabel", wrap=False))
            counters.addWidget(box, 1)
        pc.addLayout(counters)
        cancel_row = QHBoxLayout()
        cancel_row.addWidget(label("You can keep this window open; sheets are saved as they are read.", "hint"), 1)
        self.cancel_scan_button = button("Stop", self.cancel_scan, "danger", "x")
        cancel_row.addWidget(self.cancel_scan_button)
        pc.addLayout(cancel_row)
        self.progress_card.hide()
        left.addWidget(self.progress_card)

        # -- stage 3: outcome
        self.outcome_card, oc = card((20, 18, 20, 18), 12)
        self.outcome_banner = Banner("", "good")
        oc.addWidget(self.outcome_banner)
        self.scan_status = label("", "muted")
        oc.addWidget(self.scan_status)
        outcome_actions = QHBoxLayout()
        outcome_actions.setSpacing(10)
        self.outcome_primary = button("Review sheets", lambda: self.show_page("review"), "primary", "review")
        outcome_actions.addWidget(self.outcome_primary)
        self.log_button = button("View batch log", self.view_batch_log, "", "list")
        self.log_button.setEnabled(False)
        outcome_actions.addWidget(self.log_button)
        outcome_actions.addStretch(1)
        outcome_actions.addWidget(button("Scan more sheets", self._reset_scan_stage, "ghost", "plus"))
        oc.addLayout(outcome_actions)
        self.outcome_card.hide()
        left.addWidget(self.outcome_card)

        history, hl = card((1, 14, 1, 6), 10)
        history_head = self._card_header("Batches for this test", "Double-click to open")
        hl.addLayout(history_head)
        self.batch_label = label("No batch open", "hint", wrap=False)
        self.batch_history = table(["Batch", "Scanned", "Sheets", "Pages", "Status"], 1)
        self.batch_history.setFrameShape(QFrame.NoFrame)
        self.batch_history.setMinimumHeight(150)
        self.batch_history.setItemDelegateForColumn(4, PillDelegate(_batch_tone, self.batch_history))
        self.batch_history.cellDoubleClicked.connect(self._open_history_batch)
        self.batch_history.setAccessibleName("Earlier batches for this test")
        self.history_empty = label("No batches yet for this test.", "hint")
        self.history_empty.setAlignment(Qt.AlignCenter)
        self.history_empty.setContentsMargins(0, 18, 0, 26)
        hl.addWidget(self.batch_history)
        hl.addWidget(self.history_empty)
        left.addWidget(history)
        left.addStretch(1)
        columns.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(14)
        tips, tl = card()
        tl.addWidget(label("For the most accurate reading", "cardTitle"))
        for icon_name, text in (("printer", "Print sheets from this app at <b>100% scale</b>."),
                                ("scan", "Scan at <b>150 DPI or higher</b>. A flat, well-lit photo also works."),
                                ("fit", "Keep all <b>four corner squares</b> in the picture. Do not crop."),
                                ("edit", "Bubbles should be filled completely with dark pen or pencil."),
                                ("shield", "Unclear marks are <b>never guessed</b>. They wait in Review.")):
            row = QHBoxLayout()
            row.setSpacing(10)
            glyph = QLabel()
            glyph.setPixmap(pixmap(icon_name, theme.ACCENT, 16))
            glyph.setFixedSize(18, 20)
            row.addWidget(glyph, 0, Qt.AlignTop)
            row.addWidget(label(text, "muted"), 1)
            tl.addLayout(row)
        right.addWidget(tips)
        reader, rl = card()
        rl.addWidget(label("Reader settings", "cardTitle"))
        self.reader_summary = label("", "muted")
        rl.addWidget(self.reader_summary)
        rl.addWidget(button("Change in Settings", lambda: self.show_page("settings"), "link"))
        right.addWidget(reader)
        right.addStretch(1)
        columns.addLayout(right, 2)
        layout.addLayout(columns, 1)
        return _scrollable(page)

    def _update_scan_banner(self):
        self.refresh_scan_page()

    def refresh_scan_page(self):
        if not hasattr(self, "scan_key_banner"):
            return
        enabled = False
        if not self.config:
            self.scan_key_banner.set("<b>No test selected.</b> Create or choose a test before scanning.", "warn")
            self.scan_key_banner.set_action("New test", self.new_test)
            self.scan_key_banner.show()
        else:
            complete, text = self._key_status(self.config)
            enabled = complete
            self.scan_key_banner.set(f"<b>The answer key is not complete</b> ({text.replace('Incomplete - ', '')}). "
                                     f"Finish it before scanning so sheets can be scored.", "warn")
            self.scan_key_banner.set_action("Open answer key", self.edit_keys)
            self.scan_key_banner.setVisible(not complete)
        busy = self._scan_worker is not None
        self.upload_button.setEnabled(enabled and not busy)
        self.folder_button.setEnabled(enabled and not busy)
        self.drop_zone.set_active_look(enabled and not busy)
        if hasattr(self, "scan_dpi_setting"):
            self.reader_summary.setText(f"PDF pages are rendered at <b>{self.scan_dpi_setting.value()} DPI</b> in "
                                        f"<b>{self.scan_color_setting.currentText().lower()}</b>.")
        self._refresh_staged()
        batches = self.repo.batches(self.active_test_id) if self.active_test_id is not None else []
        counts = self.repo.pending_review_counts()
        self._history_batches = batches
        self.batch_history.setRowCount(len(batches))
        for row, batch in enumerate(batches):
            values = (f"#{batch['id']}", _when(batch["created_at"]), batch["sheet_count"],
                      f"{batch['processed_pages']}/{batch['expected_pages']}", _batch_state(batch, counts.get(int(batch["id"]), 0)))
            for column, value in enumerate(values):
                self.batch_history.setItem(row, column, _item(value))
        self.batch_history.setVisible(bool(batches))
        self.history_empty.setVisible(not batches)

    def _open_history_batch(self, row: int, _column: int = 0):
        if self._scan_worker is None and 0 <= row < len(self._history_batches):
            self._open_batch(self._history_batches[row])

    # -- staging: collect any number of files and folders before reading
    def add_scan_paths(self, paths: list[str]):
        if self._scan_worker is not None:
            return
        try:
            self._require_complete_keys()
        except Exception as exc:
            alert(self, "Answer key required", str(exc), "warn")
            return
        known = {path for path, _pages in self.staged_scans}
        added = skipped = 0
        for raw in paths:
            source = Path(raw)
            candidates = scan_files_in_folder(source) if source.is_dir() else [source]
            for candidate in candidates:
                if candidate.suffix.lower() not in SUPPORTED_SCAN_SUFFIXES or not candidate.is_file():
                    skipped += 1
                elif candidate not in known:
                    try:
                        pages: int | None = count_scan_pages(candidate)
                    except Exception:
                        pages = None
                    self.staged_scans.append((candidate, pages))
                    known.add(candidate)
                    added += 1
        self.outcome_card.hide()
        self._refresh_staged()
        if skipped and not added:
            self.toast(f"{skipped} unsupported file{'s' if skipped != 1 else ''} skipped", "warn")
        elif added:
            self.toast(f"Added {added} file{'s' if added != 1 else ''}" + (f" · {skipped} unsupported skipped" if skipped else ""))
        elif paths:
            self.toast("Those files are already in the queue", "info")

    def scan_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add scanned sheets (select as many as you like)", "", IMAGE_TYPES)
        if files:
            self.add_scan_paths(files)

    def choose_scan_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Add a folder of scanned sheets")
        if not folder:
            return
        try:
            if not scan_files_in_folder(folder):
                alert(self, "No scans found", "That folder has no supported scan files (PNG, JPG, TIFF, BMP, PDF).", "warn")
                return
        except Exception as exc:
            alert(self, "The folder could not be read", str(exc), "bad")
            return
        self.add_scan_paths([folder])

    def clear_staged_scans(self):
        self.staged_scans = []
        self._refresh_staged()

    def _remove_staged(self, path: Path):
        self.staged_scans = [(p, pages) for p, pages in self.staged_scans if p != path]
        self._refresh_staged()

    def _refresh_staged(self):
        staged = self.staged_scans
        busy = self._scan_worker is not None
        self.queue_card.setVisible(bool(staged) and not busy)
        self.staged_table.setRowCount(len(staged))
        total_pages = 0
        for row, (path, pages) in enumerate(staged):
            total_pages += pages or 0
            try:
                size = _size(path.stat().st_size)
            except OSError:
                size = "—"
            self.staged_table.setItem(row, 0, _item(path.name))
            self.staged_table.setItem(row, 1, _item(str(path.parent)))
            self.staged_table.setItem(row, 2, _item(pages if pages is not None else "Unreadable", align=Qt.AlignRight))
            self.staged_table.setItem(row, 3, _item(size, align=Qt.AlignRight))
            remove = button("", lambda p=path: self._remove_staged(p), "tool", "x", f"Remove {path.name}")
            self.staged_table.setCellWidget(row, 4, remove)
        files = len(staged)
        self.queue_title.setText(f"Upload queue · {files} file{'s' if files != 1 else ''}")
        self.staged_summary.setText(f"{total_pages} page{'s' if total_pages != 1 else ''} to read")
        self.start_scan_button.setText(f"Start reading {total_pages} page{'s' if total_pages != 1 else ''}")
        self.start_scan_button.setEnabled(total_pages > 0)

    def _reset_scan_stage(self):
        self.outcome_card.hide()
        self.drop_zone.show()
        self._refresh_staged()

    # -- reading
    def _scan_settings(self) -> tuple[int, str]:
        return self.scan_dpi_setting.value(), str(self.scan_color_setting.currentData())

    def start_scan(self):
        if self._scan_worker is not None or not self.staged_scans:
            return
        try:
            self._require_complete_keys()
        except Exception as exc:
            alert(self, "Answer key required", str(exc), "warn")
            return
        files = [str(path) for path, _pages in self.staged_scans]
        dpi, color_mode = self._scan_settings()
        worker = ScanWorker(self.repo, self.db_path, self.active_test_id, self.config, files, dpi, color_mode, self)
        worker.progressed.connect(self._scan_progressed)
        worker.batch_created.connect(self._scan_batch_created)
        worker.completed.connect(self._scan_finished)
        worker.finished.connect(worker.deleteLater)
        self._scan_worker = worker
        self.staged_scans = []
        self._set_busy(True)
        self.drop_zone.hide()
        self.outcome_card.hide()
        self.progress_card.show()
        self.progress_title.setText("Preparing files…")
        self.progress_detail.setText("")
        self.scan_progress.setRange(0, 0)
        for counter in self.scan_counters.values():
            counter.setText("0")
        self.cancel_scan_button.setEnabled(True)
        self.cancel_scan_button.setText("Stop")
        self.refresh_scan_page()
        worker.start()

    def _scan_batch_created(self, batch_id: int):
        self.progress_title.setText(f"Reading batch #{batch_id}…")

    def _scan_progressed(self, progress: ScanProgress):
        self.scan_progress.setRange(0, max(1, progress.total))
        self.scan_progress.setValue(progress.processed)
        self.progress_detail.setText(f"{progress.processed} of {progress.total} pages · {progress.current_file}")
        self.scan_counters["read"].setText(str(progress.processed))
        self.scan_counters["review"].setText(str(progress.needs_review))
        self.scan_counters["errors"].setText(str(progress.errors))

    def cancel_scan(self):
        if self._scan_worker is not None:
            self._scan_worker.cancel()
            self.cancel_scan_button.setEnabled(False)
            self.cancel_scan_button.setText("Stopping…")

    def _process_scan_files(self, files):
        """Read files immediately on the calling thread (used by scripts and tests)."""
        dpi, color_mode = self._scan_settings()
        outcome = process_scan_batch(self.repo, self.db_path, self.active_test_id, self.config, list(files),
                                     scan_dpi=dpi, color_mode=color_mode)
        self._scan_finished(outcome)
        return outcome

    def _scan_finished(self, outcome: ScanOutcome):
        self._scan_worker = None
        self._set_busy(False)
        self.progress_card.hide()
        self.outcome_card.show()
        if outcome.batch_id is None or (outcome.fatal and not outcome.processed):
            self.outcome_banner.set("<b>Nothing could be read.</b> " + (outcome.fatal or "").replace("\n", "<br>"), "bad")
            self.scan_status.setText("Check that the files are scans of sheets printed from this app, then try again.")
            self.outcome_primary.hide()
            self.log_button.setEnabled(outcome.batch_id is not None)
            if outcome.batch_id is not None:
                self.batch_id = outcome.batch_id
            self.drop_zone.show()
            self.refresh_scan_page()
            return
        self.batch_id = outcome.batch_id
        self.repo.set_setting("last_batch_id", str(self.batch_id))
        self.log_button.setEnabled(True)
        self.result_records = self.repo.results_for_batch(self.batch_id)
        self.batch_label.setText(f"Batch #{self.batch_id} · {len(self.result_records)} sheet(s) · {outcome.status}")
        self.load_review_table()
        read, review, errors = outcome.processed, outcome.needs_review, len(outcome.errors)
        if outcome.cancelled:
            self.outcome_banner.set(f"<b>Stopped after {read} of {outcome.expected} pages.</b> The sheets already read are saved, "
                                    f"but this batch is marked incomplete and cannot be exported.", "warn")
        elif errors:
            self.outcome_banner.set(f"<b>{read} sheet{'s' if read != 1 else ''} read, {errors} file{'s' if errors != 1 else ''} failed.</b> "
                                    f"The batch is marked incomplete. See the batch log for details.", "warn")
        elif review:
            self.outcome_banner.set(f"<b>{read} sheet{'s' if read != 1 else ''} read.</b> {review} need{'s' if review == 1 else ''} "
                                    f"your review before results can be exported.", "warn")
        else:
            self.outcome_banner.set(f"<b>{read} sheet{'s' if read != 1 else ''} read with nothing to review.</b> Results are ready.", "good")
        self.scan_status.setText("<br>".join(outcome.errors[:6]) + ("<br>…" if len(outcome.errors) > 6 else ""))
        self.scan_status.setVisible(bool(outcome.errors))
        self.outcome_primary.show()
        try:
            self.outcome_primary.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        target = "review" if review else "results"
        self.outcome_primary.setText(f"Review {review} sheet{'s' if review != 1 else ''}" if review else "View results")
        self.outcome_primary.setIcon(icon(target, "#FFFFFF", 16))
        self.outcome_primary.clicked.connect(lambda _c=False, k=target: self.show_page(k))
        self.drop_zone.show()
        self.refresh_scan_page()
        self.refresh_dashboard()

    def view_batch_log(self):
        if self.batch_id is None:
            alert(self, "No batch open", "Scan sheets or open a batch first.")
            return
        try:
            BatchLogDialog(self, self.batch_id, self.repo.batch_status(self.batch_id), self.repo.batch_log(self.batch_id)).exec()
        except Exception as exc:
            alert(self, "The batch log could not be loaded", str(exc), "bad")

    def load_saved_batch(self):
        if self._scan_worker is not None:
            return
        batches = self.repo.batches()
        if not batches:
            alert(self, "No saved batches", "Scan a batch of sheets first.")
            return
        dialog = BatchPickerDialog(self, batches, self.repo.pending_review_counts(), self.batch_id)
        if dialog.exec() and dialog.selected is not None:
            self._open_batch(dialog.selected, navigate=self._current_page not in ("review", "results"))
            if self._current_page == "results":
                self.refresh_results_summary()

    # ------------------------------------------------------------ results
    def _build_results_page(self) -> QWidget:
        more = button("More exports", None, "", "file-export")
        menu = QMenu(more)
        menu.addSection("Class marklist")
        menu.addAction("CSV file", lambda: self.export_results("csv"))
        menu.addAction("PDF report", lambda: self.export_results("pdf"))
        menu.addSection("For students")
        menu.addAction("Student scorecards (PDF)", lambda: self.export_results("cards"))
        menu.addSection("Analysis")
        menu.addAction("Item analysis (CSV)", lambda: self.export_results("item"))
        menu.addAction("Topic analysis (CSV)", lambda: self.export_results("topic"))
        menu.addAction("Subject analysis (CSV)", lambda: self.export_results("subject"))
        more.setMenu(menu)
        self.export_more_button = more
        self.export_main_button = button("Export marklist (Excel)", lambda: self.export_results("xlsx"), "primary", "download")
        page, layout = _page("Results", "Scores for the open batch. Export once every flagged sheet has been reviewed.",
                             (button("Open batch", self.load_saved_batch, "", "layers"), more, self.export_main_button))
        self.results_summary = Banner("Scan sheets or open a batch to see results here.", "info")
        layout.addWidget(self.results_summary)

        stats = QHBoxLayout()
        stats.setSpacing(14)
        self.result_stats: dict[str, StatTile] = {}
        for key, caption, icon_name in (("sheets", "Sheets", "sheet"), ("average", "Average score", "results"),
                                        ("highest", "Highest", "chevron-up"), ("lowest", "Lowest", "chevron-down"),
                                        ("pending", "Need review", "review")):
            tile = StatTile(caption, icon_name)
            tile.set_value("–")
            self.result_stats[key] = tile
            stats.addWidget(tile)
        layout.addLayout(stats)

        charts = QHBoxLayout()
        charts.setSpacing(14)
        distribution, dl = card()
        dl.addWidget(label("Score distribution", "cardTitle"))
        self.histogram_hint = label("Number of students in each score range. The dashed line is the class average.", "hint")
        dl.addWidget(self.histogram_hint)
        self.histogram = Histogram()
        dl.addWidget(self.histogram, 1)
        charts.addWidget(distribution, 3)
        hardest, hl = card((1, 16, 1, 6), 8)
        hl.addLayout(self._card_header("Hardest questions", "Lowest share of correct answers"))
        self.hardest_table = table(["Question", "Topic", "Correct"], 1)
        self.hardest_table.setFrameShape(QFrame.NoFrame)
        self.hardest_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.hardest_table.setMinimumHeight(232)
        self.hardest_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.hardest_table.setAccessibleName("Hardest questions")
        hl.addWidget(self.hardest_table, 1)
        charts.addWidget(hardest, 2)
        layout.addLayout(charts)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        ranking, rl = card((1, 16, 1, 6), 8)
        rl.addLayout(self._card_header("Student ranking", "Select a student to see every answer"))
        self.performance_table = table(["Rank", "Roll number", "Student", "Class", "Set", "Score", "Correct", "Wrong", "Blank", "Status"], 2)
        self.performance_table.setFrameShape(QFrame.NoFrame)
        self.performance_table.setMinimumHeight(250)
        self.performance_table.setItemDelegateForColumn(9, PillDelegate(lambda text: "good" if text == "Ready" else "warn", self.performance_table))
        self.performance_table.setAccessibleName("Ranked student performance")
        self.performance_table.currentCellChanged.connect(lambda row, _c, _pr, _pc: self.show_student_performance(row))
        rl.addWidget(self.performance_table, 1)
        split.addWidget(ranking)
        detail, sl = card((1, 16, 1, 6), 8)
        head = QVBoxLayout()
        head.setContentsMargins(18, 0, 18, 0)
        head.setSpacing(2)
        self.student_title = label("Student answers", "cardTitle")
        self.student_performance_detail = label("Select a student to inspect each response and the expected answer.", "hint")
        head.addWidget(self.student_title)
        head.addWidget(self.student_performance_detail)
        sl.addLayout(head)
        self.question_performance_table = table(["Question", "Section / topic", "Response", "Answer key", "Outcome", "Points"], 1)
        self.question_performance_table.setFrameShape(QFrame.NoFrame)
        self.question_performance_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.question_performance_table.setItemDelegateForColumn(4, PillDelegate(_outcome_tone, self.question_performance_table))
        self.question_performance_table.setAccessibleName("Selected student's question by question results")
        sl.addWidget(self.question_performance_table, 1)
        split.addWidget(detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setMinimumHeight(720)
        layout.addWidget(split, 1)
        return _scrollable(page)

    def refresh_results_summary(self):
        if not hasattr(self, "performance_table"):
            return
        records = self.result_records
        has_results = bool(records) and bool(self.config)
        self.export_main_button.setEnabled(has_results)
        self.export_more_button.setEnabled(has_results)
        if not has_results:
            for tile in self.result_stats.values():
                tile.set_value("–")
            self._result_rows = []
            self.performance_table.setRowCount(0)
            self.question_performance_table.setRowCount(0)
            self.hardest_table.setRowCount(0)
            self.histogram.set_scores([], 1)
            self.student_title.setText("Student answers")
            self.student_performance_detail.setText("Select a student to inspect each response and the expected answer.")
            self.results_summary.set("No results yet for this test. Scan sheets to see scores here.", "info")
            self.results_summary.set_action("Scan sheets", lambda: self.show_page("scan"))
            return
        scores = [float(r["score"]) for r in records]
        pending = sum(bool(r.get("review_required")) for r in records)
        self.result_stats["sheets"].set_value(str(len(records)), f"Batch #{self.batch_id}")
        self.result_stats["average"].set_value(f"{sum(scores) / len(scores):.1f}")
        self.result_stats["highest"].set_value(f"{max(scores):g}")
        self.result_stats["lowest"].set_value(f"{min(scores):g}")
        self.result_stats["pending"].set_value(str(pending), "", "warn" if pending else "good")
        status = self.repo.batch_status(self.batch_id) if self.batch_id is not None else {"status": "complete"}
        if status["status"] != "complete":
            self.results_summary.set(f"<b>This batch is incomplete</b> ({status['processed_pages']} of {status['expected_pages']} pages, "
                                     f"{status['error_count']} errors). Scan a clean batch to export official results.", "bad")
            self.results_summary.set_action("View batch log", self.view_batch_log)
        elif pending:
            self.results_summary.set(f"<b>{pending} sheet{'s' if pending != 1 else ''} still need{'s' if pending == 1 else ''} review.</b> "
                                     f"Scores below are provisional and exports are locked until they are checked.", "warn")
            self.results_summary.set_action("Go to Review", lambda: self.show_page("review"))
        else:
            self.results_summary.set("<b>All sheets reviewed.</b> Results are final and ready to export.", "good")
            self.results_summary.set_action()
        scoring = self.config["scoring"]
        dropped = {str(q) for q in scoring.get("dropped_questions", [])}
        maximum = sum(float(scoring["correct"]) for q in self.config["questions"] if str(q["id"]) not in dropped)
        self.histogram.set_scores(scores, maximum)

        try:
            analysis = [row for row in item_analysis(self.config, records) if row.get("difficulty") is not None]
        except Exception:
            analysis = []
        analysis.sort(key=lambda row: (row["difficulty"], row["question"]))
        hardest = analysis[:5]
        self.hardest_table.setRowCount(len(hardest))
        for index, row in enumerate(hardest):
            self.hardest_table.setItem(index, 0, _item(f"Q{row['question']}"))
            self.hardest_table.setItem(index, 1, _item(row.get("topic") or "—"))
            self.hardest_table.setItem(index, 2, _item(f"{round(row['difficulty'] * 100)}%", align=Qt.AlignRight))

        rows = self._result_rows = class_results(self.config, records)
        self.performance_table.blockSignals(True)
        self.performance_table.setColumnHidden(4, len(self.config["sheet"]["set_codes"]) < 2)
        self.performance_table.setRowCount(len(rows))
        for index, record in enumerate(rows):
            values = (record["rank"], record.get("roll_number", "") or "—", record.get("student_name", "") or "—",
                      record.get("class_name", "") or "—", record.get("set_code", "") or "—",
                      "—" if record.get("total") is None else f"{record['total']:g}",
                      record.get("correct", 0), record.get("incorrect", 0), record.get("blank", 0),
                      "Needs review" if record.get("review_required") else "Ready")
            for column, value in enumerate(values):
                self.performance_table.setItem(index, column, _item(value, align=Qt.AlignRight if column in (0, 5, 6, 7, 8) else None))
        self.performance_table.blockSignals(False)
        if rows:
            self.performance_table.selectRow(0)
            self.show_student_performance(0, 0)

    def show_student_performance(self, row: int, _column: int = 0):
        if not self.config:
            return
        rows = self._result_rows
        if row < 0 or row >= len(rows):
            return
        record = rows[row]
        self.student_title.setText(record.get("student_name") or f"Roll number {record.get('roll_number') or '—'}")
        review_note = " · pending scan review" if record.get("review_required") else ""
        total = record.get("total")
        self.student_performance_detail.setText(
            f"Roll {record.get('roll_number') or '—'} · score {'—' if total is None else f'{total:g}'} · "
            f"{record.get('correct', 0)} correct, {record.get('incorrect', 0)} wrong, {record.get('blank', 0)} blank{review_note}")
        question_rows = record.get("questions", [])
        metadata = {str(item["id"]): item for item in self.config["questions"]}
        self.question_performance_table.setRowCount(len(question_rows))
        for index, question in enumerate(question_rows):
            meta = metadata.get(str(question["question"]), {})
            topic = " · ".join(filter(None, (str(meta.get("section", "")), str(meta.get("topic", ""))))) or "—"
            values = (question["question"], topic, question.get("answer") or "Blank", question.get("expected") or "—",
                      str(question.get("status", "")).capitalize(), f"{question.get('points', 0):g}")
            for column, value in enumerate(values):
                self.question_performance_table.setItem(index, column, _item(value, align=Qt.AlignRight if column == 5 else None))

    def export_results(self, kind: str):
        if not self.config or not self.result_records:
            alert(self, "No results yet", "Scan a batch of sheets first.")
            return
        if self.batch_id is not None:
            status = self.repo.batch_status(self.batch_id)
            if status["status"] != "complete":
                alert(self, "This batch is incomplete",
                      f"It processed {status['processed_pages']} of {status['expected_pages']} expected pages, with "
                      f"{status['error_count']} file or page errors. Scan a clean batch before exporting official results.", "warn")
                return
        unresolved = sum(bool(record.get("review_required")) for record in self.result_records)
        if unresolved:
            if confirm(self, "Review needed before exporting",
                       f"{unresolved} sheet{'s' if unresolved != 1 else ''} still {'have' if unresolved != 1 else 'has'} unclear marks "
                       f"or roll numbers. Official results can only be exported once they are checked.", "Go to Review", "Close"):
                self.show_page("review")
            return
        filters = {"csv": ("CSV (*.csv)", "marklist.csv"), "xlsx": ("Excel workbook (*.xlsx)", "marklist.xlsx"), "pdf": ("PDF report (*.pdf)", "results.pdf"),
                   "cards": ("PDF scorecards (*.pdf)", "student-scorecards.pdf"), "item": ("CSV (*.csv)", "item-analysis.csv"),
                   "topic": ("CSV (*.csv)", "topic-analysis.csv"), "subject": ("CSV (*.csv)", "subject-analysis.csv")}
        filter_text, name = filters[kind]
        path, _ = QFileDialog.getSaveFileName(self, "Export results", name, filter_text)
        if not path:
            return
        try:
            function = {"csv": export_csv, "xlsx": export_xlsx, "pdf": export_report_pdf, "cards": export_student_scorecards_pdf,
                        "item": export_item_analysis_csv, "topic": export_topic_analysis_csv, "subject": export_subject_analysis_csv}[kind]
            function(self.config, self.result_records, path)
            if confirm(self, "Export complete", f"Saved <b>{Path(path).name}</b>.", "Open file", "Close"):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as exc:
            alert(self, "Export failed", str(exc), "bad")

    # ------------------------------------------------------------ settings
    def _build_settings_page(self) -> QWidget:
        page, layout = _page("Settings", "Changes are saved automatically. Most people never need to change these.")
        self.settings_saved = Pill("Saved automatically", "good")
        self.settings_saved.hide()

        def section(title: str, hint: str) -> tuple[QFrame, QVBoxLayout]:
            frame, inner = card((20, 18, 20, 18), 10)
            frame.setMaximumWidth(760)
            inner.addWidget(label(title, "cardTitle"))
            inner.addWidget(label(hint, "hint"))
            return frame, inner

        def form() -> QFormLayout:
            layout_ = QFormLayout()
            layout_.setHorizontalSpacing(24)
            layout_.setVerticalSpacing(10)
            layout_.setLabelAlignment(Qt.AlignLeft)
            return layout_

        scanning, sl = section("Scanning", "How PDF scans are converted to images before they are read.")
        self.scan_dpi_setting = QSpinBox()
        self.scan_dpi_setting.setRange(150, 600)
        self.scan_dpi_setting.setSuffix(" DPI")
        self.scan_dpi_setting.setValue(int(self.repo.get_setting("scan_dpi", "216")))
        self.scan_color_setting = combo()
        self.scan_color_setting.addItem("Grayscale", "grayscale")
        self.scan_color_setting.addItem("Color", "color")
        self.scan_color_setting.setCurrentIndex(max(0, self.scan_color_setting.findData(self.repo.get_setting("scan_color_mode", "grayscale"))))
        scan_form = form()
        scan_form.addRow("PDF rendering quality", self.scan_dpi_setting)
        scan_form.addRow("Scan colour mode", self.scan_color_setting)
        sl.addLayout(scan_form)
        layout.addWidget(scanning)

        marking, ml = section("Default marking", "Starting points for new tests. Each test can still use its own marking.")
        self.default_correct_setting = QDoubleSpinBox()
        self.default_incorrect_setting = QDoubleSpinBox()
        self.default_blank_setting = QDoubleSpinBox()
        mark_form = form()
        for box, key, default, caption in ((self.default_correct_setting, "default_correct_points", "4", "Points for a correct answer"),
                                           (self.default_incorrect_setting, "default_incorrect_points", "-1", "Points for a wrong answer"),
                                           (self.default_blank_setting, "default_blank_points", "0", "Points for a blank answer")):
            box.setRange(-1000, 1000)
            box.setDecimals(2)
            box.setValue(float(self.repo.get_setting(key, default)))
            mark_form.addRow(caption, box)
        ml.addLayout(mark_form)
        layout.addWidget(marking)
        for control in (self.scan_dpi_setting, self.default_correct_setting, self.default_incorrect_setting, self.default_blank_setting):
            control.setMaximumWidth(180)
            control.valueChanged.connect(lambda _value: self.save_settings(quiet=True))
        self.scan_color_setting.setMaximumWidth(180)
        self.scan_color_setting.currentIndexChanged.connect(lambda _index: self.save_settings(quiet=True))

        brand_card, bl = section("Institute", "QualifyIAS is printed by default. Add a PNG or JPG logo to replace the built-in wordmark.")
        self.institute_setting = QLineEdit(self.repo.get_setting("institute_name", "QualifyIAS"))
        self.institute_setting.setPlaceholderText("e.g. Greenfield Public School")
        self.institute_setting.setMaxLength(90)
        self.institute_setting.setMaximumWidth(360)
        self.institute_setting.editingFinished.connect(lambda: self.save_settings(quiet=True))
        brand_form = form()
        brand_form.addRow("Institute name", self.institute_setting)
        bl.addLayout(brand_form)
        logo_row = QHBoxLayout()
        logo_row.setSpacing(12)
        self.logo_preview = QLabel()
        self.logo_preview.setFixedSize(84, 70)
        self.logo_preview.setAlignment(Qt.AlignCenter)
        self.logo_preview.setObjectName("inset")
        logo_row.addWidget(self.logo_preview)
        logo_row.addWidget(button("Choose logo", self.choose_logo, "", "upload"))
        self.logo_remove_button = button("Remove", self.remove_logo, "ghost", "trash")
        logo_row.addWidget(self.logo_remove_button)
        logo_row.addStretch(1)
        bl.addLayout(logo_row)
        bl.addWidget(label("Use a PNG or JPG with a plain or transparent background. It prints in black and white, about 22 x 15 mm, so wide logos work best.", "hint"))
        layout.addWidget(brand_card)
        self._refresh_logo_preview()

        data, dl = section("Backup and data", "A backup holds your tests, students, scans, and results in one file.")
        self.auto_backup_label = label("", "muted")
        dl.addWidget(self.auto_backup_label)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(button("Back up now", self.backup_data, "", "download"))
        row.addWidget(button("Restore from backup", self.restore_data, "", "refresh"))
        row.addWidget(button("Open data folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.db_path.parent))), "ghost", "folder"))
        row.addStretch(1)
        dl.addLayout(row)
        layout.addWidget(data)

        about, al = section("About", f"OMRaut version {__version__}. Works fully offline.")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.update_button = button("Check for updates", self.check_updates, "", "refresh")
        row.addWidget(self.update_button)
        row.addWidget(self.settings_saved)
        row.addStretch(1)
        al.addLayout(row)
        layout.addWidget(about)
        layout.addStretch(1)
        self._refresh_backup_label()
        return _scrollable(page)

    def save_settings(self, quiet: bool = False):
        values = {
            "scan_dpi": str(self.scan_dpi_setting.value()),
            "scan_color_mode": str(self.scan_color_setting.currentData()),
            "default_correct_points": str(self.default_correct_setting.value()),
            "default_incorrect_points": str(self.default_incorrect_setting.value()),
            "default_blank_points": str(self.default_blank_setting.value()),
            "institute_name": self.institute_setting.text().strip(),
        }
        try:
            for key, value in values.items():
                self.repo.set_setting(key, value)
        except Exception as exc:
            alert(self, "Settings could not be saved", str(exc), "bad")
            return
        self.refresh_scan_page()
        self.settings_saved.show()
        if not quiet:
            self.toast("Settings saved")

    # ------------------------------------------------------------ institute branding
    def _logo_path(self) -> Path:
        return self.db_path.parent / "branding" / "logo.png"

    def sheet_branding(self) -> tuple[str, Path | None]:
        """Institute name (test's own, else the Settings default) and the saved logo, if any."""
        name = ((self.config or {}).get("test", {}).get("institute", "")
                or self.repo.get_setting("institute_name", "QualifyIAS") or "QualifyIAS")
        logo = self._logo_path()
        return name, (logo if logo.is_file() else None)

    def _refresh_logo_preview(self):
        logo = self._logo_path()
        shown = QPixmap(str(logo)) if logo.is_file() else QPixmap()
        if shown.isNull():
            self.logo_preview.setPixmap(QPixmap())
            self.logo_preview.setText("No logo")
        else:
            self.logo_preview.setText("")
            self.logo_preview.setPixmap(shown.scaled(76, 62, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.logo_remove_button.setEnabled(logo.is_file())

    def choose_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose institute logo", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        image = QImage(path)
        if image.isNull():
            alert(self, "That image could not be opened", "Choose a PNG or JPG file.", "warn")
            return
        if max(image.width(), image.height()) > 600:
            image = image.scaled(600, 600, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        target = self._logo_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not image.save(str(target), "PNG"):
                raise OSError("The logo could not be saved")
        except Exception as exc:
            alert(self, "Logo could not be saved", str(exc), "bad")
            return
        self._refresh_logo_preview()
        self.toast("Logo saved. It prints on new answer sheets")

    def remove_logo(self):
        try:
            self._logo_path().unlink(missing_ok=True)
        except OSError as exc:
            alert(self, "Logo could not be removed", str(exc), "bad")
            return
        self._refresh_logo_preview()

    def _refresh_backup_label(self):
        last = self.repo.get_setting("last_auto_backup", "")
        self.auto_backup_label.setText(
            f"Automatic safety copies of the database are kept for 7 days. Last copy: <b>{last or 'not yet made'}</b>.")

    def run_startup_maintenance(self) -> None:
        """Housekeeping at launch: close batches a crash left open, and take the daily safety copy."""
        try:
            recovered = self.repo.recover_interrupted_batches()
            if recovered:
                self.refresh_dashboard()
                self.toast(f"{recovered} batch{'es were' if recovered != 1 else ' was'} interrupted last time and marked incomplete", "warn")
            today = datetime.now().strftime("%Y-%m-%d")
            if self.repo.tests() and self.repo.get_setting("last_auto_backup", "") != today:
                folder = self.db_path.parent / "auto-backups"
                self.repo.backup_to(folder / f"omr-exam-{today}.sqlite3")
                for stale in sorted(folder.glob("omr-exam-*.sqlite3"))[:-7]:
                    stale.unlink(missing_ok=True)
                self.repo.set_setting("last_auto_backup", today)
                self._refresh_backup_label()
        except Exception:
            import logging
            logging.getLogger("omr_exam").exception("Startup maintenance failed")

    def backup_data(self):
        suggested = str(self.db_path.with_name(f"omr-exam-backup-{datetime.now():%Y%m%d-%H%M%S}.omrbackup"))
        path, _ = QFileDialog.getSaveFileName(self, "Save app backup", suggested, "OMRaut backup (*.omrbackup)")
        if not path:
            return
        try:
            self.review_page.flush_drafts()
            self.repo.backup_bundle_to(path)
            self.toast("Backup saved")
        except Exception as exc:
            alert(self, "Backup failed", str(exc), "bad")

    def restore_data(self):
        if self._scan_worker is not None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Restore app backup", "", "OMRaut backup (*.omrbackup)")
        if not path:
            return
        if not confirm(self, "Replace current data with this backup?",
                       "All current tests, students, and results will be replaced. A safety backup of the current data "
                       "is made first, so this can be undone.", "Restore backup", danger=True):
            return
        try:
            safety = self.db_path.with_name(f"omr-exam-before-restore-{datetime.now():%Y%m%d-%H%M%S}.omrbackup")
            self.repo.backup_bundle_to(safety)
            self.repo.restore_bundle_from(path)
            self.batch_id = None
            self.result_records = []
            self.active_test_id = None
            self.log_button.setEnabled(False)
            self.review_page.clear_batch()
            self.refresh_tests()
            self.refresh_students()
            alert(self, "Restore complete", f"Your data was restored. The previous data is saved in:\n{safety}", "good")
        except Exception as exc:
            alert(self, "Restore failed", f"{exc}\n\nYour current data was not changed.", "bad")

    # ------------------------------------------------------------ updates
    def schedule_automatic_update_check(self):
        """Check once a day after startup when this build has an update feed."""
        if not UPDATE_SOURCE_URL or not should_check_automatically(self.repo.get_setting("last_update_check_utc", "")):
            return
        QTimer.singleShot(6500, lambda: self.check_updates(automatic=True))

    def check_updates(self, automatic: bool = False):
        worker = getattr(self, "_update_worker", None)
        if worker is not None and worker.isRunning():
            return
        available = getattr(self, "_available_update", None)
        if not automatic and available is not None:
            self._offer_update_install(*available)
            return
        if automatic:
            self.repo.set_setting("last_update_check_utc", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.update_button.setEnabled(False)
        if not automatic:
            self._show_update_progress("Checking for updates…")
        worker = _UpdateCheckWorker(automatic, self)
        worker.checked.connect(self._update_check_finished)
        worker.failed.connect(self._update_failed)
        worker.finished.connect(worker.deleteLater)
        self._update_worker = worker
        worker.start()

    def _show_update_progress(self, message: str):
        progress = BusyDialog(self, message)
        progress.show()
        self._update_progress = progress

    def _close_update_progress(self):
        progress = getattr(self, "_update_progress", None)
        if progress is not None:
            progress.finish()
            progress.deleteLater()
            self._update_progress = None

    def _update_check_finished(self, result, automatic: bool = False):
        self._close_update_progress()
        self._update_worker = None
        if result is None:
            self.update_button.setEnabled(True)
            if not automatic:
                alert(self, "Updates", "This installation is up to date." if UPDATE_SOURCE_URL
                      else "Automatic updates are not configured for this build.")
            return
        manager, update = result
        self._available_update = (manager, update)
        self.update_button.setText("Update available")
        self.update_button.setToolTip("A new release is ready. Click to review and install it.")
        self.update_notice.set("A new OMRaut update is available. Open Settings and click ‘Update available’ when you’re ready.", "info")
        self.update_notice.show()
        if automatic:
            self.update_button.setEnabled(True)
            return
        self._offer_update_install(manager, update)

    def _offer_update_install(self, manager, update):
        if not confirm(self, "Update available", "A new OMRaut release is available. Download it and restart to install?",
                       "Download and restart", "Later"):
            self.update_button.setEnabled(True)
            return
        self.update_button.setEnabled(False)
        self._show_update_progress("Downloading and applying the update…")
        worker = _UpdateApplyWorker(manager, update, self)
        worker.applied.connect(self._update_applied)
        worker.failed.connect(self._update_failed)
        worker.finished.connect(worker.deleteLater)
        self._update_worker = worker
        worker.start()

    def _update_applied(self):
        self._close_update_progress()
        self._update_worker = None
        self.update_button.setEnabled(True)
        self._available_update = None
        self.update_notice.hide()

    def _update_failed(self, message: str, automatic: bool = False):
        self._close_update_progress()
        self._update_worker = None
        self.update_button.setEnabled(True)
        if automatic:
            import logging
            logging.getLogger("omr_exam.updater").warning("Automatic update check failed: %s", message)
        else:
            alert(self, "Update check failed", message, "warn")

    # ------------------------------------------------------------ window state
    def _restore_geometry(self) -> None:
        try:
            saved = self.repo.get_setting("window_geometry", "")
            if saved:
                self.restoreGeometry(QByteArray(base64.b64decode(saved)))
        except Exception:
            pass

    def closeEvent(self, event):
        worker = getattr(self, "_update_worker", None)
        if worker is not None and worker.isRunning():
            alert(self, "Update in progress", "Wait for the update to finish before closing the app.")
            event.ignore()
            return
        if self._scan_worker is not None:
            if not confirm(self, "Stop reading and close?",
                           "Sheets are still being read. The sheets already read are saved, but this batch will be marked incomplete.",
                           "Stop and close", "Keep reading", danger=True):
                event.ignore()
                return
            scan_worker = self._scan_worker
            scan_worker.cancel()
            scan_worker.wait(15000)
        try:
            self.review_page.flush_drafts()
            self.repo.set_setting("window_geometry", base64.b64encode(bytes(self.saveGeometry())).decode("ascii"))
        except Exception:
            pass
        super().closeEvent(event)
