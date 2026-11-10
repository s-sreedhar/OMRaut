"""Dialogs: new test (template picker), answer keys (fast entry), student editor."""
from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QStyledItemDelegate,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from omr_exam.answer_keys import (import_excel_key, import_master_sheet, parse_compact_key,
                                  parse_pasted_key, validate_answer_key)
from omr_exam.config import validate_config
from omr_exam.ingest import scan_pages
from omr_exam.layout import maximum_columns_for_options, maximum_questions_per_column
from omr_exam.templates import TEMPLATES, TestTemplate, build_from_template
from omr_exam.theme import BAD_BG, GOOD_BG

IMAGE_TYPES = "Scanned sheets (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.pdf);;All files (*.*)"


def _label(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(True)
    return label


class NewTestDialog(QDialog):
    """Pick a template, name the test, done. Everything else is optional under 'Customize'."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create a new test")
        self.resize(780, 760)
        self.template: TestTemplate = TEMPLATES[2]

        self.title = QLineEdit(); self.title.setPlaceholderText("e.g. Unit Test 1 - Science")
        self.class_name = QLineEdit(); self.class_name.setPlaceholderText("e.g. 10A")
        self.subject = QLineEdit(); self.subject.setPlaceholderText("e.g. Science")
        self.date = QDateEdit(QDate.currentDate()); self.date.setCalendarPopup(True)

        self.question_count = QSpinBox(); self.question_count.setRange(1, 180)
        self.options = QLineEdit()
        self.roll_digits = QSpinBox(); self.roll_digits.setRange(1, 20)
        self.set_codes = QLineEdit()
        self.columns = QSpinBox(); self.columns.setRange(1, 4)
        self.sections = QLineEdit(); self.sections.setPlaceholderText("Optional, e.g. Physics, Chemistry, Maths")
        self.name_field = QCheckBox("Name"); self.name_field.setChecked(True)
        self.class_field = QCheckBox("Class"); self.class_field.setChecked(True)
        self.correct_points = self._points(); self.incorrect_points = self._points(); self.blank_points = self._points()
        self.mark_threshold = QDoubleSpinBox(); self.mark_threshold.setRange(0.05, 0.95); self.mark_threshold.setSingleStep(0.01); self.mark_threshold.setDecimals(2); self.mark_threshold.setValue(0.24)
        self.faint_threshold = QDoubleSpinBox(); self.faint_threshold.setRange(0.005, 0.90); self.faint_threshold.setSingleStep(0.01); self.faint_threshold.setDecimals(3); self.faint_threshold.setValue(0.02)
        self.bonus = QLineEdit(); self.dropped = QLineEdit(); self.partial = QLineEdit()
        self.options.textChanged.connect(self._update_column_capacity)
        self.columns.valueChanged.connect(self._update_question_capacity)

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 16); outer.setSpacing(0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget(); content.setObjectName("page")
        scroll.setWidget(content); outer.addWidget(scroll, 1)
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 20, 24, 8); root.setSpacing(12)
        root.addWidget(_label("Create a new test", "pageTitle"))
        root.addWidget(_label("1 · Choose a template", "sectionTitle"))
        grid = QGridLayout(); grid.setSpacing(10)
        self.template_group = QButtonGroup(self); self.template_group.setExclusive(True)
        self.template_buttons: dict[str, QPushButton] = {}
        for index, template in enumerate(TEMPLATES):
            button = QPushButton(f"{template.name}\n{template.description}")
            button.setObjectName("templateCard"); button.setCheckable(True)
            button.setMinimumHeight(66)
            button.clicked.connect(lambda _c=False, t=template: self.apply_template(t))
            self.template_group.addButton(button)
            self.template_buttons[template.key] = button
            grid.addWidget(button, index // 2, index % 2)
        root.addLayout(grid)
        root.addWidget(_label("2 · Name your test", "sectionTitle"))
        form = QFormLayout(); form.setLabelAlignment(Qt.AlignLeft); form.setHorizontalSpacing(18)
        form.addRow("Test title *", self.title)
        form.addRow("Class *", self.class_name)
        form.addRow("Subject *", self.subject)
        form.addRow("Date", self.date)
        root.addLayout(form)
        self.summary = _label("", "banner")
        root.addWidget(self.summary)

        self.customize = QGroupBox("Customize details (optional)")
        self.customize.setCheckable(True); self.customize.setChecked(False)
        adv = QFormLayout(self.customize)
        adv.addRow("Number of questions", self.question_count)
        adv.addRow("Answer options (comma separated)", self.options)
        adv.addRow("Question columns", self.columns)
        adv.addRow("Test set codes", self.set_codes)
        adv.addRow("Roll number digits", self.roll_digits)
        adv.addRow("Subject sections", self.sections)
        fields_row = QHBoxLayout(); fields_row.addWidget(self.name_field); fields_row.addWidget(self.class_field); fields_row.addStretch(1)
        adv.addRow("Printed identity fields", fields_row)
        adv.addRow("Points for correct", self.correct_points)
        adv.addRow("Points for wrong (negative = penalty)", self.incorrect_points)
        adv.addRow("Points for blank", self.blank_points)
        adv.addRow("Bonus question numbers", self.bonus)
        adv.addRow("Dropped question numbers", self.dropped)
        adv.addRow("Partial points (Q:points)", self.partial)
        adv.addRow("Filled-mark sensitivity", self.mark_threshold)
        adv.addRow("Faint-mark sensitivity", self.faint_threshold)
        self.customize.toggled.connect(self._toggle_customize)
        self._toggle_customize(False)
        root.addWidget(self.customize)
        root.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Create test and set answer key")
        buttons.button(QDialogButtonBox.Save).setObjectName("primary")
        buttons.accepted.connect(self.attempt_accept); buttons.rejected.connect(self.reject)
        buttons_row = QHBoxLayout(); buttons_row.setContentsMargins(24, 10, 24, 0); buttons_row.addWidget(buttons)
        outer.addLayout(buttons_row)
        self.apply_template(self.template)

    @staticmethod
    def _points() -> QDoubleSpinBox:
        box = QDoubleSpinBox(); box.setRange(-1000, 1000); box.setDecimals(2)
        return box

    def _toggle_customize(self, on: bool):
        for child in self.customize.findChildren(QWidget):
            child.setVisible(on)
        self.customize.setMaximumHeight(16777215 if on else 40)

    def apply_template(self, template: TestTemplate):
        self.template = template
        self.template_buttons[template.key].setChecked(True)
        self.options.setText(", ".join(template.options))
        self.columns.setValue(template.columns)
        self._update_column_capacity()
        self.columns.setValue(template.columns)
        self.question_count.setValue(template.question_count)
        self.set_codes.setText(", ".join(template.set_codes))
        self.roll_digits.setValue(template.roll_number_digits)
        self.sections.setText(", ".join(template.sections))
        self.correct_points.setValue(template.correct_points)
        self.incorrect_points.setValue(template.incorrect_points)
        self.blank_points.setValue(template.blank_points)
        self.summary.setText(f"<b>{template.name}</b> - {template.summary()}")

    def _update_column_capacity(self, _text: str = ""):
        option_count = len([item for item in self.options.text().split(",") if item.strip()])
        max_columns = maximum_columns_for_options(210, max(2, option_count))
        self.columns.setMaximum(max(1, max_columns))
        self.options.setToolTip("" if max_columns else "These options do not fit on the sheet; reduce the number of options.")
        self._update_question_capacity()

    def _update_question_capacity(self, _columns: int | None = None):
        max_rows = maximum_questions_per_column(297)
        self.question_count.setMaximum(max(1, self.columns.value() * max_rows))
        self.question_count.setToolTip(f"Up to {max_rows} questions per column fit on an A4 sheet.")

    @staticmethod
    def _numbers(text: str) -> list[int]:
        try:
            return [int(x.strip()) for x in text.split(",") if x.strip()]
        except ValueError:
            raise ValueError("Question numbers must be whole numbers separated by commas") from None

    def config(self) -> dict[str, Any]:
        partial_credit = {}
        for entry in self.partial.text().split(","):
            if entry.strip():
                if ":" not in entry:
                    raise ValueError("Partial points must look like 3:2, 7:1.5")
                question, points = entry.split(":", 1)
                partial_credit[str(int(question.strip()))] = float(points.strip())
        sections = [x.strip() for x in self.sections.text().split(",") if x.strip()] or None
        fields = [name for name, checked in (("name", self.name_field.isChecked()), ("class", self.class_field.isChecked())) if checked]
        return build_from_template(
            self.template, title=self.title.text(), class_name=self.class_name.text(), subject=self.subject.text(),
            test_date=self.date.date().toString("yyyy-MM-dd"),
            question_count=self.question_count.value(),
            options=[x.strip() for x in self.options.text().split(",") if x.strip()],
            roll_number_digits=self.roll_digits.value(),
            set_codes=[x.strip() for x in self.set_codes.text().split(",") if x.strip()],
            fields=fields, columns=self.columns.value(), sections=sections,
            correct_points=self.correct_points.value(), incorrect_points=self.incorrect_points.value(),
            blank_points=self.blank_points.value(), bonus_questions=self._numbers(self.bonus.text()),
            dropped_questions=self._numbers(self.dropped.text()), partial_credit=partial_credit,
            mark_threshold=self.mark_threshold.value(), faint_threshold=self.faint_threshold.value())

    def attempt_accept(self):
        try:
            self.result_config = self.config()
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Please check the test details", str(exc))


class _AnswerDelegate(QStyledItemDelegate):
    """One-letter editor that commits the moment a valid option is typed."""

    def __init__(self, options: list[str], parent=None):
        super().__init__(parent)
        self.options = [str(o).upper() for o in options]

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setMaxLength(max(len(o) for o in self.options))
        editor.setAlignment(Qt.AlignCenter)

        def typed(text: str):
            value = text.strip().upper()
            if value in self.options and not any(o != value and o.startswith(value) for o in self.options):
                self.commitData.emit(editor)
                self.closeEditor.emit(editor, QStyledItemDelegate.NoHint)
        editor.textEdited.connect(typed)
        return editor

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text().strip().upper())


class AnswerKeyDialog(QDialog):
    """Type, paste, import, or scan the answer key for each set; mistakes are highlighted live."""

    def __init__(self, config: dict[str, Any], parent=None):
        super().__init__(parent)
        self.config = config
        self.codes = [str(code) for code in config["sheet"]["set_codes"]]
        self.options = [str(o) for o in config["sheet"]["options"]]
        self.questions = config["questions"]
        self.dropped = {str(q) for q in config.get("scoring", {}).get("dropped_questions", [])}
        self._loading = False
        self.setWindowTitle(f"Answer keys - {config['test']['title']}")
        self.resize(1020, 720)

        self.table = QTableWidget(len(self.questions), 2 + len(self.codes))
        self.table.setHorizontalHeaderLabels(["Q", "Topic (optional)"] + [f"Set {c}" for c in self.codes])
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in range(2, 2 + len(self.codes)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
            self.table.setItemDelegateForColumn(column, _AnswerDelegate(self.options, self.table))
        self.table.verticalHeader().setVisible(False)
        self._loading = True
        for row, question in enumerate(self.questions):
            qitem = QTableWidgetItem(str(question["id"])); qitem.setFlags(qitem.flags() & ~Qt.ItemIsEditable)
            qitem.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, qitem)
            self.table.setItem(row, 1, QTableWidgetItem(str(question.get("topic", ""))))
            for offset, code in enumerate(self.codes, 2):
                item = QTableWidgetItem(str(config["sets"][code]["answer_key"].get(str(question["id"]), "")))
                item.setTextAlignment(Qt.AlignCenter)
                if str(question["id"]) in self.dropped:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable); item.setText("-")
                self.table.setItem(row, offset, item)
                self._color(item)
        self._loading = False
        self.table.itemChanged.connect(self._item_changed)

        side = QFrame(); side.setObjectName("card"); side.setFixedWidth(330)
        sv = QVBoxLayout(side); sv.setContentsMargins(16, 16, 16, 16); sv.setSpacing(10)
        sv.addWidget(_label("Fastest ways to enter a key", "sectionTitle"))
        self.set_picker = QComboBox(); self.set_picker.addItems([f"Set {c}" for c in self.codes])
        sv.addWidget(_label("Apply to:", "hint")); sv.addWidget(self.set_picker)
        example = "".join(self.options[i % len(self.options)] for i in range(8))
        sv.addWidget(_label(f"<b>Type answers in order</b><br>e.g. {example}... or 1-A 2-B 3-C", "hint"))
        self.quick = QLineEdit(); self.quick.setPlaceholderText("Type or paste the whole key here")
        self.quick.returnPressed.connect(self.apply_quick)
        sv.addWidget(self.quick)
        apply = QPushButton("Apply typed key"); apply.setObjectName("primary"); apply.clicked.connect(self.apply_quick)
        sv.addWidget(apply)
        for text, handler in (("Paste from Excel / clipboard...", self.paste_key),
                              ("Import Excel file...", self.import_excel),
                              ("Read from a filled master sheet scan...", self.import_master_sheet),
                              ("Clear this set", self.clear_set)):
            button = QPushButton(text); button.clicked.connect(handler); sv.addWidget(button)
        sv.addWidget(_label("Or click a cell in the table and just type the letter - it jumps to the next question automatically.", "hint"))
        sv.addStretch(1)
        self.status = _label("", "banner")
        sv.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Save answer keys"); buttons.button(QDialogButtonBox.Save).setObjectName("primary")
        buttons.accepted.connect(self.attempt_accept); buttons.rejected.connect(self.reject)

        body = QHBoxLayout(); body.setSpacing(14)
        body.addWidget(self.table, 1); body.addWidget(side)
        root = QVBoxLayout(self); root.setContentsMargins(20, 18, 20, 18); root.setSpacing(12)
        root.addWidget(_label("Answer keys", "pageTitle"))
        root.addWidget(_label("Every question needs a correct answer in every set before sheets can be scanned. Cells turn red if the answer is not one of the sheet's options.", "pageSub"))
        root.addLayout(body, 1); root.addWidget(buttons)
        self._refresh_status()

    # --- helpers -------------------------------------------------------
    def _valid_options(self) -> set[str]:
        return {o.upper() for o in self.options}

    def _item_changed(self, item: QTableWidgetItem):
        if self._loading or item.column() < 2:
            return
        self._loading = True
        item.setText(item.text().strip().upper())
        self._loading = False
        self._color(item)
        self._refresh_status()
        row, column = item.row(), item.column()
        if item.text() in self._valid_options() and row + 1 < self.table.rowCount():
            QTimer.singleShot(0, lambda: self.table.setCurrentCell(row + 1, column))

    def _color(self, item: QTableWidgetItem):
        if item.column() < 2 or item.text() in ("", "-"):
            item.setBackground(QBrush())
        elif item.text() in self._valid_options():
            item.setBackground(QBrush(QColor(GOOD_BG)))
        else:
            item.setBackground(QBrush(QColor(BAD_BG)))

    def _set_column(self) -> int:
        return 2 + self.set_picker.currentIndex()

    def _fill_column(self, column: int, values: dict[str, str]):
        self._loading = True
        for row, question in enumerate(self.questions):
            if str(question["id"]) in self.dropped:
                continue
            item = self.table.item(row, column)
            item.setText(values.get(str(question["id"]), ""))
            self._color(item)
        self._loading = False
        self._refresh_status()

    def _refresh_status(self):
        needed = [row for row, q in enumerate(self.questions) if str(q["id"]) not in self.dropped]
        lines, complete = [], True
        for offset, code in enumerate(self.codes, 2):
            filled = sum(1 for row in needed if self.table.item(row, offset).text() in self._valid_options())
            complete &= filled == len(needed)
            lines.append(f"Set {code}: {filled}/{len(needed)} {'(done)' if filled == len(needed) else ''}")
        self.status.setText("<br>".join(lines))
        self.status.setObjectName("goodBanner" if complete else "banner")
        self.status.style().unpolish(self.status); self.status.style().polish(self.status)

    # --- actions -------------------------------------------------------
    def apply_quick(self):
        try:
            parsed = parse_compact_key(self.quick.text(), self.config)
            self._fill_column(self._set_column(), parsed)
            self.quick.clear()
        except ValueError as exc:
            QMessageBox.warning(self, "Check the typed key", str(exc))

    def paste_key(self):
        text, ok = QInputDialog.getMultiLineText(self, "Paste key", "Paste answers as rows (1  B) or one per line in question order:")
        if not ok:
            return
        try:
            self._fill_column(self._set_column(), parse_pasted_key(text, self.config))
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid answer key", str(exc))

    def import_excel(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select answer key workbook", "", "Excel workbook (*.xlsx)")
        if not path:
            return
        try:
            self._fill_column(self._set_column(), import_excel_key(path, self.config))
        except Exception as exc:
            QMessageBox.warning(self, "Excel key import failed", str(exc))

    def import_master_sheet(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select completed master sheet scan", "", IMAGE_TYPES)
        if not path:
            return
        try:
            pages = list(scan_pages(path))
            if len(pages) != 1:
                raise ValueError("Choose a single-page image or a one-page PDF master sheet")
            code = self.codes[self.set_picker.currentIndex()]
            parsed = import_master_sheet(pages[0][1], self.config, code)
            self._fill_column(self._set_column(), parsed)
            QMessageBox.information(self, "Master sheet read", "Key read from the scan. Please compare it with your master copy before saving.")
        except Exception as exc:
            QMessageBox.warning(self, "Master sheet read failed", str(exc))

    def clear_set(self):
        self._fill_column(self._set_column(), {})

    def attempt_accept(self):
        try:
            self.result_config = self.updated_config()
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Answer key needs correction", str(exc))

    def updated_config(self) -> dict[str, Any]:
        updated = json.loads(json.dumps(self.config))
        for row, question in enumerate(updated["questions"]):
            question["topic"] = self.table.item(row, 1).text().strip()
        for column, code in enumerate(self.codes, 2):
            answers = {}
            for row, question in enumerate(updated["questions"]):
                answer = self.table.item(row, column).text().strip()
                if answer and answer != "-":
                    answers[str(question["id"])] = answer
            updated["sets"][code]["answer_key"] = validate_answer_key(updated, answers)
        validate_config(updated)
        return updated


class StudentDialog(QDialog):
    def __init__(self, parent=None, roll_number: str = "", name: str = "", class_name: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Student")
        self.roll = QLineEdit(roll_number); self.roll.setPlaceholderText("Exactly as bubbled on the sheet, e.g. 000123")
        if roll_number:
            self.roll.setReadOnly(True)
        self.name = QLineEdit(name); self.name.setPlaceholderText("Full name")
        self.class_name = QLineEdit(class_name); self.class_name.setPlaceholderText("e.g. 10A (optional)")
        form = QFormLayout(self)
        form.addRow("Roll number *", self.roll); form.addRow("Name *", self.name); form.addRow("Class", self.class_name)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setObjectName("primary")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setMinimumWidth(440)

    def values(self) -> tuple[str, str, str]:
        return self.roll.text().strip(), self.name.text().strip(), self.class_name.text().strip()
