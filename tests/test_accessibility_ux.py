"""Comprehensive headless Qt (PySide6 / QTest) accessibility, UX, workflow, and export verification."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import cv2
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QTableWidgetSelectionRange

from omr_exam import theme
from omr_exam.dialogs import (
    AnswerKeyDialog,
    BatchLogDialog,
    BatchPickerDialog,
    ExportDialog,
    NewTestDialog,
    ReferenceDataDialog,
    SetSubjectsDialog,
    StudentDialog,
)
from omr_exam.icons import icon
from omr_exam.ingest import count_scan_pages, scan_files_in_folder, scan_pages
from omr_exam.layout import create_layout
from omr_exam.reports import (
    EXPORTS,
    class_results,
    class_summary,
    distractor_analysis,
    export_all,
    item_analysis,
    max_score,
    score_bands,
    set_summary,
    subject_analysis,
    subject_max_scores,
    subject_summary,
    summary_stats,
    topic_analysis,
)
from omr_exam.review import COL_REVIEWED, COL_ROLL, COL_SET, FIRST_ANSWER_COLUMN
from omr_exam.scan_job import ScanProgress, process_scan_batch
from omr_exam.sheets import generate_sheet_pdf
from omr_exam.test_builder import (
    create_test_config,
    parse_partial_credit,
    parse_question_list,
    sheet_capacity_summary,
)
from omr_exam.ui import MainWindow, NAV_ITEMS
from omr_exam.widgets import Banner, Segmented, Stepper


def _app_and_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch | None = None) -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication([])
    theme.apply_light_theme(app)
    if monkeypatch is not None:
        from PySide6.QtWidgets import QInputDialog
        import omr_exam.dialogs as dialogs_mod
        import omr_exam.review as review_mod
        import omr_exam.ui as ui_mod
        import omr_exam.widgets as widgets_mod

        def _unexpected_alert(_parent, title, message, *_args, **_kwargs):
            pytest.fail(f"Unexpected modal alert({title!r}, {message!r})")

        def _unexpected_confirm(_parent, title, message, *_args, **_kwargs):
            pytest.fail(f"Unexpected modal confirm({title!r}, {message!r})")

        def _unexpected_input(_parent, title, label_text, *_args, **_kwargs):
            pytest.fail(f"Unexpected modal QInputDialog.getText({title!r}, {label_text!r})")

        for mod in (dialogs_mod, review_mod, ui_mod, widgets_mod):
            if hasattr(mod, "alert"):
                monkeypatch.setattr(mod, "alert", _unexpected_alert)
            if hasattr(mod, "confirm"):
                monkeypatch.setattr(mod, "confirm", _unexpected_confirm)
        monkeypatch.setattr(QInputDialog, "getText", _unexpected_input)
    window = MainWindow(str(tmp_path / "data" / "omr.sqlite3"))
    window.resize(1366, 820)
    window.show()
    app.processEvents()
    return app, window


def _build_scan_fixture(
    tmp_path: Path,
    sheets_spec: list[tuple[str, str, dict[str, str], list[tuple[str, str, str]]]],
    *,
    set_codes: list[str] | None = None,
    subjects: list[str] | None = None,
    question_count: int = 10,
) -> tuple[dict, list[Path]]:
    """Generate a printable sheet PDF and synthesize scanned PNG images for each student spec.

    Each entry in ``sheets_spec`` is ``(roll_number, set_code, answers_dict, extra_marks)``.
    """
    codes = set_codes or ["A", "B"]
    subj_list = subjects or ["Physics", "Chemistry"]
    config = create_test_config(
        title="Comprehensive Science Assessment",
        test_date="2026-11-15",
        class_name="10A",
        subjects=subj_list,
        question_count=question_count,
        options=["A", "B", "C", "D"],
        roll_number_digits=6,
        set_codes=codes,
        columns=2,
        correct_points=4.0,
        incorrect_points=-1.0,
        blank_points=0.0,
        institute="QualifyIAS",
    )
    key_pattern = "ABCDABCDAB"
    for code in codes:
        config["sets"][code]["answer_key"] = {
            str(q["id"]): key_pattern[(i + (0 if code == "A" else 1)) % 4]
            for i, q in enumerate(config["questions"])
        }
    for i, q in enumerate(config["questions"]):
        q["topic"] = "Mechanics" if i < question_count // 2 else "Thermodynamics"

    pdf_path = generate_sheet_pdf(config, tmp_path / "blank_sheet.pdf")
    base_image = list(scan_pages(pdf_path))[0][1]
    layout = create_layout(config)
    sx, sy = base_image.shape[1] / layout.width, base_image.shape[0] / layout.height

    scan_dir = tmp_path / "scans_in"
    scan_dir.mkdir(exist_ok=True)
    paths: list[Path] = []
    for idx, (roll, set_code, answers, extra_marks) in enumerate(sheets_spec, 1):
        img = base_image.copy()
        marks: list[tuple[str, str, str]] = [("roll", str(pos), digit) for pos, digit in enumerate(roll)]
        if len(codes) > 1 and set_code:
            marks.append(("set", "0", set_code))
        for qid, opt in answers.items():
            if opt:
                marks.append(("answer", str(qid), opt))
        marks.extend(extra_marks)
        for group, index, value in marks:
            bubble = next(
                b for b in layout.bubbles if b.group == group and b.index == index and b.value == value
            )
            cv2.circle(
                img,
                (round(bubble.x * sx), round(bubble.y * sy)),
                round(1.05 * min(sx, sy)),
                0,
                -1,
            )
        out_path = scan_dir / f"sheet_{idx:03d}.png"
        cv2.imwrite(str(out_path), img)
        paths.append(out_path)
    return config, paths


def test_main_window_all_tabs_navigation_and_accessibility_attributes(tmp_path: Path):
    app, window = _app_and_window(tmp_path)
    try:
        # Check window title and theme lightness
        assert "OMRaut" in window.windowTitle()
        assert window.palette().window().color().lightness() > 200

        # Verify all SVG icons including newly added ones render cleanly
        for icon_name in ("search", "filter", "sort", "copy", "help", "external-link", "check", "alert"):
            ic = icon(icon_name, theme.TEXT, 16)
            assert not ic.isNull()

        # Verify all navigation buttons have tooltips and accessible attributes
        for idx, (key, text) in enumerate(NAV_ITEMS, 1):
            btn = window.nav_buttons[key]
            assert btn.isCheckable()
            assert btn.accessibleName()
            assert f"Ctrl+{idx}" in btn.toolTip()
            QTest.mouseClick(btn, Qt.LeftButton)
            app.processEvents()
            assert window._current_page == key
            assert window.pages.currentIndex() == window.page_index[key]
            assert btn.isChecked()
            # Test refresh_current_page (F5 / Ctrl+R handler) on every page
            window.refresh_current_page()
            app.processEvents()

        # Verify key interactive controls have accessible names, clear buttons, and tooltips
        assert window.test_combo.accessibleName()
        assert window.new_test_button.toolTip()
        assert window.tests_search.accessibleName()
        assert window.tests_search.isClearButtonEnabled()
        assert window.tests_search.placeholderText()
        assert window.tests_filter.accessibleName()
        assert window.tests_table.accessibleName()

        assert window.student_search.accessibleName()
        assert window.student_search.isClearButtonEnabled()
        assert window.student_search.placeholderText()
        assert window.student_class_filter.accessibleName()
        assert window.students_table.accessibleName()

        assert window.staged_table.accessibleName()
        assert window.batch_history.accessibleName()

        assert window.review_page.queue.accessibleName()
        assert window.review_page.queue_search.accessibleName()
        assert window.review_page.queue_search.isClearButtonEnabled()
        assert window.review_page.table.accessibleName()
        assert window.review_page.viewer.accessibleName()

        assert window.results_search.accessibleName()
        assert window.results_search.isClearButtonEnabled()
        assert window.results_search.placeholderText()
        assert window.results_sort.accessibleName()
        assert window.results_filter.accessibleName()
        assert window.question_subject_filter.accessibleName()
        assert window.question_outcome_filter.accessibleName()
        assert window.performance_table.accessibleName()
        assert window.question_performance_table.accessibleName()
        assert window.subject_summary_table.accessibleName()
        assert window.hardest_table.accessibleName()
        assert window.histogram.accessibleName()

        # Settings controls
        assert window.institute_setting.accessibleName()
        assert window.institute_setting.isClearButtonEnabled()
        assert window.scan_dpi_setting.accessibleName()
        assert window.scan_color_setting.accessibleName()

        # Test focus_current_search (Ctrl+F) and _shortcut_escape (Escape) across searchable pages
        for page_key, search_widget in (
            ("tests", window.tests_search),
            ("students", window.student_search),
            ("results", window.results_search),
        ):
            window.show_page(page_key)
            app.processEvents()
            window.focus_current_search()
            app.processEvents()
            search_widget.setText("filter_query")
            assert search_widget.text() == "filter_query"
            window._shortcut_escape()
            app.processEvents()
            assert search_widget.text() == ""

        # Dashboard & Results stat tiles have accessible names and tooltips
        for tile in window.stat_labels.values():
            assert tile.accessibleName()
            assert tile.toolTip()
        for tile in window.result_stats.values():
            assert tile.accessibleName()
            assert tile.toolTip()

        # Check shortcuts exist on window
        shortcut_keys = {sc.key().toString() for sc in window.findChildren(QShortcut)}
        for expected_sc in ("Ctrl+1", "Ctrl+7", "Ctrl+N", "Ctrl+F", "F5", "Ctrl+R", "Ctrl+O", "Ctrl+E", "Esc"):
            assert expected_sc in shortcut_keys

        # Check reusable widgets keyboard & accessibility helpers
        seg = Segmented([("a", "Alpha"), ("b", "Beta"), ("c", "Gamma")], window)
        assert seg.current() == "a"
        QTest.keyClick(seg, Qt.Key_Right)
        assert seg.current() == "b"
        QTest.keyClick(seg, Qt.Key_Left)
        assert seg.current() == "a"

        stepper = Stepper(window)
        stepper.set_steps([
            {"title": "Step 1", "caption": "Done", "state": "done"},
            {"title": "Step 2", "caption": "Current", "state": "current"},
        ])
        clicked_steps: list[int] = []
        stepper.clicked.connect(clicked_steps.append)
        QTest.keyClick(stepper, Qt.Key_Right)
        QTest.keyClick(stepper, Qt.Key_Return)
        assert clicked_steps

        banner = Banner("<b>Important</b> notice", "info", window)
        assert banner.accessibleName() == "Important notice"
    finally:
        window.close()


def test_test_builder_and_dialogs_accessibility_and_workflows(tmp_path: Path, monkeypatch):
    app, window = _app_and_window(tmp_path, monkeypatch)
    repo = window.repo
    try:
        # 1. Test Builder live helpers & validation
        assert parse_question_list("1-3, 5, 8", max_question=10) == [1, 2, 3, 5, 8]
        with pytest.raises(ValueError, match="between 1 and 5"):
            parse_question_list("6", max_question=5)
        assert parse_partial_credit("2:1.5, 4:0.5", max_question=10) == {"2": 1.5, "4": 0.5}
        cap = sheet_capacity_summary(question_count=50, options=["A", "B", "C", "D"], columns=2)
        assert cap["fits"] is True
        assert "column" in cap["message"]

        with pytest.raises(ValueError, match="required"):
            create_test_config(
                title="",
                test_date="2026-11-15",
                class_name="10A",
                subjects=["Physics"],
                question_count=10,
                options=["A", "B", "C", "D"],
                roll_number_digits=6,
                set_codes=["A"],
            )
        cfg = create_test_config(
            title="Builder Exam",
            test_date="2026-11-15",
            class_name="10A",
            subjects=["Physics", "Mathematics"],
            question_count=20,
            options=["A", "B", "C", "D"],
            roll_number_digits=6,
            set_codes=["A", "B"],
            bonus_questions=[2],
            dropped_questions=[5],
            partial_credit={"3": 2.0},
            institute="Test Institute",
        )
        assert cfg["test"]["title"] == "Builder Exam"
        assert cfg["test"]["subjects"] == ["Physics", "Mathematics"]
        assert len(cfg["questions"]) == 20

        # 2. ReferenceDataDialog: search, inline add, accessibility, count labels
        repo.add_reference_value("classes", "10A")
        repo.add_reference_value("classes", "11B")
        repo.add_reference_value("subjects", "Physics")
        repo.add_reference_value("subjects", "Chemistry")

        ref_dlg = ReferenceDataDialog(repo, window)
        ref_dlg.show()
        app.processEvents()
        for kind in ("classes", "subjects"):
            assert ref_dlg.searches[kind].isClearButtonEnabled()
            assert ref_dlg.searches[kind].accessibleName()
            assert ref_dlg.quick_inputs[kind].accessibleName()
            assert ref_dlg.quick_inputs[kind].isClearButtonEnabled()
            assert ref_dlg.lists[kind].accessibleName()

        # Add a new subject via inline input
        ref_dlg.quick_inputs["subjects"].setText("Biology")
        ref_dlg._add_from_inline("subjects")
        app.processEvents()
        assert "Biology" in repo.reference_values("subjects")
        assert ref_dlg._count_labels["subjects"].text() == "3"

        # Filter subjects in ReferenceDataDialog
        ref_dlg.searches["subjects"].setText("bio")
        app.processEvents()
        assert ref_dlg.lists["subjects"].count() == 1
        assert ref_dlg.lists["subjects"].item(0).text() == "Biology"
        assert ref_dlg._count_labels["subjects"].text() == "1 of 3"
        ref_dlg.searches["subjects"].clear()
        app.processEvents()
        assert ref_dlg.lists["subjects"].count() == 3
        ref_dlg.close()

        # 3. NewTestDialog: templates, subject filter, Select all / Clear subjects, capacity hint
        new_dlg = NewTestDialog(
            window,
            classes=repo.reference_values("classes"),
            subjects=repo.reference_values("subjects"),
        )
        new_dlg.show()
        app.processEvents()
        assert new_dlg.title.isClearButtonEnabled()
        assert new_dlg.subject_search.isClearButtonEnabled()
        assert new_dlg.subject_list.accessibleName()
        assert new_dlg.capacity_hint.text()
        for btn in new_dlg.template_buttons.values():
            assert btn.accessibleName()

        # Test subject_search filter and Select all / Clear buttons
        new_dlg._set_all_subjects(False)
        assert new_dlg.selected_subjects() == []
        new_dlg.subject_search.setText("phys")
        app.processEvents()
        new_dlg._set_all_subjects(True)
        assert new_dlg.selected_subjects() == ["Physics"]
        new_dlg.subject_search.clear()
        app.processEvents()

        # Trigger empty validation
        new_dlg.title.clear()
        new_dlg.class_name.setText("")
        new_dlg.attempt_accept()
        assert not new_dlg.form_error.isHidden()
        assert new_dlg.title.property("invalid") == "true"

        # Fill valid fields and click a class suggestion chip
        new_dlg.title.setText("Midterm Science")
        chips = new_dlg.class_name.findChildren(QPushButton, "chip")
        assert chips
        QTest.mouseClick(chips[0], Qt.LeftButton)
        app.processEvents()
        assert new_dlg.class_name.text() == "10A"

        # Check Physics and Chemistry
        for i in range(new_dlg.subject_list.count()):
            item = new_dlg.subject_list.item(i)
            item.setCheckState(Qt.Checked if item.text() in ("Physics", "Chemistry") else Qt.Unchecked)

        # Toggle customize section and configure 10 questions
        new_dlg.customize_toggle.setChecked(True)
        app.processEvents()
        assert new_dlg.customize.isVisible()
        new_dlg.question_count.setValue(10)
        new_dlg.use_sets.setChecked(True)
        new_dlg.attempt_accept()
        created_cfg = new_dlg.result_config
        assert created_cfg["test"]["title"] == "Midterm Science"
        assert len(created_cfg["questions"]) == 10
        new_dlg.close()

        # 4. AnswerKeyDialog & SetSubjectsDialog: filter, jump missing, ExcelFillTable, quick entry
        ak_dlg = AnswerKeyDialog(created_cfg, window, available_subjects=repo.reference_values("subjects"))
        ak_dlg.show()
        app.processEvents()
        assert ak_dlg.table.accessibleName()
        assert ak_dlg.quick.accessibleName()
        assert ak_dlg.quick.isClearButtonEnabled()
        assert ak_dlg.table_search.isClearButtonEnabled()

        # Test quick_count live counter
        ak_dlg.quick.setText("ABCD")
        app.processEvents()
        assert "4 / 10" in ak_dlg.quick_count.text()

        # Invalid quick key triggers live error
        ak_dlg.quick.setText("INVALID_KEY")
        ak_dlg.apply_quick()
        assert not ak_dlg.quick_error.isHidden()

        # Valid quick key fills Set A and advances picker to Set B
        ak_dlg.quick.setText("ABCDABCDAB")
        ak_dlg.apply_quick()
        assert ak_dlg.set_picker.current() == "B"

        # Test AnswerKeyDialog table_filter ("missing" vs "filled") & jump_to_next_missing
        ak_dlg.table_filter.set_current("missing")
        ak_dlg._apply_table_filter()
        # Set B is still empty, so rows are missing
        assert not ak_dlg.table.isRowHidden(0)
        ak_dlg.jump_to_next_missing()
        assert ak_dlg.table.currentColumn() >= 3

        ak_dlg.quick.setText("BCDABCDABC")
        ak_dlg.apply_quick()
        assert ak_dlg._complete
        ak_dlg.table_filter.set_current("all")
        ak_dlg._apply_table_filter()

        # Test ExcelFillTable fill-down and Delete clear on Topic column (column 1)
        ak_dlg.table.clearSelection()
        ak_dlg.table.item(0, 1).setText("Kinematics")
        ak_dlg.table.setRangeSelected(QTableWidgetSelectionRange(0, 1, 2, 1), True)
        ak_dlg.table.fill_down_selection()
        assert ak_dlg.table.item(1, 1).text() == "Kinematics"
        assert ak_dlg.table.item(2, 1).text() == "Kinematics"
        QTest.keyClick(ak_dlg.table, Qt.Key_Delete)
        assert ak_dlg.table.item(1, 1).text() == ""

        # Test SetSubjectsDialog: search filter, split_evenly, range assignment
        sub_dlg = SetSubjectsDialog(
            ak_dlg.available_subjects,
            ak_dlg.codes,
            [int(q["id"]) for q in ak_dlg.questions],
            ak_dlg._set_subjects,
            ak_dlg,
        )
        sub_dlg.show()
        app.processEvents()
        assert sub_dlg.search_input.isClearButtonEnabled()
        assert sub_dlg.summary_label.text()
        sub_dlg._split_evenly()
        sub_dlg.search_input.setText("Q10")
        app.processEvents()
        assert sub_dlg.table.isRowHidden(0)
        assert not sub_dlg.table.isRowHidden(9)
        sub_dlg.search_input.clear()
        app.processEvents()

        sub_dlg.subject_combo.setCurrentText("Chemistry")
        sub_dlg.from_q.setValue(6)
        sub_dlg.to_q.setValue(10)
        sub_dlg._apply_range(all_sets=True)
        assert sub_dlg.set_subjects["A"]["6"] == "Chemistry"
        assert sub_dlg.set_subjects["B"]["10"] == "Chemistry"
        sub_dlg.accept()
        ak_dlg._set_subjects = sub_dlg.set_subjects
        ak_dlg.attempt_accept()
        final_cfg = ak_dlg.result_config
        assert final_cfg["sets"]["A"]["answer_key"]["1"] == "A"
        assert final_cfg["sets"]["B"]["answer_key"]["1"] == "B"
        ak_dlg.close()

        # 5. StudentDialog: validation, error auto-clearing, and save
        stu_dlg = StudentDialog(window, classes=["10A", "11B"])
        stu_dlg.show()
        app.processEvents()
        assert stu_dlg.roll.isClearButtonEnabled()
        assert stu_dlg.name.isClearButtonEnabled()
        stu_dlg._attempt()
        assert not stu_dlg.error.isHidden()
        assert stu_dlg.roll.property("invalid") == "true"
        stu_dlg.roll.setText("000001")
        assert stu_dlg.roll.property("invalid") == "false"
        stu_dlg.name.setText("Asha Verma")
        assert stu_dlg.error.isHidden()
        stu_dlg.class_name.setText("10A")
        stu_dlg._attempt()
        assert stu_dlg.values() == ("000001", "Asha Verma", "10A")
        stu_dlg.close()
    finally:
        window.close()


def test_full_omr_scan_processing_review_workflow_and_all_exports(tmp_path: Path, monkeypatch):
    app, window = _app_and_window(tmp_path, monkeypatch)
    repo = window.repo
    try:
        # Seed student roster
        repo.save_student("000001", "Asha Verma", "10A")
        repo.save_student("000002", "Bala Rao", "10A")
        repo.save_student("000003", "Chitra Nair", "10B")
        window.refresh_students()

        # Test student filtering on Students tab
        window.show_page("students")
        window.student_class_filter.setCurrentText("10B")
        app.processEvents()
        assert window.students_table.rowCount() == 1
        window.student_class_filter.setCurrentIndex(0)
        window.student_search.setText("Bala")
        app.processEvents()
        assert window.students_table.rowCount() == 1
        window.student_search.clear()
        app.processEvents()
        assert window.students_table.rowCount() == 3

        # Sheet 1 (000001, Set A): all 10 answers match Set A key ("ABCDABCDAB") -> 40/40, no review
        key_a = {str(i): "ABCDABCDAB"[i - 1] for i in range(1, 11)}
        # Sheet 2 (000002, Set B): Q1-Q8 match Set B key ("BCDABCDABC"), Q9 blank, Q10 wrong -> clean read
        key_b = {str(i): "BCDABCDABC"[i - 1] for i in range(1, 11)}
        ans_2 = {str(i): key_b[str(i)] for i in range(1, 9)}
        ans_2["10"] = "A"  # Set B Q10 key is C, so A is wrong
        # Sheet 3 (000003, Set A): Q2 has a double mark (B and D) -> flagged for review!
        ans_3 = dict(key_a)
        extra_3 = [("answer", "2", "D")]

        config, scan_paths = _build_scan_fixture(
            tmp_path,
            [
                ("000001", "A", key_a, []),
                ("000002", "B", ans_2, []),
                ("000003", "A", ans_3, extra_3),
            ],
            set_codes=["A", "B"],
            subjects=["Physics", "Chemistry"],
            question_count=10,
        )
        test_id = repo.save_test(config)
        window.refresh_tests(test_id)

        # Test tests_filter on Tests tab (complete vs incomplete)
        window.show_page("tests")
        window.tests_filter.setCurrentIndex(window.tests_filter.findData("complete"))
        app.processEvents()
        assert window.tests_table.rowCount() == 1
        window.tests_filter.setCurrentIndex(window.tests_filter.findData("incomplete"))
        app.processEvents()
        assert window.tests_table.rowCount() == 0
        window.tests_filter.setCurrentIndex(0)
        app.processEvents()

        # Verify ScanProgress.percent helper
        prog = ScanProgress(processed=2, total=4, needs_review=0, errors=0)
        assert prog.percent == 50.0

        # Verify scan folder helper & page counter
        found_files = scan_files_in_folder(scan_paths[0].parent)
        assert len(found_files) == 3
        assert count_scan_pages(scan_paths[0]) == 1

        # Stage scans in UI and process batch
        window.show_page("scan")
        window.add_scan_paths([str(p) for p in scan_paths])
        assert len(window.staged_scans) == 3
        outcome = window._process_scan_files([str(p) for p in scan_paths])
        app.processEvents()

        assert outcome.status == "complete"
        assert outcome.processed == 3
        assert outcome.needs_review == 1
        assert window.review_badge.isVisibleTo(window)
        assert window.review_badge.text() == "1"

        # Check BatchPickerDialog search filter & open
        picker = BatchPickerDialog(window, repo.batches(), repo.pending_review_counts(), window.batch_id)
        picker.show()
        app.processEvents()
        assert picker.table.rowCount() == 1
        picker.search.setText("nonexistent_batch_xyz")
        app.processEvents()
        assert not picker.open_button.isEnabled()
        picker.search.clear()
        app.processEvents()
        assert picker.open_button.isEnabled()
        picker._open()
        assert picker.selected is not None and int(picker.selected["id"]) == window.batch_id
        picker.close()

        # Check BatchLogDialog with both empty and non-empty events
        sample_events = [
            {"logged_at": "2026-11-15T09:00:00", "level": "warning", "source_file": "001-sheet_003.png", "message": "Q2 has multiple marks"},
            {"logged_at": "2026-11-15T09:01:00", "level": "error", "source_file": "002-bad.png", "message": "Corner marks not found"},
        ]
        log_dlg = BatchLogDialog(
            window,
            window.batch_id,
            repo.batch_status(window.batch_id),
            sample_events,
        )
        log_dlg.show()
        app.processEvents()
        assert log_dlg.log_table.rowCount() == 2
        log_dlg.level_filter.set_current("error")
        log_dlg._filter_log()
        assert log_dlg.log_table.isRowHidden(0)
        assert not log_dlg.log_table.isRowHidden(1)
        log_dlg.level_filter.set_current("all")
        log_dlg.search.setText("multiple")
        app.processEvents()
        assert not log_dlg.log_table.isRowHidden(0)
        assert log_dlg.log_table.isRowHidden(1)
        log_dlg._copy()
        assert log_dlg.copy_button.text() == "Copied"
        log_dlg.close()

        # Official exports must be blocked while 1 sheet still needs review
        with pytest.raises(ValueError, match="Official exports require resolved scans"):
            export_all(window.config, window.result_records, tmp_path / "blocked_exports")

        # Navigate to Review page and inspect the flagged sheet
        window.show_page("review")
        app.processEvents()
        review = window.review_page
        assert review.pending_count() == 1

        # Filter toggle between "flagged" and "all" + queue_search
        assert review.filter.current() == "flagged"
        visible_flagged = sum(not review.queue.item(r).isHidden() for r in range(review.queue.count()))
        assert visible_flagged == 1
        review.filter.set_current("all")
        review.apply_filter()
        visible_all = sum(not review.queue.item(r).isHidden() for r in range(review.queue.count()))
        assert visible_all == 3
        review.queue_search.setText("Chitra")
        app.processEvents()
        visible_searched = sum(not review.queue.item(r).isHidden() for r in range(review.queue.count()))
        assert visible_searched == 1
        review.queue_search.clear()
        app.processEvents()

        # Select the flagged sheet (Sheet 3, roll 000003)
        flagged_row = next(i for i, r in enumerate(window.result_records) if r["review_required"])
        review.select_row(flagged_row)
        app.processEvents()
        assert "2" in review._cards
        card_q2 = review._cards["2"]

        # Test inspector answer_filter ("all", "flagged", "blank") and question_search
        assert review.question_search.accessibleName()
        assert review.question_search.isClearButtonEnabled()
        review.answer_filter.set_current("flagged")
        review._filter_answer_chips()
        assert not review._chips["2"].isHidden()
        assert review._chips["1"].isHidden()
        review.answer_filter.set_current("all")
        review._filter_answer_chips()
        review.question_search.setText("Q2")
        app.processEvents()
        assert not review._chips["2"].isHidden()
        assert review._chips["1"].isHidden()
        review.question_search.clear()
        app.processEvents()

        # Test _AnswerChipButton direct keyboard toggling and clearing
        chip_q2 = review._chips["2"]
        chip_q2.setFocus()
        QTest.keyClick(chip_q2, Qt.Key_B)
        app.processEvents()
        assert review.table.item(flagged_row, FIRST_ANSWER_COLUMN + 1).text() == "B"
        # Pressing B again on already-selected B toggles it to Blank ("")
        QTest.keyClick(chip_q2, Qt.Key_B)
        app.processEvents()
        assert review.table.item(flagged_row, FIRST_ANSWER_COLUMN + 1).text() == ""

        # Resolve Q2 via numeric key '2' (2nd option = 'B') on IssueCard
        card_q2.setFocus()
        QTest.keyClick(card_q2, Qt.Key_2)
        app.processEvents()
        assert card_q2.value == "B"
        assert review.table.item(flagged_row, FIRST_ANSWER_COLUMN + 1).text() == "B"

        # Test SheetViewer zoom in, zoom out, fit
        initial_zoom = review.viewer.zoom()
        review.viewer.zoom_by(1.25)
        assert review.viewer.zoom() > initial_zoom
        review.viewer.zoom_by(1 / 1.25)
        review._fit_viewer()
        assert review.viewer._fit_mode

        # Switch to Grid view, test grid_search, grid_filter, and grid_viewer zoom toolbar
        review.mode.set_current("grid")
        review._set_mode("grid")
        app.processEvents()
        assert review.stack.currentIndex() == 2
        review.table.setCurrentCell(flagged_row, FIRST_ANSWER_COLUMN + 1)
        app.processEvents()
        assert review.grid_viewer.has_image()
        assert "Q2" in review.grid_cell_label.text()
        review.grid_viewer.zoom_by(1.25)
        assert "%" in review.grid_zoom_label.text()
        review._fit_grid_viewer()

        review.grid_search.setText("000001")
        app.processEvents()
        assert not review.table.isRowHidden(0)
        review.grid_search.clear()
        app.processEvents()

        review.mode.set_current("sheet")
        review._set_mode("sheet")
        app.processEvents()
        assert review.stack.currentIndex() == 1

        # Confirm the resolved sheet
        review.confirm_current()
        app.processEvents()
        assert review.pending_count() == 0
        assert not window.review_badge.isVisibleTo(window)

        # Navigate to Results page and verify statistics, search, filter, sort, and row clicking
        window.show_page("results")
        app.processEvents()
        assert window.performance_table.rowCount() == 3
        assert window.result_stats["pending"].text() == "0"
        assert window.result_stats["highest"].text() == "40"

        # Click each row in performance_table and verify detail panel updates
        for r_idx in range(3):
            rect = window.performance_table.visualRect(window.performance_table.model().index(r_idx, 1))
            QTest.mouseClick(window.performance_table.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
            app.processEvents()
            expected_name = window.performance_table.item(r_idx, 2).text()
            assert window.student_title.text() == expected_name
            assert window.question_performance_table.rowCount() == 10

        # Select student 000002 (has 8 correct, 1 blank Q9, 1 wrong Q10) and test question_outcome_filter
        for r_idx in range(window.performance_table.rowCount()):
            if window.performance_table.item(r_idx, 1).text() == "000002":
                window.performance_table.selectRow(r_idx)
                break
        app.processEvents()
        window.question_outcome_filter.setCurrentIndex(window.question_outcome_filter.findData("blank"))
        app.processEvents()
        visible_q_blank = sum(
            not window.question_performance_table.isRowHidden(r)
            for r in range(window.question_performance_table.rowCount())
        )
        assert visible_q_blank == 1
        window.question_outcome_filter.setCurrentIndex(window.question_outcome_filter.findData("incorrect"))
        app.processEvents()
        visible_q_wrong = sum(
            not window.question_performance_table.isRowHidden(r)
            for r in range(window.question_performance_table.rowCount())
        )
        assert visible_q_wrong == 1
        window.question_outcome_filter.setCurrentIndex(0)
        app.processEvents()

        # Test ExportDialog interaction (search filter, Select all / Clear / Export selected)
        exp_dlg = ExportDialog(window)
        exp_dlg.show()
        app.processEvents()
        assert exp_dlg.search.isClearButtonEnabled()
        assert exp_dlg.summary_label.text()
        exp_dlg.search.setText("Excel")
        app.processEvents()
        assert not exp_dlg._row_frames["xlsx"].isHidden()
        assert exp_dlg._row_frames["csv"].isHidden()
        exp_dlg.search.clear()
        app.processEvents()
        exp_dlg._set_all(False)
        assert not exp_dlg.save_all.isEnabled()
        exp_dlg._set_all(True)
        assert exp_dlg.save_all.isEnabled()
        exp_dlg._many()
        assert exp_dlg.action == ("many", list(EXPORTS))
        exp_dlg.close()

        # Verify all 13 export formats via export_all
        export_dir = tmp_path / "all_exports"
        written_paths = export_all(window.config, window.result_records, export_dir)
        assert len(written_paths) == len(EXPORTS)
        for path in written_paths:
            assert path.is_file() and path.stat().st_size > 0

        # Verify CSV marklist contents
        csv_path = export_dir / EXPORTS["csv"][2]
        with csv_path.open("r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 3
        assert {r["roll_number"] for r in rows} == {"000001", "000002", "000003"}

        # Verify analytical report functions directly
        c_res = class_results(window.config, window.result_records)
        assert len(c_res) == 3
        stats = summary_stats(window.config, window.result_records, rows=c_res)
        assert stats["students"] == 3 and stats["maximum"] == 40.0
        assert len(item_analysis(window.config, window.result_records, rows=c_res)) == 10
        assert len(distractor_analysis(window.config, window.result_records, rows=c_res)) == 10
        assert len(topic_analysis(window.config, window.result_records)) >= 1
        assert len(subject_analysis(window.config, window.result_records)) == 2
        assert len(subject_summary(window.config, window.result_records, rows=c_res)) == 2
        assert len(class_summary(window.config, window.result_records)) >= 1
        assert len(set_summary(window.config, window.result_records)) == 2
        assert sum(count for _band, count in score_bands(window.config, window.result_records)) == 3
        assert subject_max_scores(window.config, "A") == {"Physics": 20.0, "Chemistry": 20.0}
        assert max_score(window.config) == 40.0
    finally:
        window.close()
