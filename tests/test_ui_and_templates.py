"""Template, quick-key, roster, and light-theme UI tests."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from omr_exam.answer_keys import parse_compact_key
from omr_exam.database import Repository
from omr_exam.layout import create_layout
from omr_exam.templates import TEMPLATES, build_from_template


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.key)
def test_every_template_builds_and_fits_the_sheet(template):
    config = build_from_template(template, title="T", class_name="C", subject="S")
    assert len(config["questions"]) == template.question_count
    assert create_layout(config).bubbles  # layout validates capacity


def _config(count=6):
    return build_from_template(TEMPLATES[0], title="T", class_name="C", subject="S", question_count=count)


def test_compact_key_formats_and_errors():
    config = _config()
    expected = {"1": "A", "2": "B", "3": "C", "4": "D", "5": "A", "6": "B"}
    assert parse_compact_key("ABCDAB", config) == expected
    assert parse_compact_key("a b c d a b", config) == expected
    assert parse_compact_key("1-A 2-B 3-C 4-D 5-A 6-B", config) == expected
    with pytest.raises(ValueError, match="6 questions"):
        parse_compact_key("ABC", config)
    with pytest.raises(ValueError, match="Not a valid option"):
        parse_compact_key("ABCDAZ", config)


def test_student_crud(tmp_path):
    repo = Repository(tmp_path / "db.sqlite3")
    repo.save_student("000001", "Asha", "10A")
    repo.save_student("000001", "Asha K", "10A")
    repo.save_student("000002", "Ravi")
    assert [s["name"] for s in repo.students()] == ["Asha K", "Ravi"]
    repo.delete_student("000001")
    assert [s["roll_number"] for s in repo.students()] == ["000002"]
    with pytest.raises(ValueError):
        repo.save_student("", "x")


def test_scan_drop_zone_accepts_local_files_and_respects_disabled_state():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QMimeData, QPoint, QPointF, QUrl, Qt
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtWidgets import QApplication
    from omr_exam.widgets import DropZone

    app = QApplication.instance() or QApplication([])
    zone = DropZone()
    received = []
    zone.dropped.connect(received.append)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("C:/scans/page 1.png"), QUrl("https://example.com/not-local")])
    enter = QDragEnterEvent(QPoint(4, 4), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    app.sendEvent(zone, enter)
    assert enter.isAccepted()
    drop = QDropEvent(QPointF(4, 4), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    app.sendEvent(zone, drop)
    assert drop.isAccepted()
    assert received == [["C:/scans/page 1.png"]]

    zone.set_active_look(False)
    rejected = QDragEnterEvent(QPoint(4, 4), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    app.sendEvent(zone, rejected)
    assert not rejected.isAccepted()
    zone.close()


def test_new_test_and_answer_key_dialogs_support_single_paper_mode():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from omr_exam.dialogs import AnswerKeyDialog, NewTestDialog

    app = QApplication.instance() or QApplication([])
    dialog = NewTestDialog()
    dialog.title.setText("Single paper")
    dialog.class_name.setText("QA")
    dialog.subject.setText("Science")
    dialog.apply_template(TEMPLATES[0])
    dialog.use_sets.setChecked(False)
    assert not dialog.add_set_button.isEnabled()
    assert "one paper" in dialog.summary.text()
    dialog.attempt_accept()
    config = dialog.result_config
    assert config["sheet"]["set_codes"] == ["A"]
    assert list(config["sets"]) == ["A"]

    keys = AnswerKeyDialog(config)
    assert keys.single
    assert keys.table.columnCount() == 3
    assert keys.set_picker.isHidden()
    dialog.close()
    keys.close()


def test_new_test_set_codes_are_typed_and_final_set_cannot_be_removed(monkeypatch):
    pytest.importorskip("PySide6")
    from omr_exam import dialogs
    from omr_exam.dialogs import NewTestDialog

    dialog = NewTestDialog(classes=["10A"], subjects=["Science"])
    dialog.title.setText("Quiz")
    dialog.class_name.setText("10A")
    dialog.subject.setText("Science")
    assert [b.text() for b in dialog.class_name.findChildren(type(dialog.add_set_button), "chip")] == ["10A"]
    assert [b.text() for b in dialog.subject.findChildren(type(dialog.add_set_button), "chip")] == ["Science"]
    assert [editor.text() for editor, _remove in dialog.set_editors] == ["A", "B"]
    dialog.add_set()
    assert [editor.text() for editor, _remove in dialog.set_editors] == ["A", "B", "C"]
    monkeypatch.setattr(dialogs, "confirm", lambda *_args, **_kwargs: True)
    dialog.remove_set(dialog.set_editors[1][0])
    assert [editor.text() for editor, _remove in dialog.set_editors] == ["A", "C"]
    dialog.remove_set(dialog.set_editors[1][0])
    assert len(dialog.set_editors) == 1
    assert not dialog.set_editors[0][1].isEnabled()
    dialog.use_sets.setChecked(False)
    assert dialog.config()["sheet"]["set_codes"] == ["A"]


def test_student_roster_json_import_is_validated_before_upsert(tmp_path):
    import json

    repo = Repository(tmp_path / "db.sqlite3")
    path = tmp_path / "students.json"
    path.write_text(json.dumps({"students": [
        {"roll_number": "000031", "name": "Asha", "class_name": "8A"},
        {"roll": "000032", "student_name": "Ravi", "class": "8A"},
    ]}), encoding="utf-8")
    assert repo.import_students_json(path) == 2
    assert repo.students() == [
        {"roll_number": "000031", "name": "Asha", "class_name": "8A"},
        {"roll_number": "000032", "name": "Ravi", "class_name": "8A"},
    ]
    path.write_text(json.dumps({"students": [
        {"roll_number": "000033", "name": "Meena"},
        {"roll_number": "000033", "name": "Duplicate"},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate roll number"):
        repo.import_students_json(path)
    assert [student["roll_number"] for student in repo.students()] == ["000031", "000032"]


def test_window_is_light_and_workflow_pages_work(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from omr_exam.dialogs import AnswerKeyDialog, NewTestDialog
    from omr_exam.theme import apply_light_theme
    from omr_exam.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    apply_light_theme(app)
    window = MainWindow(str(tmp_path / "omr.sqlite3"))
    assert window.palette().window().color().lightness() > 200
    for key in window.page_index:
        window.show_page(key)
    # Template dialog produces a valid config.
    dialog = NewTestDialog(window)
    dialog.title.setText("Unit"); dialog.class_name.setText("9A"); dialog.subject.setText("Math")
    dialog.apply_template(TEMPLATES[1])
    dialog.question_count.setValue(24)
    assert "24 questions" in dialog.summary.text()
    dialog.question_count.setValue(25)
    dialog.attempt_accept()
    config = dialog.result_config
    assert len(config["questions"]) == 25
    test_id = window.repo.save_test(config)
    window.refresh_tests(test_id)
    assert window.active_test_id == test_id
    assert "Incomplete" in window.tests_table.item(0, 5).text()
    assert not window.upload_button.isEnabled()  # scanning blocked until keys are complete
    # Fast key entry completes every set and enables scanning.
    keys = AnswerKeyDialog(window.config, window)
    keys.quick.setText("ABCDABCDABCDABCDABCDABCDA")
    for index in range(len(keys.codes)):
        keys.set_picker.set_current(keys.codes[index]); keys.apply_quick(); keys.quick.setText("ABCDABCDABCDABCDABCDABCDA")
    keys.attempt_accept()
    window.repo.update_test_config_and_rescore(test_id, keys.result_config)
    window.refresh_tests(test_id)
    assert window.upload_button.isEnabled()
    # Roster.
    window._save_student("000001", "Asha", "9A")
    assert window.students_table.rowCount() == 1
    window.close()


# ---------------------------------------------------------------- workflow, auto-save and recovery
def _scan_fixture(tmp_path, marks_for, set_codes=None):
    """Render the printable sheet and fill bubbles per scan; returns (config, [image paths])."""
    import cv2
    from omr_exam.ingest import scan_pages
    from omr_exam.sheets import generate_sheet_pdf

    config = build_from_template(TEMPLATES[0], title="Quiz", class_name="9A", subject="Science")
    if set_codes is not None:
        config = build_from_template(TEMPLATES[0], title="Quiz", class_name="9A", subject="Science",
                                     set_codes=set_codes)
    key = "ABCDABCDAB"
    config["sets"]["A"]["answer_key"] = {str(q["id"]): key[i] for i, q in enumerate(config["questions"])}
    base = list(scan_pages(generate_sheet_pdf(config, tmp_path / "sheet.pdf")))[0][1]
    layout = create_layout(config)
    sx, sy = base.shape[1] / layout.width, base.shape[0] / layout.height
    folder = tmp_path / "incoming"
    folder.mkdir()
    paths = []
    for number, extra in enumerate(marks_for, 1):
        image = base.copy()
        marks = [("roll", str(i), d) for i, d in enumerate(f"{number:06d}")]
        marks += [("answer", str(q["id"]), key[i]) for i, q in enumerate(config["questions"])] + list(extra)
        for group, index, value in marks:
            bubble = next(b for b in layout.bubbles if b.group == group and b.index == index and b.value == value)
            cv2.circle(image, (round(bubble.x * sx), round(bubble.y * sy)), round(1.05 * min(sx, sy)), 0, -1)
        path = folder / f"scan-{number:03d}.png"
        cv2.imwrite(str(path), image)
        paths.append(path)
    return config, paths


def _window(tmp_path):
    from PySide6.QtWidgets import QApplication
    from omr_exam.theme import apply_light_theme
    from omr_exam.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    apply_light_theme(app)
    return app, MainWindow(str(tmp_path / "data" / "omr.sqlite3"))


def test_silent_update_check_notifies_without_opening_install_prompt(tmp_path, monkeypatch):
    import omr_exam.ui as ui

    _app, window = _window(tmp_path)
    window.show()
    monkeypatch.setattr(ui, "confirm", lambda *_args, **_kwargs: pytest.fail("automatic check must not prompt"))
    window._update_check_finished((object(), object()), automatic=True)

    assert window.update_notice.isVisible()
    assert "Open Settings" in window.update_notice._action.text()
    assert window.update_button.text() == "Update available"
    assert window.update_button.isEnabled()
    window.close()


def test_many_files_and_folders_queue_then_read_in_the_background(tmp_path):
    import time

    config, paths = _scan_fixture(tmp_path, [(), (), ()])
    app, window = _window(tmp_path)
    window.refresh_tests(window.repo.save_test(config))
    window.show_page("scan")
    # A folder plus an overlapping file and an unsupported file: no duplicates, junk skipped.
    (tmp_path / "notes.txt").write_text("not a scan", encoding="utf-8")
    window.add_scan_paths([str(paths[0].parent), str(paths[1]), str(tmp_path / "notes.txt")])
    assert [path.name for path, _pages in window.staged_scans] == ["scan-001.png", "scan-002.png", "scan-003.png"]
    assert "3 pages" in window.start_scan_button.text()
    window._remove_staged(paths[2])
    assert len(window.staged_scans) == 2
    window.start_scan()
    assert not window.nav_buttons["tests"].isEnabled()      # navigation locked while reading
    deadline = time.monotonic() + 60
    while window._scan_worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert window._scan_worker is None and window.nav_buttons["tests"].isEnabled()
    assert window.repo.batch_status(window.batch_id)["status"] == "complete"
    assert len(window.result_records) == 2 and not window.review_page.pending_count()
    window.show_page("results")
    assert window.performance_table.rowCount() == 2
    assert sum(count for _low, _high, count in window.histogram.bins()) == 2
    window.close()


def test_review_corrections_are_drafted_restored_and_confirmed(tmp_path):
    config, paths = _scan_fixture(tmp_path, [(), (("answer", "3", "A"),)])   # sheet 2: Q3 has two marks
    app, window = _window(tmp_path)
    window.refresh_tests(window.repo.save_test(config))
    window._process_scan_files(paths)
    page = window.review_page
    assert page.pending_count() == 1
    row = next(i for i, record in enumerate(window.result_records) if record["review_required"])
    result_id = int(window.result_records[row]["id"])
    page.select_row(row)
    assert list(page._cards) == ["3"]                        # one uncertain mark to decide
    page._cards["3"]._choose("C")
    assert window.repo.review_drafts(window.batch_id)[result_id]["answers"]["3"] == "C"   # saved without asking
    assert window.repo.results_for_batch(window.batch_id)[row]["answers"]["3"] is None    # official result untouched
    window.close()

    # A new session (after a restart or crash) reopens the batch with the correction in place.
    _app, reopened = _window(tmp_path)
    reopened.show_page("review")
    page = reopened.review_page
    assert reopened.batch_id is not None and page.has_unsaved()
    row = next(i for i, record in enumerate(reopened.result_records) if int(record["id"]) == result_id)
    assert page.table.item(row, 6 + 2).text() == "C"
    page.select_row(row)
    page.confirm_current()
    saved = next(r for r in reopened.repo.results_for_batch(reopened.batch_id) if int(r["id"]) == result_id)
    assert saved["answers"]["3"] == "C" and not saved["review_required"] and saved["score"] == 10
    assert not reopened.repo.review_drafts(reopened.batch_id) and not page.has_unsaved()
    assert not reopened.review_badge.isVisibleTo(reopened)
    reopened.close()


def test_single_paper_scan_opens_in_review_without_set_mark_issue(tmp_path):
    config, paths = _scan_fixture(tmp_path, [(('answer', '3', 'A'),)], set_codes=["A"])
    app, window = _window(tmp_path)
    window.refresh_tests(window.repo.save_test(config))
    window._process_scan_files(paths)

    assert window.review_page.pending_count() == 1
    record = window.result_records[0]
    assert record["set_code"] == "A"
    assert not any(flag["kind"] == "invalid_set" for flag in record["flags"])
    window.show_page("review")
    assert list(window.review_page._cards) == ["3"]
    window.close()


def test_interrupted_batch_is_closed_and_daily_safety_copy_is_made(tmp_path):
    config, _paths = _scan_fixture(tmp_path, [])
    _app, window = _window(tmp_path)
    test_id = window.repo.save_test(config)
    batch_id = window.repo.create_batch(test_id, "crashed", expected_pages=4)   # left 'processing' by a crash
    window.run_startup_maintenance()
    assert window.repo.batch_status(batch_id)["status"] == "incomplete"
    assert any("closed while" in event["message"] for event in window.repo.batch_log(batch_id))
    assert len(list((tmp_path / "data" / "auto-backups").glob("omr-exam-*.sqlite3"))) == 1
    window.close()


def test_institute_name_and_logo_print_without_disturbing_the_reader(tmp_path):
    import cv2
    import numpy as np
    from omr_exam.ingest import scan_pages
    from omr_exam.reader import read_image
    from omr_exam.sheets import generate_sheet_pdf

    config = build_from_template(TEMPLATES[1], title="Term 1 Exam", class_name="9A", subject="Science",
                                 institute="Greenfield Public School of Excellence and Learning")
    assert config["test"]["institute"].startswith("Greenfield")
    logo = tmp_path / "logo.png"
    cv2.imwrite(str(logo), np.zeros((120, 160, 3), np.uint8))          # a solid black block: worst case for the reader
    plain = list(scan_pages(generate_sheet_pdf(config, tmp_path / "plain.pdf")))[0][1]
    branded = list(scan_pages(generate_sheet_pdf(config, tmp_path / "branded.pdf", logo=logo)))[0][1].copy()
    assert (branded != plain).any()                                    # something was printed in the header
    layout = create_layout(config)
    sx, sy = branded.shape[1] / layout.width, branded.shape[0] / layout.height
    for group, index, value in [("roll", str(i), d) for i, d in enumerate("000123")] + [("set", "0", "B")]:
        bubble = next(b for b in layout.bubbles if (b.group, b.index, b.value) == (group, index, value))
        cv2.circle(branded, (round(bubble.x * sx), round(bubble.y * sy)), round(1.05 * min(sx, sy)), 0, -1)
    result = read_image(branded, config)
    assert result.roll_number == "000123" and result.set_code == "B" and not result.flags[:0]
    assert not any(flag["kind"] in ("unreadable", "invalid_id", "invalid_set") for flag in result.flags)


def test_answer_key_cells_are_typed_not_picked():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit
    from omr_exam.dialogs import AnswerKeyDialog

    app = QApplication.instance() or QApplication([])
    config = build_from_template(TEMPLATES[0], title="Typed", class_name="A", subject="B")
    keys = AnswerKeyDialog(config)
    keys.show()
    keys.table.setCurrentCell(0, 2)
    keys.table.edit(keys.table.model().index(0, 2))
    editor = keys.table.findChild(QLineEdit, "cellEditor")
    assert editor is not None and not keys.table.findChildren(QComboBox)
    editor.textEdited.emit("c")             # a complete answer is saved at once and the cursor moves down
    app.processEvents()
    assert keys.table.item(0, 2).text() == "C"
    assert keys.table.currentRow() == 1
    assert not keys.findChildren(QComboBox)
    keys._changed = False
    keys.close()
