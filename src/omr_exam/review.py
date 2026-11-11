"""Review workspace: an exception queue, the scanned sheet, and a keyboard-first inspector.

The layout follows the verification screens of professional capture software: flagged
sheets queue on the left, the scan sits in the middle with the reader's decisions drawn on
it, and each uncertain mark is resolved on the right next to a close-up of the bubbles.
A spreadsheet-style grid of the whole batch stays available for bulk edits.

The grid table doubles as the edit buffer for both views, so a change made in one view is
visible in the other and nothing is written to the database until the operator confirms.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QIntValidator, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QProgressBar, QPushButton, QScrollArea, QSplitter, QStackedWidget, QStyledItemDelegate, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from omr_exam import theme
from omr_exam.layout import SheetLayout, create_layout
from omr_exam.widgets import (QUEUE_BADGE, QUEUE_SUB, QUEUE_TITLE, QUEUE_TONE, Banner, EmptyState, Pill,
                              QueueDelegate, Segmented, SheetViewer, alert, button, card, divider, grid_table, kbd,
                              label, table)

COL_ROLL, COL_SET, COL_SCORE, COL_REVIEWED, COL_FLAGS, COL_IMAGE = range(6)
FIRST_ANSWER_COLUMN = 6

KIND_TEXT = {
    "multiple": "More than one mark", "faint": "Faint mark", "review": "Borderline mark", "blank": "Blank",
    "invalid_id": "Roll number unclear", "duplicate_id": "Duplicate roll number", "unknown_id": "Not in roster",
    "invalid_set": "Set unclear", "unreadable": "Sheet unreadable", "preview_error": "Preview missing",
}
ANSWER_ISSUES = {"multiple", "faint", "review"}
IDENTITY_KINDS = {"invalid_id", "duplicate_id", "unknown_id"}


class _ChoiceDelegate(QStyledItemDelegate):
    """Keep corrections inside the test's valid answer or set choices."""

    def __init__(self, values: list[str], *, editable: bool = False, max_length: int = 0, parent=None):
        super().__init__(parent)
        self.values = values
        self.editable = editable
        self.max_length = max_length

    def createEditor(self, parent, _option, _index):
        from PySide6.QtCore import QRegularExpression
        from PySide6.QtGui import QRegularExpressionValidator
        from PySide6.QtWidgets import QLineEdit

        editor = QLineEdit(parent)
        editor.setObjectName("cellEditor")
        editor.setAlignment(Qt.AlignCenter)
        if self.editable:                       # roll numbers: digits only
            editor.setMaxLength(self.max_length or 64)
            if self.max_length:
                editor.setValidator(QIntValidator(0, 10 ** self.max_length - 1, editor))
        else:                                   # answers / sets: only the test's own values
            editor.setMaxLength(max((len(v) for v in self.values), default=1))
            pattern = "|".join(QRegularExpression.escape(v) for v in self.values)
            editor.setValidator(QRegularExpressionValidator(
                QRegularExpression(f"(?:{pattern})?", QRegularExpression.CaseInsensitiveOption), editor))
            editor.textEdited.connect(lambda text, e=editor: self._typed(e, text))
        return editor

    def _typed(self, editor, text: str) -> None:
        editor.setText(text.upper())
        if text.upper() in {v.upper() for v in self.values}:       # complete value: save it, no Enter needed
            self.commitData.emit(editor)
            self.closeEditor.emit(editor, QStyledItemDelegate.NoHint)

    def setEditorData(self, editor, index):
        editor.setText(str(index.data(Qt.EditRole) or ""))
        editor.selectAll()

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text().strip(), Qt.EditRole)

    def updateEditorGeometry(self, editor, option, _index):
        editor.setGeometry(option.rect)


def flag_text(kind: str) -> str:
    return KIND_TEXT.get(kind, kind.replace("_", " ").capitalize())


class IssueCard(QFrame):
    """One uncertain mark: a close-up of the bubbles and the choices, driven by the keyboard."""

    chosen = Signal(str, object)     # question id, option or None for blank
    focused = Signal(str)
    navigate = Signal(str, int)      # question id, +1 next / -1 previous

    def __init__(self, question: str, kind: str, description: str, options: list[str], current: str | None,
                 crop: QPixmap | None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("issue")
        self.setFocusPolicy(Qt.StrongFocus)
        self.question = question
        self.options = {str(option).upper(): str(option) for option in options}
        self.value = current
        self.setAccessibleName(f"Question {question}: {flag_text(kind)}. {description}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label(f"Question {question}", "strong", wrap=False))
        head.addWidget(Pill(flag_text(kind), "bad" if kind == "multiple" else "warn"))
        head.addStretch(1)
        self.done = Pill("Checked", "good")
        self.done.hide()
        head.addWidget(self.done)
        layout.addLayout(head)
        layout.addWidget(label(description, "hint"))
        if crop is not None:
            picture = QLabel()
            picture.setPixmap(crop)
            picture.setObjectName("inset")
            picture.setAlignment(Qt.AlignCenter)
            picture.setFixedHeight(round(crop.height() / crop.devicePixelRatio()) + 10)
            layout.addWidget(picture)
        row = QHBoxLayout()
        row.setSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str | None, QPushButton] = {}
        for option in options:
            choice = QPushButton(str(option))
            choice.setObjectName("choice")
            self._add_choice(choice, str(option), row)
        blank = QPushButton("Blank")
        blank.setObjectName("choiceBlank")
        self._add_choice(blank, None, row)
        row.addStretch(1)
        layout.addLayout(row)
        self._sync()

    def _add_choice(self, widget: QPushButton, value: str | None, row: QHBoxLayout) -> None:
        widget.setCheckable(True)
        widget.setFocusPolicy(Qt.NoFocus)
        widget.setCursor(Qt.PointingHandCursor)
        widget.clicked.connect(lambda _c=False, v=value: self._choose(v))
        self._group.addButton(widget)
        self._buttons[value] = widget
        row.addWidget(widget)

    def _sync(self) -> None:
        target = self._buttons.get(self.value)
        if target is not None:
            target.setChecked(True)

    def _choose(self, value: str | None) -> None:
        self.value = value
        self._sync()
        self.setFocus()
        self.chosen.emit(self.question, value)

    def set_resolved(self, resolved: bool) -> None:
        self.done.setVisible(resolved)
        theme.set_property(self, "resolved", "true" if resolved else "false")

    def focusInEvent(self, event):  # noqa: N802
        super().focusInEvent(event)
        theme.set_property(self, "active", "true")
        self.focused.emit(self.question)

    def focusOutEvent(self, event):  # noqa: N802
        super().focusOutEvent(event)
        theme.set_property(self, "active", "false")

    def mousePressEvent(self, event):  # noqa: N802
        self.setFocus()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            return super().keyPressEvent(event)
        typed = event.text().strip().upper()
        if typed and typed in self.options:
            self._choose(self.options[typed])
            self.navigate.emit(self.question, 1)
        elif event.key() in (Qt.Key_Backspace, Qt.Key_Delete, Qt.Key_Space):
            self._choose(None)
            self.navigate.emit(self.question, 1)
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._choose(self.value)
            self.navigate.emit(self.question, 1)
        elif event.key() == Qt.Key_Down:
            self.navigate.emit(self.question, 1)
        elif event.key() == Qt.Key_Up:
            self.navigate.emit(self.question, -1)
        else:
            super().keyPressEvent(event)


class ReviewPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.setObjectName("page")
        self.window_ = window
        self._layout: SheetLayout | None = None
        self._loading = False
        self._dirty: set[int] = set()                 # rows with unsaved edits
        self._checked: dict[int, set[str]] = {}       # result id -> issue questions the operator has looked at
        self._row = -1
        self._cards: dict[str, IssueCard] = {}
        self._chips: dict[str, QPushButton] = {}
        self._pixmaps: dict[str, QPixmap] = {}
        # Unsaved corrections are written to the database as drafts shortly after each edit,
        # so closing the app, a crash, or a power cut never loses review work.
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(600)
        self._draft_timer.timeout.connect(self.flush_drafts)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 20)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(label("Review", "pageTitle", wrap=False))
        self.batch_pill = Pill("No batch open", "neutral")
        title_row.addWidget(self.batch_pill)
        title_row.addStretch(1)
        titles.addLayout(title_row)
        titles.addWidget(label("Check each flagged sheet against its scan. Nothing unclear is ever guessed.", "pageSub"))
        head.addLayout(titles, 1)
        self.mode = Segmented((("sheet", "Sheet by sheet"), ("grid", "All answers grid")))
        self.mode.changed.connect(self._set_mode)
        head.addWidget(self.mode, 0, Qt.AlignBottom)
        self.open_button = button("Open batch", window.load_saved_batch, "", "layers")
        head.addWidget(self.open_button, 0, Qt.AlignBottom)
        self.save_all_button = button("Save all corrections", self.save_all, "primary", "check")
        self.save_all_button.setToolTip("Validate every sheet in this batch and save all corrections together")
        head.addWidget(self.save_all_button, 0, Qt.AlignBottom)
        root.addLayout(head)

        self.summary = Banner("No batch loaded. Scan sheets or open a saved batch.", "info")
        root.addWidget(self.summary)
        progress_row = QHBoxLayout()
        progress_row.setSpacing(12)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress_label = label("", "hint", wrap=False)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.progress_label)
        self.progress_host = QWidget()
        self.progress_host.setLayout(progress_row)
        progress_row.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.progress_host)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.empty = EmptyState("review", "No batch open",
                                "Scan a set of answer sheets, or open a batch you scanned earlier, to review it here.",
                                (button("Scan sheets", lambda: window.show_page("scan"), "primary", "scan"),
                                 button("Open batch", window.load_saved_batch, "", "layers")))
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self._build_sheet_view())
        self.stack.addWidget(self._build_grid_view())

        for sequence, handler in (("Ctrl+Return", self.confirm_current), ("Ctrl+Enter", self.confirm_current),
                                  ("Alt+Down", lambda: self._step_sheet(1)), ("Alt+Up", lambda: self._step_sheet(-1))):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)
        self._refresh_chrome()

    # ================================================================ construction
    def _build_sheet_view(self) -> QWidget:
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        workspace_splitter = QSplitter(Qt.Vertical)
        workspace_splitter.setChildrenCollapsible(False)

        # -- queue
        queue_card, ql = card((0, 0, 0, 0), 0)
        queue_head = QVBoxLayout()
        queue_head.setContentsMargins(12, 12, 12, 10)
        queue_head.setSpacing(8)
        queue_head.addWidget(label("Sheets", "cardTitle"))
        self.filter = Segmented((("flagged", "To review"), ("all", "All")))
        self.filter.changed.connect(lambda _key: self.apply_filter())
        queue_head.addWidget(self.filter)
        ql.addLayout(queue_head)
        ql.addWidget(divider())
        self.queue = QListWidget()
        self.queue.setObjectName("queue")
        self.queue.setItemDelegate(QueueDelegate(self.queue))
        self.queue.setMouseTracking(True)
        self.queue.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.queue.setAccessibleName("Sheets in this batch")
        self.queue.currentRowChanged.connect(self._queue_row_changed)
        ql.addWidget(self.queue, 1)
        self.queue_empty = label("Every sheet has been reviewed.", "hint")
        self.queue_empty.setAlignment(Qt.AlignCenter)
        self.queue_empty.setContentsMargins(12, 24, 12, 24)
        self.queue_empty.hide()
        ql.addWidget(self.queue_empty)
        queue_card.setMinimumWidth(230)
        splitter.addWidget(queue_card)

        # -- viewer
        viewer_card, vl = card((10, 8, 10, 10), 8)
        tools = QHBoxLayout()
        tools.setSpacing(2)
        self.sheet_title = label("", "strong", wrap=False)
        tools.addWidget(self.sheet_title)
        tools.addStretch(1)
        self.viewer = SheetViewer()
        self.marks_toggle = button("", None, "tool", "eye", "Show or hide what the reader detected")
        self.marks_toggle.setCheckable(True)
        self.marks_toggle.setChecked(True)
        self.marks_toggle.toggled.connect(self.viewer.set_marks_visible)
        tools.addWidget(self.marks_toggle)
        tools.addSpacing(8)
        tools.addWidget(button("", lambda: self.viewer.zoom_by(1 / 1.25), "tool", "zoom-out", "Zoom out (−)"))
        self.zoom_label = label("100%", "hint", wrap=False)
        self.zoom_label.setMinimumWidth(46)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        tools.addWidget(self.zoom_label)
        tools.addWidget(button("", lambda: self.viewer.zoom_by(1.25), "tool", "zoom-in", "Zoom in (+)"))
        tools.addWidget(button("", self._fit_viewer, "tool", "fit", "Fit sheet width and return to the top (0)"))
        self.viewer.zoomChanged.connect(lambda value: self.zoom_label.setText(f"{round(value * 100)}%"))
        vl.addLayout(tools)
        vl.addWidget(self.viewer, 1)
        legend = QHBoxLayout()
        legend.setSpacing(14)
        self.viewer.setToolTip("Scroll vertically to inspect the full sheet. Drag to pan while zoomed; Ctrl + scroll or the zoom buttons to zoom. Double-click to fit the sheet width.")
        for colour, text in (("#16A34A", "Read answer"), ("#DC2626", "Needs your decision"), ("#2563EB", "Roll number and set")):
            dot = QLabel()
            dot.setFixedSize(12, 12)
            dot.setStyleSheet(f"border: 2px solid {colour}; border-radius: 6px; background: transparent;")
            pair = QHBoxLayout()
            pair.setSpacing(5)
            pair.addWidget(dot)
            pair.addWidget(label(text, "hint", wrap=False))
            legend.addLayout(pair)
        legend.addStretch(1)
        vl.addLayout(legend)
        workspace_splitter.addWidget(viewer_card)

        # -- inspector
        inspector_card, il = card((0, 0, 0, 0), 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inspector_host = QWidget()
        self.inspector = QVBoxLayout(self.inspector_host)
        self.inspector.setContentsMargins(14, 14, 14, 14)
        self.inspector.setSpacing(10)
        scroll.setWidget(self.inspector_host)
        self.inspector_scroll = scroll
        il.addWidget(scroll, 1)
        il.addWidget(divider())
        foot = QVBoxLayout()
        foot.setContentsMargins(14, 10, 14, 12)
        foot.setSpacing(8)
        self.sheet_note = label("", "hint")
        self.sheet_note.hide()
        foot.addWidget(self.sheet_note)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.prev_button = button("", lambda: self._step_sheet(-1), "", "chevron-left", "Previous sheet (Alt+Up)")
        self.next_button = button("", lambda: self._step_sheet(1), "", "chevron-right", "Next sheet (Alt+Down)")
        self.confirm_button = button("Confirm sheet", self.confirm_current, "primary", "check",
                                     "Save this sheet and move to the next one that needs review (Ctrl+Enter)")
        actions.addWidget(self.prev_button)
        actions.addWidget(self.next_button)
        actions.addWidget(self.confirm_button, 1)
        foot.addLayout(actions)
        keys = QGridLayout()
        keys.setHorizontalSpacing(6)
        keys.setVerticalSpacing(4)
        for position, (key_text, meaning) in enumerate((("A–E", "choose answer"), ("Space", "mark blank"),
                                                        ("↑ ↓", "next / previous mark"), ("Ctrl+Enter", "confirm sheet"))):
            keys.addWidget(kbd(key_text), position // 2, (position % 2) * 2, Qt.AlignLeft)
            keys.addWidget(label(meaning, "hint", wrap=False), position // 2, (position % 2) * 2 + 1)
        keys.setColumnStretch(1, 1)
        keys.setColumnStretch(3, 1)
        foot.addLayout(keys)
        il.addLayout(foot)
        inspector_card.setMinimumWidth(0)
        workspace_splitter.addWidget(inspector_card)
        workspace_splitter.setStretchFactor(0, 1)
        workspace_splitter.setStretchFactor(1, 1)
        workspace_splitter.setSizes([440, 520])
        splitter.addWidget(workspace_splitter)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 1050])
        return splitter

    def _build_grid_view(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(label("Double-click a cell to correct it, tick Reviewed for each flagged sheet, then save.", "hint", wrap=False))
        bar.addStretch(1)
        bar.addWidget(Pill("Blank", "warn"))
        bar.addWidget(Pill("Unclear or double mark", "bad"))
        layout.addLayout(bar)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.table = grid_table(table([], None, editable=True))
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setShowGrid(True)
        self.table.setAccessibleName("All sheets and answers in this batch")
        self.table.setToolTip("Double-click an answer or set to choose a valid value. Select a cell to inspect its scan crop.")
        self.table.itemChanged.connect(self._table_item_changed)
        self.table.currentCellChanged.connect(self._grid_cell_changed)
        split.addWidget(self.table)
        self.grid_viewer = SheetViewer()
        self.grid_viewer.clear("Select a cell to see that part of the scan.")
        self.grid_viewer.setMinimumWidth(300)
        split.addWidget(self.grid_viewer)
        split.setStretchFactor(0, 1)
        split.setSizes([820, 360])
        layout.addWidget(split, 1)
        return host

    # ================================================================ shortcuts to window state
    @property
    def records(self) -> list[dict[str, Any]]:
        return self.window_.result_records

    @property
    def config(self) -> dict[str, Any] | None:
        return self.window_.config

    def _questions(self) -> list[str]:
        return [str(question["id"]) for question in self.config["questions"]] if self.config else []

    def _options(self) -> list[str]:
        return [str(option) for option in self.config["sheet"]["options"]] if self.config else []

    def has_unsaved(self) -> bool:
        return bool(self._dirty)

    def pending_count(self) -> int:
        return sum(bool(record.get("review_required")) for record in self.records)

    # ================================================================ loading
    def clear_batch(self) -> None:
        self.flush_drafts()
        self._loading = True
        self.table.setRowCount(0)
        self.queue.clear()
        self._loading = False
        self._dirty.clear()
        self._checked.clear()
        self._pixmaps.clear()
        self._row = -1
        self._clear_inspector()
        self.viewer.clear()
        self.grid_viewer.clear("Select a cell to see that part of the scan.")
        self._refresh_chrome()

    def load(self) -> None:
        """Rebuild the whole workspace from the window's current batch records."""
        if not self.config:
            return
        self.flush_drafts()
        self._layout = create_layout(self.config)
        self._dirty.clear()
        self._pixmaps.clear()
        self._row = -1
        questions = self._questions()
        headers = ["Roll number", "Set", "Score", "Reviewed", "Flags", "Image", *[f"Q{qid}" for qid in questions]]
        self._loading = True
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self.records))
        for column in range(len(headers)):
            self.table.setColumnWidth(column, (118, 56, 64, 84, 200, 170)[column] if column < FIRST_ANSWER_COLUMN else 52)
            self.table.setColumnHidden(COL_SET, len(self.config["sheet"]["set_codes"]) < 2)
        self.table.setItemDelegateForColumn(COL_SET, _ChoiceDelegate([str(code) for code in self.config["sheet"]["set_codes"]], parent=self.table))
        roster_rolls = sorted({str(student["roll_number"]) for student in self.window_.repo.students()})
        self.table.setItemDelegateForColumn(COL_ROLL, _ChoiceDelegate(roster_rolls, editable=True,
            max_length=int(self.config["sheet"]["roll_number_digits"]), parent=self.table))
        for column in range(FIRST_ANSWER_COLUMN, len(headers)):
            self.table.setItemDelegateForColumn(column, _ChoiceDelegate(self._options(), parent=self.table))
        for row in range(len(self.records)):
            for column in range(len(headers)):
                item = QTableWidgetItem("")
                if column >= FIRST_ANSWER_COLUMN or column in (COL_SET, COL_SCORE):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, column, item)
            self._paint_row(row, reset_text=True)
        restored = self._restore_drafts()
        self.queue.clear()
        for row in range(len(self.records)):
            self.queue.addItem(QListWidgetItem())
            self._paint_queue_item(row)
        self._loading = False
        self._refresh_chrome()
        self.apply_filter()
        first = self._next_flagged(-1)
        self.select_row(first if first >= 0 else (0 if self.records else -1))
        if restored:
            self.window_.toast(f"Restored unsaved corrections on {restored} sheet{'s' if restored != 1 else ''}", "info")

    # ================================================================ drafts (auto-save)
    def _draft_payload(self, row: int) -> dict[str, Any]:
        record = self.records[row]
        return {
            "roll": self.table.item(row, COL_ROLL).text(), "set": self.table.item(row, COL_SET).text(),
            "answers": {qid: self.table.item(row, column).text()
                        for column, qid in enumerate(self._questions(), FIRST_ANSWER_COLUMN)},
            "reviewed": self.table.item(row, COL_REVIEWED).checkState() == Qt.Checked,
            "checked": sorted(self._checked.get(int(record["id"]), set())),
        }

    def flush_drafts(self) -> None:
        """Write every unsaved correction to the database now."""
        self._draft_timer.stop()
        if not self._dirty or self.window_.batch_id is None or self.table.rowCount() != len(self.records):
            return
        try:
            self.window_.repo.save_review_drafts(
                {int(self.records[row]["id"]): self._draft_payload(row) for row in self._dirty if row < len(self.records)})
        except Exception:
            import logging
            logging.getLogger("omr_exam").exception("Could not auto-save review drafts")

    def _restore_drafts(self) -> int:
        """Re-apply drafts left by an earlier session to the edit buffer; returns how many sheets."""
        try:
            drafts = self.window_.repo.review_drafts(self.window_.batch_id) if self.window_.batch_id is not None else {}
        except Exception:
            drafts = {}
        questions = self._questions()
        restored = 0
        for row, record in enumerate(self.records):
            draft = drafts.get(int(record["id"]))
            if not draft:
                continue
            self.table.item(row, COL_ROLL).setText(str(draft.get("roll", record["roll_number"])))
            self.table.item(row, COL_SET).setText(str(draft.get("set", record["set_code"])))
            answers = draft.get("answers") if isinstance(draft.get("answers"), dict) else {}
            for column, qid in enumerate(questions, FIRST_ANSWER_COLUMN):
                if qid in answers:
                    self.table.item(row, column).setText(str(answers[qid] or ""))
            if draft.get("reviewed"):
                self.table.item(row, COL_REVIEWED).setCheckState(Qt.Checked)
            self._checked[int(record["id"])] = {str(q) for q in draft.get("checked", [])}
            self._dirty.add(row)
            self._paint_row(row, reset_text=False)
            restored += 1
        return restored

    def _paint_row(self, row: int, reset_text: bool) -> None:
        """Refresh one grid row from its record; ``reset_text`` discards unsaved edits in it."""
        record = self.records[row]
        questions = self._questions()
        pending = bool(record.get("review_required"))
        names = [flag.get("kind", "error") for flag in record["flags"]]
        flag_names = "; ".join(dict.fromkeys(flag_text(name) for name in names))
        if any(name != "blank" for name in names) and not pending:
            flag_names = "Reviewed · " + flag_names
        kinds = set(names) if pending else set()
        flagged = ({str(flag.get("question")) for flag in record["flags"]
                    if flag.get("group") == "answer" and flag.get("kind") in ANSWER_ISSUES} if pending else set())
        bad, warn, plain = QBrush(QColor(theme.BAD_BG)), QBrush(QColor(theme.WARN_BG)), QBrush()
        was_loading, self._loading = self._loading, True
        cells = {COL_SCORE: f"{float(record['score']):g}", COL_FLAGS: flag_names,
                 COL_IMAGE: Path(record["image_path"].split("#", 1)[0]).name}
        if reset_text:
            cells.update({COL_ROLL: record["roll_number"], COL_SET: record["set_code"]})
        for column, value in cells.items():
            self.table.item(row, column).setText(str(value))
        reviewed = self.table.item(row, COL_REVIEWED)
        reviewed.setFlags((reviewed.flags() & ~Qt.ItemIsEditable) | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        if reset_text or not pending:
            reviewed.setCheckState(Qt.Unchecked if pending else Qt.Checked)
        for column in (COL_SCORE, COL_FLAGS, COL_IMAGE):
            item = self.table.item(row, column)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        self.table.item(row, COL_FLAGS).setToolTip(flag_names)
        self.table.item(row, COL_ROLL).setBackground(bad if kinds & (IDENTITY_KINDS | {"unreadable"}) else plain)
        self.table.item(row, COL_SET).setBackground(bad if "invalid_set" in kinds else plain)
        for offset, qid in enumerate(questions, FIRST_ANSWER_COLUMN):
            item = self.table.item(row, offset)
            if reset_text:
                item.setText(record["answers"].get(qid) or "")
            item.setBackground(bad if qid in flagged else warn if not item.text().strip() else plain)
        self._loading = was_loading

    def _issue_summary(self, record: dict[str, Any]) -> tuple[str, str, int]:
        """(text, tone, issue count) describing why a sheet is in the queue."""
        if not record.get("review_required"):
            return "Reviewed" if any(flag.get("kind") != "blank" for flag in record["flags"]) else "No issues", "good", 0
        kinds = [flag.get("kind", "") for flag in record["flags"] if flag.get("kind") not in {"blank", "preview_error"}]
        if "unreadable" in kinds:
            return "Sheet could not be read", "bad", 1
        marks = sum(kind in ANSWER_ISSUES for kind in kinds)
        parts = []
        if marks:
            parts.append(f"{marks} unclear mark{'s' if marks != 1 else ''}")
        parts.extend(dict.fromkeys(flag_text(kind) for kind in kinds if kind not in ANSWER_ISSUES))
        identity = bool(set(kinds) & (IDENTITY_KINDS | {"invalid_set"}))
        return " · ".join(parts) or "Needs a check", "bad" if identity else "warn", max(1, len(kinds))

    def _paint_queue_item(self, row: int) -> None:
        record = self.records[row]
        item = self.queue.item(row)
        if item is None:
            return
        roll = self.table.item(row, COL_ROLL).text().strip()
        name = record.get("student_name") or ""
        summary, tone, count = self._issue_summary(record)
        item.setData(QUEUE_TITLE, " · ".join(filter(None, (roll or "No roll number", name))))
        item.setData(QUEUE_SUB, summary)
        item.setData(QUEUE_TONE, tone)
        item.setData(QUEUE_BADGE, "Draft" if row in self._dirty else (str(count) if count else f"{float(record['score']):g}"))
        item.setData(Qt.AccessibleTextRole, f"Sheet {roll or 'without roll number'} {name}: {summary}")

    def _refresh_chrome(self) -> None:
        window = self.window_
        has_batch = window.batch_id is not None and bool(self.records)
        total, pending = len(self.records), self.pending_count()
        self.progress_host.setVisible(has_batch)
        self.mode.setEnabled(has_batch)
        self.save_all_button.setVisible(has_batch and self.mode.current() == "grid")
        if not has_batch:
            self.batch_pill.set("No batch open", "neutral")
            self.summary.set("No batch loaded. Scan sheets or open a saved batch.", "info")
            self.summary.set_action()
            self.stack.setCurrentIndex(0)
            window.update_review_badge()
            return
        self.stack.setCurrentIndex(1 if self.mode.current() == "sheet" else 2)
        self.batch_pill.set(f"Batch #{window.batch_id} · {total} sheet{'s' if total != 1 else ''}", "info")
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(total - pending)
        theme.set_property(self.progress, "tone", "good" if not pending else "")
        self.progress_label.setText(f"{total - pending} of {total} sheets ready")
        if pending:
            self.summary.set(f"<b>{pending} sheet{'s' if pending != 1 else ''}</b> need{'s' if pending == 1 else ''} your attention "
                             f"before results can be exported.", "warn")
            self.summary.set_action()
        else:
            self.summary.set("<b>All sheets are reviewed.</b> Results are ready to export.", "good")
            self.summary.set_action("View results", lambda: window.show_page("results"))
        self.filter.set_text("flagged", f"To review ({pending})")
        self.filter.set_text("all", f"All ({total})")
        window.update_review_badge()

    # ================================================================ queue & navigation
    def _set_mode(self, key: str) -> None:
        self._refresh_chrome()
        if key == "sheet" and self._row >= 0:
            self._show_sheet(self._row)
        elif key == "grid" and self._row >= 0:
            self.table.setCurrentCell(self._row, max(self.table.currentColumn(), 0))

    def apply_filter(self, *_args) -> None:
        only = self.filter.current() == "flagged"
        visible = 0
        for row, record in enumerate(self.records):
            hidden = only and not record.get("review_required") and row != self._row
            if row < self.queue.count():
                self.queue.item(row).setHidden(hidden)
            visible += not hidden
        self.queue.setVisible(visible > 0)
        self.queue_empty.setVisible(visible == 0 and bool(self.records))

    def _next_flagged(self, after: int) -> int:
        count = len(self.records)
        for step in range(1, count + 1):
            row = (after + step) % count if count else -1
            if row >= 0 and self.records[row].get("review_required") and row != after:
                return row
        return -1

    def _step_sheet(self, direction: int) -> None:
        if not self.records:
            return
        row = self._row
        for _ in range(len(self.records)):
            row = (row + direction) % len(self.records)
            if not self.queue.item(row).isHidden():
                break
        self.select_row(row)

    def select_row(self, row: int) -> None:
        if row < 0 or row >= len(self.records):
            return
        if self.queue.item(row).isHidden():
            self.queue.item(row).setHidden(False)
        if self.queue.currentRow() != row:
            self.queue.setCurrentRow(row)       # triggers _queue_row_changed
        else:
            self._show_sheet(row)

    def _queue_row_changed(self, row: int) -> None:
        if self._loading or row < 0:
            return
        self._show_sheet(row)

    # ================================================================ sheet images
    def _preview_file(self, record: dict[str, Any]) -> Path | None:
        raw = record.get("preview_path") or ""
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = self.window_.db_path.parent / path
        return path if path.is_file() else None

    def _pixmap(self, record: dict[str, Any]) -> QPixmap | None:
        path = self._preview_file(record)
        if path is None:
            return None
        key = str(path)
        if key not in self._pixmaps:
            if len(self._pixmaps) >= 8:
                self._pixmaps.pop(next(iter(self._pixmaps)))
            image = QPixmap(key)
            if image.isNull():
                return None
            self._pixmaps[key] = image
        return self._pixmaps[key]

    @staticmethod
    def _aligned(record: dict[str, Any]) -> bool:
        return not any(flag.get("kind") == "unreadable" for flag in record.get("flags", []))

    def _scale(self, image: QPixmap) -> tuple[float, float]:
        return image.width() / self._layout.width, image.height() / self._layout.height

    def _group_rect(self, image: QPixmap, group: str, index: str | None = None, pad: tuple[float, float, float, float] = (9, 3.2, 4, 3.2)) -> QRectF | None:
        """Pixel rectangle around a bubble group, padded in millimetres (left, top, right, bottom)."""
        bubbles = [b for b in self._layout.bubbles if b.group == group and (index is None or b.index == index)]
        if not bubbles:
            return None
        sx, sy = self._scale(image)
        left, right = min(b.x for b in bubbles) - pad[0], max(b.x for b in bubbles) + pad[2]
        top, bottom = min(b.y for b in bubbles) - pad[1], max(b.y for b in bubbles) + pad[3]
        return QRectF(left * sx, top * sy, (right - left) * sx, (bottom - top) * sy).intersected(QRectF(image.rect()))

    def _crop(self, image: QPixmap, rect: QRectF | None, width: int = 292, max_height: int = 190) -> QPixmap | None:
        if rect is None or rect.isEmpty():
            return None
        piece = image.copy(rect.toRect())
        scaled = piece.scaled(width * 2, max_height * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(2.0)
        return scaled

    def _marks(self, row: int, image: QPixmap) -> list[tuple[float, float, float, str]]:
        record = self.records[row]
        sx, sy = self._scale(image)
        radius = 2.15 * sx
        answers = {qid: self.table.item(row, column).text().strip().upper()
                   for column, qid in enumerate(self._questions(), FIRST_ANSWER_COLUMN)}
        roll = self.table.item(row, COL_ROLL).text().strip()
        set_code = self.table.item(row, COL_SET).text().strip().upper()
        uncertain: dict[str, dict[str, str]] = {}
        if record.get("review_required"):
            for flag in record["flags"]:
                if flag.get("group") == "answer" and flag.get("kind") in ANSWER_ISSUES:
                    values = flag.get("values") or ([flag["value"]] if flag.get("value") else [])
                    question = uncertain.setdefault(str(flag.get("question")), {})
                    if flag.get("kind") == "multiple":
                        selected = flag.get("selected_values") or values
                        weak = flag.get("weak_values") or []
                        question.update({str(v).upper(): "bad" for v in selected})
                        question.update({str(v).upper(): "warn" for v in weak})
                    else:
                        question.update({str(v).upper(): "warn" for v in values})
        marks = []
        for bubble in self._layout.bubbles:
            value = str(bubble.value).upper()
            if bubble.group == "answer":
                if value in uncertain.get(bubble.index, {}):
                    tone = uncertain[bubble.index][value]
                elif answers.get(bubble.index) == value:
                    tone = "good"
                else:
                    continue
            elif bubble.group == "roll":
                position = int(bubble.index)
                if position >= len(roll) or roll[position] != value:
                    continue
                tone = "info"
            elif bubble.group == "set" and value == set_code:
                tone = "info"
            else:
                continue
            marks.append((bubble.x * sx, bubble.y * sy, radius, tone))
        return marks

    def _refresh_marks(self) -> None:
        if self._row < 0:
            return
        record = self.records[self._row]
        image = self._pixmap(record)
        if image is not None and self._aligned(record):
            self.viewer.set_marks(self._marks(self._row, image), self.marks_toggle.isChecked())

    def _fit_viewer(self) -> None:
        self.viewer.highlight(None)
        self.viewer.fit()

    # ================================================================ inspector
    def _clear_inspector(self) -> None:
        self._cards, self._chips = {}, {}
        while self.inspector.count():
            item = self.inspector.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                _delete_layout(item.layout())
        self.sheet_title.setText("")
        self.sheet_note.hide()
        for control in (self.prev_button, self.next_button, self.confirm_button):
            control.setEnabled(False)

    def _show_sheet(self, row: int) -> None:
        self._row = row
        record = self.records[row]
        image = self._pixmap(record)
        aligned = self._aligned(record)
        self.sheet_title.setText(Path(record["image_path"].split("#", 1)[0]).name
                                 + (f" · page {record['image_path'].split('#page=', 1)[1]}" if "#page=" in record["image_path"] else ""))
        if image is None:
            self.viewer.clear("No scan preview is stored for this sheet.")
        else:
            self.viewer.set_image(image)
            if aligned:
                self.viewer.set_marks(self._marks(row, image), self.marks_toggle.isChecked())
        self._build_inspector(row, image if aligned else None)
        self.sheet_title.setText(Path(record["image_path"].split("#", 1)[0]).name
                                 + (f" · page {record['image_path'].split('#page=', 1)[1]}" if "#page=" in record["image_path"] else ""))
        for control in (self.prev_button, self.next_button, self.confirm_button):
            control.setEnabled(True)
        if self.mode.current() == "sheet":
            first = next((c for q, c in self._cards.items() if q not in self._checked.get(int(record["id"]), set())), None)
            QTimer.singleShot(0, (first or self.confirm_button).setFocus)

    def _build_inspector(self, row: int, image: QPixmap | None) -> None:
        self._clear_inspector()
        record = self.records[row]
        pending = bool(record.get("review_required"))
        flags = record["flags"] if pending else []
        kinds = {flag.get("kind") for flag in flags}
        add = self.inspector.addWidget

        # status line
        status = QHBoxLayout()
        status.setSpacing(8)
        summary, tone, _count = self._issue_summary(record)
        status.addWidget(Pill("Needs review" if pending else "Ready", "warn" if pending else "good"))
        self.score_label = label("", "hint", wrap=False)
        status.addStretch(1)
        status.addWidget(self.score_label)
        self.inspector.addLayout(status)

        if "unreadable" in kinds:
            message = next((flag.get("message", "") for flag in flags if flag.get("kind") == "unreadable"), "")
            add(Banner(f"<b>This sheet could not be read.</b> {message}<br>Rescan it in a new batch, or type the roll number, "
                       f"set, and answers below from the paper sheet.", "bad"))

        # identity
        add(label("STUDENT", "eyebrow"))
        roll_issue = [flag for flag in flags if flag.get("kind") in IDENTITY_KINDS or flag.get("group") == "roll"]
        if image is not None:
            crop = self._crop(image, self._group_rect(image, "roll", None, (3, 3, 3, 3)), 292, 150)
            if crop is not None and roll_issue:
                picture = QLabel()
                picture.setPixmap(crop)
                picture.setObjectName("inset")
                picture.setAlignment(Qt.AlignCenter)
                picture.setFixedHeight(round(crop.height() / 2) + 10)
                add(picture)
        digits = int(self.config["sheet"]["roll_number_digits"])
        self.roll_edit = QLineEdit(self.table.item(row, COL_ROLL).text())
        self.roll_edit.setMaxLength(digits)
        self.roll_edit.setPlaceholderText(f"{digits}-digit roll number")
        self.roll_edit.setAccessibleName("Roll number")
        self.roll_edit.textEdited.connect(self._roll_edited)
        codes = [str(code) for code in self.config["sheet"]["set_codes"]]
        self.set_combo = Segmented([(code, code) for code in codes])
        self.set_combo.setAccessibleName("Test set")
        current_set = self.table.item(row, COL_SET).text().strip().upper()
        self.set_combo.set_current(next((c for c in codes if c.upper() == current_set), ""))
        self.set_combo.changed.connect(self._set_changed)
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)
        form.addWidget(label("Roll number", "hint", wrap=False), 0, 0)
        if len(codes) > 1:
            form.addWidget(label("Set", "hint", wrap=False), 0, 1)
            form.addWidget(self.set_combo, 1, 1)
        else:
            self.set_combo.hide()
        form.addWidget(self.roll_edit, 1, 0)
        form.setColumnStretch(0, 3)
        form.setColumnStretch(1, 2)
        self.inspector.addLayout(form)
        self.identity_note = label("", "hint")
        add(self.identity_note)
        self.roster_button = button("Add this roll number to the roster", self._add_to_roster, "link", "user-plus")
        add(self.roster_button)
        self._update_identity_note()

        # uncertain marks
        answer_flags = [flag for flag in flags if flag.get("group") == "answer" and flag.get("kind") in ANSWER_ISSUES]
        if answer_flags:
            add(label(f"NEEDS YOUR DECISION · {len(answer_flags)}", "eyebrow"))
        checked = self._checked.setdefault(int(record["id"]), set())
        questions = self._questions()
        for flag in answer_flags:
            qid = str(flag.get("question"))
            if qid not in questions or qid in self._cards:
                continue
            values = [str(v) for v in (flag.get("values") or [])]
            kind = flag.get("kind")
            description = {
                "multiple": f"Filled marks detected on {' and '.join(values)}. Choose the intended answer.",
                "faint": f"Light or partial mark detected on {', '.join(values)}. Decide whether it counts.",
                "review": (f"Read as {flag.get('value', '')}; also check the light or partial mark on "
                           f"{', '.join(value for value in values if value != flag.get('value'))}."
                           if any(value != flag.get("value") for value in values)
                           else f"Read as {flag.get('value', '')}, but the fill is light or partial. Confirm it."),
            }[kind]
            column = FIRST_ANSWER_COLUMN + questions.index(qid)
            current = self._option(self.table.item(row, column).text())
            crop = self._crop(image, self._group_rect(image, "answer", qid), 292, 64) if image is not None else None
            issue = IssueCard(qid, kind, description, self._options(), current, crop)
            issue.set_resolved(qid in checked)
            issue.chosen.connect(self._issue_chosen)
            issue.focused.connect(self._issue_focused)
            issue.navigate.connect(self._issue_navigate)
            self._cards[qid] = issue
            add(issue)
        if pending and not answer_flags and "unreadable" not in kinds and not roll_issue and "invalid_set" not in kinds:
            add(Banner("This sheet was flagged for a manual check. Compare it with the scan, then confirm.", "info"))

        # all answers
        add(label("ALL ANSWERS", "eyebrow"))
        add(label("Click a question to change it. Amber is blank; red still needs a decision.", "hint"))
        grid = QGridLayout()
        grid.setSpacing(6)
        for position, qid in enumerate(questions):
            chip = QPushButton()
            chip.setObjectName("answerChip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _c=False, q=qid: self._chip_menu(q))
            self._chips[qid] = chip
            grid.addWidget(chip, position // 5, position % 5)
            self._paint_chip(qid)
        grid.setColumnStretch(5, 1)
        self.inspector.addLayout(grid)
        self.inspector.addStretch(1)
        self._update_footer()

    # -- small helpers
    def _option(self, text: str) -> str | None:
        lookup = {option.upper(): option for option in self._options()}
        return lookup.get(str(text).strip().upper())

    def _paint_chip(self, qid: str) -> None:
        chip = self._chips.get(qid)
        if chip is None or self._row < 0:
            return
        record = self.records[self._row]
        column = FIRST_ANSWER_COLUMN + self._questions().index(qid)
        text = self.table.item(self._row, column).text().strip()
        original = record["answers"].get(qid) or ""
        unresolved = qid in self._cards and qid not in self._checked.get(int(record["id"]), set())
        state = "flagged" if unresolved else "edited" if text.upper() != str(original).upper() else "blank" if not text else ""
        chip.setText(f"{qid}  {text or '–'}")
        chip.setAccessibleName(f"Question {qid}: {text or 'blank'}")
        theme.set_property(chip, "state", state)

    def _chip_menu(self, qid: str) -> None:
        menu = QMenu(self)
        menu.addSection(f"Question {qid}")
        for option in self._options():
            menu.addAction(str(option), lambda v=option: self._set_answer(qid, v))
        menu.addSeparator()
        menu.addAction("Blank", lambda: self._set_answer(qid, None))
        chip = self._chips[qid]
        self._highlight_question(qid)
        menu.exec(chip.mapToGlobal(chip.rect().bottomLeft()))

    def _mark_dirty(self, row: int) -> None:
        self._dirty.add(row)
        self._paint_queue_item(row)
        # Persist this sheet's draft straight away: one small write, and nothing is lost if
        # the batch is switched, the app is closed, or the computer loses power.
        try:
            if row < len(self.records):
                self.window_.repo.save_review_drafts({int(self.records[row]["id"]): self._draft_payload(row)})
        except Exception:
            self._draft_timer.start()

    def _set_answer(self, qid: str, value: str | None) -> None:
        row = self._row
        if row < 0:
            return
        column = FIRST_ANSWER_COLUMN + self._questions().index(qid)
        self._loading = True
        item = self.table.item(row, column)
        item.setText(value or "")
        self._loading = False
        record = self.records[row]
        if qid in self._cards:
            self._checked.setdefault(int(record["id"]), set()).add(qid)
            self._cards[qid].value = value
            self._cards[qid]._sync()
            self._cards[qid].set_resolved(True)
        item.setBackground(QBrush(QColor(theme.WARN_BG)) if not value else QBrush())
        self._mark_dirty(row)
        self._paint_chip(qid)
        self._refresh_marks()
        self._update_footer()

    def _issue_chosen(self, qid: str, value) -> None:
        self._set_answer(qid, value)

    def _highlight_question(self, qid: str, zoom: bool = True) -> None:
        if self._row < 0:
            return
        record = self.records[self._row]
        image = self._pixmap(record)
        if image is not None and self._aligned(record):
            self.viewer.highlight(self._group_rect(image, "answer", qid, (9, 2.2, 4, 2.2)), zoom)

    def _issue_focused(self, qid: str) -> None:
        self._highlight_question(qid)
        card_widget = self._cards.get(qid)
        if card_widget is not None:
            self.inspector_scroll.ensureWidgetVisible(card_widget, 0, 24)

    def _issue_navigate(self, qid: str, direction: int) -> None:
        order = list(self._cards)
        target = order.index(qid) + direction
        if 0 <= target < len(order):
            self._cards[order[target]].setFocus()
        elif direction > 0:
            self.confirm_button.setFocus()

    def _roll_edited(self, text: str) -> None:
        if self._row < 0:
            return
        self._loading = True
        self.table.item(self._row, COL_ROLL).setText(text.strip())
        self._loading = False
        self._mark_dirty(self._row)
        self._update_identity_note()
        self._refresh_marks()

    def _set_changed(self, _key: str) -> None:
        if self._row < 0:
            return
        self._loading = True
        self.table.item(self._row, COL_SET).setText(self.set_combo.current())
        self._loading = False
        self._mark_dirty(self._row)
        self._refresh_marks()
        self._update_footer()

    def _roll_problem(self, row: int) -> str:
        """Why the roll number in the edit buffer is not acceptable yet ('' when it is fine)."""
        digits = int(self.config["sheet"]["roll_number_digits"])
        roll = self.table.item(row, COL_ROLL).text().strip()
        if not roll:
            return "Enter the roll number shown on the sheet."
        if len(roll) != digits or not roll.isdigit():
            return f"The roll number must be exactly {digits} digits."
        for other in range(self.table.rowCount()):
            if other != row and self.table.item(other, COL_ROLL).text().strip() == roll:
                return f"Roll number {roll} is already used by another sheet in this batch."
        return ""

    def _update_identity_note(self) -> None:
        row = self._row
        problem = self._roll_problem(row)
        roll = self.table.item(row, COL_ROLL).text().strip()
        repo = self.window_.repo
        self.roster_button.hide()
        theme.set_property(self.roll_edit, "invalid", "true" if problem else "false")
        if problem:
            self.identity_note.setText(f"<span style='color:{theme.BAD}'>{problem}</span>")
            return
        student = next((s for s in repo.students() if s["roll_number"] == roll), None) if repo.has_students() else None
        if student is not None:
            detail = " · ".join(filter(None, (student["name"], student["class_name"])))
            self.identity_note.setText(f"<span style='color:{theme.GOOD}'>✓ {detail}</span>")
        elif repo.has_students():
            self.identity_note.setText(f"<span style='color:{theme.WARN}'>Roll number {roll} is not in the student roster.</span>")
            self.roster_button.show()
        else:
            self.identity_note.setText("No roster imported, so names are not matched.")

    def _add_to_roster(self) -> None:
        from omr_exam.dialogs import StudentDialog

        roll = self.table.item(self._row, COL_ROLL).text().strip()
        classes = self.window_._known_classes()
        dialog = StudentDialog(self, roll, classes=classes)
        if dialog.exec():
            try:
                self.window_.repo.save_student(*dialog.values())
            except Exception as exc:
                alert(self, "Could not save student", str(exc), "bad")
                return
            self.window_.refresh_students()
            self._update_identity_note()

    def _update_footer(self) -> None:
        if self._row < 0:
            return
        record = self.records[self._row]
        unresolved = [q for q in self._cards if q not in self._checked.get(int(record["id"]), set())]
        dirty = self._row in self._dirty
        self.score_label.setText(f"Score {float(record['score']):g}" + (" · draft saved automatically" if dirty else ""))
        if unresolved:
            self.sheet_note.setText(f"{len(unresolved)} flagged mark{'s' if len(unresolved) != 1 else ''} not checked yet. "
                                    f"Confirming keeps {'them' if len(unresolved) != 1 else 'it'} as shown.")
            self.sheet_note.show()
        else:
            self.sheet_note.hide()
        pending = bool(record.get("review_required"))
        self.confirm_button.setText("Confirm sheet" if pending or dirty else "Reviewed · next sheet")

    # ================================================================ grid view
    def _table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        row, column = item.row(), item.column()
        if column >= FIRST_ANSWER_COLUMN or column == COL_SET:
            self._loading = True
            item.setText(item.text().strip().upper())
            if column >= FIRST_ANSWER_COLUMN:
                valid = not item.text() or self._option(item.text()) is not None
                item.setBackground(QBrush(QColor(theme.BAD_BG)) if not valid else
                                   QBrush(QColor(theme.WARN_BG)) if not item.text() else QBrush())
            self._loading = False
        self._mark_dirty(row)

    def _grid_cell_changed(self, row: int, column: int, _previous_row: int, _previous_column: int) -> None:
        if self._loading or self.mode.current() != "grid" or row < 0 or row >= len(self.records):
            return
        self._row = row
        record = self.records[row]
        image = self._pixmap(record)
        if image is None:
            self.grid_viewer.clear("No scan preview is stored for this sheet.")
            return
        self.grid_viewer.set_image(image)
        if not self._aligned(record):
            return
        self.grid_viewer.set_marks(self._marks(row, image))
        if column >= FIRST_ANSWER_COLUMN:
            qid = self._questions()[column - FIRST_ANSWER_COLUMN]
            self.grid_viewer.highlight(self._group_rect(image, "answer", qid, (9, 2.2, 4, 2.2)))
        elif column == COL_ROLL:
            self.grid_viewer.highlight(self._group_rect(image, "roll", None, (4, 4, 4, 4)))
        elif column == COL_SET:
            self.grid_viewer.highlight(self._group_rect(image, "set", None, (22, 4, 6, 4)))

    # ================================================================ saving
    def _collect(self, row: int) -> dict[str, Any]:
        """Validate one row of the edit buffer and return the update for the repository."""
        record = self.records[row]
        name = Path(record["image_path"].split("#", 1)[0]).name
        digits = int(self.config["sheet"]["roll_number_digits"])
        roll = self.table.item(row, COL_ROLL).text().strip()
        if len(roll) != digits or not roll.isdigit():
            raise ValueError(f"Sheet {name}: roll number must have {digits} digits")
        set_lookup = {str(code).upper(): str(code) for code in self.config["sheet"]["set_codes"]}
        code = set_lookup.get(self.table.item(row, COL_SET).text().strip().upper(), "")
        if len(set_lookup) == 1:
            code = next(iter(set_lookup.values()))
        if not code:
            raise ValueError(f"Sheet {name}: choose the test set")
        answers: dict[str, str | None] = {}
        for offset, qid in enumerate(self._questions(), FIRST_ANSWER_COLUMN):
            text = self.table.item(row, offset).text().strip()
            answers[qid] = self._option(text)
            if text and answers[qid] is None:
                raise ValueError(f"Sheet {name}: invalid option {text!r} for Q{qid}")
        return {"id": record["id"], "answers": answers, "roll_number": roll, "set_code": code}

    def _after_save(self, saved_rows: list[int]) -> None:
        """Pull fresh records (keeping queue order) and repaint without losing other unsaved edits."""
        window = self.window_
        window.repo.reconcile_batch_roster_flags(window.batch_id)
        fresh = {int(record["id"]): record for record in window.repo.results_for_batch(window.batch_id)}
        for index, record in enumerate(self.records):
            self.records[index] = fresh.get(int(record["id"]), record)
        for row in saved_rows:
            self._dirty.discard(row)
        try:
            window.repo.clear_review_drafts([int(self.records[row]["id"]) for row in saved_rows])
        except Exception:
            pass
        for row in range(len(self.records)):
            self._paint_row(row, reset_text=row in saved_rows)
            self._paint_queue_item(row)
        self._refresh_chrome()
        self.apply_filter()
        window.refresh_dashboard()

    def confirm_current(self) -> None:
        """Save the open sheet and move on to the next one that needs review."""
        window = self.window_
        row = self._row
        if self.mode.current() != "sheet" or row < 0 or not self.config or window.batch_id is None:
            return
        record = self.records[row]
        if not record.get("review_required") and row not in self._dirty:
            self._advance(row)
            return
        try:
            problem = self._roll_problem(row)
            if problem:
                raise ValueError(problem)
            update = self._collect(row)
            window.repo.update_results([update], self.config)
        except Exception as exc:
            self.sheet_note.setText(f"<span style='color:{theme.BAD}'><b>Not saved.</b> {exc}</span>")
            self.sheet_note.show()
            if "roll" in str(exc).lower():
                self.roll_edit.setFocus()
            return
        self._after_save([row])
        if self.records[row].get("review_required"):
            # Identity problems (duplicate or unknown roll number) survive a save by design.
            self._show_sheet(row)
            summary, _tone, _count = self._issue_summary(self.records[row])
            self.sheet_note.setText(f"<span style='color:{theme.WARN}'><b>Saved, but still needs attention:</b> {summary}.</span>")
            self.sheet_note.show()
            return
        window.toast("Sheet saved and re-scored")
        self._advance(row)

    def _advance(self, row: int) -> None:
        target = self._next_flagged(row)
        if target >= 0:
            self.select_row(target)
        elif row + 1 < len(self.records) and self.filter.current() == "all":
            self.select_row(row + 1)
        else:
            self._show_sheet(row)
            self.apply_filter()

    def save_all(self) -> None:
        """Grid view: validate every sheet and commit all corrections in one transaction."""
        window = self.window_
        if not self.config or not self.records:
            return
        try:
            rolls = [self.table.item(row, COL_ROLL).text().strip() for row in range(self.table.rowCount())]
            duplicates = sorted({roll for roll in rolls if roll and rolls.count(roll) > 1})
            if duplicates:
                raise ValueError(f"Duplicate roll number(s) remain in this batch: {', '.join(duplicates)}")
            pending = []
            for row, record in enumerate(self.records):
                reviewed = self.table.item(row, COL_REVIEWED).checkState() == Qt.Checked
                if record.get("review_required") and not reviewed:
                    name = Path(record["image_path"].split("#", 1)[0]).name
                    raise ValueError(f"Inspect {name}, correct its marks, and tick 'Reviewed' before saving")
                pending.append(self._collect(row))
            window.repo.update_results(pending, self.config)
        except Exception as exc:
            alert(self, "Could not save corrections", str(exc), "bad")
            return
        self._after_save(list(range(len(self.records))))
        if self._row >= 0:
            self._show_sheet(self._row) if self.mode.current() == "sheet" else None
        window.toast("All corrections saved and scores recalculated")


def _delete_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().hide()
            item.widget().setParent(None)
            item.widget().deleteLater()
        elif item.layout() is not None:
            _delete_layout(item.layout())
