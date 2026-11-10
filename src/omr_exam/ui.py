"""Operator desktop workflow: tests, keys, sheet PDF, scan batches, review, export."""
from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton, QSpinBox, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from omr_exam.answer_keys import validate_answer_key
from omr_exam.config import load_config
from omr_exam.database import Repository
from omr_exam.dialogs import AnswerKeyDialog, NewTestDialog, StudentDialog
from omr_exam.ingest import count_scan_pages, scan_files_in_folder, scan_pages
from omr_exam.layout import create_layout
from omr_exam.reader import read_image
from omr_exam.reports import (export_csv, export_item_analysis_csv, export_report_pdf,
                              export_student_scorecards_pdf, export_subject_analysis_csv,
                              export_topic_analysis_csv, export_xlsx)
from omr_exam.scoring import score_answers
from omr_exam.sheets import generate_sheet_pdf
from omr_exam.theme import BAD_BG, WARN_BG
from omr_exam.updater import UPDATE_SOURCE_URL, check_for_update, download_and_apply

IMAGE_TYPES = "Scanned sheets (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.pdf);;All files (*.*)"

# Review table columns before the per-question answers.
FIRST_ANSWER_COLUMN = 6

NAV_ITEMS = (
    ("dashboard", "Home"),
    ("tests", "Tests & Keys"),
    ("students", "Students"),
    ("scan", "Scan Sheets"),
    ("review", "Review"),
    ("results", "Results"),
    ("settings", "Settings"),
)


class _UpdateCheckWorker(QThread):
    checked = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            self.checked.emit(check_for_update())
        except Exception as exc:
            self.failed.emit(str(exc))


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


# ---------------------------------------------------------------- UI helpers
def _label(text: str = "", name: str = "", wrap: bool = True) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _button(text: str, handler=None, kind: str = "") -> QPushButton:
    button = QPushButton(text)
    if kind:
        button.setObjectName(kind)
    button.setCursor(Qt.PointingHandCursor)
    if handler is not None:
        button.clicked.connect(lambda _checked=False: handler())
    return button


def _card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(); frame.setObjectName("card")
    layout = QVBoxLayout(frame); layout.setContentsMargins(18, 16, 18, 16); layout.setSpacing(10)
    return frame, layout


def _table(headers: list[str], stretch_column: int | None = None, editable: bool = False) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    if not editable:
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    header = table.horizontalHeader()
    if stretch_column is not None:
        header.setSectionResizeMode(stretch_column, QHeaderView.Stretch)
    return table


def _page(title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
    page = QWidget(); page.setObjectName("page")
    layout = QVBoxLayout(page); layout.setContentsMargins(32, 26, 32, 24); layout.setSpacing(16)
    layout.addWidget(_label(title, "pageTitle"))
    if subtitle:
        layout.addWidget(_label(subtitle, "pageSub"))
    return page, layout


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
        self.setWindowTitle("OMR Exam App")
        self.resize(1360, 840)
        self.setMinimumSize(1100, 700)

        self.pages = QStackedWidget()
        self.page_index: dict[str, int] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        self.test_combo = QComboBox(); self.test_combo.setMinimumWidth(320)
        self.test_combo.currentIndexChanged.connect(self.select_test)

        shell = QWidget(); outer = QHBoxLayout(shell); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())
        right = QVBoxLayout(); right.setSpacing(0)
        right.addWidget(self._build_topbar())
        right.addWidget(self.pages, 1)
        outer.addLayout(right, 1)
        self.setCentralWidget(shell)

        for key, builder in (("dashboard", self._build_dashboard_page), ("tests", self._build_tests_page),
                             ("students", self._build_students_page), ("scan", self._build_scan_page),
                             ("review", self._build_review_page), ("results", self._build_results_page),
                             ("settings", self._build_settings_page)):
            self.page_index[key] = self.pages.addWidget(builder())
        self.refresh_tests()
        self.refresh_students()
        self.show_page("dashboard")

    # ------------------------------------------------------------ chrome
    def _build_sidebar(self) -> QWidget:
        side = QFrame(); side.setObjectName("sidebar"); side.setFixedWidth(224)
        layout = QVBoxLayout(side); layout.setContentsMargins(14, 20, 14, 16); layout.setSpacing(4)
        layout.addWidget(_label("OMR Exam", "brand"))
        layout.addWidget(_label("Scan, score, and report", "brandSub"))
        for key, text in NAV_ITEMS:
            button = QPushButton(text); button.setObjectName("nav"); button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _c=False, k=key: self.show_page(k))
            self.nav_buttons[key] = button
            layout.addWidget(button)
        layout.addStretch(1)
        layout.addWidget(_label("All data stays on this computer.", "hint"))
        return side

    def _build_topbar(self) -> QWidget:
        bar = QFrame(); bar.setObjectName("topbar"); bar.setFixedHeight(60)
        layout = QHBoxLayout(bar); layout.setContentsMargins(32, 8, 32, 8); layout.setSpacing(10)
        layout.addWidget(_label("Working on test:", "muted", wrap=False))
        layout.addWidget(self.test_combo)
        layout.addStretch(1)
        layout.addWidget(_button("+ New test", self.new_test, "primary"))
        return bar

    def show_page(self, key: str):
        self.pages.setCurrentIndex(self.page_index[key])
        for name, button in self.nav_buttons.items():
            button.setChecked(name == key)
        if key == "dashboard":
            self.refresh_dashboard()
        elif key == "students":
            self.refresh_students()
        elif key == "results":
            self.refresh_results_summary()

    # ------------------------------------------------------------ dashboard
    def _build_dashboard_page(self) -> QWidget:
        page, layout = _page("Welcome", "Create a test, print sheets, scan them, and get marks in minutes.")
        stats = QHBoxLayout(); stats.setSpacing(14)
        self.stat_labels: dict[str, QLabel] = {}
        for key, caption in (("tests", "Tests"), ("students", "Students"), ("batches", "Scan batches"), ("sheets", "Sheets scanned")):
            card, cl = _card()
            value = _label("0", "statValue", wrap=False); self.stat_labels[key] = value
            cl.addWidget(value); cl.addWidget(_label(caption, "statLabel", wrap=False))
            stats.addWidget(card)
        layout.addLayout(stats)

        steps_card, sl = _card()
        sl.addWidget(_label("How it works", "sectionTitle"))
        row = QHBoxLayout(); row.setSpacing(14)
        for number, title, hint, handler, label in (
            ("1", "Create a test", "Pick a ready-made template.", self.new_test, "New test"),
            ("2", "Enter the answer key", "Type, paste, or import it.", self.edit_keys, "Answer keys"),
            ("3", "Print the sheets", "Print at 100% scale.", self.make_sheets, "Make sheet PDF"),
            ("4", "Scan and review", "Upload photos or scans.", lambda: self.show_page("scan"), "Scan sheets"),
        ):
            column = QVBoxLayout(); column.setSpacing(6)
            head = QHBoxLayout(); head.addWidget(_label(number, "stepNumber", wrap=False)); head.addWidget(_label(title, "sectionTitle")); head.addStretch(1)
            column.addLayout(head); column.addWidget(_label(hint, "hint")); column.addWidget(_button(label, handler)); column.addStretch(1)
            row.addLayout(column, 1)
        sl.addLayout(row)
        layout.addWidget(steps_card)

        layout.addWidget(_label("Your tests (double-click to open)", "sectionTitle"))
        self.dashboard_tests_table = _table(["Test", "Created", "ID"], 0)
        self.dashboard_tests_table.setMaximumHeight(190)
        self.dashboard_tests_table.cellDoubleClicked.connect(self.open_dashboard_test)
        layout.addWidget(self.dashboard_tests_table)
        layout.addWidget(_label("Recent scan batches (double-click to review)", "sectionTitle"))
        self.dashboard_batches_table = _table(["Batch", "Test", "Created", "Sheets", "Pages", "Status"], 1)
        self.dashboard_batches_table.cellDoubleClicked.connect(self.open_dashboard_batch)
        layout.addWidget(self.dashboard_batches_table, 1)
        return page

    def refresh_dashboard(self):
        tests = self.repo.tests()
        all_batches = self.repo.batches()
        batches = all_batches[:10]
        self.stat_labels["tests"].setText(str(len(tests)))
        self.stat_labels["students"].setText(str(len(self.repo.students())))
        self.stat_labels["batches"].setText(str(len(all_batches)))
        self.stat_labels["sheets"].setText(str(sum(int(b["sheet_count"]) for b in all_batches)))
        self.dashboard_tests_table.setRowCount(len(tests))
        for row, test in enumerate(tests):
            for column, value in enumerate((test["title"], test["created_at"], str(test["id"]))):
                self.dashboard_tests_table.setItem(row, column, QTableWidgetItem(str(value)))
        self.dashboard_batches = batches
        self.dashboard_batches_table.setRowCount(len(batches))
        for row, batch in enumerate(batches):
            values = (f"#{batch['id']}", batch["title"], batch["created_at"], str(batch["sheet_count"]),
                      f"{batch['processed_pages']}/{batch['expected_pages']}", batch["status"])
            for column, value in enumerate(values):
                self.dashboard_batches_table.setItem(row, column, QTableWidgetItem(str(value)))

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

    def _open_batch(self, batch: dict[str, Any]):
        test_index = self.test_combo.findData(batch["test_id"])
        if test_index >= 0:
            self.test_combo.setCurrentIndex(test_index)
        self.batch_id = int(batch["id"])
        self.log_button.setEnabled(True)
        self.repo.reconcile_batch_roster_flags(self.batch_id)
        self.result_records = self.repo.results_for_batch(self.batch_id)
        self.batch_label.setText(f"Batch #{self.batch_id}: {len(self.result_records)} sheet(s) · {batch['status']}")
        self.load_review_table()
        self.show_page("review")

    # ------------------------------------------------------------ tests & keys
    def _build_tests_page(self) -> QWidget:
        page, layout = _page("Tests & answer keys", "Create a test from a template, then enter its answer key. Sheets can only be printed and scanned once every set has a complete key.")
        actions = QHBoxLayout(); actions.setSpacing(10)
        actions.addWidget(_button("+ New test", self.new_test, "primary"))
        actions.addWidget(_button("Import test file (JSON)...", self.import_config))
        actions.addStretch(1)
        layout.addLayout(actions)

        self.tests_table = _table(["Test", "Class", "Subject", "Questions", "Sets", "Answer key"], 0)
        self.tests_table.setMaximumHeight(240)
        self.tests_table.cellClicked.connect(self._tests_table_clicked)
        layout.addWidget(self.tests_table)

        detail, dl = _card()
        self.test_title_label = _label("No test selected", "sectionTitle")
        self.test_info = _label("Create a test or import a JSON configuration to get started.", "muted")
        self.key_banner = _label("", "banner")
        dl.addWidget(self.test_title_label); dl.addWidget(self.test_info); dl.addWidget(self.key_banner)
        row = QHBoxLayout(); row.setSpacing(10)
        self.key_button = _button("Enter / edit answer keys", self.edit_keys, "primary")
        self.sheet_button = _button("Print answer sheet (PDF)", self.make_sheets)
        self.scan_button = _button("Scan sheets for this test", lambda: self.show_page("scan"))
        self.export_config_button = _button("Save test file...", self.export_config)
        for button in (self.key_button, self.sheet_button, self.scan_button, self.export_config_button):
            row.addWidget(button)
        row.addStretch(1)
        dl.addLayout(row)
        layout.addWidget(detail)
        layout.addStretch(1)
        return page

    def _tests_table_clicked(self, row: int, _column: int):
        item = self.tests_table.item(row, 0)
        if item is not None:
            index = self.test_combo.findData(item.data(Qt.UserRole))
            if index >= 0:
                self.test_combo.setCurrentIndex(index)

    def _key_status(self, config: dict[str, Any]) -> tuple[bool, str]:
        problems = []
        for code in config["sheet"]["set_codes"]:
            try:
                validate_answer_key(config, config["sets"][code]["answer_key"])
            except ValueError:
                filled = len(config["sets"][code]["answer_key"])
                problems.append(f"Set {code}: {filled}/{len(config['questions'])}")
        if problems:
            return False, "Incomplete - " + ", ".join(problems)
        return True, "Complete"

    def refresh_tests(self, select_id: int | None = None):
        tests = self.repo.tests()
        self.test_combo.blockSignals(True); self.test_combo.clear()
        for item in tests:
            self.test_combo.addItem(f"{item['title']}  (#{item['id']})", item["id"])
        if select_id is not None:
            index = self.test_combo.findData(select_id)
            if index >= 0:
                self.test_combo.setCurrentIndex(index)
        self.test_combo.blockSignals(False)
        self.tests_table.setRowCount(len(tests))
        for row, item in enumerate(tests):
            try:
                config = self.repo.config_for_test(item["id"])
                values = [item["title"], config["test"]["class"], config["test"]["subject"], str(len(config["questions"])),
                          ", ".join(config["sheet"]["set_codes"]), self._key_status(config)[1]]
            except Exception:
                values = [item["title"], "", "", "", "", "Unreadable"]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column == 0:
                    cell.setData(Qt.UserRole, item["id"])
                self.tests_table.setItem(row, column, cell)
            if self.test_combo.itemData(self.test_combo.currentIndex()) == item["id"]:
                self.tests_table.selectRow(row)
        self.select_test(self.test_combo.currentIndex())
        self.refresh_dashboard()

    def select_test(self, index: int):
        test_id = self.test_combo.itemData(index) if index >= 0 else None
        if test_id != self.active_test_id:
            self.batch_id = None
            self.result_records = []
            self.review_table.setRowCount(0)
            self.batch_label.setText("No batch selected")
            self.log_button.setEnabled(False)
            self.review_summary.setText("No batch loaded. Scan sheets or open a batch from Home.")
        self.active_test_id = test_id
        self.config = self.repo.config_for_test(test_id) if test_id is not None else None
        has_test = self.config is not None
        for button in (self.key_button, self.sheet_button, self.scan_button, self.export_config_button):
            button.setEnabled(has_test)
        if self.config:
            c = self.config
            self.test_title_label.setText(c["test"]["title"])
            self.test_info.setText(f"Class {c['test']['class']} · {c['test']['subject']} · {len(c['questions'])} questions · "
                                   f"options {', '.join(map(str, c['sheet']['options']))} · sets {', '.join(c['sheet']['set_codes'])} · "
                                   f"+{c['scoring']['correct']:g} correct / {c['scoring']['incorrect']:g} wrong / {c['scoring']['blank']:g} blank")
            complete, text = self._key_status(c)
            self.key_banner.setText(("Answer key: complete. You can print and scan sheets." if complete
                                     else f"Answer key {text}. Click 'Enter / edit answer keys' to finish it before scanning."))
            self.key_banner.setObjectName("goodBanner" if complete else "warnBanner")
        else:
            self.test_title_label.setText("No test selected")
            self.test_info.setText("Create a test or import a JSON configuration to get started.")
            self.key_banner.setText("Click '+ New test' and choose a template - it takes about a minute.")
            self.key_banner.setObjectName("banner")
        self.key_banner.style().unpolish(self.key_banner); self.key_banner.style().polish(self.key_banner)
        self._update_scan_banner()

    def new_test(self):
        dialog = NewTestDialog(self)
        if not dialog.exec():
            return
        try:
            config = dialog.result_config
            test_id = self.repo.save_test(config)
            self.refresh_tests(test_id)
            self.show_page("tests")
            if not self.edit_keys():
                QMessageBox.information(self, "Answer keys", "Test saved. Complete all set answer keys before printing sheets or scanning.")
        except Exception as exc:
            QMessageBox.critical(self, "Could not create test", str(exc))

    def import_config(self):
        file, _ = QFileDialog.getOpenFileName(self, "Import test configuration", "", "JSON config (*.json)")
        if not file:
            return
        try:
            test_id = self.repo.save_test(load_config(file))
            self.refresh_tests(test_id)
        except Exception as exc:
            QMessageBox.critical(self, "Invalid configuration", str(exc))

    def edit_keys(self) -> bool:
        if not self.config or self.active_test_id is None:
            QMessageBox.information(self, "No test", "Create or select a test first.")
            return False
        dialog = AnswerKeyDialog(self.config, self)
        if not dialog.exec():
            return False
        try:
            candidate_config = dialog.result_config
            updated = self.repo.update_test_config_and_rescore(self.active_test_id, candidate_config)
            self.config = candidate_config
            self.refresh_tests(self.active_test_id)
            self.results_summary.setText(f"Answer keys saved. Re-scored {updated} saved sheet(s) without rescanning.")
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Invalid answer key", str(exc))
            return False

    def export_config(self):
        if not self.config:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save test file", "test-config.json", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(self.config, indent=2, ensure_ascii=False), encoding="utf-8")

    def _require_complete_keys(self):
        if not self.config:
            raise ValueError("Select a test first")
        for code in self.config["sheet"]["set_codes"]:
            validate_answer_key(self.config, self.config["sets"][code]["answer_key"])

    def make_sheets(self):
        try:
            self._require_complete_keys()
        except Exception as exc:
            QMessageBox.warning(self, "Answer key required", f"{exc}\n\nUse 'Enter / edit answer keys' first.")
            return
        try:
            path, _ = QFileDialog.getSaveFileName(self, "Save printable answer sheet PDF", "answer-sheets.pdf", "PDF (*.pdf)")
            if path:
                generate_sheet_pdf(self.config, path)
                QMessageBox.information(self, "Sheet PDF ready", f"Saved print-ready sheet:\n{path}\n\nPrint at 100% scale (no 'fit to page'). Do not crop the corner marks.")
        except Exception as exc:
            QMessageBox.critical(self, "Could not generate sheet", str(exc))

    # ------------------------------------------------------------ students
    def _build_students_page(self) -> QWidget:
        page, layout = _page("Students", "Add the class roster so results show names. Roll numbers must match what students bubble on the sheet.")
        row = QHBoxLayout(); row.setSpacing(10)
        self.student_search = QLineEdit(); self.student_search.setPlaceholderText("Search by roll number, name or class")
        self.student_search.textChanged.connect(self.refresh_students)
        self.student_search.setMinimumWidth(300)
        row.addWidget(self.student_search)
        row.addStretch(1)
        row.addWidget(_button("+ Add student", self.add_student, "primary"))
        row.addWidget(_button("Import CSV / Excel...", self.import_students))
        row.addWidget(_button("Download blank roster file", self.download_roster_template))
        row.addWidget(_button("Delete selected", self.delete_students, "danger"))
        layout.addLayout(row)
        self.students_table = _table(["Roll number", "Name", "Class"], 1)
        self.students_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.students_table.cellDoubleClicked.connect(self.edit_student)
        layout.addWidget(self.students_table, 1)
        self.students_hint = _label("", "hint")
        layout.addWidget(self.students_hint)
        return page

    def refresh_students(self, *_args):
        needle = self.student_search.text().strip().lower()
        students = [s for s in self.repo.students() if not needle or needle in f"{s['roll_number']} {s['name']} {s['class_name']}".lower()]
        self.students_table.setRowCount(len(students))
        for row, student in enumerate(students):
            for column, key in enumerate(("roll_number", "name", "class_name")):
                self.students_table.setItem(row, column, QTableWidgetItem(str(student[key])))
        total = len(self.repo.students())
        self.students_hint.setText(f"{total} student(s) saved." if total else
                                   "No students yet. Add them one by one, or import a CSV/Excel file with columns roll_number, name, class_name.")

    def add_student(self):
        dialog = StudentDialog(self)
        if dialog.exec():
            self._save_student(*dialog.values())

    def edit_student(self, row: int, _column: int = 0):
        roll, name, cls = (self.students_table.item(row, c).text() for c in range(3))
        dialog = StudentDialog(self, roll, name, cls)
        if dialog.exec():
            self._save_student(*dialog.values())

    def _save_student(self, roll: str, name: str, cls: str):
        try:
            self.repo.save_student(roll, name, cls)
            self._after_roster_change()
        except Exception as exc:
            QMessageBox.warning(self, "Could not save student", str(exc))

    def delete_students(self):
        rolls = sorted({self.students_table.item(i.row(), 0).text() for i in self.students_table.selectedIndexes()})
        if not rolls:
            QMessageBox.information(self, "Delete students", "Select one or more rows first.")
            return
        if QMessageBox.question(self, "Delete students", f"Delete {len(rolls)} student(s) from the roster? Saved results are kept.") != QMessageBox.Yes:
            return
        for roll in rolls:
            self.repo.delete_student(roll)
        self._after_roster_change()

    def _after_roster_change(self):
        if self.batch_id is not None:
            self.repo.reconcile_batch_roster_flags(self.batch_id)
            self.result_records = self.repo.results_for_batch(self.batch_id)
            self.load_review_table()
        self.refresh_students(); self.refresh_dashboard()

    def import_students(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import student roster", "", "Roster files (*.csv *.xlsx);;CSV (*.csv);;Excel workbook (*.xlsx)")
        if not path:
            return
        try:
            count = self.repo.import_students_excel(path) if Path(path).suffix.lower() == ".xlsx" else self.repo.import_students_csv(path)
            self._after_roster_change()
            QMessageBox.information(self, "Roster imported", f"Imported or updated {count} student record(s).")
        except Exception as exc:
            QMessageBox.critical(self, "Could not import roster", str(exc))

    def download_roster_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save blank roster file", "students-template.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["roll_number", "name", "class_name"])
            writer.writerow(["000001", "Example Student", "10A"])
        QMessageBox.information(self, "Roster file saved", f"Fill in this file in Excel, save as CSV, then use 'Import CSV / Excel'.\n\n{path}")

    # ------------------------------------------------------------ scan
    def _build_scan_page(self) -> QWidget:
        page, layout = _page("Scan sheets", "Upload the scanned or photographed answer sheets. Each sheet is straightened, read, scored, and saved. Unclear sheets are held for your review.")
        self.scan_key_banner = _label("", "warnBanner")
        layout.addWidget(self.scan_key_banner)
        row = QHBoxLayout(); row.setSpacing(14)
        self.upload_button = _button("Upload scanned images or PDF\nPNG, JPG, TIFF, multi-page PDF", self.scan_images, "big")
        self.folder_button = _button("Scan a whole folder\nEvery supported file inside is read", self.choose_scan_folder, "big")
        for button in (self.upload_button, self.folder_button):
            button.setMinimumHeight(86)
            row.addWidget(button, 1)
        layout.addLayout(row)
        status_card, sl = _card()
        top = QHBoxLayout()
        self.batch_label = _label("No batch selected", "sectionTitle")
        self.log_button = _button("View batch log", self.view_batch_log); self.log_button.setEnabled(False)
        top.addWidget(self.batch_label, 1); top.addWidget(self.log_button)
        sl.addLayout(top)
        self.scan_status = _label("Ready. Scan at 150 DPI or higher, with all four black corner squares visible.", "muted")
        sl.addWidget(self.scan_status)
        layout.addWidget(status_card)
        tips, tl = _card()
        tl.addWidget(_label("For the most accurate reading", "sectionTitle"))
        tl.addWidget(_label("• Print the sheets from this app at 100% scale and use a flatbed or document scanner (a steady, well-lit, flat photo also works).<br>"
                            "• Students should fill bubbles completely with dark pen or pencil and erase fully when changing an answer.<br>"
                            "• Keep all four corner squares in the picture and the page flat - do not crop.<br>"
                            "• Anything unclear (faint, double-marked, missing roll number) is never guessed: it goes to <b>Review</b> for you to confirm.", "muted"))
        layout.addWidget(tips)
        layout.addStretch(1)
        return page

    def _update_scan_banner(self):
        if not hasattr(self, "scan_key_banner"):
            return
        if not self.config:
            self.scan_key_banner.setText("Create or select a test first (top-left of this window).")
            self.scan_key_banner.show(); enabled = False
        else:
            complete, text = self._key_status(self.config)
            enabled = complete
            self.scan_key_banner.setText(f"The answer key is not complete ({text}). Finish it under Tests & Keys before scanning.")
            self.scan_key_banner.setVisible(not complete)
        self.upload_button.setEnabled(enabled); self.folder_button.setEnabled(enabled)

    def scan_images(self):
        try:
            self._require_complete_keys()
        except Exception as exc:
            QMessageBox.warning(self, "Answer key required", str(exc)); return
        files, _ = QFileDialog.getOpenFileNames(self, "Upload scanned OMR sheets or scan PDFs", "", IMAGE_TYPES)
        if files:
            self._process_scan_files(files)

    def choose_scan_folder(self):
        try:
            self._require_complete_keys()
        except Exception as exc:
            QMessageBox.warning(self, "Answer key required", str(exc)); return
        folder = QFileDialog.getExistingDirectory(self, "Select folder containing scanned sheets")
        if not folder:
            return
        try:
            files = scan_files_in_folder(folder)
        except Exception as exc:
            QMessageBox.warning(self, "Could not read scan folder", str(exc)); return
        if not files:
            QMessageBox.information(self, "No scans found", "The selected folder contains no supported scan files.")
            return
        self._process_scan_files(files)

    def view_batch_log(self):
        if self.batch_id is None:
            QMessageBox.information(self, "Batch log", "Select or scan a batch first.")
            return
        try:
            status = self.repo.batch_status(self.batch_id)
            events = self.repo.batch_log(self.batch_id)
            lines = [f"Batch #{self.batch_id}: {status['status']} - "
                     f"{status['processed_pages']}/{status['expected_pages']} pages, {status['error_count']} errors"]
            lines.extend(f"{event['logged_at']} [{event['level']}] "
                         f"{event['source_file'] + ': ' if event['source_file'] else ''}{event['message']}" for event in events)
            if not events:
                lines.append("No batch events were recorded.")
            dialog = QDialog(self)
            dialog.setWindowTitle(f"Batch #{self.batch_id} log")
            dialog.resize(850, 500)
            layout = QVBoxLayout(dialog)
            output = QPlainTextEdit(); output.setReadOnly(True); output.setPlainText("\n".join(lines))
            layout.addWidget(output)
            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            buttons.rejected.connect(dialog.reject); buttons.accepted.connect(dialog.accept)
            layout.addWidget(buttons)
            dialog.exec()
        except Exception as exc:
            QMessageBox.warning(self, "Could not load batch log", str(exc))

    def _process_scan_files(self, files):
        page_counts, load_errors = [], []
        for file in files:
            try:
                page_counts.append((file, count_scan_pages(file)))
            except Exception as exc:
                load_errors.append(f"{Path(file).name}: {exc}")
        total_pages = sum(count for _, count in page_counts)
        if not total_pages:
            QMessageBox.warning(self, "No readable scans", "\n".join(load_errors) or "The selected files contained no pages.")
            return
        expected_pages = total_pages
        self.batch_id = self.repo.create_batch(self.active_test_id, str(Path(page_counts[0][0]).parent), expected_pages)
        self.log_button.setEnabled(True)
        for error in load_errors:
            self.repo.log_batch_event(self.batch_id, "ERROR", error)
        managed_dir = self.db_path.parent / "scans" / f"batch-{self.batch_id}"
        managed_dir.mkdir(parents=True, exist_ok=True)
        managed_pages = []
        for index, (file, count) in enumerate(page_counts, 1):
            copy_path = managed_dir / f"{index:04d}-{Path(file).name}"
            try:
                shutil.copy2(file, copy_path)
                managed_pages.append((str(copy_path), count))
            except Exception as exc:
                message = f"Could not copy to app data ({exc})"
                load_errors.append(f"{Path(file).name}: {message}")
                self.repo.log_batch_event(self.batch_id, "ERROR", message, Path(file).name)
        page_counts = managed_pages
        total_pages = sum(count for _, count in page_counts)
        if not total_pages:
            self.repo.finish_batch(self.batch_id, 0, error_count=max(1, len(load_errors)))
            self.repo.log_batch_event(self.batch_id, "ERROR", "No scan files could be copied into app data.")
            QMessageBox.warning(self, "No readable scans", "Selected scans could not be copied to app data.\n" + "\n".join(load_errors))
            return
        progress = QProgressDialog("Reading scanned sheets…", "Cancel", 0, total_pages, self)
        progress.setWindowModality(Qt.WindowModal); progress.show()
        results = []
        processed = 0
        cancelled = False
        scan_dpi = self.scan_dpi_setting.value()
        scan_color_mode = str(self.scan_color_setting.currentData())
        for file, _count in page_counts:
            try:
                for label, image_data in scan_pages(file, dpi=scan_dpi, color_mode=scan_color_mode):
                    progress.setValue(processed)
                    QApplication.processEvents()
                    if progress.wasCanceled():
                        cancelled = True
                        break
                    result = read_image(image_data, self.config)
                    if result.requires_review:
                        flag_summary = "; ".join(
                            f"{flag.get('kind', 'review')}"
                            f"{(' ' + str(flag['group'])) if flag.get('group') else ''}"
                            f"{(' Q' + str(flag['question'])) if flag.get('question') else ''}"
                            f"{(': ' + ', '.join(map(str, flag['values']))) if flag.get('values') else ''}"
                            for flag in result.flags if flag.get("kind") != "blank"
                        ) or "manual review required"
                        self.repo.log_batch_event(self.batch_id, "WARNING", f"Reader flags: {flag_summary}", Path(file).name)
                    result.thresholds["color_mode"] = scan_color_mode
                    if Path(file).suffix.lower() == ".pdf":
                        result.thresholds["pdf_render_dpi"] = float(scan_dpi)
                    original_label, page_suffix = (label.split("#", 1) + [""])[:2]
                    result.image_path = Path(original_label).relative_to(self.db_path.parent).as_posix() + (f"#{page_suffix}" if page_suffix else "")
                    preview = result.aligned_image if result.aligned_image is not None else image_data
                    if preview is not None:
                        preview_dir = self.db_path.parent / "previews"
                        preview_dir.mkdir(parents=True, exist_ok=True)
                        preview_path = preview_dir / f"batch-{self.batch_id}-sheet-{processed + 1}.png"
                        if cv2.imwrite(str(preview_path), preview):
                            result.preview_path = preview_path.relative_to(self.db_path.parent).as_posix()
                        else:
                            result.flags.append({"kind": "preview_error", "message": "Could not save scan preview"})
                    if result.set_code in self.config["sets"]:
                        scored = score_answers(self.config, result.set_code, result.answers)
                    else:
                        scored = {"total": 0, "correct": 0, "incorrect": 0, "blank": len(self.config["questions"]), "questions": []}
                    result_id = self.repo.store_read(self.batch_id, result, scored)
                    results.append({"id": result_id, "image_path": result.image_path, "preview_path": result.preview_path,
                                    "roll_number": result.roll_number or "", "set_code": result.set_code or "",
                                    "answers": result.answers, "score": scored["total"], "flags": result.flags,
                                    "review_required": result.requires_review})
                    processed += 1
                if cancelled:
                    break
            except Exception as exc:
                load_errors.append(f"{Path(file).name}: {exc}")
                self.repo.log_batch_event(self.batch_id, "ERROR", str(exc), Path(file).name)
        self.repo.reconcile_batch_roster_flags(self.batch_id)
        batch_status = self.repo.finish_batch(self.batch_id, processed, error_count=len(load_errors), cancelled=cancelled)
        if cancelled:
            self.repo.log_batch_event(self.batch_id, "WARNING", f"Batch cancelled after {processed} of {expected_pages} pages.")
        else:
            self.repo.log_batch_event(self.batch_id, "INFO", f"Batch finished as {batch_status}: {processed}/{expected_pages} pages, {len(load_errors)} file/page errors.")
        progress.setValue(processed)
        self.result_records = self.repo.results_for_batch(self.batch_id)
        self.batch_label.setText(f"Batch #{self.batch_id}: {len(results)} sheet(s) · {batch_status}")
        error_note = f" {len(load_errors)} file(s) failed to open." if load_errors else ""
        self.scan_status.setText(f"Read {len(results)} sheet(s); {sum(bool(row['review_required']) for row in results)} need manual review.{error_note}")
        self.load_review_table()
        self.refresh_dashboard()
        self.show_page("review")
        if load_errors:
            QMessageBox.warning(self, "Some files could not be opened", "\n".join(load_errors))

    # ------------------------------------------------------------ review
    def _build_review_page(self) -> QWidget:
        page, layout = _page("Review", "Check every highlighted sheet against its picture, fix anything wrong, tick 'Reviewed', then save.")
        self.review_summary = _label("No batch loaded. Scan sheets or open a batch from Home.", "banner")
        layout.addWidget(self.review_summary)
        bar = QHBoxLayout(); bar.setSpacing(10)
        self.only_flagged = QCheckBox("Show only sheets that need attention"); self.only_flagged.toggled.connect(self.apply_review_filter)
        bar.addWidget(self.only_flagged)
        bar.addWidget(_label("Colors: <span style='background:%s'>&nbsp;blank&nbsp;</span> &nbsp; <span style='background:%s'>&nbsp;unclear / double mark&nbsp;</span>" % (WARN_BG, BAD_BG), "hint", wrap=False))
        bar.addStretch(1)
        bar.addWidget(_button("Open another batch...", self.load_saved_batch))
        bar.addWidget(_button("Save corrections and re-score", self.save_reviews, "primary"))
        layout.addLayout(bar)
        self.review_table = _table([], None, editable=True)
        self.review_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.review_table.horizontalHeader().setStretchLastSection(True)
        self.review_table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.preview = QLabel("Select a sheet or answer to see the scan here")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setObjectName("card")
        self.preview.setMinimumWidth(340); self.preview.setWordWrap(True)
        split = QHBoxLayout(); split.setSpacing(14)
        split.addWidget(self.review_table, 4); split.addWidget(self.preview, 2)
        layout.addLayout(split, 1)
        self.review_table.currentCellChanged.connect(self.show_preview)
        return page

    def apply_review_filter(self, *_args):
        only = self.only_flagged.isChecked()
        for row, record in enumerate(self.result_records):
            if row < self.review_table.rowCount():
                self.review_table.setRowHidden(row, only and not record.get("review_required"))

    def load_review_table(self):
        if not self.config:
            return
        question_ids = [str(q["id"]) for q in self.config["questions"]]
        headers = ["Roll number", "Set", "Score", "Reviewed", "Flags", "Image", *[f"Q{qid}" for qid in question_ids]]
        self.review_table.clear(); self.review_table.setColumnCount(len(headers)); self.review_table.setHorizontalHeaderLabels(headers)
        self.review_table.setRowCount(len(self.result_records))
        bad, warn = QBrush(QColor(BAD_BG)), QBrush(QColor(WARN_BG))
        for row, record in enumerate(self.result_records):
            names = [flag.get("kind", "error") for flag in record["flags"]]
            flag_names = "; ".join(dict.fromkeys(names))
            if any(name != "blank" for name in names) and not record.get("review_required"):
                flag_names = "Reviewed · " + flag_names
            flagged_questions = {str(f.get("question")): f.get("kind") for f in record["flags"] if f.get("question") and f.get("kind") != "blank"}
            if record.get("review_required") is False:
                flagged_questions = {}
            kinds = {f.get("kind") for f in record["flags"]} if record.get("review_required") else set()
            values = [record["roll_number"], record["set_code"], str(record["score"]), "", flag_names, Path(record["image_path"].split("#", 1)[0]).name]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 3:
                    item.setFlags((item.flags() & ~Qt.ItemIsEditable) | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                    item.setCheckState(Qt.Unchecked if record.get("review_required") else Qt.Checked)
                elif col in {2, 4, 5}:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if col == 0 and kinds & {"invalid_id", "duplicate_id", "unknown_id", "unreadable"}:
                    item.setBackground(bad)
                if col == 1 and "invalid_set" in kinds:
                    item.setBackground(bad)
                self.review_table.setItem(row, col, item)
            for offset, qid in enumerate(question_ids, FIRST_ANSWER_COLUMN):
                answer = record["answers"].get(qid)
                item = QTableWidgetItem(answer or "")
                item.setTextAlignment(Qt.AlignCenter)
                if qid in flagged_questions:
                    item.setBackground(bad)
                elif answer is None:
                    item.setBackground(warn)
                self.review_table.setItem(row, offset, item)
        flagged = sum(bool(item.get("review_required")) for item in self.result_records)
        if flagged:
            self.review_summary.setText(f"{len(self.result_records)} sheet(s) read · {flagged} need your attention. Double-click a cell to correct it, tick 'Reviewed', then save.")
            self.review_summary.setObjectName("warnBanner")
        else:
            self.review_summary.setText(f"{len(self.result_records)} sheet(s) read · nothing needs attention. You can go to Results.")
            self.review_summary.setObjectName("goodBanner")
        self.review_summary.style().unpolish(self.review_summary); self.review_summary.style().polish(self.review_summary)
        self.apply_review_filter()

    def show_preview(self, row: int, column: int, _previous_row: int, _previous_column: int):
        if row < 0 or row >= len(self.result_records) or not self.config:
            self.preview.setText("Select a sheet or answer to see the scan here")
            return
        record = self.result_records[row]
        preview_path = Path(record.get("preview_path", "")) if record.get("preview_path") else Path()
        if str(preview_path) not in {"", "."} and not preview_path.is_absolute():
            preview_path = self.db_path.parent / preview_path
        if not preview_path or not Path(preview_path).is_file():
            self.preview.setText("No aligned preview is available for this saved sheet")
            return
        image = cv2.imread(str(preview_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            self.preview.setText("Could not display the scan preview")
            return
        is_unreadable = any(flag.get("kind") == "unreadable" for flag in record.get("flags", []))
        if column >= FIRST_ANSWER_COLUMN and not is_unreadable:
            question = str(self.config["questions"][column - FIRST_ANSWER_COLUMN]["id"])
            layout = create_layout(self.config)
            bubbles = [bubble for bubble in layout.bubbles if bubble.group == "answer" and bubble.index == question]
            if bubbles:
                sx, sy = image.shape[1] / layout.width, image.shape[0] / layout.height
                x1 = max(0, round((min(b.x for b in bubbles) - 5) * sx)); x2 = min(image.shape[1], round((max(b.x for b in bubbles) + 5) * sx))
                y1 = max(0, round((bubbles[0].y - 4) * sy)); y2 = min(image.shape[0], round((bubbles[0].y + 4) * sy))
                image = image[y1:y2, x1:x2]
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
        height, width = image.shape[:2]
        qimage = QImage(image.data, width, height, int(image.strides[0]), QImage.Format_RGB888).copy()
        self.preview.setPixmap(QPixmap.fromImage(qimage).scaled(self.preview.size() - QSize(24, 24), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def save_reviews(self):
        if not self.config or not self.result_records:
            return
        questions = [str(q["id"]) for q in self.config["questions"]]
        set_lookup = {str(code).upper(): str(code) for code in self.config["sheet"]["set_codes"]}
        option_lookup = {str(option).upper(): str(option) for option in self.config["sheet"]["options"]}
        try:
            candidate_rolls = [self.review_table.item(row, 0).text().strip() for row in range(self.review_table.rowCount())]
            seen, duplicates = set(), set()
            for candidate in candidate_rolls:
                if candidate and candidate in seen:
                    duplicates.add(candidate)
                seen.add(candidate)
            duplicates = sorted(duplicates)
            if duplicates:
                raise ValueError(f"Duplicate roll number(s) remain in this batch: {', '.join(duplicates)}")
            pending = []
            for row, record in enumerate(self.result_records):
                roll = self.review_table.item(row, 0).text().strip()
                code = set_lookup.get(self.review_table.item(row, 1).text().strip().upper(), "")
                reviewed = self.review_table.item(row, 3).checkState() == Qt.Checked
                name = Path(record['image_path']).name
                if record.get("review_required") and not reviewed:
                    raise ValueError(f"Inspect {name}, correct its marks, and tick 'Reviewed' before saving")
                if len(roll) != int(self.config["sheet"]["roll_number_digits"]) or not roll.isdigit():
                    raise ValueError(f"Sheet {name}: roll number must have {self.config['sheet']['roll_number_digits']} digits")
                if not code:
                    raise ValueError(f"Sheet {name}: unknown set")
                answers = {qid: option_lookup.get(self.review_table.item(row, i + FIRST_ANSWER_COLUMN).text().strip().upper()) for i, qid in enumerate(questions)}
                invalid = [(qid, self.review_table.item(row, i + FIRST_ANSWER_COLUMN).text().strip()) for i, qid in enumerate(questions)
                           if self.review_table.item(row, i + FIRST_ANSWER_COLUMN).text().strip() and answers[qid] is None]
                if invalid:
                    raise ValueError(f"Sheet {name}: invalid option {invalid[0][1]!r} for Q{invalid[0][0]}")
                pending.append({"id": record["id"], "answers": answers, "roll_number": roll, "set_code": code})
            scored_records = self.repo.update_results(pending, self.config)
            for update, record in zip(pending, self.result_records):
                scored = scored_records[int(record["id"])]
                record.update({"answers": update["answers"], "roll_number": update["roll_number"], "set_code": update["set_code"],
                               "score": scored["total"], "review_required": False})
            if self.batch_id is not None:
                self.repo.reconcile_batch_roster_flags(self.batch_id)
                self.result_records = self.repo.results_for_batch(self.batch_id)
            self.load_review_table()
            QMessageBox.information(self, "Corrections saved", "All changes were written to the correction audit and scores were recalculated.")
        except Exception as exc:
            QMessageBox.critical(self, "Could not save corrections", str(exc))

    def load_saved_batch(self):
        batches = self.repo.batches()
        if not batches:
            QMessageBox.information(self, "No saved batches", "Scan a batch first.")
            return
        labels = [f"Batch #{item['id']} · {item['title']} · {item['created_at']} · {item['sheet_count']} sheets · {item['status']}" for item in batches]
        label, ok = QInputDialog.getItem(self, "Open batch", "Batch", labels, 0, False)
        if ok:
            self._open_batch(batches[labels.index(label)])

    # ------------------------------------------------------------ results
    def _build_results_page(self) -> QWidget:
        page, layout = _page("Results & reports", "Export marklists and analysis once every uncertain sheet has been reviewed.")
        stats = QHBoxLayout(); stats.setSpacing(14)
        self.result_stats: dict[str, QLabel] = {}
        for key, caption in (("sheets", "Sheets"), ("average", "Average score"), ("highest", "Highest"), ("lowest", "Lowest"), ("pending", "Need review")):
            card, cl = _card()
            value = _label("-", "statValue", wrap=False); self.result_stats[key] = value
            cl.addWidget(value); cl.addWidget(_label(caption, "statLabel", wrap=False))
            stats.addWidget(card)
        layout.addLayout(stats)
        self.results_summary = _label("Open a batch (Review > Open another batch) or scan sheets to see results here.", "banner")
        layout.addWidget(self.results_summary)
        groups = (("Class marklist", (("Excel workbook (.xlsx)", "xlsx"), ("CSV (.csv)", "csv"), ("PDF report", "pdf"))),
                  ("For students", (("Student scorecards (PDF)", "cards"),)),
                  ("Analysis", (("Item analysis (CSV)", "item"), ("Topic analysis (CSV)", "topic"), ("Subject analysis (CSV)", "subject"))))
        for title, items in groups:
            card, cl = _card()
            cl.addWidget(_label(title, "sectionTitle"))
            row = QHBoxLayout(); row.setSpacing(10)
            for text, kind in items:
                row.addWidget(_button(text, lambda k=kind: self.export_results(k), "primary" if kind == "xlsx" else ""))
            row.addStretch(1)
            cl.addLayout(row)
            layout.addWidget(card)
        layout.addStretch(1)
        return page

    def refresh_results_summary(self):
        records = self.result_records
        if not records:
            for label in self.result_stats.values():
                label.setText("-")
            return
        scores = [float(r["score"]) for r in records]
        self.result_stats["sheets"].setText(str(len(records)))
        self.result_stats["average"].setText(f"{sum(scores) / len(scores):.1f}")
        self.result_stats["highest"].setText(f"{max(scores):g}")
        self.result_stats["lowest"].setText(f"{min(scores):g}")
        pending = sum(bool(r.get("review_required")) for r in records)
        self.result_stats["pending"].setText(str(pending))
        self.results_summary.setText("All sheets reviewed - results are ready to export." if not pending else
                                     f"{pending} sheet(s) still need review. Finish them in Review before exporting official results.")

    def export_results(self, kind: str):
        if not self.config or not self.result_records:
            QMessageBox.information(self, "No results", "Scan a batch first."); return
        if self.batch_id is not None:
            status = self.repo.batch_status(self.batch_id)
            if status["status"] != "complete":
                QMessageBox.warning(self, "Batch incomplete", f"This batch processed {status['processed_pages']} of {status['expected_pages']} expected pages, with {status['error_count']} file or page errors. Complete a clean scan batch before exporting official results.")
                return
        unresolved = sum(bool(record.get("review_required")) for record in self.result_records)
        if unresolved:
            QMessageBox.warning(self, "Review required", f"{unresolved} sheet(s) still have uncertain marks or identity errors. Correct them in Review before exporting official results.")
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
            QMessageBox.information(self, "Export complete", f"Saved results to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    # ------------------------------------------------------------ settings
    def _build_settings_page(self) -> QWidget:
        page, layout = _page("Settings", "Defaults for new tests and scanning. Most people never need to change these.")
        card, cl = _card()
        form = QFormLayout(); form.setHorizontalSpacing(24); form.setVerticalSpacing(10)
        self.scan_dpi_setting = QSpinBox(); self.scan_dpi_setting.setRange(150, 600)
        self.scan_dpi_setting.setValue(int(self.repo.get_setting("scan_dpi", "216")))
        self.scan_color_setting = QComboBox()
        self.scan_color_setting.addItem("Grayscale", "grayscale"); self.scan_color_setting.addItem("Color", "color")
        self.scan_color_setting.setCurrentIndex(max(0, self.scan_color_setting.findData(self.repo.get_setting("scan_color_mode", "grayscale"))))
        self.default_correct_setting = QDoubleSpinBox(); self.default_incorrect_setting = QDoubleSpinBox(); self.default_blank_setting = QDoubleSpinBox()
        for box, key, default in ((self.default_correct_setting, "default_correct_points", "4"),
                                  (self.default_incorrect_setting, "default_incorrect_points", "-1"),
                                  (self.default_blank_setting, "default_blank_points", "0")):
            box.setRange(-1000, 1000); box.setDecimals(2); box.setValue(float(self.repo.get_setting(key, default)))
        form.addRow("PDF scan rendering quality (DPI)", self.scan_dpi_setting)
        form.addRow("Scan color mode", self.scan_color_setting)
        form.addRow("Default points for correct", self.default_correct_setting)
        form.addRow("Default points for wrong", self.default_incorrect_setting)
        form.addRow("Default points for blank", self.default_blank_setting)
        cl.addLayout(form)
        row = QHBoxLayout(); row.addWidget(_button("Save settings", self.save_settings, "primary")); row.addStretch(1)
        cl.addLayout(row)
        layout.addWidget(card)
        data, dl = _card()
        dl.addWidget(_label("Backup and updates", "sectionTitle"))
        dl.addWidget(_label("Back up regularly - a backup contains your tests, students, scans, and results in one file.", "hint"))
        row = QHBoxLayout(); row.setSpacing(10)
        row.addWidget(_button("Back up all data...", self.backup_data))
        row.addWidget(_button("Restore from backup...", self.restore_data))
        self.update_button = _button("Check for updates", self.check_updates); row.addWidget(self.update_button)
        row.addStretch(1)
        dl.addLayout(row)
        layout.addWidget(data)
        layout.addStretch(1)
        return page

    def save_settings(self):
        values = {
            "scan_dpi": str(self.scan_dpi_setting.value()),
            "scan_color_mode": str(self.scan_color_setting.currentData()),
            "default_correct_points": str(self.default_correct_setting.value()),
            "default_incorrect_points": str(self.default_incorrect_setting.value()),
            "default_blank_points": str(self.default_blank_setting.value()),
        }
        for key, value in values.items():
            self.repo.set_setting(key, value)
        QMessageBox.information(self, "Settings saved", "Scan and default marking settings were saved on this computer.")

    def backup_data(self):
        suggested = str(self.db_path.with_name(f"omr-exam-backup-{datetime.now():%Y%m%d-%H%M%S}.omrbackup"))
        path, _ = QFileDialog.getSaveFileName(self, "Save app backup", suggested, "OMR Exam App backup (*.omrbackup)")
        if not path:
            return
        try:
            self.repo.backup_bundle_to(path)
            QMessageBox.information(self, "Backup complete", f"Saved the database, scans, and aligned previews to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Backup failed", str(exc))

    def restore_data(self):
        path, _ = QFileDialog.getOpenFileName(self, "Restore app backup", "", "OMR Exam App backup (*.omrbackup)")
        if not path:
            return
        confirm = QMessageBox.question(self, "Restore backup", "This replaces the current tests, students, and results. An automatic safety backup will be made first. Continue?")
        if confirm != QMessageBox.Yes:
            return
        try:
            safety = self.db_path.with_name(f"omr-exam-before-restore-{datetime.now():%Y%m%d-%H%M%S}.omrbackup")
            self.repo.backup_bundle_to(safety)
            self.repo.restore_bundle_from(path)
            self.batch_id = None; self.result_records = []
            self.log_button.setEnabled(False)
            self.refresh_tests(); self.refresh_students()
            QMessageBox.information(self, "Restore complete", f"Restored database, scans, and previews. Safety backup:\n{safety}")
        except Exception as exc:
            QMessageBox.critical(self, "Restore failed", str(exc))

    # ------------------------------------------------------------ updates
    def check_updates(self):
        worker = getattr(self, "_update_worker", None)
        if worker is not None and worker.isRunning():
            return
        self.update_button.setEnabled(False)
        self._show_update_progress("Checking for updates…")
        worker = _UpdateCheckWorker(self)
        worker.checked.connect(self._update_check_finished)
        worker.failed.connect(self._update_failed)
        worker.finished.connect(worker.deleteLater)
        self._update_worker = worker
        worker.start()

    def _show_update_progress(self, message: str):
        progress = QProgressDialog(message, "", 0, 0, self)
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()
        self._update_progress = progress

    def _close_update_progress(self):
        progress = getattr(self, "_update_progress", None)
        if progress is not None:
            progress.close()
            progress.deleteLater()
            self._update_progress = None

    def _update_check_finished(self, result):
        self._close_update_progress()
        self._update_worker = None
        if result is None:
            self.update_button.setEnabled(True)
            message = "This installation is up to date." if UPDATE_SOURCE_URL else "Automatic updates are not configured for this build."
            QMessageBox.information(self, "Updates", message)
            return
        manager, update = result
        answer = QMessageBox.question(self, "Update available", "A new OMR Exam App release is available. Download and restart to install it?")
        if answer != QMessageBox.Yes:
            self.update_button.setEnabled(True)
            return
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

    def _update_failed(self, message: str):
        self._close_update_progress()
        self._update_worker = None
        self.update_button.setEnabled(True)
        QMessageBox.warning(self, "Update check failed", message)

    def closeEvent(self, event):
        worker = getattr(self, "_update_worker", None)
        if worker is not None and worker.isRunning():
            QMessageBox.information(self, "Update in progress", "Wait for the update operation to finish before closing the app.")
            event.ignore()
            return
        super().closeEvent(event)
