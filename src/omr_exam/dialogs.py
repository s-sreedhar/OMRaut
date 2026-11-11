"""Dialogs: new test (template picker), answer keys (fast entry), students, batch picker and log."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from omr_exam import theme
from omr_exam.answer_keys import (import_excel_key, import_master_sheet, parse_compact_key,
                                  parse_pasted_key, validate_answer_key, write_sample_key)
from omr_exam.config import validate_config
from omr_exam.ingest import scan_pages
from omr_exam.layout import maximum_columns_for_options, maximum_questions_per_column
from omr_exam.templates import TEMPLATES, TestTemplate, build_from_template
from omr_exam.theme import BAD_BG, GOOD_BG
from omr_exam.widgets import (AppDialog, Banner, ChoiceDelegate, PillDelegate, Segmented, SuggestField, TintDelegate, alert, ask_text,
                              button, confirm, divider, label, table)

IMAGE_TYPES = "Scanned sheets (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.pdf);;All files (*.*)"


def _label(text: str, name: str = "", wrap: bool = True) -> QLabel:
    return label(text, name, wrap)


def _footer(layout: QVBoxLayout) -> QHBoxLayout:
    layout.addWidget(divider())
    row = QHBoxLayout()
    row.setContentsMargins(24, 12, 24, 14)
    row.setSpacing(10)
    layout.addLayout(row)
    return row


class NewTestDialog(QDialog):
    """Pick a template, name the test, done. Everything else is optional under 'Customize'."""

    def __init__(self, parent=None, classes: list[str] | None = None, subjects: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Create a new test")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.resize(820, 780)
        self.template: TestTemplate = TEMPLATES[2]

        self.title = QLineEdit(); self.title.setPlaceholderText("e.g. Unit Test 1 - Science")
        self.class_name = SuggestField(classes or [], "e.g. 10A")
        self.subject = SuggestField(subjects or [], "e.g. Science")
        self.date = QDateEdit(QDate.currentDate()); self.date.setCalendarPopup(False); self.date.setDisplayFormat("d MMM yyyy")

        self.question_count = QSpinBox(); self.question_count.setRange(1, 180)
        self.options = QLineEdit()
        self.roll_digits = QSpinBox(); self.roll_digits.setRange(1, 20)
        self.institute = QLineEdit("QualifyIAS"); self.institute.setPlaceholderText("Institute name printed on the sheet")
        self.use_sets = QCheckBox("Students get different papers (sets A, B, ...)")
        self.use_sets.setToolTip("Leave this off for one paper. The sheet then has no set bubbles to fill in.")
        self.set_codes = QLineEdit()  # serialized config value
        self.set_codes.hide()
        self.set_editors: list[tuple[QLineEdit, QPushButton]] = []
        self.columns = QSpinBox(); self.columns.setRange(1, 4)
        self.sections = QLineEdit(); self.sections.setPlaceholderText("Optional, e.g. Physics, Chemistry, Maths")
        self.name_field = QCheckBox("Name"); self.name_field.setChecked(True)
        self.class_field = QCheckBox("Class"); self.class_field.setChecked(True)
        self.correct_points = self._points(); self.incorrect_points = self._points(); self.blank_points = self._points()
        self.mark_threshold = QDoubleSpinBox(); self.mark_threshold.setRange(0.05, 0.95); self.mark_threshold.setSingleStep(0.01); self.mark_threshold.setDecimals(2); self.mark_threshold.setValue(0.24)
        self.faint_threshold = QDoubleSpinBox(); self.faint_threshold.setRange(0.005, 0.90); self.faint_threshold.setSingleStep(0.01); self.faint_threshold.setDecimals(3); self.faint_threshold.setValue(0.02)
        self.bonus = QLineEdit(); self.bonus.setPlaceholderText("e.g. 5, 12")
        self.dropped = QLineEdit(); self.dropped.setPlaceholderText("e.g. 7")
        self.partial = QLineEdit(); self.partial.setPlaceholderText("e.g. 3:2, 7:1.5")
        self.options.textChanged.connect(self._update_column_capacity)
        self.columns.valueChanged.connect(self._update_question_capacity)

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget(); content.setObjectName("page")
        scroll.setWidget(content); outer.addWidget(scroll, 1)
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 20, 24, 16); root.setSpacing(12)
        root.addWidget(_label("Create a new test", "pageTitle"))
        root.addWidget(_label("Choose the template closest to your exam. You can adjust the details afterwards.", "pageSub"))
        root.addSpacing(4)
        root.addWidget(_label("1 · CHOOSE A TEMPLATE", "eyebrow"))
        grid = QGridLayout(); grid.setSpacing(10)
        self.template_group = QButtonGroup(self); self.template_group.setExclusive(True)
        self.template_buttons: dict[str, QPushButton] = {}
        for index, template in enumerate(TEMPLATES):
            choice = QPushButton()
            choice.setObjectName("templateCard"); choice.setCheckable(True)
            choice.setCursor(Qt.PointingHandCursor)
            choice.setAccessibleName(f"{template.name}. {template.description}")
            inner = QVBoxLayout(choice); inner.setContentsMargins(16, 12, 16, 12); inner.setSpacing(3)
            for text, name in ((template.name, "strong"), (template.description, "hint")):
                line = _label(text, name); line.setAttribute(Qt.WA_TransparentForMouseEvents); line.setWordWrap(False)
                inner.addWidget(line)
            choice.clicked.connect(lambda _c=False, t=template: self.apply_template(t))
            self.template_group.addButton(choice)
            self.template_buttons[template.key] = choice
            grid.addWidget(choice, index // 2, index % 2)
        root.addLayout(grid)
        root.addSpacing(4)
        root.addWidget(_label("2 · NAME YOUR TEST", "eyebrow"))
        form = QGridLayout(); form.setHorizontalSpacing(14); form.setVerticalSpacing(4)
        for column, (caption, widget) in enumerate((("Test title", self.title), ("Class", self.class_name),
                                                    ("Subject", self.subject), ("Date", self.date))):
            form.addWidget(_label(caption, "hint"), 0, column)
            form.addWidget(widget, 1, column)
        form.setColumnStretch(0, 4); form.setColumnStretch(1, 2); form.setColumnStretch(2, 3); form.setColumnStretch(3, 2)
        root.addLayout(form)
        self.form_error = _label("", "hint")
        self.form_error.hide()
        root.addWidget(self.form_error)
        self.title.textChanged.connect(lambda _text: self._clear_invalid(self.title))
        self.class_name.textChanged.connect(lambda _text: self._clear_invalid(self.class_name))
        self.subject.textChanged.connect(lambda _text: self._clear_invalid(self.subject))
        self.summary = Banner("", "info")
        root.addWidget(self.summary)
        for signal in (self.question_count.valueChanged, self.options.textChanged, self.use_sets.toggled,
                       self.correct_points.valueChanged, self.incorrect_points.valueChanged):
            signal.connect(self._update_summary)
        self.use_sets.toggled.connect(self._sets_toggled)

        self.customize_toggle = button("Customize questions, sets, and marking", None, "link", "chevron-right")
        self.customize_toggle.setCheckable(True)
        self.customize_toggle.toggled.connect(self._toggle_customize)
        root.addWidget(self.customize_toggle)
        self.customize = QFrame(); self.customize.setObjectName("card")
        columns = QHBoxLayout(self.customize); columns.setContentsMargins(18, 16, 18, 16); columns.setSpacing(28)
        sheet_form = QFormLayout(); sheet_form.setVerticalSpacing(8); sheet_form.setHorizontalSpacing(14)
        sheet_form.addRow(_label("SHEET", "eyebrow"))
        sheet_form.addRow("Questions", self.question_count)
        sheet_form.addRow("Answer options", self.options)
        sheet_form.addRow("Question columns", self.columns)
        sheet_form.addRow("Institute name", self.institute)
        sheet_form.addRow("Multiple test sets", self.use_sets)
        self.sets_host = QWidget()
        self.sets_layout = QVBoxLayout(self.sets_host); self.sets_layout.setContentsMargins(0, 0, 0, 0); self.sets_layout.setSpacing(6)
        self.add_set_button = button("Add set", self.add_set, "ghost", "plus")
        sets_row = QVBoxLayout(); sets_row.setContentsMargins(0, 0, 0, 0); sets_row.setSpacing(6)
        sets_row.addWidget(self.sets_host); sets_row.addWidget(self.add_set_button, 0, Qt.AlignLeft)
        sheet_form.addRow("Set codes", sets_row)
        sheet_form.addRow("Roll number digits", self.roll_digits)
        sheet_form.addRow("Subject sections", self.sections)
        fields_row = QHBoxLayout(); fields_row.addWidget(self.name_field); fields_row.addWidget(self.class_field); fields_row.addStretch(1)
        sheet_form.addRow("Printed fields", fields_row)
        mark_form = QFormLayout(); mark_form.setVerticalSpacing(8); mark_form.setHorizontalSpacing(14)
        mark_form.addRow(_label("MARKING", "eyebrow"))
        mark_form.addRow("Points for correct", self.correct_points)
        mark_form.addRow("Points for wrong", self.incorrect_points)
        mark_form.addRow("Points for blank", self.blank_points)
        mark_form.addRow("Bonus questions", self.bonus)
        mark_form.addRow("Dropped questions", self.dropped)
        mark_form.addRow("Partial points (Q:points)", self.partial)
        mark_form.addRow(_label("READER", "eyebrow"))
        mark_form.addRow("Filled-mark sensitivity", self.mark_threshold)
        mark_form.addRow("Faint-mark sensitivity", self.faint_threshold)
        self.incorrect_points.setToolTip("Use a negative number for negative marking")
        self.options.setToolTip("Comma separated, for example A, B, C, D")
        columns.addLayout(sheet_form, 1); columns.addLayout(mark_form, 1)
        self.customize.hide()
        root.addWidget(self.customize)
        root.addStretch(1)

        foot = _footer(outer)
        foot.addStretch(1)
        foot.addWidget(button("Cancel", self.reject))
        self.create_button = button("Create test and enter answer key", self.attempt_accept, "primary", "arrow-right")
        self.create_button.setLayoutDirection(Qt.RightToLeft)
        self.create_button.setDefault(True)
        foot.addWidget(self.create_button)
        self.apply_template(self.template)
        self.title.setFocus()

    @staticmethod
    def _points() -> QDoubleSpinBox:
        box = QDoubleSpinBox(); box.setRange(-1000, 1000); box.setDecimals(2)
        return box

    def _toggle_customize(self, on: bool):
        from omr_exam.icons import icon
        self.customize.setVisible(on)
        self.customize_toggle.setIcon(icon("chevron-down" if on else "chevron-right", theme.ACCENT, 16))

    def _clear_invalid(self, field: QWidget):
        theme.set_property(getattr(field, "edit", field), "invalid", "false")
        self.form_error.hide()

    def apply_template(self, template: TestTemplate):
        self.template = template
        self.template_buttons[template.key].setChecked(True)
        self.options.setText(", ".join(template.options))
        self.columns.setValue(template.columns)
        self._update_column_capacity()
        self.columns.setValue(template.columns)
        self.question_count.setValue(template.question_count)
        self.use_sets.setChecked(len(template.set_codes) > 1)
        self._set_codes(list(template.set_codes))
        self.roll_digits.setValue(template.roll_number_digits)
        self.sections.setText(", ".join(template.sections))
        self.correct_points.setValue(template.correct_points)
        self.incorrect_points.setValue(template.incorrect_points)
        self.blank_points.setValue(template.blank_points)
        self._update_summary()

    def _update_summary(self, *_args):
        options = [item.strip().upper() for item in self.options.text().split(",") if item.strip()]
        self.add_set_button.setEnabled(self.use_sets.isChecked())
        for editor, remove_button in self.set_editors:
            editor.setEnabled(self.use_sets.isChecked())
            remove_button.setEnabled(self.use_sets.isChecked() and len(self.set_editors) > 1)
        sets = [editor.text().strip().upper() for editor, _button in self.set_editors] if self.use_sets.isChecked() else []
        self.set_codes.setText(", ".join(sets))
        self.summary.setText(
            f"<b>{self.template.name}</b> · {self.question_count.value()} questions · "
            f"{', '.join(options) or 'no options'} · {('sets ' + ', '.join(sets)) if sets else 'one paper'} · "
            f"+{self.correct_points.value():g} correct / {self.incorrect_points.value():g} wrong")

    def _sets_toggled(self, enabled: bool):
        if enabled and len(self.set_editors) == 1:
            code = next((candidate for candidate in "ABCDEFGH" if candidate != self.set_editors[0][0].text().strip().upper()), "B")
            self._set_codes([self.set_editors[0][0].text(), code])
        self._update_summary()

    def _set_codes(self, codes: list[str]):
        while self.sets_layout.count():
            item = self.sets_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        normalized = [str(code).strip().upper() for code in codes if str(code).strip()] or ["A"]
        self.set_editors.clear()
        for index, code in enumerate(normalized, 1):
            row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(8)
            row.addWidget(_label(f"Set {index}", "hint", wrap=False))
            editor = QLineEdit(code); editor.setMaxLength(3); editor.setPlaceholderText("Code, e.g. A")
            editor.setAccessibleName(f"Set {index} code")
            editor.textChanged.connect(self._update_summary)
            row.addWidget(editor, 1)
            remove_button = button("Remove", lambda _checked=False, field=editor: self.remove_set(field), "ghost", "trash")
            row.addWidget(remove_button)
            host = QWidget(); host.setLayout(row); self.sets_layout.addWidget(host)
            self.set_editors.append((editor, remove_button))
        self._update_summary()

    def add_set(self):
        if len(self.set_editors) >= 8:
            self.form_error.setText(f"<span style='color:{theme.BAD}'>A test can have at most 8 sets.</span>"); self.form_error.show(); return
        if not self.use_sets.isChecked():
            return
        codes = [editor.text().strip().upper() for editor, _button in self.set_editors]
        code = next((candidate for candidate in "ABCDEFGH" if candidate not in codes), f"S{len(codes) + 1}")
        self._set_codes(codes + [code])
        self.set_editors[-1][0].setFocus()

    def remove_set(self, editor: QLineEdit | None = None):
        if len(self.set_editors) <= 1 or not self.use_sets.isChecked():
            return
        editor = editor or self.set_editors[-1][0]
        code = editor.text().strip().upper()
        if not confirm(self, f"Remove set {code}?", "Its answer key will not be included in this new test.", "Remove set", danger=True):
            return
        self._set_codes([field.text() for field, _button in self.set_editors if field is not editor])

    def _update_column_capacity(self, _text: str = ""):
        option_count = len([item for item in self.options.text().split(",") if item.strip()])
        max_columns = maximum_columns_for_options(210, max(2, option_count))
        self.columns.setMaximum(max(1, max_columns))
        self.options.setToolTip("Comma separated, for example A, B, C, D" if max_columns
                                else "These options do not fit on the sheet; reduce the number of options.")
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
            set_codes=([editor.text().strip().upper() for editor, _button in self.set_editors] if self.use_sets.isChecked() else ["A"]),
            fields=fields, columns=self.columns.value(), sections=sections,
            correct_points=self.correct_points.value(), incorrect_points=self.incorrect_points.value(),
            blank_points=self.blank_points.value(), bonus_questions=self._numbers(self.bonus.text()),
            dropped_questions=self._numbers(self.dropped.text()), partial_credit=partial_credit,
            mark_threshold=self.mark_threshold.value(), faint_threshold=self.faint_threshold.value(),
            institute=self.institute.text())

    def attempt_accept(self):
        missing = [(field, name) for field, name in ((self.title, "a test title"), (self.class_name, "the class"),
                                                     (self.subject, "the subject")) if not field.text().strip()]
        if missing:
            for field, _name in missing:
                theme.set_property(getattr(field, "edit", field), "invalid", "true")
            self.form_error.setText(f"<span style='color:{theme.BAD}'>Please enter {', '.join(name for _f, name in missing)}.</span>")
            self.form_error.show()
            missing[0][0].setFocus()
            return
        try:
            self.result_config = self.config()
            self.accept()
        except Exception as exc:
            self.form_error.setText(f"<span style='color:{theme.BAD}'>{exc}</span>")
            self.form_error.show()
            if not self.customize_toggle.isChecked():
                self.customize_toggle.setChecked(True)


def _AnswerDelegate(options: list[str], parent=None) -> ChoiceDelegate:
    """Answer cells accept only the sheet's options, typed straight into the cell (no pop-up list)."""
    return ChoiceDelegate(options, parent)


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
        self._changed = False
        self.setWindowTitle(f"Answer key - {config['test']['title']}")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.resize(1060, 740)

        self.table = QTableWidget(len(self.questions), 2 + len(self.codes))
        self.single = len(self.codes) == 1
        self.table.setHorizontalHeaderLabels(["Q", "Topic (optional)"] + (["Answer"] if self.single else [f"Set {c}" for c in self.codes]))
        self.table.setShowGrid(True)
        self.table.setItemDelegate(TintDelegate(self.table))
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setAccessibleName("Answer key by question and set")
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in range(2, 2 + len(self.codes)):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self.table.setColumnWidth(column, 84)
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

        side = QFrame(); side.setObjectName("card"); side.setFixedWidth(340)
        sv = QVBoxLayout(side); sv.setContentsMargins(16, 16, 16, 16); sv.setSpacing(10)
        sv.addWidget(_label("FASTEST WAY", "eyebrow"))
        sv.addWidget(_label("Type the whole key", "cardTitle"))
        self.set_picker = Segmented([(c, f"Set {c}") for c in self.codes])
        self.set_picker.setAccessibleName("Set to fill")
        self.set_picker_caption = _label("Fill which set?", "hint")
        sv.addWidget(self.set_picker_caption)
        sv.addWidget(self.set_picker)
        if self.single:
            self.set_picker.hide()
            self.set_picker_caption.hide()
        example = "".join(self.options[i % len(self.options)] for i in range(8))
        self.quick = QLineEdit(); self.quick.setPlaceholderText(f"e.g. {example}…  or  1-A 2-B 3-C")
        self.quick.setAccessibleName("Answers in question order")
        self.quick.returnPressed.connect(self.apply_quick)
        sv.addWidget(self.quick)
        self.quick_error = _label("", "hint"); self.quick_error.hide()
        sv.addWidget(self.quick_error)
        self.quick.textChanged.connect(lambda _t: self.quick_error.hide())
        sv.addWidget(button("Fill the key" if self.single else "Fill this set", self.apply_quick, "primary", "check"))
        sv.addWidget(_label("Or click a cell in the table and type the letter. It moves to the next question by itself.", "hint"))
        sv.addWidget(divider())
        sv.addWidget(_label("USE AN EXISTING KEY", "eyebrow"))
        for text, handler, icon_name in (("Paste from a spreadsheet", self.paste_key, "edit"),
                                         ("Import an Excel file", self.import_excel, "upload"),
                                         ("Import an answer key file (JSON)", self.import_json_key, "upload"),
                                         ("Read a filled-in master sheet", self.import_master_sheet, "scan"),
                                         ("Save this key as a file", self.export_json_key, "download"),
                                         ("Download a sample file to fill in", self.save_sample_key, "sheet")):
            sv.addWidget(button(text, handler, "link", icon_name))
        sv.addStretch(1)
        sv.addWidget(divider())
        sv.addWidget(_label("PROGRESS", "eyebrow"))
        self.set_bars: dict[str, tuple[QProgressBar, QLabel]] = {}
        for code in self.codes:
            row = QHBoxLayout(); row.setSpacing(8)
            name = _label("Key" if self.single else f"Set {code}", "strong"); name.setFixedWidth(46)
            bar = QProgressBar(); bar.setTextVisible(False)
            count = _label("", "hint"); count.setFixedWidth(64); count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(name); row.addWidget(bar, 1); row.addWidget(count)
            sv.addLayout(row)
            self.set_bars[code] = (bar, count)
        self.status = _label("", "hint")   # plain-text summary kept for screen readers and callers
        self.status.hide()
        sv.addWidget(button("Clear the key" if self.single else "Clear this set", self.clear_set, "ghost", "trash"))

        body = QHBoxLayout(); body.setSpacing(14)
        body.addWidget(self.table, 1); body.addWidget(side)
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        top = QVBoxLayout(); top.setContentsMargins(24, 20, 24, 16); top.setSpacing(6)
        top.addWidget(_label("Answer key", "pageTitle"))
        top.addWidget(_label("Every question needs a correct answer in every set before sheets can be printed or scanned. "
                             "A cell turns red if the answer is not one of the sheet's options.", "pageSub"))
        top.addSpacing(6)
        top.addLayout(body, 1)
        root.addLayout(top, 1)
        foot = _footer(root)
        self.foot_note = _label("", "hint")
        foot.addWidget(self.foot_note, 1)
        foot.addWidget(button("Cancel", self.reject))
        self.save_button = button("Save answer key", self.attempt_accept, "primary", "check")
        self.save_button.setAutoDefault(False)
        foot.addWidget(self.save_button)
        self._refresh_status()

    # --- helpers -------------------------------------------------------
    def _valid_options(self) -> set[str]:
        return {o.upper() for o in self.options}

    def _item_changed(self, item: QTableWidgetItem):
        if self._loading:
            return
        self._changed = True
        if item.column() < 2:
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
        return 2 + self._set_index()

    def _set_index(self) -> int:
        return self.codes.index(self.set_picker.current()) if self.set_picker.current() in self.codes else 0

    def _fill_column(self, column: int, values: dict[str, str]):
        self._loading = True
        for row, question in enumerate(self.questions):
            if str(question["id"]) in self.dropped:
                continue
            item = self.table.item(row, column)
            item.setText(values.get(str(question["id"]), ""))
            self._color(item)
        self._loading = False
        self._changed = True
        self._refresh_status()

    def _refresh_status(self):
        needed = [row for row, q in enumerate(self.questions) if str(q["id"]) not in self.dropped]
        lines, complete = [], True
        for offset, code in enumerate(self.codes, 2):
            filled = sum(1 for row in needed if self.table.item(row, offset).text() in self._valid_options())
            done = filled == len(needed)
            complete &= done
            lines.append(f"Set {code}: {filled}/{len(needed)} {'(done)' if done else ''}")
            bar, count = self.set_bars[code]
            bar.setRange(0, max(1, len(needed))); bar.setValue(filled)
            theme.set_property(bar, "tone", "good" if done else "")
            count.setText(f"{filled}/{len(needed)}")
        self.status.setText("<br>".join(lines))
        self._complete = complete
        self.foot_note.setText("All sets are complete." if complete else
                               "Some answers are still missing. You can save now and finish later.")
        self.save_button.setText("Save answer key" if complete else "Save as draft")

    # --- actions -------------------------------------------------------
    def apply_quick(self):
        try:
            parsed = parse_compact_key(self.quick.text(), self.config)
            self._fill_column(self._set_column(), parsed)
            self.quick.clear()
            if self._set_index() + 1 < len(self.codes):
                self.set_picker.set_current(self.codes[self._set_index() + 1])
        except ValueError as exc:
            self.quick_error.setText(f"<span style='color:{theme.BAD}'>{exc}</span>")
            self.quick_error.show()

    def paste_key(self):
        text = ask_text(self, "Paste answer key", f"Paste the answers for <b>{'the key' if self.single else 'Set ' + self.codes[self._set_index()]}</b> as rows "
                        f"(question number, answer) or one answer per line in question order.", "1\tB\n2\tD\n3\tA", "Fill this set")
        if text is None:
            return
        try:
            self._fill_column(self._set_column(), parse_pasted_key(text, self.config))
        except ValueError as exc:
            alert(self, "The pasted key could not be used", str(exc), "warn")

    def import_excel(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select answer key workbook", "", "Excel workbook (*.xlsx)")
        if not path:
            return
        try:
            self._fill_column(self._set_column(), import_excel_key(path, self.config))
        except Exception as exc:
            alert(self, "The Excel key could not be imported", str(exc), "warn")

    def import_json_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import answer key file", "", "JSON files (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig") as handle:
                payload = json.load(handle)
            set_payload = payload.get("sets") if isinstance(payload, dict) else None
            if isinstance(set_payload, dict):
                pending: dict[str, dict[str, str]] = {}
                code_lookup = {code.upper(): code for code in self.codes}
                for raw_code, value in set_payload.items():
                    code = code_lookup.get(str(raw_code).upper())
                    if code is None:
                        continue
                    answers = value.get("answer_key", value) if isinstance(value, dict) else value
                    if not isinstance(answers, dict):
                        raise ValueError(f"Set {code} must contain a question-to-answer object")
                    pending[code] = validate_answer_key(self.config, answers, complete=False)
                if not pending:
                    raise ValueError("No matching answer keys were found for this test's set codes")
                topics = payload.get("topics", {})
                if not isinstance(topics, dict):
                    raise ValueError('"topics" must map question numbers to topic names')
                valid_questions = {str(question["id"]) for question in self.questions}
                unknown_topics = set(map(str, topics)) - valid_questions
                if unknown_topics:
                    raise ValueError(f"Unknown topic question number(s): {', '.join(sorted(unknown_topics))}")
                for code, answers in pending.items():
                    self._fill_column(2 + self.codes.index(code), answers)
                self._loading = True
                for row, question in enumerate(self.questions):
                    self.table.item(row, 1).setText(str(topics.get(str(question["id"]), question.get("topic", ""))))
                self._loading = False
            else:
                answers = payload.get("answers", payload) if isinstance(payload, dict) else None
                if not isinstance(answers, dict):
                    raise ValueError('Use {"answers": {"1": "A"}} or {"sets": {"A": {"1": "A"}}}')
                self._fill_column(self._set_column(), validate_answer_key(self.config, answers, complete=False))
            self.foot_note.setText("Key imported. Check it against your master copy before saving.")
        except Exception as exc:
            alert(self, "The answer key file could not be imported", str(exc), "warn")

    def save_sample_key(self):
        path, chosen = QFileDialog.getSaveFileName(
            self, "Save a sample answer key", "answer-key-sample.json",
            "JSON file (*.json);;Excel workbook (*.xlsx);;Text, one line (*.txt)")
        if not path:
            return
        try:
            if not Path(path).suffix:
                path += ".xlsx" if "xlsx" in chosen else ".txt" if "txt" in chosen else ".json"
            write_sample_key(self.config, path)
            self.foot_note.setText(f"Sample saved to {path}. Replace the placeholder answers, then import it here.")
        except Exception as exc:
            alert(self, "The sample could not be saved", str(exc), "warn")

    def export_json_key(self):
        try:
            keys = {}
            for column, code in enumerate(self.codes, 2):
                answers = {str(question["id"]): self.table.item(row, column).text().strip()
                           for row, question in enumerate(self.questions)
                           if self.table.item(row, column).text().strip() not in {"", "-"}}
                keys[code] = validate_answer_key(self.config, answers, complete=False)
            topics = {str(question["id"]): self.table.item(row, 1).text().strip()
                      for row, question in enumerate(self.questions) if self.table.item(row, 1).text().strip()}
            payload = {"format": "omr-answer-keys-v1", "test_title": self.config["test"]["title"],
                       "sets": keys, "topics": topics}
            path, _ = QFileDialog.getSaveFileName(self, "Save answer key file", "answer-keys.json", "JSON files (*.json)")
            if path:
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2, ensure_ascii=False)
                self.foot_note.setText(f"Key saved to {path}")
        except Exception as exc:
            alert(self, "The answer key could not be saved to a file", str(exc), "warn")

    def import_master_sheet(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select the scan of a filled-in master sheet", "", IMAGE_TYPES)
        if not path:
            return
        try:
            pages = list(scan_pages(path))
            if len(pages) != 1:
                raise ValueError("Choose a single-page image or a one-page PDF master sheet")
            code = self.codes[self._set_index()]
            parsed = import_master_sheet(pages[0][1], self.config, code)
            self._fill_column(self._set_column(), parsed)
            self.foot_note.setText("Key read from the scan. Compare it with your master copy before saving.")
        except Exception as exc:
            alert(self, "The master sheet could not be read", str(exc), "warn")

    def clear_set(self):
        if confirm(self, f"Clear {'the key' if self.single else 'Set ' + self.codes[self._set_index()]}?", "Every answer entered for this set will be removed.",
                   "Clear set", danger=True):
            self._fill_column(self._set_column(), {})

    def attempt_accept(self):
        try:
            if self._complete:
                self.result_config = self.updated_config()
            else:
                draft = self.updated_config(complete=False)
                if not confirm(self, "Save an unfinished answer key?",
                               "Your progress is kept, but sheets cannot be printed or scanned until every set is complete.",
                               "Save as draft", "Keep editing"):
                    return
                self.result_config = draft
            self._changed = False
            self.accept()
        except Exception as exc:
            alert(self, "The answer key needs a correction", str(exc), "warn")

    def reject(self):
        if self._changed and not confirm(self, "Discard changes to the answer key?",
                                         "The answers you entered in this window have not been saved.",
                                         "Discard changes", "Keep editing", danger=True):
            return
        super().reject()

    def updated_config(self, complete: bool = True) -> dict[str, Any]:
        updated = json.loads(json.dumps(self.config))
        for row, question in enumerate(updated["questions"]):
            question["topic"] = self.table.item(row, 1).text().strip()
        for column, code in enumerate(self.codes, 2):
            answers = {}
            for row, question in enumerate(updated["questions"]):
                answer = self.table.item(row, column).text().strip()
                if answer and answer != "-":
                    answers[str(question["id"])] = answer
            updated["sets"][code]["answer_key"] = validate_answer_key(updated, answers, complete=complete)
        validate_config(updated)
        return updated


class StudentDialog(AppDialog):
    def __init__(self, parent=None, roll_number: str = "", name: str = "", class_name: str = "", editing: bool = False,
                 classes: list[str] | None = None):
        super().__init__(parent, "Edit student" if editing else "Add student",
                         "The roll number must match what students bubble on the sheet.", "user-plus", "info", width=460)
        self.roll = QLineEdit(roll_number); self.roll.setPlaceholderText("e.g. 000123")
        if editing:
            self.roll.setReadOnly(True)
            self.roll.setEnabled(False)
        self.name = QLineEdit(name); self.name.setPlaceholderText("Full name")
        self.class_name = SuggestField(classes or [], "Class (optional)", class_name)
        form = QFormLayout(); form.setVerticalSpacing(10); form.setHorizontalSpacing(16)
        form.addRow("Roll number", self.roll); form.addRow("Name", self.name); form.addRow("Class", self.class_name)
        self.body.addLayout(form)
        self.error = _label("", "hint"); self.error.hide()
        self.body.addWidget(self.error)
        self.add_button("Cancel", "", accept=False)
        self.add_button("Save student", "primary", handler=self._attempt, default=True)
        (self.name if roll_number else self.roll).setFocus()

    def _attempt(self):
        missing = [field for field in (self.roll, self.name) if not field.text().strip()]
        for field in (self.roll, self.name):
            theme.set_property(field, "invalid", "true" if field in missing else "false")
        if missing:
            self.error.setText(f"<span style='color:{theme.BAD}'>Roll number and name are both required.</span>")
            self.error.show()
            missing[0].setFocus()
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return self.roll.text().strip(), self.name.text().strip(), self.class_name.text().strip()


class BatchPickerDialog(AppDialog):
    """Choose a saved scan batch from a list instead of a plain drop-down."""

    def __init__(self, parent, batches: list[dict[str, Any]], pending: dict[int, int], current_id: int | None = None):
        from omr_exam.ui import _batch_state, _batch_tone, _when

        super().__init__(parent, "Open a scan batch", "Batches are listed newest first.", "layers", "info", width=760)
        self.batches = batches
        self.selected: dict[str, Any] | None = None
        self.table = table(["Batch", "Test", "Scanned", "Sheets", "Status"], 1)
        self.table.setItemDelegateForColumn(4, PillDelegate(_batch_tone, self.table))
        self.table.setMinimumHeight(340)
        self.table.setRowCount(len(batches))
        for row, batch in enumerate(batches):
            values = (f"#{batch['id']}", batch["title"], _when(batch["created_at"]), batch["sheet_count"],
                      _batch_state(batch, pending.get(int(batch["id"]), 0)))
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
            if current_id is not None and int(batch["id"]) == current_id:
                self.table.selectRow(row)
        if batches and self.table.currentRow() < 0:
            self.table.selectRow(0)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._open())
        self.body.addWidget(self.table)
        self.add_button("Cancel", "", accept=False)
        self.add_button("Open batch", "primary", handler=self._open, default=True)
        self.table.setFocus()

    def _open(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.batches):
            self.selected = self.batches[row]
            self.accept()


class BatchLogDialog(AppDialog):
    """Everything that happened while a batch was read, as a readable table."""

    def __init__(self, parent, batch_id: int, status: dict[str, Any], events: list[dict[str, Any]]):
        from omr_exam.ui import _when

        summary = (f"{status['status'].capitalize()} · {status['processed_pages']} of {status['expected_pages']} pages read · "
                   f"{status['error_count']} error{'s' if status['error_count'] != 1 else ''}")
        super().__init__(parent, f"Batch #{batch_id} log", summary, "list", "info", width=860)
        self._lines = [f"Batch #{batch_id}: {summary}"]
        log = table(["Time", "Level", "File", "Message"], 3)
        log.setItemDelegateForColumn(1, PillDelegate(lambda text: {"Error": "bad", "Warning": "warn"}.get(text, "neutral"), log))
        log.setSelectionMode(QAbstractItemView.NoSelection)
        log.setWordWrap(True)
        log.setMinimumHeight(380)
        log.setRowCount(len(events))
        for row, event in enumerate(events):
            name = str(event["source_file"]).split("-", 1)[-1] if event["source_file"] else ""
            for column, value in enumerate((_when(event["logged_at"]), str(event["level"]).capitalize(), name, event["message"])):
                cell = QTableWidgetItem(str(value))
                cell.setToolTip(str(value))
                log.setItem(row, column, cell)
            self._lines.append(f"{event['logged_at']} [{event['level']}] {name + ': ' if name else ''}{event['message']}")
        log.resizeRowsToContents()
        if events:
            self.body.addWidget(log)
        else:
            self.body.addWidget(Banner("No events were recorded for this batch.", "info"))
        self.copy_button = self.add_button("Copy log", "", handler=self._copy)
        self.add_button("Close", "primary", accept=True, default=True)

    def _copy(self):
        QGuiApplication.clipboard().setText("\n".join(self._lines))
        self.copy_button.setText("Copied")


class ExportDialog(AppDialog):
    """Choose what to export: save one file, or tick several and save them together in one folder."""

    def __init__(self, parent, defaults: tuple[str, ...] = ("xlsx", "pdf", "cards")):
        from omr_exam.reports import EXPORTS

        super().__init__(parent, "Export results", "Save one file, or tick several and save them together in a folder.",
                         "download", "info", width=620)
        self.action: tuple[str, Any] | None = None
        self.checks: dict[str, QCheckBox] = {}
        for kind, (title, hint, _name) in EXPORTS.items():
            row = QFrame(); row.setObjectName("issue")
            line = QHBoxLayout(row); line.setContentsMargins(12, 9, 12, 9); line.setSpacing(10)
            check = QCheckBox(); check.setChecked(kind in defaults); check.setAccessibleName(f"Include {title}")
            check.toggled.connect(self._refresh)
            self.checks[kind] = check
            line.addWidget(check)
            text = QVBoxLayout(); text.setSpacing(1)
            text.addWidget(_label(title, "strong", wrap=False)); text.addWidget(_label(hint, "hint"))
            line.addLayout(text, 1)
            line.addWidget(button("Save as…", lambda k=kind: self._one(k), "", "download"))
            self.body.addWidget(row)
        pick = QHBoxLayout()
        pick.addWidget(button("Select all", lambda: self._set_all(True), "link"))
        pick.addWidget(button("Clear", lambda: self._set_all(False), "link"))
        pick.addStretch(1)
        self.body.addLayout(pick)
        self.add_button("Close", "", accept=False)
        self.save_all = self.add_button("Export selected to a folder", "primary", handler=self._many, default=True)
        self._refresh()

    def _set_all(self, state: bool):
        for check in self.checks.values():
            check.setChecked(state)

    def _refresh(self, *_args):
        count = sum(check.isChecked() for check in self.checks.values())
        self.save_all.setEnabled(count > 0)
        self.save_all.setText(f"Export {count} selected to a folder" if count else "Tick at least one file")

    def _one(self, kind: str):
        self.action = ("one", kind)
        self.accept()

    def _many(self):
        self.action = ("many", [kind for kind, check in self.checks.items() if check.isChecked()])
        self.accept()
