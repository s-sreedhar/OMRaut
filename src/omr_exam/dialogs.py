"""Dialogs: new test (template picker), answer keys (fast entry), students, batch picker and log."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QInputDialog,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar, QPushButton, QScrollArea, QSpinBox,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from omr_exam import theme
from omr_exam.icons import icon
from omr_exam.answer_keys import (import_excel_key, import_master_sheet, parse_compact_key,
                                  parse_pasted_key, validate_answer_key, write_sample_key)
from omr_exam.config import validate_config
from omr_exam.ingest import scan_pages
from omr_exam.layout import maximum_columns_for_options, maximum_questions_per_column
from omr_exam.templates import TEMPLATES, TestTemplate, build_from_template
from omr_exam.test_builder import parse_partial_credit, parse_question_list, sheet_capacity_summary
from omr_exam.theme import BAD_BG, GOOD_BG
from omr_exam.widgets import (AppDialog, Banner, ChoiceDelegate, ExcelFillTable, PillDelegate, Segmented, SubjectDelegate,
                              SuggestField, TintDelegate, alert, ask_text, button, card, combo, confirm, divider, grid_table, label, table)

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


def _fit_dialog(dialog: QDialog, width: int, height: int) -> None:
    """Resize a dialog while keeping it inside the available screen geometry."""
    screen = dialog.screen() or (dialog.parentWidget().screen() if dialog.parentWidget() is not None else None) or QGuiApplication.primaryScreen()
    if screen is not None:
        avail = screen.availableGeometry()
        max_w = max(560, avail.width() - 24)
        max_h = max(420, avail.height() - 24)
        dialog.setMaximumSize(max_w, max_h)
        width = min(width, max(560, avail.width() - 40))
        height = min(height, max(420, avail.height() - 40))
    dialog.resize(width, height)


class NewTestDialog(QDialog):
    """Pick a template, name the test, done. Everything else is optional under 'Customize'."""

    def __init__(self, parent=None, classes: list[str] | None = None, subjects: list[str] | None = None,
                 manage_subjects=None, manage_classes=None, edit_class=None, edit_subject=None):
        super().__init__(parent)
        self.setWindowTitle("Create a new test")
        self.setAccessibleName("Create a new test")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        _fit_dialog(self, 820, 780)
        self.template: TestTemplate = TEMPLATES[2]

        self.title = QLineEdit()
        self.title.setPlaceholderText("e.g. Unit Test 1 - Science")
        self.title.setClearButtonEnabled(True)
        self.title.setAccessibleName("Test title")
        self.title.setToolTip("Enter the title of the test (e.g. Unit Test 1 - Science)")
        self.edit_class_handler = edit_class
        self.edit_subject_handler = edit_subject
        self.class_name = SuggestField(classes or [], "e.g. 10A", on_edit=self._edit_class_pill if edit_class else None)
        self.class_name.setAccessibleName("Class")
        self.class_name.setToolTip("Enter or select the class or batch name (e.g. 10A)")
        self.subject_options = sorted({str(value).strip() for value in (subjects or []) if str(value).strip()}, key=str.casefold)
        self.manage_subjects_handler = manage_subjects
        self.manage_classes_handler = manage_classes
        self.subject_search = QLineEdit()
        self.subject_search.setObjectName("search")
        self.subject_search.setClearButtonEnabled(True)
        self.subject_search.addAction(icon("search", theme.MUTED, 14), QLineEdit.LeadingPosition)
        self.subject_search.setPlaceholderText("Filter subjects...")
        self.subject_search.setAccessibleName("Filter subjects")
        self.subject_search.setToolTip("Filter the subject checklist by name")
        self.subject_search.textChanged.connect(self._filter_subjects)
        self.subject_list = QListWidget(); self.subject_list.setObjectName("checkList")
        self.subject_list.setMinimumHeight(96); self.subject_list.setMaximumHeight(150)
        self.subject_list.setAccessibleName("Subjects included in this test")
        if edit_subject is not None:
            self.subject_list.setToolTip("Check to select · Double-click or right-click a subject to edit/update")
            self.subject_list.itemDoubleClicked.connect(self._edit_subject_item)
            self.subject_list.setContextMenuPolicy(Qt.CustomContextMenu)
            self.subject_list.customContextMenuRequested.connect(self._subject_context_menu)
        else:
            self.subject_list.setToolTip("Check one or more subjects included in this test")
        self._populate_subjects()
        self.date = QDateEdit(QDate.currentDate()); self.date.setCalendarPopup(True); self.date.setDisplayFormat("d MMM yyyy")
        self.date.setAccessibleName("Test date")
        self.date.setToolTip("Select the date of the exam")

        self.question_count = QSpinBox(); self.question_count.setRange(1, 180)
        self.question_count.setAccessibleName("Number of questions")
        self.question_count.setToolTip("How many questions this exam actually uses (answer key, marking and results cover only these)")
        self.sheet_questions = QSpinBox(); self.sheet_questions.setRange(0, 180)
        self.sheet_questions.setSpecialValueText("Same as questions")
        self.sheet_questions.setAccessibleName("Questions printed on the sheet")
        self.sheet_questions.setToolTip("Rows on your pre-printed OMR sheet (e.g. 160). Questions beyond the number used are ignored when reading.")
        self.options = QLineEdit()
        self.options.setPlaceholderText("e.g. A, B, C, D")
        self.options.setClearButtonEnabled(True)
        self.options.setAccessibleName("Answer options")
        self.roll_digits = QSpinBox(); self.roll_digits.setRange(1, 20)
        self.roll_digits.setAccessibleName("Roll number digits")
        self.roll_digits.setToolTip("Number of digits in student roll numbers (1 to 20)")
        self.institute = QLineEdit("QualifyIAS"); self.institute.setPlaceholderText("Institute name printed on the sheet")
        self.institute.setClearButtonEnabled(True)
        self.institute.setAccessibleName("Institute name")
        self.institute.setToolTip("Institute name printed in the answer sheet header")
        self.use_sets = QCheckBox("Students get different papers (sets A, B, ...)")
        self.use_sets.setAccessibleName("Multiple test sets")
        self.use_sets.setToolTip("Leave this off for one paper. The sheet then has no set bubbles to fill in.")
        self.set_codes = QLineEdit()  # serialized config value
        self.set_codes.hide()
        self.set_editors: list[tuple[QLineEdit, QPushButton]] = []
        self.columns = QSpinBox(); self.columns.setRange(1, 4)
        self.columns.setAccessibleName("Question columns")
        self.columns.setToolTip("Number of answer columns on the A4 sheet (1 to 4)")
        self.name_field = QCheckBox("Name"); self.name_field.setChecked(True)
        self.name_field.setAccessibleName("Print student name field")
        self.name_field.setToolTip("Include a handwritten Name line on the printed sheet")
        self.class_field = QCheckBox("Class"); self.class_field.setChecked(True)
        self.class_field.setAccessibleName("Print class field")
        self.class_field.setToolTip("Include a handwritten Class line on the printed sheet")
        self.correct_points = self._points(); self.incorrect_points = self._points(); self.blank_points = self._points()
        self.correct_points.setAccessibleName("Points for correct answer")
        self.correct_points.setToolTip("Marks awarded for each correct answer")
        self.incorrect_points.setAccessibleName("Points for wrong answer")
        self.blank_points.setAccessibleName("Points for blank answer")
        self.blank_points.setToolTip("Marks awarded when a question is left blank")
        self.mark_threshold = QDoubleSpinBox(); self.mark_threshold.setRange(0.05, 0.95); self.mark_threshold.setSingleStep(0.01); self.mark_threshold.setDecimals(2); self.mark_threshold.setValue(0.24)
        self.mark_threshold.setAccessibleName("Filled-mark sensitivity")
        self.mark_threshold.setToolTip("Darkness threshold above which a bubble is read as filled (default 0.24)")
        self.faint_threshold = QDoubleSpinBox(); self.faint_threshold.setRange(0.005, 0.90); self.faint_threshold.setSingleStep(0.01); self.faint_threshold.setDecimals(3); self.faint_threshold.setValue(0.02)
        self.faint_threshold.setAccessibleName("Faint-mark sensitivity")
        self.faint_threshold.setToolTip("Darkness threshold above which a bubble is flagged as a faint/partial mark (default 0.020)")
        self.bonus = QLineEdit(); self.bonus.setPlaceholderText("e.g. 5, 12")
        self.bonus.setClearButtonEnabled(True)
        self.bonus.setAccessibleName("Bonus questions")
        self.bonus.setToolTip("Comma-separated question numbers that award full marks to everyone (e.g. 5, 12)")
        self.dropped = QLineEdit(); self.dropped.setPlaceholderText("e.g. 7")
        self.dropped.setClearButtonEnabled(True)
        self.dropped.setAccessibleName("Dropped questions")
        self.dropped.setToolTip("Comma-separated question numbers excluded from scoring (e.g. 7)")
        self.partial = QLineEdit(); self.partial.setPlaceholderText("e.g. 3:2, 7:1.5")
        self.partial.setClearButtonEnabled(True)
        self.partial.setAccessibleName("Partial points")
        self.partial.setToolTip("Comma-separated question:points pairs (e.g. 3:2, 7:1.5)")
        self.capacity_hint = _label("", "hint")
        self.options.textChanged.connect(self._update_column_capacity)
        self.columns.valueChanged.connect(self._update_question_capacity)
        self.question_count.valueChanged.connect(self._update_question_capacity)
        self.sheet_questions.valueChanged.connect(self._update_question_capacity)
        for field in (self.bonus, self.dropped, self.partial, self.options):
            field.textChanged.connect(lambda _text, f=field: self._clear_invalid(f))

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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
            choice.setToolTip(f"{template.name}: {template.summary()}")
            inner = QVBoxLayout(choice); inner.setContentsMargins(16, 12, 16, 12); inner.setSpacing(3)
            for text, name in ((template.name, "strong"), (template.description, "hint")):
                line = _label(text, name); line.setAttribute(Qt.WA_TransparentForMouseEvents); line.setWordWrap(True)
                line.setMinimumWidth(0)
                inner.addWidget(line)
            choice.clicked.connect(lambda _c=False, t=template: self.apply_template(t))
            self.template_group.addButton(choice)
            self.template_buttons[template.key] = choice
            grid.addWidget(choice, index // 2, index % 2)
        root.addLayout(grid)
        root.addSpacing(4)
        root.addWidget(_label("2 · NAME YOUR TEST", "eyebrow"))
        form = QGridLayout(); form.setHorizontalSpacing(14); form.setVerticalSpacing(4)
        form.addWidget(_label("Test title", "hint"), 0, 0)
        class_heading = QHBoxLayout(); class_heading.setContentsMargins(0, 0, 0, 0)
        class_heading.addWidget(_label("Class", "hint"))
        class_heading.addStretch(1)
        if self.manage_classes_handler is not None:
            class_heading.addWidget(button("Manage classes", self._manage_classes, "ghost", "settings",
                                           "Add, rename, or remove classes"))
        form.addLayout(class_heading, 0, 1)
        form.addWidget(_label("Date", "hint"), 0, 2)
        for column, widget in enumerate((self.title, self.class_name, self.date)):
            form.addWidget(widget, 1, column)
        form.setColumnStretch(0, 5); form.setColumnStretch(1, 3); form.setColumnStretch(2, 2)
        root.addLayout(form)
        subject_heading = QHBoxLayout()
        subject_heading.addWidget(_label("Subjects in this test", "hint"))
        subject_heading.addWidget(_label("Select one or more", "muted"))
        subject_heading.addStretch(1)
        self.select_all_subjects_button = button("Select all", lambda: self._set_all_subjects(True), "link",
                                                 tip="Select all visible subjects")
        self.clear_subjects_button = button("Clear", lambda: self._set_all_subjects(False), "link",
                                            tip="Uncheck all subjects")
        subject_heading.addWidget(self.select_all_subjects_button)
        subject_heading.addWidget(self.clear_subjects_button)
        subject_heading.addWidget(self.subject_search)
        subject_heading.addWidget(button("Manage subjects", self._manage_subjects, "ghost", "settings",
                                         "Add, rename, or remove subjects"))
        root.addLayout(subject_heading)
        root.addWidget(self.subject_list)
        self.form_error = _label("", "hint")
        self.form_error.hide()
        root.addWidget(self.form_error)
        self.title.textChanged.connect(lambda _text: self._clear_invalid(self.title))
        self.class_name.textChanged.connect(lambda _text: self._clear_invalid(self.class_name))
        self.subject_list.itemChanged.connect(self._subjects_changed)
        self.summary = Banner("", "info")
        root.addWidget(self.summary)
        for signal in (self.question_count.valueChanged, self.options.textChanged, self.use_sets.toggled,
                       self.correct_points.valueChanged, self.incorrect_points.valueChanged):
            signal.connect(self._update_summary)
        self.use_sets.toggled.connect(self._sets_toggled)

        self.customize_toggle = button("Customize questions, sets, and marking", None, "link", "chevron-right",
                                       "Show or hide advanced sheet, set, marking, and reader settings")
        self.customize_toggle.setCheckable(True)
        self.customize_toggle.toggled.connect(self._toggle_customize)
        root.addWidget(self.customize_toggle)
        self.customize = QFrame(); self.customize.setObjectName("card")
        columns = QVBoxLayout(self.customize); columns.setContentsMargins(18, 16, 18, 16); columns.setSpacing(18)
        sheet_form = QFormLayout(); sheet_form.setVerticalSpacing(8); sheet_form.setHorizontalSpacing(14)
        sheet_form.addRow(_label("SHEET", "eyebrow"))
        sheet_form.addRow("Questions used in this test", self.question_count)
        sheet_form.addRow("Questions printed on sheet", self.sheet_questions)
        sheet_form.addRow("Answer options", self.options)
        sheet_form.addRow("Question columns", self.columns)
        sheet_form.addRow("Sheet capacity", self.capacity_hint)
        sheet_form.addRow("Institute name", self.institute)
        sheet_form.addRow("Multiple test sets", self.use_sets)
        self.sets_host = QWidget()
        self.sets_layout = QVBoxLayout(self.sets_host); self.sets_layout.setContentsMargins(0, 0, 0, 0); self.sets_layout.setSpacing(6)
        self.add_set_button = button("Add set", self.add_set, "ghost", "plus", "Add another paper set code (up to 8 sets)")
        sets_row = QVBoxLayout(); sets_row.setContentsMargins(0, 0, 0, 0); sets_row.setSpacing(6)
        sets_row.addWidget(self.sets_host); sets_row.addWidget(self.add_set_button, 0, Qt.AlignLeft)
        sheet_form.addRow("Set codes", sets_row)
        sheet_form.addRow("Roll number digits", self.roll_digits)
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
        columns.addLayout(sheet_form)
        columns.addWidget(divider())
        columns.addLayout(mark_form)
        self.customize.hide()
        root.addWidget(self.customize)
        root.addStretch(1)

        foot = _footer(outer)
        foot.addStretch(1)
        self.cancel_button = button("Cancel", self.reject, tip="Cancel and close without creating a test (Esc)")
        foot.addWidget(self.cancel_button)
        self.create_button = button("Create test and enter answer key", self.attempt_accept, "primary", "arrow-right",
                                    "Create the test and open the answer key editor (Enter)")
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

    def _populate_subjects(self, selected: list[str] | None = None):
        selected_keys = {str(value).strip().casefold() for value in (selected or [])}
        values = list(self.subject_options)
        for value in selected or []:
            normalized = str(value).strip()
            if normalized and normalized.casefold() not in {item.casefold() for item in values}:
                values.append(normalized)
        self.subject_list.blockSignals(True)
        self.subject_list.clear()
        for value in values:
            item = QListWidgetItem(value)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if value.casefold() in selected_keys else Qt.Unchecked)
            self.subject_list.addItem(item)
        self.subject_list.blockSignals(False)
        self._filter_subjects()

    def _filter_subjects(self, text: str | None = None):
        if not hasattr(self, "subject_search"):
            return
        raw = self.subject_search.text() if not isinstance(text, str) else text
        needle = raw.strip().casefold()
        for index in range(self.subject_list.count()):
            item = self.subject_list.item(index)
            item.setHidden(bool(needle) and needle not in item.text().casefold())

    def _set_all_subjects(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        self.subject_list.blockSignals(True)
        for index in range(self.subject_list.count()):
            item = self.subject_list.item(index)
            if not item.isHidden() or not checked:
                item.setCheckState(state)
        self.subject_list.blockSignals(False)
        self._subjects_changed(None)

    def selected_subjects(self) -> list[str]:
        return [self.subject_list.item(index).text() for index in range(self.subject_list.count())
                if self.subject_list.item(index).checkState() == Qt.Checked]

    def _subjects_changed(self, _item):
        self._clear_invalid(self.subject_list)
        self._update_summary()

    def _edit_class_pill(self, old: str):
        if self.edit_class_handler is None:
            return
        new_name, accepted = QInputDialog.getText(self, "Edit / Update class", "Class name:", text=old)
        if not accepted or not new_name.strip() or new_name.strip() == old:
            return
        try:
            updated = self.edit_class_handler(old, new_name.strip())
            if updated is not None:
                self.class_name.set_suggestions(updated)
            if self.class_name.text().strip().casefold() == old.casefold():
                self.class_name.setText(new_name.strip())
        except Exception as exc:
            alert(self, "Could not update class", str(exc), "bad")

    def _subject_context_menu(self, pos):
        from PySide6.QtWidgets import QMenu

        item = self.subject_list.itemAt(pos)
        if item is None or self.edit_subject_handler is None:
            return
        menu = QMenu(self.subject_list)
        menu.addAction(f"Edit / Update '{item.text()}'", lambda: self._edit_subject_item(item))
        menu.exec(self.subject_list.mapToGlobal(pos))

    def _edit_subject_item(self, item: QListWidgetItem):
        if self.edit_subject_handler is None or item is None:
            return
        old = item.text()
        new_name, accepted = QInputDialog.getText(self, "Edit / Update subject", "Subject name:", text=old)
        if not accepted or not new_name.strip() or new_name.strip() == old:
            return
        try:
            previous = self.selected_subjects()
            updated = self.edit_subject_handler(old, new_name.strip())
            if updated is not None:
                self.subject_options = sorted({str(value).strip() for value in updated if str(value).strip()}, key=str.casefold)
            selected = [new_name.strip() if value.casefold() == old.casefold() else value for value in previous]
            self._populate_subjects(selected)
            self._update_summary()
        except Exception as exc:
            alert(self, "Could not update subject", str(exc), "bad")

    def _manage_classes(self):
        if self.manage_classes_handler is None:
            return
        updated = self.manage_classes_handler()
        if updated is not None:
            self.class_name.set_suggestions(updated)

    def _manage_subjects(self):
        if self.manage_subjects_handler is None:
            return
        previous = self.selected_subjects()
        updated = self.manage_subjects_handler()
        if updated is not None:
            self.subject_options = sorted({str(value).strip() for value in updated if str(value).strip()}, key=str.casefold)
            allowed = {value.casefold() for value in self.subject_options}
            self._populate_subjects([value for value in previous if value.casefold() in allowed])
            self._update_summary()

    def _clear_invalid(self, field: QWidget):
        target = field.edit if isinstance(field, SuggestField) else field
        theme.set_property(target, "invalid", "false")
        self.form_error.hide()

    def apply_template(self, template: TestTemplate):
        self.template = template
        self.template_buttons[template.key].setChecked(True)
        self.options.setText(", ".join(template.options))
        self.columns.setValue(template.columns)
        self._update_column_capacity()
        self.columns.setValue(template.columns)
        self.sheet_questions.setValue(template.sheet_questions)
        self.question_count.setValue(template.question_count)
        self.use_sets.setChecked(len(template.set_codes) > 1)
        self._set_codes(list(template.set_codes))
        self.roll_digits.setValue(template.roll_number_digits)
        selected = list(template.sections) if template.sections else (self.subject_options[:1] if self.subject_options else [])
        self._populate_subjects(selected)
        self.correct_points.setValue(template.correct_points)
        self.incorrect_points.setValue(template.incorrect_points)
        self.blank_points.setValue(template.blank_points)
        self._update_summary()
        if template.key == "custom":
            self.customize_toggle.setChecked(True)
            self.question_count.setFocus()

    def _update_summary(self, *_args):
        options = [item.strip().upper() for item in self.options.text().split(",") if item.strip()]
        self.add_set_button.setEnabled(self.use_sets.isChecked())
        for editor, remove_button in self.set_editors:
            editor.setEnabled(self.use_sets.isChecked())
            remove_button.setEnabled(self.use_sets.isChecked() and len(self.set_editors) > 1)
        sets = [editor.text().strip().upper() for editor, _button in self.set_editors] if self.use_sets.isChecked() else []
        self.set_codes.setText(", ".join(sets))
        self.summary.setText(
            f"<b>{self.template.name}</b> · {self.question_count.value()} questions"
            f"{(' of ' + str(self.sheet_questions.value()) + ' printed') if self.sheet_questions.value() else ''} · "
            f"{', '.join(options) or 'no options'} · {(', '.join(self.selected_subjects()) or 'no subjects selected')} · "
            f"{('sets ' + ', '.join(sets)) if sets else 'one paper'} · "
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
            editor.setToolTip(f"Paper code for Set {index} (e.g. A, B, C)")
            editor.textChanged.connect(self._update_summary)
            row.addWidget(editor, 1)
            remove_button = button("Remove", lambda _checked=False, field=editor: self.remove_set(field), "ghost", "trash",
                                   f"Remove Set {index}")
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
        capacity = max(1, self.columns.value() * max_rows)
        self.sheet_questions.setMaximum(capacity)
        printed = self.sheet_questions.value()
        self.question_count.setMaximum(max(1, min(capacity, printed) if printed else capacity))
        self.question_count.setToolTip(f"Up to {max_rows} questions per column fit on an A4 sheet.")
        if hasattr(self, "capacity_hint"):
            cap = sheet_capacity_summary(
                question_count=self.sheet_questions.value() or self.question_count.value(),
                options=[x.strip() for x in self.options.text().split(",") if x.strip()],
                columns=self.columns.value(),
            )
            self.capacity_hint.setText(cap["message"])

    @staticmethod
    def _numbers(text: str) -> list[int]:
        return parse_question_list(text)

    def config(self) -> dict[str, Any]:
        partial_credit = parse_partial_credit(self.partial.text())
        selected_subjects = self.selected_subjects()
        fields = [name for name, checked in (("name", self.name_field.isChecked()), ("class", self.class_field.isChecked())) if checked]
        return build_from_template(
            self.template, title=self.title.text(), class_name=self.class_name.text(), subjects=selected_subjects,
            test_date=self.date.date().toString("yyyy-MM-dd"),
            question_count=self.question_count.value(),
            sheet_questions=self.sheet_questions.value() or None,
            options=[x.strip() for x in self.options.text().split(",") if x.strip()],
            roll_number_digits=self.roll_digits.value(),
            set_codes=([editor.text().strip().upper() for editor, _button in self.set_editors] if self.use_sets.isChecked() else ["A"]),
            fields=fields, columns=self.columns.value(), sections=selected_subjects,
            correct_points=self.correct_points.value(), incorrect_points=self.incorrect_points.value(),
            blank_points=self.blank_points.value(), bonus_questions=self._numbers(self.bonus.text()),
            dropped_questions=self._numbers(self.dropped.text()), partial_credit=partial_credit,
            mark_threshold=self.mark_threshold.value(), faint_threshold=self.faint_threshold.value(),
            institute=self.institute.text())

    def attempt_accept(self):
        missing = [(field, name) for field, name in ((self.title, "a test title"), (self.class_name, "the class")) if not field.text().strip()]
        if not self.selected_subjects():
            missing.append((self.subject_list, "at least one subject"))
        if missing:
            for field, _name in missing:
                target = field.edit if isinstance(field, SuggestField) else field
                theme.set_property(target, "invalid", "true")
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


def _default_set_subjects(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    from omr_exam.reports import _subject_for_question

    codes = [str(code) for code in config["sheet"]["set_codes"]]
    return {code: _subject_for_question(config, code) for code in codes}


class SetSubjectsDialog(AppDialog):
    """Assign subjects to question ranges or individual questions for each set."""

    def __init__(self, subjects: list[str], codes: list[str], question_ids: list[int],
                 set_subjects: dict[str, dict[str, str]], parent=None):
        super().__init__(parent, "Define subjects per set",
                         "Assign which subject each question belongs to in each set. Report analysis uses each student's set.",
                         "settings", "info", width=660)
        self.subjects = list(subjects)
        self.codes = list(codes)
        self.question_ids = list(question_ids)
        self.set_subjects = {code: dict(set_subjects.get(code, {})) for code in self.codes}
        self._loading = False

        if len(self.codes) > 1:
            self.set_picker = Segmented([(c, f"Set {c}") for c in self.codes])
            self.set_picker.setAccessibleName("Select set to assign subjects")
            self.set_picker.setToolTip("Switch between paper sets")
            self.set_picker.changed.connect(lambda _c: self._load_set())
            self.body.addWidget(self.set_picker)
        else:
            self.set_picker = None

        range_row = QHBoxLayout()
        range_row.setSpacing(8)
        self.subject_combo = combo()
        self.subject_combo.addItems(self.subjects)
        self.subject_combo.setAccessibleName("Subject to assign")
        self.subject_combo.setToolTip("Select the subject to apply to the chosen question range")
        self.from_q = QSpinBox(); self.from_q.setRange(1, max(self.question_ids)); self.from_q.setValue(1)
        self.from_q.setAccessibleName("From question number")
        self.from_q.setToolTip("Starting question number")
        self.to_q = QSpinBox(); self.to_q.setRange(1, max(self.question_ids)); self.to_q.setValue(max(self.question_ids))
        self.to_q.setAccessibleName("To question number")
        self.to_q.setToolTip("Ending question number")
        range_row.addWidget(_label("Subject:", "hint", wrap=False))
        range_row.addWidget(self.subject_combo, 1)
        range_row.addWidget(_label("From Q:", "hint", wrap=False))
        range_row.addWidget(self.from_q)
        range_row.addWidget(_label("To Q:", "hint", wrap=False))
        range_row.addWidget(self.to_q)
        self.body.addLayout(range_row)

        apply_row = QHBoxLayout()
        apply_row.setSpacing(8)
        self.summary_label = _label("", "hint", wrap=False)
        apply_row.addWidget(self.summary_label, 1)
        if len(self.subjects) > 1:
            self.split_evenly_button = button("Split evenly", self._split_evenly, "ghost",
                                              tip="Divide questions evenly across all subjects in this set")
            apply_row.addWidget(self.split_evenly_button)
        apply_row.addWidget(button("Apply to set", lambda: self._apply_range(all_sets=False), "primary",
                                   tip="Assign the selected subject to the question range in the current set"))
        if len(self.codes) > 1:
            apply_row.addWidget(button("Apply to all sets", lambda: self._apply_range(all_sets=True), "ghost",
                                       tip="Assign the selected subject to the question range across all sets"))
        self.body.addLayout(apply_row)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("search")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.addAction(icon("search", theme.MUTED, 14), QLineEdit.LeadingPosition)
        self.search_input.setPlaceholderText("Filter by question number or subject...")
        self.search_input.setAccessibleName("Filter questions by number or subject")
        self.search_input.setToolTip("Filter rows by question number or assigned subject")
        self.search_input.textChanged.connect(self._filter_rows)
        self.body.addWidget(self.search_input)

        self.table = ExcelFillTable(len(self.question_ids), 2, self, editable_from_col=1)
        self.table.setHorizontalHeaderLabels(["Question", "Subject"])
        self.table.setAccessibleName("Questions and assigned subjects")
        self.table.setToolTip("Click a Subject cell to choose from dropdown, or drag the corner / press Ctrl+D to fill down")
        grid_table(self.table)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setItemDelegateForColumn(1, SubjectDelegate(self.subjects, self.table))
        self.table.setMinimumHeight(200)
        self.table.itemChanged.connect(self._on_item_changed)
        self.body.addWidget(self.table, 1)
        self.body.addWidget(_label("Tip: Click a Subject cell for dropdown, drag the cell corner or press Ctrl+D to fill down.", "hint"))
        self.add_button("Cancel", "", accept=False)
        self.add_button("Save subjects", "primary", accept=True, default=True)
        self._load_set()

    def _active_code(self) -> str:
        return self.set_picker.current() if self.set_picker is not None else self.codes[0]

    def _split_evenly(self):
        if not self.subjects:
            return
        code = self._active_code()
        base, extra = divmod(len(self.question_ids), len(self.subjects))
        idx = 0
        for s_idx, subj in enumerate(self.subjects):
            count = base + (1 if s_idx < extra else 0)
            for _ in range(count):
                if idx < len(self.question_ids):
                    self.set_subjects[code][str(self.question_ids[idx])] = subj
                    idx += 1
        self._load_set()

    def _apply_range(self, *, all_sets: bool):
        start = min(self.from_q.value(), self.to_q.value())
        end = max(self.from_q.value(), self.to_q.value())
        subject = self.subject_combo.currentText()
        targets = self.codes if all_sets else [self._active_code()]
        for code in targets:
            for qid in range(start, end + 1):
                self.set_subjects[code][str(qid)] = subject
        self._load_set()

    def _load_set(self):
        code = self._active_code()
        mapping = self.set_subjects[code]
        self._loading = True
        for row, qid in enumerate(self.question_ids):
            qitem = QTableWidgetItem(f"Q{qid}")
            qitem.setFlags(qitem.flags() & ~Qt.ItemIsEditable)
            qitem.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, qitem)
            current = mapping.get(str(qid), self.subjects[0] if self.subjects else "")
            self.table.setItem(row, 1, QTableWidgetItem(current))
        self._loading = False
        self._filter_rows()
        self._update_summary()

    def _filter_rows(self, *_args):
        needle = self.search_input.text().strip().casefold() if hasattr(self, "search_input") else ""
        for row in range(self.table.rowCount()):
            q_txt = (self.table.item(row, 0).text() if self.table.item(row, 0) else "").casefold()
            s_txt = (self.table.item(row, 1).text() if self.table.item(row, 1) else "").casefold()
            match = not needle or needle in q_txt or needle in s_txt
            self.table.setRowHidden(row, not match)

    def _update_summary(self):
        if not hasattr(self, "summary_label"):
            return
        code = self._active_code()
        mapping = self.set_subjects.get(code, {})
        counts: dict[str, int] = {}
        for qid in self.question_ids:
            subj = mapping.get(str(qid), self.subjects[0] if self.subjects else "")
            if subj:
                counts[subj] = counts.get(subj, 0) + 1
        self.summary_label.setText(" · ".join(f"{subj}: {n}" for subj, n in counts.items()))

    def _on_item_changed(self, item: QTableWidgetItem):
        if not self._loading and item.column() == 1 and item.text().strip():
            qid = str(self.question_ids[item.row()])
            self.set_subjects[self._active_code()][qid] = item.text().strip()
            self._update_summary()


class AnswerKeyDialog(QDialog):
    """Type, paste, import, or scan the answer key for each set; mistakes are highlighted live."""

    def __init__(self, config: dict[str, Any], parent=None, *, available_subjects: list[str] | None = None):
        super().__init__(parent)
        self.config = config
        self.codes = [str(code) for code in config["sheet"]["set_codes"]]
        self.options = [str(o) for o in config["sheet"]["options"]]
        self.questions = config["questions"]
        self.dropped = {str(q) for q in config.get("scoring", {}).get("dropped_questions", [])}
        self._set_subjects = _default_set_subjects(config)
        merged_subjects: list[str] = []
        seen_subjects: set[str] = set()
        for value in [*(config["test"].get("subjects") or []), *(available_subjects or [])]:
            clean = str(value).strip()
            if clean and clean.casefold() not in seen_subjects:
                seen_subjects.add(clean.casefold())
                merged_subjects.append(clean)
        self.available_subjects = merged_subjects
        self._loading = False
        self._changed = False
        self.setWindowTitle(f"Answer key - {config['test']['title']}")
        self.setAccessibleName(f"Answer key - {config['test']['title']}")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        _fit_dialog(self, 1100, 740)

        self.table_search = QLineEdit()
        self.table_search.setObjectName("search")
        self.table_search.setClearButtonEnabled(True)
        self.table_search.addAction(icon("search", theme.MUTED, 14), QLineEdit.LeadingPosition)
        self.table_search.setPlaceholderText("Filter by question #, topic, subject, or answer (Ctrl+F)...")
        self.table_search.setAccessibleName("Filter answer key questions")
        self.table_search.setToolTip("Filter questions by number, topic, subject, or answer (Ctrl+F)")
        self.table_search.textChanged.connect(self._apply_table_filter)

        self.table_filter = Segmented([("all", "All"), ("missing", "Missing"), ("filled", "Filled")])
        self.table_filter.setAccessibleName("Filter questions by completion status")
        self.table_filter.setToolTip("Show all questions, only questions missing an answer, or only filled questions")
        self.table_filter.changed.connect(lambda _k: self._apply_table_filter())

        self.jump_missing_button = button("Next missing", self.jump_to_next_missing, "ghost", "arrow-right",
                                          "Jump to the next question missing a valid answer (Ctrl+G)")

        self.table = ExcelFillTable(len(self.questions), 3 + len(self.codes), self, editable_from_col=1)
        self.single = len(self.codes) == 1
        self.table.setHorizontalHeaderLabels(["Q", "Topic (optional)", "Subject"] + (["Answer"] if self.single else [f"Set {c}" for c in self.codes]))
        grid_table(self.table)
        self.table.setItemDelegate(TintDelegate(self.table))
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setFocusPolicy(Qt.StrongFocus)
        self.table.setAccessibleName("Answer key by question and set")
        self.table.setToolTip("Type answer options directly into cells, or drag the cell handle / press Ctrl+D to fill down")
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        self.table.setColumnWidth(2, 150)
        self.table.setItemDelegateForColumn(2, SubjectDelegate(self.available_subjects, self.table))
        for column in range(3, 3 + len(self.codes)):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self.table.setColumnWidth(column, 84)
            self.table.setItemDelegateForColumn(column, _AnswerDelegate(self.options, self.table))
        self.table.verticalHeader().setVisible(False)
        self._loading = True
        active_code = self.codes[0]
        for row, question in enumerate(self.questions):
            qitem = QTableWidgetItem(str(question["id"])); qitem.setFlags(qitem.flags() & ~Qt.ItemIsEditable)
            qitem.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, qitem)
            self.table.setItem(row, 1, QTableWidgetItem(str(question.get("topic", ""))))
            self.table.setItem(row, 2, QTableWidgetItem(self._set_subjects.get(active_code, {}).get(str(question["id"]), "")))
            for offset, code in enumerate(self.codes, 3):
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
        self.set_picker.setToolTip("Choose which paper set to fill or clear")
        self.set_picker.changed.connect(lambda _c: self._refresh_subject_column())
        self.set_picker_caption = _label("Fill which set?", "hint")
        sv.addWidget(self.set_picker_caption)
        sv.addWidget(self.set_picker)
        if self.single:
            self.set_picker.hide()
            self.set_picker_caption.hide()
        example = "".join(self.options[i % len(self.options)] for i in range(8))
        self.quick = QLineEdit(); self.quick.setPlaceholderText(f"e.g. {example}…  or  1-A 2-B 3-C")
        self.quick.setClearButtonEnabled(True)
        self.quick.setAccessibleName("Answers in question order")
        self.quick.setToolTip("Type answers in question order (e.g. ABCD...) or as pairs (1-A 2-B) and press Enter")
        self.quick.returnPressed.connect(self.apply_quick)
        sv.addWidget(self.quick)
        self.quick_count = _label("", "hint")
        self.quick_count.hide()
        sv.addWidget(self.quick_count)
        self.quick_error = _label("", "hint"); self.quick_error.hide()
        sv.addWidget(self.quick_error)
        self.quick.textChanged.connect(self._on_quick_text_changed)
        sv.addWidget(button("Fill the key" if self.single else "Fill this set", self.apply_quick, "primary", "check",
                            "Parse and fill the typed answers into the selected set"))
        sv.addWidget(_label("Tip: Drag any cell's corner handle or press Ctrl+D to fill down Topic/Subject like Excel.", "hint"))
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
            bar.setAccessibleName(f"Set {code} completion progress")
            count = _label("", "hint"); count.setFixedWidth(64); count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(name); row.addWidget(bar, 1); row.addWidget(count)
            sv.addLayout(row)
            self.set_bars[code] = (bar, count)
        self.status = _label("", "hint")   # plain-text summary kept for screen readers and callers
        self.status.hide()
        sv.addWidget(button("Define subjects per set" if not self.single else "Define question subjects",
                            self.edit_set_subjects, "ghost", "settings",
                            "Assign subjects to question ranges for each set"))
        sv.addWidget(button("Clear the key" if self.single else "Clear this set", self.clear_set, "ghost", "trash",
                            "Remove all entered answers for the selected set"))

        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QFrame.NoFrame)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        side_scroll.setFixedWidth(356)
        side_scroll.setWidget(side)

        self.table.setMinimumHeight(180)
        table_col = QVBoxLayout(); table_col.setSpacing(8)
        filter_bar = QHBoxLayout(); filter_bar.setSpacing(8)
        filter_bar.addWidget(self.table_search, 1)
        filter_bar.addWidget(self.table_filter)
        filter_bar.addWidget(self.jump_missing_button)
        table_col.addLayout(filter_bar)
        table_col.addWidget(self.table, 1)

        body = QHBoxLayout(); body.setSpacing(14)
        body.addLayout(table_col, 1); body.addWidget(side_scroll)
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
        self.cancel_button = button("Cancel", self.reject, tip="Discard unsaved changes and close (Esc)")
        foot.addWidget(self.cancel_button)
        self.save_button = button("Save answer key", self.attempt_accept, "primary", "check",
                                  "Save the answer key and close (Ctrl+S)")
        self.save_button.setAutoDefault(False)
        foot.addWidget(self.save_button)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(lambda: (self.table_search.setFocus(), self.table_search.selectAll()))
        QShortcut(QKeySequence("Ctrl+G"), self).activated.connect(self.jump_to_next_missing)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.attempt_accept)
        self._refresh_status()
        first_answer = next((row for row, question in enumerate(self.questions) if str(question["id"]) not in self.dropped), None)
        if first_answer is not None:
            self.table.setCurrentCell(first_answer, 3)
            QTimer.singleShot(0, self.table.setFocus)

    # --- helpers -------------------------------------------------------
    def _valid_options(self) -> set[str]:
        return {o.upper() for o in self.options}

    def _on_quick_text_changed(self, text: str):
        self.quick_error.hide()
        clean = text.strip()
        if not clean:
            self.quick_count.hide()
            return
        needed = sum(1 for q in self.questions if str(q["id"]) not in self.dropped)
        valid = self._valid_options()
        tokens = [ch.upper() for ch in clean if ch.upper() in valid] if "-" not in clean and " " not in clean else [
            part.split("-")[-1].strip().upper() for part in clean.replace(",", " ").split() if part.strip()
        ]
        self.quick_count.setText(f"{len(tokens)} / {needed} answers typed")
        self.quick_count.show()

    def _apply_table_filter(self, *_args):
        needle = self.table_search.text().strip().casefold() if hasattr(self, "table_search") else ""
        mode = self.table_filter.current() if hasattr(self, "table_filter") else "all"
        valid = self._valid_options()
        for row, question in enumerate(self.questions):
            is_dropped = str(question["id"]) in self.dropped
            row_filled = is_dropped or all(
                (self.table.item(row, col) and self.table.item(row, col).text() in valid)
                for col in range(3, 3 + len(self.codes))
            )
            if mode == "missing" and row_filled:
                self.table.setRowHidden(row, True)
                continue
            if mode == "filled" and not row_filled:
                self.table.setRowHidden(row, True)
                continue
            if needle:
                haystack = " ".join(
                    (self.table.item(row, col).text() if self.table.item(row, col) else "")
                    for col in range(self.table.columnCount())
                ).casefold()
                if f"q{question['id']}" not in haystack:
                    haystack = f"q{question['id']} {haystack}"
                self.table.setRowHidden(row, needle not in haystack)
            else:
                self.table.setRowHidden(row, False)

    def jump_to_next_missing(self):
        valid = self._valid_options()
        start_row = max(0, self.table.currentRow())
        total = self.table.rowCount()
        for offset in range(total):
            row = (start_row + offset) % total
            if str(self.questions[row]["id"]) in self.dropped:
                continue
            for col in range(3, 3 + len(self.codes)):
                item = self.table.item(row, col)
                if item is None or item.text() not in valid:
                    if self.table.isRowHidden(row):
                        self.table_filter.set_current("all")
                        self.table_search.clear()
                    self.table.setCurrentCell(row, col)
                    self.table.setFocus()
                    return

    def _refresh_subject_column(self):
        code = self.codes[self._set_index()]
        mapping = self._set_subjects.get(code, {})
        self._loading = True
        for row, question in enumerate(self.questions):
            item = self.table.item(row, 2)
            if item is not None:
                item.setText(mapping.get(str(question["id"]), ""))
        self._loading = False

    def _item_changed(self, item: QTableWidgetItem):
        if self._loading:
            return
        self._changed = True
        if item.column() == 1:
            return
        if item.column() == 2:
            qid = str(self.questions[item.row()]["id"])
            value = item.text().strip()
            if value:
                self._set_subjects.setdefault(self.codes[self._set_index()], {})[qid] = value
            return
        self._loading = True
        item.setText(item.text().strip().upper())
        self._loading = False
        self._color(item)
        self._refresh_status()
        row, column = item.row(), item.column()
        if item.text() in self._valid_options():
            next_row = next((candidate for candidate in range(row + 1, self.table.rowCount())
                             if str(self.questions[candidate]["id"]) not in self.dropped and not self.table.isRowHidden(candidate)), None)
            if next_row is not None:
                QTimer.singleShot(0, lambda r=next_row, c=column: self.table.setCurrentCell(r, c))

    def _color(self, item: QTableWidgetItem):
        if item.column() < 3 or item.text() in ("", "-"):
            item.setBackground(QBrush())
        elif item.text() in self._valid_options():
            item.setBackground(QBrush(QColor(GOOD_BG)))
        else:
            item.setBackground(QBrush(QColor(BAD_BG)))

    def _set_column(self) -> int:
        return 3 + self._set_index()

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
        for offset, code in enumerate(self.codes, 3):
            filled = sum(1 for row in needed if self.table.item(row, offset).text() in self._valid_options())
            done = filled == len(needed)
            complete &= done
            lines.append(f"Set {code}: {filled}/{len(needed)} {'(done)' if done else ''}")
            bar, count = self.set_bars[code]
            bar.setRange(0, max(1, len(needed))); bar.setValue(filled)
            bar.setToolTip(f"Set {code}: {filled} of {len(needed)} questions answered")
            theme.set_property(bar, "tone", "good" if done else "")
            count.setText(f"{filled}/{len(needed)}")
        self.status.setText("<br>".join(lines))
        self._complete = complete
        if hasattr(self, "jump_missing_button"):
            self.jump_missing_button.setEnabled(not complete)
        self.foot_note.setText("All sets are complete." if complete else
                               "Some answers are still missing. You can save now and finish later.")
        self.save_button.setText("Save answer key" if complete else "Save as draft")
        self.save_button.setAccessibleName(self.save_button.text().strip())
        if hasattr(self, "table_filter") and self.table_filter.current() != "all":
            self._apply_table_filter()

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
                    self._fill_column(3 + self.codes.index(code), answers)
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
            for column, code in enumerate(self.codes, 3):
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

    def edit_set_subjects(self):
        subjects = list(self.available_subjects)
        qids = [int(q["id"]) for q in self.questions]
        dialog = SetSubjectsDialog(subjects, self.codes, qids, self._set_subjects, self)
        if dialog.exec():
            self._set_subjects = dialog.set_subjects
            self._refresh_subject_column()
            self._changed = True

    def updated_config(self, complete: bool = True) -> dict[str, Any]:
        updated = json.loads(json.dumps(self.config))
        for row, question in enumerate(updated["questions"]):
            question["topic"] = self.table.item(row, 1).text().strip()
        used_subjects = list(updated["test"].get("subjects", []))
        seen_subjects = {s.strip().casefold() for s in used_subjects if s.strip()}
        for column, code in enumerate(self.codes, 3):
            answers = {}
            for row, question in enumerate(updated["questions"]):
                answer = self.table.item(row, column).text().strip()
                if answer and answer != "-":
                    answers[str(question["id"])] = answer
            updated["sets"][code]["answer_key"] = validate_answer_key(updated, answers, complete=complete)
            if self._set_subjects.get(code):
                updated["sets"][code]["question_subjects"] = dict(self._set_subjects[code])
                for subj in self._set_subjects[code].values():
                    clean = str(subj).strip()
                    if clean and clean.casefold() not in seen_subjects:
                        seen_subjects.add(clean.casefold())
                        used_subjects.append(clean)
        updated["test"]["subjects"] = used_subjects
        validate_config(updated)
        return updated


class ReferenceDataDialog(AppDialog):
    """Manage global class and subject names shared by student and test forms."""

    def __init__(self, repo, parent=None, *, classes_only: bool = False, subjects_only: bool = False):
        title = "Manage classes" if classes_only else ("Manage subjects" if subjects_only else "Manage classes and subjects")
        super().__init__(parent, title,
                         "Reusable lists appear in student and test forms. Edit / Update renames saved records; Remove only hides a name from future choices.",
                         "settings", "info", width=560 if (classes_only or subjects_only) else 800)
        self.repo = repo
        self.lists: dict[str, QListWidget] = {}
        self.cards: dict[str, QFrame] = {}
        self.searches: dict[str, QLineEdit] = {}
        self.quick_inputs: dict[str, QLineEdit] = {}
        self._count_labels: dict[str, QLabel] = {}
        row = QHBoxLayout()
        row.setSpacing(14)
        for kind, card_title, description in (("classes", "Classes", "Used for student rosters and tests."),
                                              ("subjects", "Subjects", "A test can include several subjects.")):
            frame, content = card((14, 14, 14, 14), 8)
            header = QHBoxLayout()
            header.addWidget(label(card_title, "cardTitle"))
            header.addStretch(1)
            count_lbl = label("", "hint", wrap=False)
            count_lbl.setAccessibleName(f"{card_title} count")
            self._count_labels[kind] = count_lbl
            header.addWidget(count_lbl)
            content.addLayout(header)
            content.addWidget(label(description, "hint"))

            search = QLineEdit()
            search.setObjectName("search")
            search.setClearButtonEnabled(True)
            search.addAction(icon("search", theme.MUTED, 14), QLineEdit.LeadingPosition)
            search.setPlaceholderText(f"Filter {kind}...")
            search.setAccessibleName(f"Filter {kind}")
            search.setToolTip(f"Filter saved {kind} by name")
            search.textChanged.connect(lambda _text, k=kind: self._populate_list(k))
            self.searches[kind] = search
            content.addWidget(search)

            add_row = QHBoxLayout()
            add_row.setSpacing(6)
            quick_input = QLineEdit()
            quick_input.setClearButtonEnabled(True)
            quick_input.setPlaceholderText(f"New {kind[:-1]} name...")
            quick_input.setAccessibleName(f"New {kind[:-1]} name")
            quick_input.setToolTip(f"Type a new {kind[:-1]} name and press Enter or click Add")
            quick_input.returnPressed.connect(lambda k=kind: self._add_from_inline(k))
            self.quick_inputs[kind] = quick_input
            add_btn = button("Add", lambda k=kind: self._add_from_inline(k), "", "plus",
                             f"Add a new {kind[:-1]} name")
            add_btn.setAccessibleName(f"Add {kind[:-1]}")
            add_row.addWidget(quick_input, 1)
            add_row.addWidget(add_btn)
            content.addLayout(add_row)

            listing = QListWidget()
            listing.setObjectName("checkList")
            listing.setMinimumHeight(230)
            listing.setAccessibleName(f"Global {card_title.lower()} list")
            listing.setToolTip(f"Saved {kind}. Double-click or press F2 to rename; press Delete to remove.")
            listing.currentItemChanged.connect(lambda _current, _previous, k=kind: self._sync_buttons(k))
            listing.itemDoubleClicked.connect(lambda _item, k=kind: self._rename(k))
            shortcut = QShortcut(QKeySequence("F2"), listing)
            shortcut.setContext(Qt.WidgetShortcut)
            shortcut.activated.connect(lambda k=kind: self._rename(k))
            del_shortcut = QShortcut(QKeySequence(Qt.Key_Delete), listing)
            del_shortcut.setContext(Qt.WidgetShortcut)
            del_shortcut.activated.connect(lambda k=kind: self._remove(k))
            self.lists[kind] = listing
            self.cards[kind] = frame
            content.addWidget(listing, 1)
            actions = QHBoxLayout()
            rename = button("Edit / Update", lambda k=kind: self._rename(k), "ghost", "edit",
                            "Edit or update the selected name across saved records (double-click or F2)")
            rename.setAccessibleName(f"Edit or update selected {kind[:-1]}")
            remove = button("Remove", lambda k=kind: self._remove(k), "ghost", "trash",
                            f"Hide the selected {kind[:-1]} from future choices (Delete)")
            remove.setAccessibleName(f"Remove selected {kind[:-1]}")
            setattr(self, f"{kind}_rename", rename)
            setattr(self, f"{kind}_remove", remove)
            actions.addWidget(rename); actions.addWidget(remove)
            actions.addStretch(1)
            content.addLayout(actions)
            row.addWidget(frame, 1)
        if subjects_only:
            self.cards["classes"].hide()
        elif classes_only:
            self.cards["subjects"].hide()
        self.body.addLayout(row)
        self.hint = label("Double-click any entry (or press F2) to rename it across tests and students.", "muted")
        self.body.addWidget(self.hint)
        self.add_button("Done", "primary", accept=True, default=False)
        self.refresh()

    def refresh(self, select_kind: str | None = None, select_value: str | None = None):
        for kind in self.lists:
            target = select_value if kind == select_kind else None
            self._populate_list(kind, select_value=target)

    def _populate_list(self, kind: str, select_value: str | None = None):
        listing = self.lists[kind]
        previous = select_value if select_value is not None else self._selected(kind)
        values = self.repo.reference_values(kind)
        needle = self.searches[kind].text().strip().casefold()
        shown = [v for v in values if not needle or needle in v.casefold()]
        listing.blockSignals(True)
        listing.clear()
        listing.addItems(shown)
        if previous:
            for row in range(listing.count()):
                if listing.item(row).text().casefold() == previous.casefold():
                    listing.setCurrentRow(row)
                    break
        if listing.currentRow() < 0 and listing.count() > 0:
            listing.setCurrentRow(0)
        listing.blockSignals(False)
        if len(values):
            self._count_labels[kind].setText(f"{len(shown)} of {len(values)}" if needle else f"{len(values)}")
        else:
            self._count_labels[kind].setText("0")
        self._sync_buttons(kind)

    def _selected(self, kind: str) -> str:
        item = self.lists[kind].currentItem()
        return item.text() if item else ""

    def _sync_buttons(self, kind: str):
        selected = bool(self._selected(kind))
        getattr(self, f"{kind}_rename").setEnabled(selected)
        getattr(self, f"{kind}_remove").setEnabled(selected)

    def _add_from_inline(self, kind: str):
        field = self.quick_inputs[kind]
        value = field.text().strip()
        if not value:
            self._add(kind)
            return
        try:
            self.repo.add_reference_value(kind, value)
            field.clear()
            self.refresh(select_kind=kind, select_value=value)
        except Exception as exc:
            alert(self, "Could not add the name", str(exc), "bad")

    def _add(self, kind: str):
        title = "Add class" if kind == "classes" else "Add subject"
        value, accepted = QInputDialog.getText(self, title, f"{title.removeprefix('Add ')} name:")
        if not accepted:
            return
        try:
            self.repo.add_reference_value(kind, value)
            self.refresh(select_kind=kind, select_value=value.strip())
        except Exception as exc:
            alert(self, "Could not add the name", str(exc), "bad")

    def _rename(self, kind: str):
        old = self._selected(kind)
        if not old:
            return
        value, accepted = QInputDialog.getText(self, "Edit / Update", "New name:", text=old)
        if not accepted or value.strip() == old:
            return
        try:
            self.repo.rename_reference_value(kind, old, value)
            self.refresh(select_kind=kind, select_value=value.strip())
        except Exception as exc:
            alert(self, "Could not rename the entry", str(exc), "bad")

    def _remove(self, kind: str):
        value = self._selected(kind)
        if not value:
            return
        if not confirm(self, f"Remove {value} from choices?",
                       "Existing student and test records keep this name. It will no longer be offered for new entries.",
                       "Remove", danger=True):
            return
        self.repo.delete_reference_value(kind, value)
        self.refresh()


class StudentDialog(AppDialog):
    def __init__(self, parent=None, roll_number: str = "", name: str = "", class_name: str = "", editing: bool = False,
                 classes: list[str] | None = None):
        super().__init__(parent, "Edit student" if editing else "Add student",
                         "The roll number must match what students bubble on the sheet.", "user-plus", "info", width=460)
        self.original_roll_number = roll_number.strip() if editing else None
        self.roll = QLineEdit(roll_number)
        self.roll.setPlaceholderText("e.g. 000123")
        self.roll.setClearButtonEnabled(True)
        self.roll.setAccessibleName("Roll number")
        self.roll.setToolTip("Student roll number as bubbled on the OMR sheet (required)")

        self.name = QLineEdit(name)
        self.name.setPlaceholderText("Full name")
        self.name.setClearButtonEnabled(True)
        self.name.setAccessibleName("Student name")
        self.name.setToolTip("Student full name (required)")

        self.class_name = SuggestField(classes or [], "Class (optional)", class_name)
        self.class_name.setAccessibleName("Student class")
        self.class_name.setToolTip("Optional class or section name")

        form = QFormLayout(); form.setVerticalSpacing(10); form.setHorizontalSpacing(16)
        form.addRow("Roll number", self.roll); form.addRow("Name", self.name); form.addRow("Class", self.class_name)
        self.body.addLayout(form)
        self.error = _label("", "hint"); self.error.hide()
        self.error.setAccessibleName("Validation message")
        self.body.addWidget(self.error)
        self.add_button("Cancel", "", accept=False)
        self.save_button = self.add_button("Save student", "primary", handler=self._attempt, default=True)
        self.roll.textChanged.connect(lambda _t: self._clear_field_error(self.roll))
        self.name.textChanged.connect(lambda _t: self._clear_field_error(self.name))
        QWidget.setTabOrder(self.roll, self.name)
        QWidget.setTabOrder(self.name, self.class_name.edit)
        (self.name if roll_number else self.roll).setFocus()

    def _clear_field_error(self, field: QLineEdit):
        if field.text().strip():
            theme.set_property(field, "invalid", "false")
            if self.roll.text().strip() and self.name.text().strip():
                self.error.hide()

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

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", theme.MUTED, 14), QLineEdit.LeadingPosition)
        self.search.setPlaceholderText("Filter batches by ID, test name, or status...")
        self.search.setAccessibleName("Filter scan batches")
        self.search.setToolTip("Filter batches by ID, test title, date, or status (Ctrl+F)")
        self.search.textChanged.connect(self._filter_rows)
        filter_row.addWidget(self.search, 1)
        self.count_label = _label(f"{len(batches)} batch{'es' if len(batches) != 1 else ''}", "hint", wrap=False)
        self.count_label.setAccessibleName("Batch count summary")
        filter_row.addWidget(self.count_label)
        self.body.addLayout(filter_row)

        self.table = table(["Batch", "Test", "Scanned", "Sheets", "Status"], 1)
        self.table.setAccessibleName("Saved scan batches table")
        self.table.setToolTip("Double-click a batch or press Enter to open it")
        self.table.setItemDelegateForColumn(4, PillDelegate(_batch_tone, self.table))
        self.table.setMinimumHeight(340)
        self.table.setRowCount(len(batches))
        for row, batch in enumerate(batches):
            values = (f"#{batch['id']}", batch["title"], _when(batch["created_at"]), batch["sheet_count"],
                      _batch_state(batch, pending.get(int(batch["id"]), 0)))
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(row, column, item)
            if current_id is not None and int(batch["id"]) == current_id:
                self.table.selectRow(row)
        if batches and self.table.currentRow() < 0:
            self.table.selectRow(0)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._open())
        self.body.addWidget(self.table)
        self.add_button("Cancel", "", accept=False)
        self.open_button = self.add_button("Open batch", "primary", handler=self._open, default=True)
        self.open_button.setEnabled(bool(batches))
        find_shortcut = QShortcut(QKeySequence.Find, self)
        find_shortcut.activated.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        self.table.setFocus()

    def _filter_rows(self, text: str = ""):
        needle = text.strip().casefold()
        visible = 0
        first_visible = -1
        for row in range(self.table.rowCount()):
            row_text = " ".join(
                (self.table.item(row, col).text() if self.table.item(row, col) else "")
                for col in range(self.table.columnCount())
            ).casefold()
            match = not needle or needle in row_text
            self.table.setRowHidden(row, not match)
            if match:
                visible += 1
                if first_visible < 0:
                    first_visible = row
        total = len(self.batches)
        self.count_label.setText(
            f"{visible} of {total} batches" if needle else f"{total} batch{'es' if total != 1 else ''}"
        )
        if self.table.currentRow() >= 0 and self.table.isRowHidden(self.table.currentRow()) and first_visible >= 0:
            self.table.selectRow(first_visible)
        self.open_button.setEnabled(visible > 0)

    def _open(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.batches) and not self.table.isRowHidden(row):
            self.selected = self.batches[row]
            self.accept()


class BatchLogDialog(AppDialog):
    """Everything that happened while a batch was read, as a readable table."""

    def __init__(self, parent, batch_id: int, status: dict[str, Any], events: list[dict[str, Any]]):
        from omr_exam.ui import _when

        summary = (f"{status['status'].capitalize()} · {status['processed_pages']} of {status['expected_pages']} pages read · "
                   f"{status['error_count']} error{'s' if status['error_count'] != 1 else ''}")
        super().__init__(parent, f"Batch #{batch_id} log", summary, "list", "info", width=860)
        self._events = events
        self._lines = [f"Batch #{batch_id}: {summary}"]

        if events:
            filter_row = QHBoxLayout()
            filter_row.setSpacing(8)
            self.search = QLineEdit()
            self.search.setObjectName("search")
            self.search.setClearButtonEnabled(True)
            self.search.addAction(icon("search", theme.MUTED, 14), QLineEdit.LeadingPosition)
            self.search.setPlaceholderText("Filter log entries by file, level, or message...")
            self.search.setAccessibleName("Filter batch log entries")
            self.search.setToolTip("Search batch log entries by file name, level, or message (Ctrl+F)")
            self.search.textChanged.connect(lambda _t: self._filter_log())
            filter_row.addWidget(self.search, 1)

            self.level_filter = Segmented([("all", "All"), ("error", "Errors"), ("warning", "Warnings")])
            self.level_filter.setAccessibleName("Filter log entries by severity")
            self.level_filter.changed.connect(lambda _val: self._filter_log())
            filter_row.addWidget(self.level_filter)

            self.count_label = _label(f"{len(events)} entries", "hint", wrap=False)
            self.count_label.setAccessibleName("Visible log entry count")
            filter_row.addWidget(self.count_label)
            self.body.addLayout(filter_row)

            find_shortcut = QShortcut(QKeySequence.Find, self)
            find_shortcut.activated.connect(lambda: (self.search.setFocus(), self.search.selectAll()))

        log = table(["Time", "Level", "File", "Message"], 3)
        log.setAccessibleName(f"Batch #{batch_id} event log")
        log.setToolTip("Batch processing events and messages")
        log.setItemDelegateForColumn(1, PillDelegate(lambda text: {"Error": "bad", "Warning": "warn"}.get(text, "neutral"), log))
        log.setSelectionBehavior(QAbstractItemView.SelectRows)
        log.setSelectionMode(QAbstractItemView.SingleSelection)
        log.setWordWrap(True)
        log.setMinimumHeight(380)
        log.setRowCount(len(events))
        self.log_table = log
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
        self.copy_button.setToolTip("Copy all log entries to the clipboard")
        self.add_button("Close", "primary", accept=True, default=True)

    def _filter_log(self):
        if not hasattr(self, "search"):
            return
        needle = self.search.text().strip().casefold()
        level_mode = self.level_filter.current()
        visible = 0
        for row in range(self.log_table.rowCount()):
            level_text = (self.log_table.item(row, 1).text() if self.log_table.item(row, 1) else "").casefold()
            row_text = " ".join(
                (self.log_table.item(row, col).text() if self.log_table.item(row, col) else "")
                for col in range(self.log_table.columnCount())
            ).casefold()
            level_ok = (
                level_mode == "all"
                or (level_mode == "error" and "error" in level_text)
                or (level_mode == "warning" and "warn" in level_text)
            )
            text_ok = not needle or needle in row_text
            match = level_ok and text_ok
            self.log_table.setRowHidden(row, not match)
            if match:
                visible += 1
        total = len(self._events)
        self.count_label.setText(
            f"{visible} of {total} entries" if (needle or level_mode != "all") else f"{total} entries"
        )

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
        self._row_frames: dict[str, QFrame] = {}

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", theme.MUTED, 14), QLineEdit.LeadingPosition)
        self.search.setPlaceholderText("Filter export formats...")
        self.search.setAccessibleName("Filter export formats")
        self.search.setToolTip("Filter available report and export formats by name or description")
        self.search.textChanged.connect(self._filter_exports)
        filter_row.addWidget(self.search, 1)
        self.summary_label = _label("", "hint", wrap=False)
        self.summary_label.setAccessibleName("Selected exports summary")
        filter_row.addWidget(self.summary_label)
        self.body.addLayout(filter_row)

        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); scroll.setMinimumHeight(260); scroll.setMaximumHeight(560)
        scroll.setAccessibleName("Export formats list")
        rows_host = QWidget()
        rows = QVBoxLayout(rows_host); rows.setContentsMargins(0, 0, 6, 0); rows.setSpacing(8)
        scroll.setWidget(rows_host)
        self.body.addWidget(scroll, 1)
        for kind, (title, hint, _name) in EXPORTS.items():
            row = QFrame(); row.setObjectName("issue")
            row.setCursor(Qt.PointingHandCursor)
            row.setToolTip(f"{title}: {hint}")
            line = QHBoxLayout(row); line.setContentsMargins(12, 9, 12, 9); line.setSpacing(10)
            check = QCheckBox(); check.setChecked(kind in defaults); check.setAccessibleName(f"Include {title}")
            check.setToolTip(f"Include {title} when exporting multiple files to a folder")
            check.toggled.connect(self._refresh)
            self.checks[kind] = check
            self._row_frames[kind] = row
            line.addWidget(check)
            text = QVBoxLayout(); text.setSpacing(1)
            title_lbl = _label(title, "strong", wrap=False)
            hint_lbl = _label(hint, "hint")
            text.addWidget(title_lbl); text.addWidget(hint_lbl)
            line.addLayout(text, 1)
            save_one_btn = button("Save as…", lambda k=kind: self._one(k), "", "download",
                                  f"Save only {title} right now")
            save_one_btn.setAccessibleName(f"Save {title} as single file")
            line.addWidget(save_one_btn)
            row.mousePressEvent = lambda event, c=check: (
                c.toggle() if event.button() == Qt.LeftButton else None
            )
            rows.addWidget(row)
        rows.addStretch(1)
        pick = QHBoxLayout()
        select_all_btn = button("Select all", lambda: self._set_all(True), "link", tip="Select all visible export formats")
        select_all_btn.setAccessibleName("Select all exports")
        clear_btn = button("Clear", lambda: self._set_all(False), "link", tip="Uncheck all export formats")
        clear_btn.setAccessibleName("Clear selected exports")
        pick.addWidget(select_all_btn)
        pick.addWidget(clear_btn)
        pick.addStretch(1)
        self.body.addLayout(pick)
        self.add_button("Close", "", accept=False)
        self.save_all = self.add_button("Export selected to a folder", "primary", handler=self._many, default=True)
        self._refresh()

    def _filter_exports(self, text: str = ""):
        from omr_exam.reports import EXPORTS

        needle = text.strip().casefold()
        for kind, (title, hint, _name) in EXPORTS.items():
            frame = self._row_frames.get(kind)
            if frame is not None:
                visible = not needle or needle in f"{title} {hint}".casefold()
                frame.setVisible(visible)

    def _set_all(self, state: bool):
        for kind, check in self.checks.items():
            frame = self._row_frames.get(kind)
            if state and frame is not None and frame.isHidden():
                continue
            check.setChecked(state)

    def _refresh(self, *_args):
        count = sum(check.isChecked() for check in self.checks.values())
        total = len(self.checks)
        for kind, check in self.checks.items():
            frame = self._row_frames.get(kind)
            if frame is not None:
                theme.set_property(frame, "active", "true" if check.isChecked() else "false")
        self.summary_label.setText(f"{count} of {total} selected")
        self.save_all.setEnabled(count > 0)
        self.save_all.setText(f"Export {count} selected to a folder" if count else "Tick at least one file")

    def _one(self, kind: str):
        self.action = ("one", kind)
        self.accept()

    def _many(self):
        self.action = ("many", [kind for kind, check in self.checks.items() if check.isChecked()])
        self.accept()
