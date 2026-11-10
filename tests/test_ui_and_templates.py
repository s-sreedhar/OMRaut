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
        keys.set_picker.setCurrentIndex(index); keys.apply_quick(); keys.quick.setText("ABCDABCDABCDABCDABCDABCDA")
    keys.attempt_accept()
    window.repo.update_test_config_and_rescore(test_id, keys.result_config)
    window.refresh_tests(test_id)
    assert window.upload_button.isEnabled()
    # Roster.
    window._save_student("000001", "Asha", "9A")
    assert window.students_table.rowCount() == 1
    window.close()
