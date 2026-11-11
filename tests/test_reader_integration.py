"""Synthetic generator-to-reader integration; real scan accuracy needs real fixtures too."""
import pytest

cv2 = pytest.importorskip("cv2")
fitz = pytest.importorskip("fitz")
import numpy as np

from omr_exam.layout import create_layout
from omr_exam.ingest import count_scan_pages, scan_pages
from omr_exam.reader import read_image
from omr_exam.sheets import generate_sheet_pdf
from omr_exam.test_builder import create_test_config


def _filled_sheet(tmp_path):
    config = create_test_config(title="Reader QA", test_date="2026-11-10", class_name="QA", subject="Test",
                                question_count=3, options=["A", "B", "C", "D"], roll_number_digits=4,
                                set_codes=["A", "B"], columns=1)
    pdf_path = generate_sheet_pdf(config, tmp_path / "qa.pdf")
    document = fitz.open(pdf_path)
    pix = document[0].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    layout = create_layout(config)
    sx, sy = image.shape[1] / layout.width, image.shape[0] / layout.height

    def fill(group, index, value):
        bubble = next(b for b in layout.bubbles if b.group == group and b.index == str(index) and b.value == str(value))
        center = round(bubble.x * sx), round(bubble.y * sy)
        cv2.circle(image, center, round(1.05 * min(sx, sy)), 0, -1)

    fill("answer", 1, "B")
    fill("answer", 2, "A")
    fill("answer", 2, "C")
    for position, digit in enumerate("1234"):
        fill("roll", position, digit)
    fill("set", 0, "A")
    document.close()
    return config, image


def test_generator_reader_flags_double_marks_and_correct_answers(tmp_path):
    config, image = _filled_sheet(tmp_path)
    result = read_image(image, config)
    assert result.answers == {"1": "B", "2": None, "3": None}
    assert result.roll_number == "1234"
    assert result.set_code == "A"
    assert any(flag["kind"] == "multiple" and flag["question"] == "2" for flag in result.flags)
    assert any(flag["kind"] == "blank" and flag["question"] == "3" for flag in result.flags)


def test_single_paper_reader_skips_hidden_set_bubbles(tmp_path):
    config = create_test_config(title="Single paper", test_date="2026-11-10", class_name="QA", subject="Test",
                                question_count=3, options=["A", "B", "C", "D"], roll_number_digits=4,
                                set_codes=["A"], columns=1)
    path = generate_sheet_pdf(config, tmp_path / "single.pdf")
    image = next(iter(scan_pages(path)))[1]
    layout = create_layout(config)

    assert not any(bubble.group == "set" for bubble in layout.bubbles)
    result = read_image(image, config)
    assert result.set_code == "A"
    assert not any(flag["kind"] == "invalid_set" for flag in result.flags)


def test_generator_reader_corrects_180_degree_scan_rotation(tmp_path):
    config, image = _filled_sheet(tmp_path)
    result = read_image(np.rot90(image, 2).copy(), config)
    assert result.answers == {"1": "B", "2": None, "3": None}
    assert result.roll_number == "1234"
    assert result.set_code == "A"


@pytest.mark.parametrize("rotation", [cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE])
def test_generator_reader_corrects_quarter_turns(tmp_path, rotation):
    config, image = _filled_sheet(tmp_path)
    result = read_image(cv2.rotate(image, rotation), config)
    assert result.answers == {"1": "B", "2": None, "3": None}
    assert result.roll_number == "1234"
    assert result.set_code == "A"


def test_generator_reader_corrects_perspective_skew(tmp_path):
    config, image = _filled_sheet(tmp_path)
    height, width = image.shape[:2]
    source = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    target = np.float32([[38, 20], [width - 25, 50], [width - 10, height - 20], [15, height - 5]])
    skewed = cv2.warpPerspective(image, cv2.getPerspectiveTransform(source, target), (width, height), borderValue=(255, 255, 255))
    result = read_image(skewed, config)
    assert result.answers == {"1": "B", "2": None, "3": None}
    assert result.roll_number == "1234"
    assert result.set_code == "A"


def test_faint_partial_fill_is_flagged_for_review(tmp_path):
    config, image = _filled_sheet(tmp_path)
    layout = create_layout(config)
    sx, sy = image.shape[1] / layout.width, image.shape[0] / layout.height
    faint = next(b for b in layout.bubbles if b.group == "answer" and b.index == "3" and b.value == "D")
    cv2.circle(image, (round(faint.x * sx), round(faint.y * sy)), round(0.60 * min(sx, sy)), (195, 195, 195), -1)
    result = read_image(image, config)
    assert result.answers["3"] is None
    assert any(flag["kind"] == "faint" and flag.get("question") == "3" for flag in result.flags)


def test_multipage_pdf_ingestion_renders_a_readable_sheet(tmp_path):
    config = create_test_config(title="PDF QA", test_date="2026-11-10", class_name="QA", subject="Test",
                                question_count=3, options=["A", "B", "C", "D"], roll_number_digits=4,
                                set_codes=["A"], columns=1)
    path = generate_sheet_pdf(config, tmp_path / "blank.pdf", count=2)
    assert count_scan_pages(path) == 2
    pages = list(scan_pages(path))
    assert len(pages) == 2
    label, image = pages[0]
    assert "#page=1" in label and min(image.shape[:2]) >= 500
    assert image.ndim == 2
    result = read_image(image, config)
    assert not any(flag["kind"] == "unreadable" for flag in result.flags)
    color_label, color_image = next(iter(scan_pages(path, dpi=300, color_mode="color")))
    assert "#page=1" in color_label and color_image.ndim == 3
    assert color_image.shape[0] > image.shape[0]
