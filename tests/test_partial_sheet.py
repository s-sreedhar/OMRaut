"""A fixed pre-printed sheet (e.g. 160 rows) used for tests that need fewer questions."""
import numpy as np
import pytest

from omr_exam.config import validate_config
from omr_exam.layout import create_layout
from omr_exam.reader import read_image
from omr_exam.scoring import score_answers
from omr_exam.test_builder import create_test_config


def _config(active=100, printed=160):
    config = create_test_config(title="Partial", test_date="2026-11-10", class_name="QA", subjects=["Physics", "Chemistry"],
                                question_count=active, sheet_questions=printed, options=list("ABCD"),
                                roll_number_digits=6, set_codes=["A"], columns=4, correct_points=4, incorrect_points=-1)
    config["sets"]["A"]["answer_key"] = {str(q["id"]): "A" for q in config["questions"]}
    return config


def test_geometry_is_identical_to_full_sheet():
    full, partial = create_layout(_config(160, 160)), create_layout(_config(100, 160))
    assert full.questions_per_column == partial.questions_per_column == 40
    assert full.bubbles == partial.bubbles
    assert len(_config(100)["questions"]) == 100


def test_cannot_use_more_than_printed():
    with pytest.raises(ValueError):
        _config(170, 160)


def test_scoring_covers_only_active_questions():
    config = _config(100)
    result = score_answers(config, "A", {str(i): "A" for i in range(1, 101)})
    assert (result["correct"], result["incorrect"], result["blank"], result["total"]) == (100, 0, 0, 400)
    assert len(result["questions"]) == 100


def test_reader_ignores_unused_rows(tmp_path):
    cv2 = pytest.importorskip("cv2")
    fitz = pytest.importorskip("fitz")
    from omr_exam.sheets import generate_sheet_pdf
    config = _config(100)
    document = fitz.open(generate_sheet_pdf(config, tmp_path / "s.pdf"))
    pix = document[0].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    document.close()
    layout = create_layout(config)
    sx, sy = image.shape[1] / layout.width, image.shape[0] / layout.height
    for b in layout.bubbles:
        fill = ((b.group == "answer" and b.value == "A" and int(b.index) <= 100)
                or (b.group == "roll" and b.value == "1")
                or (b.group == "answer" and int(b.index) > 100 and b.value == "B"))   # stray marks on unused rows
        if fill:
            cv2.circle(image, (round(b.x * sx), round(b.y * sy)), round(1.05 * min(sx, sy)), 0, -1)
    result = read_image(image, config)
    assert set(result.answers) == {str(i) for i in range(1, 101)}
    assert all(v == "A" for v in result.answers.values())
    assert not [f for f in result.flags if f.get("group") == "answer" or f["kind"] == "blank"]
    assert result.roll_number == "111111"
    scored = score_answers(config, "A", result.answers)
    assert scored["total"] == 400


def test_scores_are_rounded_without_float_noise():
    config = create_test_config(title="R", test_date="2026-11-10", class_name="QA", subject="S", question_count=100,
                                options=list("ABCD"), roll_number_digits=6, set_codes=["A"], columns=4,
                                correct_points=2, incorrect_points=-0.66)
    config["sets"]["A"]["answer_key"] = {str(i): "A" for i in range(1, 101)}
    answers = {str(i): ("A" if i <= 37 else "B") for i in range(1, 101)}
    total = score_answers(config, "A", answers)["total"]
    assert total == round(37 * 2 - 63 * 0.66, 2) == 32.42
    assert repr(total) == "32.42"
