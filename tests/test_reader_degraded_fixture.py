"""Deterministic image-quality regression checks using the labeled synthetic scan."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from omr_exam.reader import read_image
from omr_exam.layout import create_layout
from omr_exam.answer_keys import import_master_sheet
from omr_exam.database import Repository
from omr_exam.scoring import score_answers

ROOT = Path(__file__).resolve().parents[1]


def _fixture():
    config = json.loads((ROOT / "examples" / "sample-test.json").read_text(encoding="utf-8"))
    with (ROOT / "tests" / "samples" / "truth.csv").open(newline="", encoding="utf-8-sig") as handle:
        truth = next(csv.DictReader(handle))
    image = cv2.imread(str(ROOT / "tests" / "samples" / truth["image"]), cv2.IMREAD_COLOR)
    assert image is not None
    return config, truth, image


def _assert_matches_truth(config, truth, image):
    expected = json.loads(truth["answers"])
    result = read_image(image, config)
    assert result.answers == expected
    assert result.roll_number == truth["roll_number"]
    assert result.set_code == truth["set_code"]
    assert not any(flag.get("kind") in {"unreadable", "invalid_id", "invalid_set"} for flag in result.flags)


def test_reader_survives_brightness_and_contrast_change():
    config, truth, image = _fixture()
    adjusted = cv2.convertScaleAbs(image, alpha=0.82, beta=25)
    _assert_matches_truth(config, truth, adjusted)


def test_reader_survives_jpeg_compression():
    config, truth, image = _fixture()
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 72])
    assert ok
    compressed = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    _assert_matches_truth(config, truth, compressed)


def test_reader_survives_moderate_sensor_noise():
    config, truth, image = _fixture()
    noise = np.random.default_rng(2026).normal(0, 6, image.shape).astype(np.int16)
    noisy = np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    _assert_matches_truth(config, truth, noisy)


def test_reader_survives_150_dpi_downscale():
    config, truth, image = _fixture()
    reduced = cv2.resize(image, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    _assert_matches_truth(config, truth, reduced)


def test_reader_survives_small_rotation():
    config, truth, image = _fixture()
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), 1.0, 1.0)
    tilted = cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    _assert_matches_truth(config, truth, tilted)


def _perspective_warp(image, normalized_corners):
    height, width = image.shape[:2]
    source = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    destination = np.float32([[x * width, y * height] for x, y in normalized_corners])
    transform = cv2.getPerspectiveTransform(source, destination)
    return cv2.warpPerspective(image, transform, (width, height), borderValue=(255, 255, 255))


def test_reader_recovers_mild_perspective_and_flags_more_severe_distortion():
    config, truth, image = _fixture()
    mild = _perspective_warp(image, [[.04, .02], [.96, .06], [.99, .97], [.02, .99]])
    _assert_matches_truth(config, truth, mild)

    # At stronger trapezoid distortion the current registration cannot reliably
    # recover this fixture. It must send the sheet to review instead of silently
    # accepting a potentially incorrect score.
    severe = _perspective_warp(image, [[.08, .03], [.92, .10], [.97, .92], [.05, .98]])
    result = read_image(severe, config)
    assert result.requires_review
    assert any(flag.get("kind") == "unreadable" for flag in result.flags)


def test_reader_survives_moderate_uneven_illumination():
    config, truth, image = _fixture()
    _height, width = image.shape[:2]
    gain = np.linspace(0.8, 1.15, width, dtype=np.float32)[None, :, None]
    adjusted = np.clip(image.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    _assert_matches_truth(config, truth, adjusted)


def test_reader_survives_a_moderate_soft_shadow():
    config, truth, image = _fixture()
    height, width = image.shape[:2]
    x = np.linspace(0, 1, width, dtype=np.float32)[None, :, None]
    y = np.linspace(0, 1, height, dtype=np.float32)[:, None, None]
    shadow = 1 - (50 / 255) * (1 - np.exp(-((x - 0.55) ** 2 + (y - 0.58) ** 2) / 0.08))
    adjusted = np.clip(image.astype(np.float32) * shadow, 0, 255).astype(np.uint8)
    _assert_matches_truth(config, truth, adjusted)


def test_reader_flags_a_severely_darkened_scan_as_unreadable():
    config, _truth, image = _fixture()
    height, width = image.shape[:2]
    x = np.linspace(0, 1, width, dtype=np.float32)[None, :, None]
    y = np.linspace(0, 1, height, dtype=np.float32)[:, None, None]
    shadow = 1 - (80 / 255) * (1 - np.exp(-((x - 0.55) ** 2 + (y - 0.58) ** 2) / 0.08))
    adjusted = np.clip(image.astype(np.float32) * shadow, 0, 255).astype(np.uint8)
    result = read_image(adjusted, config)
    assert result.requires_review
    assert any(flag.get("kind") == "unreadable" for flag in result.flags)


def test_master_sheet_import_does_not_require_a_student_roll_number():
    config, truth, image = _fixture()
    image = image.copy()
    layout = create_layout(config)
    sx, sy = image.shape[1] / layout.width, image.shape[0] / layout.height
    radius = round(1.25 * min(sx, sy))
    for bubble in layout.bubbles:
        if bubble.group == "roll":
            center = round(bubble.x * sx), round(bubble.y * sy)
            cv2.circle(image, center, radius, (255, 255, 255), -1)
    expected = json.loads(truth["answers"])
    assert import_master_sheet(image, config, truth["set_code"]) == expected


def test_master_sheet_import_rejects_wrong_set_and_ambiguous_answers():
    config, truth, image = _fixture()
    with pytest.raises(ValueError, match="Selected set"):
        import_master_sheet(image, config, "B")

    ambiguous = image.copy()
    layout = create_layout(config)
    sx, sy = image.shape[1] / layout.width, image.shape[0] / layout.height
    bubble = next(item for item in layout.bubbles
                  if item.group == "answer" and item.index == "1" and item.value == "A")
    center = round(bubble.x * sx), round(bubble.y * sy)
    cv2.circle(ambiguous, center, round(1.05 * min(sx, sy)), (0, 0, 0), -1)
    with pytest.raises(ValueError, match="uncertain answer or set marks"):
        import_master_sheet(ambiguous, config, truth["set_code"])


def test_reader_accepts_explicit_single_channel_image_arrays():
    config, truth, image = _fixture()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _assert_matches_truth(config, truth, gray[:, :, None])


def test_50_generated_scans_read_and_score_exactly_across_all_sets(tmp_path):
    config, _truth, template = _fixture()
    layout = create_layout(config)
    sx, sy = template.shape[1] / layout.width, template.shape[0] / layout.height
    bubble_radius = round(1.05 * min(sx, sy))
    blank = template.copy()
    for bubble in layout.bubbles:
        center = round(bubble.x * sx), round(bubble.y * sy)
        # Clear the previous synthetic fill while preserving the printed bubble outline.
        cv2.circle(blank, center, round(1.20 * min(sx, sy)), (255, 255, 255), -1)

    repository = Repository(tmp_path / "50-scans.sqlite3")
    test_id = repository.save_test(config)
    batch_id = repository.create_batch(test_id, "50 synthetic scans", expected_pages=50)
    options = config["sheet"]["options"]
    expected_by_roll = {}
    for index in range(1, 51):
        roll = f"{index:06d}"
        set_code = ["A", "B", "C"][(index - 1) % 3]
        key = config["sets"][set_code]["answer_key"]
        expected_answers = {str(question["id"]): key[str(question["id"])] for question in config["questions"]}
        if index % 2 == 0:
            expected_answers["1"] = next(option for option in options if option != key["1"])
        if index % 5 == 0:
            expected_answers["2"] = None

        image = blank.copy()
        for question, answer in expected_answers.items():
            if answer is None:
                continue
            bubble = next(item for item in layout.bubbles
                          if item.group == "answer" and item.index == question and item.value == answer)
            cv2.circle(image, (round(bubble.x * sx), round(bubble.y * sy)), bubble_radius, (0, 0, 0), -1)
        for position, digit in enumerate(roll):
            bubble = next(item for item in layout.bubbles
                          if item.group == "roll" and item.index == str(position) and item.value == digit)
            cv2.circle(image, (round(bubble.x * sx), round(bubble.y * sy)), bubble_radius, (0, 0, 0), -1)
        set_bubble = next(item for item in layout.bubbles
                          if item.group == "set" and item.index == "0" and item.value == set_code)
        cv2.circle(image, (round(set_bubble.x * sx), round(set_bubble.y * sy)), bubble_radius, (0, 0, 0), -1)

        result = read_image(image, config)
        assert result.answers == expected_answers, f"answer mismatch on generated scan {index}"
        assert result.roll_number == roll, f"roll-number mismatch on generated scan {index}"
        assert result.set_code == set_code, f"set mismatch on generated scan {index}"
        assert not result.requires_review, f"unexpected review flag on generated scan {index}: {result.flags}"
        score = score_answers(config, set_code, result.answers)
        expected_total = 20 - (5 if index % 2 == 0 else 0) - (4 if index % 5 == 0 else 0)
        assert score["total"] == expected_total
        expected_by_roll[roll] = expected_total
        result.image_path = f"generated-sheet-{index:03d}.png"
        repository.store_read(batch_id, result, score)

    assert repository.finish_batch(batch_id, 50) == "complete"
    records = repository.results_for_batch(batch_id)
    assert len(records) == 50
    assert {record["roll_number"]: record["score"] for record in records} == expected_by_roll
    assert sum(record["score"] for record in records) == 835
