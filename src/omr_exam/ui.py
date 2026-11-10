"""Operator desktop workflow: tests, keys, sheet PDF, scan batches, review, export."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from datetime import datetime

from PySide6.QtCore import Qt, QDate, QThread, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog, QHeaderView,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QInputDialog,
    QPlainTextEdit, QProgressDialog, QPushButton, QSpinBox, QDoubleSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
import cv2

from omr_exam.answer_keys import import_excel_key, import_master_sheet, parse_pasted_key, validate_answer_key
from omr_exam.config import load_config, validate_config
from omr_exam.database import Repository
from omr_exam.ingest import count_scan_pages, scan_files_in_folder, scan_pages
from omr_exam.layout import create_layout, maximum_columns_for_options, maximum_questions_per_column
from omr_exam.reader import read_image
from omr_exam.reports import (export_csv, export_item_analysis_csv, export_report_pdf,
                              export_student_scorecards_pdf, export_subject_analysis_csv,
                              export_topic_analysis_csv, export_xlsx)
from omr_exam.scoring import score_answers
from omr_exam.sheets import generate_sheet_pdf
from omr_exam.test_builder import create_test_config
from omr_exam.updater import UPDATE_SOURCE_URL, check_for_update, download_and_apply

IMAGE_TYPES = "Scanned sheets (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.pdf);;All files (*.*)"


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


class NewTestDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create test")
        self.title = QLineEdit()
        self.class_name = QLineEdit()
        self.subject = QLineEdit()
        self.sections = QLineEdit()
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.question_count = QSpinBox(); self.question_count.setRange(1, 180); self.question_count.setValue(50)
        self.options = QLineEdit("A, B, C, D")
        self.roll_digits = QSpinBox(); self.roll_digits.setRange(1, 20); self.roll_digits.setValue(6)
        self.set_codes = QLineEdit("A, B, C")
        self.columns = QSpinBox(); self.columns.setRange(1, 4); self.columns.setValue(2)
        self.options.textChanged.connect(self._update_column_capacity)
        self.columns.valueChanged.connect(self._update_question_capacity)
        self._update_column_capacity()
        self.name_field = QCheckBox("Name"); self.name_field.setChecked(True)
        self.class_field = QCheckBox("Class"); self.class_field.setChecked(True)
        self.correct_points = QDoubleSpinBox(); self.correct_points.setRange(-1000, 1000); self.correct_points.setValue(4); self.correct_points.setDecimals(2)
        self.incorrect_points = QDoubleSpinBox(); self.incorrect_points.setRange(-1000, 1000); self.incorrect_points.setValue(-1); self.incorrect_points.setDecimals(2)
        self.blank_points = QDoubleSpinBox(); self.blank_points.setRange(-1000, 1000); self.blank_points.setValue(0); self.blank_points.setDecimals(2)
        self.mark_threshold = QDoubleSpinBox(); self.mark_threshold.setRange(0.05, 0.95); self.mark_threshold.setSingleStep(0.01); self.mark_threshold.setValue(0.24); self.mark_threshold.setDecimals(2)
        self.faint_threshold = QDoubleSpinBox(); self.faint_threshold.setRange(0.005, 0.90); self.faint_threshold.setSingleStep(0.01); self.faint_threshold.setValue(0.02); self.faint_threshold.setDecimals(3)
        self.bonus = QLineEdit(); self.dropped = QLineEdit(); self.partial = QLineEdit()
        form = QFormLayout(self)
        form.addRow("Test title", self.title)
        form.addRow("Class", self.class_name)
        form.addRow("Subject", self.subject)
        form.addRow("Sections (comma separated)", self.sections)
        form.addRow("Date", self.date)
        form.addRow("Questions", self.question_count)
        form.addRow("Options (comma separated)", self.options)
        form.addRow("Roll number digits", self.roll_digits)
        form.addRow("Set codes", self.set_codes)
        form.addRow("Question columns", self.columns)
        fields_row = QHBoxLayout(); fields_row.addWidget(self.name_field); fields_row.addWidget(self.class_field)
        form.addRow("Printed identity fields", fields_row)
        form.addRow("Correct points", self.correct_points)
        form.addRow("Incorrect points", self.incorrect_points)
        form.addRow("Blank points", self.blank_points)
        form.addRow("Filled mark threshold", self.mark_threshold)
        form.addRow("Faint mark threshold", self.faint_threshold)
        form.addRow("Bonus question numbers", self.bonus)
        form.addRow("Dropped question numbers", self.dropped)
        form.addRow("Partial points (Q:points)", self.partial)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.attempt_accept); buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _update_column_capacity(self, _text: str = ""):
        option_count = len([item for item in self.options.text().split(",") if item.strip()])
        max_columns = maximum_columns_for_options(210, max(2, option_count))
        self.columns.setMaximum(max(1, max_columns))
        if max_columns == 0:
            self.options.setToolTip("These options do not fit on the configured sheet; reduce the option count or labels.")
        else:
            self.options.setToolTip("")
        self._update_question_capacity()

    def _update_question_capacity(self, _columns: int | None = None):
        max_rows = maximum_questions_per_column(297)
        maximum = max(1, self.columns.value() * max_rows)
        self.question_count.setMaximum(maximum)
        self.question_count.setToolTip(f"Up to {max_rows} questions per column fit on an A4 sheet.")

    def config(self) -> dict[str, Any]:
        partial_credit = {}
        for entry in self.partial.text().split(","):
            if entry.strip():
                question, points = entry.split(":", 1)
                partial_credit[str(int(question.strip()))] = float(points.strip())
        bonus_questions = [int(x.strip()) for x in self.bonus.text().split(",") if x.strip()]
        dropped_questions = [int(x.strip()) for x in self.dropped.text().split(",") if x.strip()]
        sections = [x.strip() for x in self.sections.text().split(",") if x.strip()] or [self.subject.text()]
        config = create_test_config(title=self.title.text(), test_date=self.date.date().toString("yyyy-MM-dd"),
                                  class_name=self.class_name.text(), subject=self.subject.text(),
                                  question_count=self.question_count.value(),
                                  options=[x.strip() for x in self.options.text().split(",")],
                                  roll_number_digits=self.roll_digits.value(),
                                  set_codes=[x.strip() for x in self.set_codes.text().split(",")],
                                  fields=[name for name, checked in (("name", self.name_field.isChecked()), ("class", self.class_field.isChecked())) if checked],
                                  columns=self.columns.value(), sections=sections,
                                  correct_points=self.correct_points.value(), incorrect_points=self.incorrect_points.value(),
                                  blank_points=self.blank_points.value(), bonus_questions=bonus_questions,
                                  dropped_questions=dropped_questions, partial_credit=partial_credit,
                                  mark_threshold=self.mark_threshold.value(), faint_threshold=self.faint_threshold.value())
        validate_config(config)
        return config

    def attempt_accept(self):
        try:
            self.result_config = self.config()
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Check test settings", str(exc))


class AnswerKeyDialog(QDialog):
    def __init__(self, config: dict[str, Any], parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Answer keys by set")
        self.resize(850, 650)
        self.table = QTableWidget(len(config["questions"]), 2 + len(config["sheet"]["set_codes"]))
        headers = ["Question", "Topic"] + [f"Set {code}" for code in config["sheet"]["set_codes"]]
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, question in enumerate(config["questions"]):
            qitem = QTableWidgetItem(str(question["id"])); qitem.setFlags(qitem.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, qitem)
            self.table.setItem(row, 1, QTableWidgetItem(str(question.get("topic", ""))))
            for offset, code in enumerate(config["sheet"]["set_codes"], 2):
                combo = QComboBox(); combo.addItem("")
                combo.addItems([str(option) for option in config["sheet"]["options"]])
                value = config["sets"][code]["answer_key"].get(str(question["id"]), "")
                combo.setCurrentText(str(value))
                self.table.setCellWidget(row, offset, combo)
        self.paste = QPushButton("Paste key into selected set…")
        self.paste.clicked.connect(self.paste_key)
        self.excel = QPushButton("Import Excel key into selected set…")
        self.excel.clicked.connect(self.import_excel)
        self.master = QPushButton("Read key from master sheet scan…")
        self.master.clicked.connect(self.import_master_sheet)
        self.hint = QLabel("Answer keys must cover every question in every set before saving.")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.attempt_accept); buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.hint); layout.addWidget(self.table); layout.addWidget(self.paste); layout.addWidget(self.excel); layout.addWidget(self.master); layout.addWidget(buttons)

    def attempt_accept(self):
        try:
            self.result_config = self.updated_config()
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Answer key needs correction", str(exc))

    def paste_key(self):
        codes = [str(code) for code in self.config["sheet"]["set_codes"]]
        code, ok = QInputDialog.getItem(self, "Paste key", "Set", codes, 0, False)
        if not ok:
            return
        text, ok = QInputDialog.getMultiLineText(self, "Paste key", "Paste answers as Q/A rows (1, B) or in question order:")
        if not ok:
            return
        try:
            parsed = parse_pasted_key(text, self.config)
            column = 2 + codes.index(code)
            for row, question in enumerate(self.config["questions"]):
                widget = self.table.cellWidget(row, column)
                widget.setCurrentText(parsed.get(str(question["id"]), ""))
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid answer key", str(exc))

    def import_excel(self):
        codes = [str(code) for code in self.config["sheet"]["set_codes"]]
        code, ok = QInputDialog.getItem(self, "Import Excel key", "Set", codes, 0, False)
        if not ok: return
        path, _ = QFileDialog.getOpenFileName(self, "Select answer key workbook", "", "Excel workbook (*.xlsx)")
        if not path: return
        try:
            parsed = import_excel_key(path, self.config)
            column = 2 + codes.index(code)
            for row, question in enumerate(self.config["questions"]):
                self.table.cellWidget(row, column).setCurrentText(parsed.get(str(question["id"]), ""))
        except Exception as exc:
            QMessageBox.warning(self, "Excel key import failed", str(exc))

    def import_master_sheet(self):
        codes = [str(code) for code in self.config["sheet"]["set_codes"]]
        code, ok = QInputDialog.getItem(self, "Read master sheet", "Save key as set", codes, 0, False)
        if not ok: return
        path, _ = QFileDialog.getOpenFileName(self, "Select completed master sheet scan", "", IMAGE_TYPES)
        if not path: return
        try:
            pages = list(scan_pages(path))
            if len(pages) != 1:
                raise ValueError("Choose a single-page image or a one-page PDF master sheet")
            _label, image = pages[0]
            parsed = import_master_sheet(image, self.config, code)
            column = 2 + codes.index(code)
            for row, question in enumerate(self.config["questions"]):
                self.table.cellWidget(row, column).setCurrentText(parsed.get(str(question["id"]), ""))
        except Exception as exc:
            QMessageBox.warning(self, "Master sheet read failed", str(exc))

    def updated_config(self) -> dict[str, Any]:
        updated = json.loads(json.dumps(self.config))
        for row, question in enumerate(updated["questions"]):
            question["topic"] = self.table.item(row, 1).text().strip()
        for column, code in enumerate(updated["sheet"]["set_codes"], 2):
            answers = {}
            for row, question in enumerate(updated["questions"]):
                answer = self.table.cellWidget(row, column).currentText()
                if answer:
                    answers[str(question["id"])] = answer
            updated["sets"][code]["answer_key"] = validate_answer_key(updated, answers)
        validate_config(updated)
        return updated


class MainWindow(QMainWindow):
    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.db_path = Path(db_path)
        self.repo = Repository(db_path)
        self.active_test_id: int | None = None
        self.config: dict[str, Any] | None = None
        self.batch_id: int | None = None
        self.result_records: list[dict[str, Any]] = []
        self.setWindowTitle("OMR Exam App")
        self.resize(1280, 780)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self._build_dashboard_tab()
        self._build_tests_tab()
        self._build_scans_tab()
        self._build_review_tab()
        self._build_results_tab()
        self._build_settings_tab()
        self.refresh_tests()

    def _build_dashboard_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        self.dashboard_summary = QLabel("Your local tests and recent scan batches")
        layout.addWidget(self.dashboard_summary)
        layout.addWidget(QLabel("Tests"))
        self.dashboard_tests_table = QTableWidget(0, 3)
        self.dashboard_tests_table.setHorizontalHeaderLabels(["Test", "Created", "ID"])
        self.dashboard_tests_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.dashboard_tests_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.dashboard_tests_table.setMaximumHeight(190)
        self.dashboard_tests_table.cellDoubleClicked.connect(self.open_dashboard_test)
        layout.addWidget(self.dashboard_tests_table)
        layout.addWidget(QLabel("Recent scan batches (double-click a row to open its review queue)"))
        self.dashboard_batches_table = QTableWidget(0, 6)
        self.dashboard_batches_table.setHorizontalHeaderLabels(["Batch", "Test", "Created", "Sheets", "Pages", "Status"])
        self.dashboard_batches_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.dashboard_batches_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.dashboard_batches_table.horizontalHeader().setStretchLastSection(True)
        self.dashboard_batches_table.cellDoubleClicked.connect(self.open_dashboard_batch)
        layout.addWidget(self.dashboard_batches_table, 1)
        self.dashboard_batches: list[dict[str, Any]] = []
        self.tabs.addTab(page, "Dashboard")

    def refresh_dashboard(self):
        tests = self.repo.tests()
        batches = self.repo.batches()[:10]
        self.dashboard_summary.setText(f"{len(tests)} test(s) saved locally Â· {len(batches)} recent batch(es)")
        self.dashboard_tests_table.setRowCount(len(tests))
        for row, test in enumerate(tests):
            for column, value in enumerate((test["title"], test["created_at"], str(test["id"]))):
                self.dashboard_tests_table.setItem(row, column, QTableWidgetItem(str(value)))
        self.dashboard_batches = batches
        self.dashboard_batches_table.setRowCount(len(batches))
        for row, batch in enumerate(batches):
            values = (f"#{batch['id']}", batch["title"], batch["created_at"],
                      str(batch["sheet_count"]),
                      f"{batch['processed_pages']}/{batch['expected_pages']}", batch["status"])
            for column, value in enumerate(values):
                self.dashboard_batches_table.setItem(row, column, QTableWidgetItem(str(value)))

    def open_dashboard_batch(self, row: int, _column: int = 0):
        if row < 0 or row >= len(self.dashboard_batches):
            return
        batch = self.dashboard_batches[row]
        test_index = self.test_combo.findData(batch["test_id"])
        if test_index >= 0:
            self.test_combo.setCurrentIndex(test_index)
        self.batch_id = int(batch["id"])
        self.log_button.setEnabled(True)
        self.repo.reconcile_batch_roster_flags(self.batch_id)
        self.result_records = self.repo.results_for_batch(self.batch_id)
        self.batch_label.setText(f"Batch #{self.batch_id}: {len(self.result_records)} sheet(s) Â· {batch['status']}")
        self.load_review_table()
        self.tabs.setCurrentIndex(3)

    def open_dashboard_test(self, row: int, _column: int = 0):
        item = self.dashboard_tests_table.item(row, 2)
        if item is None:
            return
        index = self.test_combo.findData(int(item.text()))
        if index >= 0:
            self.test_combo.setCurrentIndex(index)
            self.tabs.setCurrentIndex(1)

    def view_batch_log(self):
        if self.batch_id is None:
            QMessageBox.information(self, "Batch log", "Select or scan a batch first.")
            return
        try:
            status = self.repo.batch_status(self.batch_id)
            events = self.repo.batch_log(self.batch_id)
            lines = [f"Batch #{self.batch_id}: {status['status']} — "
                     f"{status['processed_pages']}/{status['expected_pages']} pages, "
                     f"{status['error_count']} errors"]
            lines.extend(
                f"{event['logged_at']} [{event['level']}] "
                f"{event['source_file'] + ': ' if event['source_file'] else ''}{event['message']}"
                for event in events
            )
            if not events:
                lines.append("No batch events were recorded.")
            dialog = QDialog(self)
            dialog.setWindowTitle(f"Batch #{self.batch_id} log")
            dialog.resize(850, 500)
            layout = QVBoxLayout(dialog)
            output = QPlainTextEdit(); output.setReadOnly(True); output.setPlainText("\n".join(lines))
            layout.addWidget(output)
            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            buttons.rejected.connect(dialog.reject)
            buttons.accepted.connect(dialog.accept)
            layout.addWidget(buttons)
            dialog.exec()
        except Exception as exc:
            QMessageBox.warning(self, "Could not load batch log", str(exc))

    def _build_tests_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        intro = QLabel("Create and manage test configurations. Enter complete answer keys for every test set before scanning.")
        intro.setWordWrap(True); layout.addWidget(intro)
        row = QHBoxLayout()
        self.test_combo = QComboBox(); self.test_combo.currentIndexChanged.connect(self.select_test)
        row.addWidget(QLabel("Active test")); row.addWidget(self.test_combo, 1)
        for label, handler in (("New test", self.new_test), ("Import JSON", self.import_config), ("Answer keys", self.edit_keys), ("Export config", self.export_config), ("Make sheet PDF", self.make_sheets), ("Backup data…", self.backup_data), ("Restore backup…", self.restore_data)):
            button = QPushButton(label); button.clicked.connect(handler); row.addWidget(button)
        self.update_button = QPushButton("Check for updates"); self.update_button.clicked.connect(self.check_updates); row.addWidget(self.update_button)
        layout.addLayout(row)
        self.test_info = QLabel("No test selected")
        self.test_info.setWordWrap(True); layout.addWidget(self.test_info)
        layout.addStretch(1)
        self.tabs.addTab(page, "Tests and sheets")

    def _build_scans_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Upload scan files or select a folder. Each sheet is normalized, read, scored, and saved to a batch. Ambiguous sheets stay in the review queue."))
        row = QHBoxLayout()
        scan = QPushButton("Upload scanned images…"); scan.clicked.connect(self.scan_images); row.addWidget(scan)
        scan_folder = QPushButton("Select scan folder…"); scan_folder.clicked.connect(self.choose_scan_folder); row.addWidget(scan_folder)
        self.batch_label = QLabel("No batch selected"); row.addWidget(self.batch_label, 1)
        self.log_button = QPushButton("View batch log"); self.log_button.setEnabled(False)
        self.log_button.clicked.connect(self.view_batch_log); row.addWidget(self.log_button)
        layout.addLayout(row)
        self.scan_status = QLabel("Supported inputs: PNG, JPEG, TIFF (including multi-page), BMP, and multi-page PDF. Scan at 150 DPI or higher, without cropping the four registration marks.")
        self.scan_status.setWordWrap(True); layout.addWidget(self.scan_status); layout.addStretch(1)
        self.tabs.addTab(page, "Scan batch")

    def _build_review_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        self.review_summary = QLabel("No batch loaded")
        layout.addWidget(self.review_summary)
        self.review_table = QTableWidget(0, 0)
        self.review_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.review_table.horizontalHeader().setStretchLastSection(True)
        self.preview = QLabel("Select a sheet or answer to inspect its scan")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumWidth(300)
        split = QHBoxLayout(); split.addWidget(self.review_table, 4); split.addWidget(self.preview, 1)
        layout.addLayout(split)
        self.review_table.currentCellChanged.connect(self.show_preview)
        row = QHBoxLayout()
        save = QPushButton("Save corrections and re-score"); save.clicked.connect(self.save_reviews); row.addWidget(save)
        layout.addLayout(row)
        self.tabs.addTab(page, "Review queue")

    def _build_results_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Export class marklists and scorecards after reviewing and saving every uncertain sheet."))
        row = QHBoxLayout()
        for label, kind in (("Export CSV…", "csv"), ("Export Excel…", "xlsx"), ("Export PDF report…", "pdf"), ("Student scorecards PDF…", "cards"), ("Item analysis CSV…", "item"), ("Topic analysis CSV…", "topic"), ("Subject analysis CSV…", "subject")):
            button = QPushButton(label); button.clicked.connect(lambda _checked=False, k=kind: self.export_results(k)); row.addWidget(button)
        load = QPushButton("Load saved batch…"); load.clicked.connect(self.load_saved_batch); row.addWidget(load)
        students = QPushButton("Import students CSV/Excel…"); students.clicked.connect(self.import_students); row.addWidget(students)
        layout.addLayout(row)
        self.results_summary = QLabel("Scores and item analysis use the saved configuration and marked answers.")
        layout.addWidget(self.results_summary); layout.addStretch(1)
        self.tabs.addTab(page, "Results and reports")

    def _build_settings_tab(self):
        page = QWidget(); form = QFormLayout(page)
        self.scan_dpi_setting = QSpinBox(); self.scan_dpi_setting.setRange(150, 600)
        self.scan_dpi_setting.setValue(int(self.repo.get_setting("scan_dpi", "216")))
        self.scan_color_setting = QComboBox()
        self.scan_color_setting.addItem("Grayscale", "grayscale")
        self.scan_color_setting.addItem("Color", "color")
        color_index = self.scan_color_setting.findData(self.repo.get_setting("scan_color_mode", "grayscale"))
        self.scan_color_setting.setCurrentIndex(max(0, color_index))
        self.default_correct_setting = QDoubleSpinBox(); self.default_correct_setting.setRange(-1000, 1000); self.default_correct_setting.setDecimals(2)
        self.default_incorrect_setting = QDoubleSpinBox(); self.default_incorrect_setting.setRange(-1000, 1000); self.default_incorrect_setting.setDecimals(2)
        self.default_blank_setting = QDoubleSpinBox(); self.default_blank_setting.setRange(-1000, 1000); self.default_blank_setting.setDecimals(2)
        self.default_correct_setting.setValue(float(self.repo.get_setting("default_correct_points", "4")))
        self.default_incorrect_setting.setValue(float(self.repo.get_setting("default_incorrect_points", "-1")))
        self.default_blank_setting.setValue(float(self.repo.get_setting("default_blank_points", "0")))
        form.addRow("PDF scan rendering DPI", self.scan_dpi_setting)
        form.addRow("Scan color mode", self.scan_color_setting)
        form.addRow("Default points for correct answers", self.default_correct_setting)
        form.addRow("Default points for incorrect answers", self.default_incorrect_setting)
        form.addRow("Default points for blank answers", self.default_blank_setting)
        save = QPushButton("Save settings"); save.clicked.connect(self.save_settings)
        form.addRow(save)
        self.tabs.addTab(page, "Settings")

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

    def refresh_tests(self, select_id: int | None = None):
        tests = self.repo.tests()
        self.test_combo.blockSignals(True); self.test_combo.clear()
        for item in tests:
            self.test_combo.addItem(f"{item['title']}  (#{item['id']})", item["id"])
        if select_id is not None:
            index = self.test_combo.findData(select_id)
            if index >= 0: self.test_combo.setCurrentIndex(index)
        self.test_combo.blockSignals(False)
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
        self.active_test_id = test_id
        self.config = self.repo.config_for_test(test_id) if test_id is not None else None
        if self.config:
            self.test_info.setText(f"{self.config['test']['title']} · {self.config['test']['class']} · {self.config['test']['subject']} · {len(self.config['questions'])} questions · sets {', '.join(self.config['sheet']['set_codes'])}")
        else:
            self.test_info.setText("Create a test or import a JSON configuration to get started.")

    def new_test(self):
        dialog = NewTestDialog(self)
        dialog.correct_points.setValue(float(self.repo.get_setting("default_correct_points", "4")))
        dialog.incorrect_points.setValue(float(self.repo.get_setting("default_incorrect_points", "-1")))
        dialog.blank_points.setValue(float(self.repo.get_setting("default_blank_points", "0")))
        if not dialog.exec(): return
        try:
            config = dialog.result_config
            test_id = self.repo.save_test(config)
            self.refresh_tests(test_id)
            if not self.edit_keys():
                QMessageBox.information(self, "Answer keys", "Test saved. Complete all set answer keys before generating sheets or scanning.")
        except Exception as exc:
            QMessageBox.critical(self, "Could not create test", str(exc))

    def import_config(self):
        file, _ = QFileDialog.getOpenFileName(self, "Import test configuration", "", "JSON config (*.json)")
        if not file: return
        try:
            config = load_config(file)
            test_id = self.repo.save_test(config)
            self.refresh_tests(test_id)
        except Exception as exc:
            QMessageBox.critical(self, "Invalid configuration", str(exc))

    def edit_keys(self) -> bool:
        if not self.config or self.active_test_id is None: return False
        dialog = AnswerKeyDialog(self.config, self)
        if not dialog.exec(): return False
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
        if not self.config: return
        path, _ = QFileDialog.getSaveFileName(self, "Export test configuration", "test-config.json", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(self.config, indent=2, ensure_ascii=False), encoding="utf-8")

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

    def backup_data(self):
        suggested = str(self.db_path.with_name(f"omr-exam-backup-{datetime.now():%Y%m%d-%H%M%S}.omrbackup"))
        path, _ = QFileDialog.getSaveFileName(self, "Save app backup", suggested, "OMR Exam App backup (*.omrbackup)")
        if not path: return
        try:
            self.repo.backup_bundle_to(path)
            QMessageBox.information(self, "Backup complete", f"Saved the database, scans, and aligned previews to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Backup failed", str(exc))

    def restore_data(self):
        path, _ = QFileDialog.getOpenFileName(self, "Restore app backup", "", "OMR Exam App backup (*.omrbackup)")
        if not path: return
        confirm = QMessageBox.question(self, "Restore backup", "This replaces the current tests, students, and results. An automatic safety backup will be made first. Continue?")
        if confirm != QMessageBox.Yes: return
        try:
            safety = self.db_path.with_name(f"omr-exam-before-restore-{datetime.now():%Y%m%d-%H%M%S}.omrbackup")
            self.repo.backup_bundle_to(safety)
            self.repo.restore_bundle_from(path)
            self.batch_id = None; self.result_records = []
            self.log_button.setEnabled(False)
            self.refresh_tests()
            QMessageBox.information(self, "Restore complete", f"Restored database, scans, and previews. Safety backup:\n{safety}")
        except Exception as exc:
            QMessageBox.critical(self, "Restore failed", str(exc))

    def _require_complete_keys(self):
        if not self.config: raise ValueError("Select a test first")
        for code in self.config["sheet"]["set_codes"]:
            validate_answer_key(self.config, self.config["sets"][code]["answer_key"])

    def make_sheets(self):
        try:
            self._require_complete_keys()
            path, _ = QFileDialog.getSaveFileName(self, "Save printable answer sheet PDF", "answer-sheets.pdf", "PDF (*.pdf)")
            if path:
                generate_sheet_pdf(self.config, path)
                QMessageBox.information(self, "Sheet PDF ready", f"Saved print-ready sheet:\n{path}\n\nPrint at 100% scale. Do not crop the corner marks.")
        except Exception as exc:
            QMessageBox.critical(self, "Could not generate sheet", str(exc))

    def scan_images(self):
        try: self._require_complete_keys()
        except Exception as exc:
            QMessageBox.warning(self, "Answer key required", str(exc)); return
        files, _ = QFileDialog.getOpenFileNames(self, "Upload scanned OMR sheets or scan PDFs", "", IMAGE_TYPES)
        if not files: return
        self._process_scan_files(files)

    def choose_scan_folder(self):
        try: self._require_complete_keys()
        except Exception as exc:
            QMessageBox.warning(self, "Answer key required", str(exc)); return
        folder = QFileDialog.getExistingDirectory(self, "Select folder containing scanned sheets")
        if not folder: return
        try:
            files = scan_files_in_folder(folder)
        except Exception as exc:
            QMessageBox.warning(self, "Could not read scan folder", str(exc)); return
        if not files:
            QMessageBox.information(self, "No scans found", "The selected folder contains no supported scan files.")
            return
        self._process_scan_files(files)

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
                    if result.aligned_image is not None:
                        preview = result.aligned_image
                    else:
                        preview = image_data
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
                    results.append({"id": result_id, "image_path": result.image_path, "preview_path": result.preview_path, "roll_number": result.roll_number or "", "set_code": result.set_code or "", "answers": result.answers, "score": scored["total"], "flags": result.flags, "review_required": result.requires_review})
                    processed += 1
                if cancelled: break
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
        self.scan_status.setText(f"Read {len(results)} sheet(s); {sum(bool(row['review_required']) for row in results)} need manual review.{error_note} Open Review queue before publishing results.")
        self.load_review_table()
        self.refresh_dashboard()
        self.tabs.setCurrentIndex(3)
        if load_errors:
            QMessageBox.warning(self, "Some files could not be opened", "\n".join(load_errors))

    def load_review_table(self):
        if not self.config: return
        question_ids = [str(q["id"]) for q in self.config["questions"]]
        headers = ["Roll number", "Set", "Score", "Reviewed", "Flags", "Image", *[f"Q{qid}" for qid in question_ids]]
        self.review_table.clear(); self.review_table.setColumnCount(len(headers)); self.review_table.setHorizontalHeaderLabels(headers)
        self.review_table.setRowCount(len(self.result_records))
        for row, record in enumerate(self.result_records):
            flag_names = "; ".join(flag.get("kind", "error") for flag in record["flags"])
            if flag_names and not record.get("review_required"):
                flag_names = "Reviewed · " + flag_names
            values = [record["roll_number"], record["set_code"], str(record["score"]), "", flag_names, Path(record["image_path"].split("#", 1)[0]).name]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 3:
                    item.setFlags((item.flags() & ~Qt.ItemIsEditable) | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                    item.setCheckState(Qt.Unchecked if record.get("review_required") else Qt.Checked)
                elif col in {2, 4, 5}:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.review_table.setItem(row, col, item)
            for offset, qid in enumerate(question_ids, 6):
                answer = record["answers"].get(qid)
                item = QTableWidgetItem(answer or "")
                if answer is None: item.setBackground(Qt.yellow)
                self.review_table.setItem(row, offset, item)
        flagged = sum(bool(item.get("review_required")) for item in self.result_records)
        self.review_summary.setText(f"{len(self.result_records)} sheet(s); {flagged} sheet(s) flagged. Double-click roll number, set, or answers to correct, then save to audit and re-score.")

    def show_preview(self, row: int, column: int, _previous_row: int, _previous_column: int):
        if row < 0 or row >= len(self.result_records) or not self.config:
            self.preview.setText("Select a sheet or answer to inspect its scan")
            return
        record = self.result_records[row]
        preview_path = Path(record.get("preview_path", "")) if record.get("preview_path") else Path()
        if str(preview_path) not in {"", "."} and not preview_path.is_absolute():
            preview_path = self.db_path.parent / preview_path
        if not preview_path or not Path(preview_path).is_file():
            self.preview.setText("No aligned preview is available for this saved sheet")
            return
        image = cv2.imread(preview_path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            self.preview.setText("Could not display the scan preview")
            return
        is_unreadable = any(flag.get("kind") == "unreadable" for flag in record.get("flags", []))
        if column >= 6 and not is_unreadable:
            question = str(self.config["questions"][column - 6]["id"])
            layout = create_layout(self.config)
            bubbles = [bubble for bubble in layout.bubbles if bubble.group == "answer" and bubble.index == question]
            if bubbles:
                sx, sy = image.shape[1] / layout.width, image.shape[0] / layout.height
                x1 = max(0, round((min(b.x for b in bubbles) - 5) * sx)); x2 = min(image.shape[1], round((max(b.x for b in bubbles) + 5) * sx))
                y1 = max(0, round((bubbles[0].y - 4) * sy)); y2 = min(image.shape[0], round((bubbles[0].y + 4) * sy))
                image = image[y1:y2, x1:x2]
        height, width = image.shape[:2]
        qimage = QImage(image.data, width, height, int(image.strides[0]), QImage.Format_Grayscale8).copy()
        pixmap = QPixmap.fromImage(qimage).scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview.setPixmap(pixmap)

    def save_reviews(self):
        if not self.config or not self.result_records: return
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
                if record.get("review_required") and not reviewed:
                    raise ValueError(f"Inspect {Path(record['image_path']).name}, correct its marks, and check Reviewed before saving")
                if len(roll) != int(self.config["sheet"]["roll_number_digits"]) or not roll.isdigit():
                    raise ValueError(f"Sheet {Path(record['image_path']).name}: roll number must have {self.config['sheet']['roll_number_digits']} digits")
                if not code: raise ValueError(f"Sheet {Path(record['image_path']).name}: unknown set")
                answers = {qid: option_lookup.get(self.review_table.item(row, i + 6).text().strip().upper()) for i, qid in enumerate(questions)}
                invalid = [(q, self.review_table.item(row, i + 6).text().strip()) for i, qid in enumerate(questions)
                           if self.review_table.item(row, i + 6).text().strip() and answers[qid] is None]
                if invalid: raise ValueError(f"Sheet {Path(record['image_path']).name}: invalid option {invalid[0][1]!r} for Q{invalid[0][0]}")
                pending.append({"id": record["id"], "answers": answers, "roll_number": roll, "set_code": code})
            scored_records = self.repo.update_results(pending, self.config)
            for update, record in zip(pending, self.result_records):
                answers, roll, code = update["answers"], update["roll_number"], update["set_code"]
                scored = scored_records[int(record["id"])]
                record.update({"answers": answers, "roll_number": roll, "set_code": code, "score": scored["total"], "review_required": False})
            if self.batch_id is not None:
                self.repo.reconcile_batch_roster_flags(self.batch_id)
                self.result_records = self.repo.results_for_batch(self.batch_id)
            self.load_review_table()
            QMessageBox.information(self, "Corrections saved", "All changes were written to the correction audit and scores were recalculated.")
        except Exception as exc:
            QMessageBox.critical(self, "Could not save corrections", str(exc))

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
            QMessageBox.warning(self, "Review required", f"{unresolved} sheet(s) still have uncertain marks or identity errors. Correct them in Review queue before exporting official results.")
            return
        filters = {"csv": ("CSV (*.csv)", "marklist.csv"), "xlsx": ("Excel workbook (*.xlsx)", "marklist.xlsx"), "pdf": ("PDF report (*.pdf)", "results.pdf"), "cards": ("PDF scorecards (*.pdf)", "student-scorecards.pdf"), "item": ("CSV (*.csv)", "item-analysis.csv"), "topic": ("CSV (*.csv)", "topic-analysis.csv"), "subject": ("CSV (*.csv)", "subject-analysis.csv")}
        filter_text, name = filters[kind]
        path, _ = QFileDialog.getSaveFileName(self, "Export results", name, filter_text)
        if not path: return
        try:
            function = {"csv": export_csv, "xlsx": export_xlsx, "pdf": export_report_pdf, "cards": export_student_scorecards_pdf,
                        "item": export_item_analysis_csv, "topic": export_topic_analysis_csv,
                        "subject": export_subject_analysis_csv}[kind]
            function(self.config, self.result_records, path)
            QMessageBox.information(self, "Export complete", f"Saved results to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def load_saved_batch(self):
        batches = self.repo.batches()
        if not batches:
            QMessageBox.information(self, "No saved batches", "Scan a batch first.")
            return
        labels = [f"Batch #{item['id']} · {item['title']} · {item['created_at']} · {item['sheet_count']} sheets · {item['status']}" for item in batches]
        label, ok = QInputDialog.getItem(self, "Load saved batch", "Batch", labels, 0, False)
        if not ok: return
        batch = batches[labels.index(label)]
        test_index = self.test_combo.findData(batch["test_id"])
        if test_index >= 0: self.test_combo.setCurrentIndex(test_index)
        self.batch_id = int(batch["id"])
        self.log_button.setEnabled(True)
        self.repo.reconcile_batch_roster_flags(self.batch_id)
        self.result_records = self.repo.results_for_batch(self.batch_id)
        self.batch_label.setText(f"Batch #{self.batch_id}: {len(self.result_records)} sheet(s) · {batch['status']}")
        self.load_review_table()
        self.tabs.setCurrentIndex(3)

    def import_students(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import student roster", "", "Roster files (*.csv *.xlsx);;CSV (*.csv);;Excel workbook (*.xlsx)")
        if not path: return
        try:
            count = self.repo.import_students_excel(path) if Path(path).suffix.lower() == ".xlsx" else self.repo.import_students_csv(path)
            if self.batch_id is not None:
                self.repo.reconcile_batch_roster_flags(self.batch_id)
                self.result_records = self.repo.results_for_batch(self.batch_id)
                self.load_review_table()
            QMessageBox.information(self, "Roster imported", f"Imported or updated {count} student record(s).")
        except Exception as exc:
            QMessageBox.critical(self, "Could not import roster", str(exc))
