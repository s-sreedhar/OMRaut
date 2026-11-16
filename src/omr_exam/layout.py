"""One deterministic page layout shared by sheet generation and image reading.

All coordinates are in millimetres from the page's top-left corner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import floor
from typing import Any


@dataclass(frozen=True)
class Bubble:
    group: str
    index: str
    value: str
    x: float
    y: float


@dataclass(frozen=True)
class SheetLayout:
    width: float
    height: float
    marker_size: float
    bubbles: tuple[Bubble, ...]
    question_labels: tuple[tuple[str, float, float], ...]
    option_labels: tuple[tuple[str, float, float], ...]
    identity_labels: tuple[tuple[str, float, float], ...]
    question_columns: int
    questions_per_column: int
    # Sheet design version 3 (QualifyIAS format) adds a boxed header table, a left
    # invigilator panel and a subject-code field; `regions` carries its drawing geometry.
    version: int = 2
    name_box: tuple[float, float, float, float, int] | None = None
    bubble_radius: float = 1.55
    regions: dict[str, Any] = field(default_factory=dict, compare=False)


def maximum_columns_for_options(width_mm: float, option_count: int, *, maximum: int = 4) -> int:
    """Return the widest supported question grid that fits the option bubbles."""
    if option_count < 2 or maximum < 1:
        return 0
    # Keep a fixed gap for the question number, then place options on a compact
    # 5.5 mm pitch. The former double reservation of 22 mm capped A-D sheets at
    # three columns and prevented 160/180-question A4 exams.
    required_width = 10.0 + (option_count - 1) * 5.6 + 2.0
    for columns in range(min(maximum, 4), 0, -1):
        column_width = (float(width_mm) - 52.0) / columns
        if required_width <= column_width:
            return columns
    return 0


def maximum_questions_per_column(height_mm: float) -> int:
    available = float(height_mm) - 17.0 - 100.0
    return max(0, floor(available / 4.0))


V3_MARGIN = 14.0
V3_PANEL = 24.0
V3_TABLE_TOP, V3_TABLE_BOTTOM = 27.0, 83.0
V3_DIGIT_Y0, V3_DIGIT_PITCH = 41.8, 4.2
V3_DIGIT_COL_PITCH = 7.4


def _create_layout_v3(config: dict[str, Any]) -> SheetLayout:
    sheet = config["sheet"]
    width, height = float(sheet["width_mm"]), float(sheet["height_mm"])
    cols, qpc = int(sheet["columns"]), int(sheet["questions_per_column"])
    if width < 150 or height < 180:
        raise ValueError("Sheet size is too small for the current printable layout (minimum 150 x 180 mm)")
    if cols > 4:
        raise ValueError("The standard sheet layout supports up to 4 question columns")
    if len(sheet["set_codes"]) > 8:
        raise ValueError("The standard sheet layout supports up to 8 test set codes")
    if len(config["questions"]) > cols * qpc:
        raise ValueError(f"Sheet capacity is {cols * qpc} questions; this test has {len(config['questions'])}")
    left, right = V3_MARGIN, width - V3_MARGIN
    start_y = 91.0
    bottom = height - 15.0
    pitch_y = min(9.0, (bottom - start_y) / qpc)
    if pitch_y < 4.2:
        raise ValueError("Questions do not fit on the page; reduce questions per column or increase page height")
    options = list(map(str, sheet["options"]))
    col_x0 = left + V3_PANEL
    col_width = (right - col_x0) / cols
    opt_pitch = min(7.0, max(5.6, (col_width - 10.0 - 1.8 - 3.0) / max(1, len(options) - 1)))
    if 10.0 + (len(options) - 1) * opt_pitch + 2.0 > col_width:
        raise ValueError("Too many options for the configured number of sheet columns")
    bubbles: list[Bubble] = []
    qlabels: list[tuple[str, float, float]] = []
    olabs: list[tuple[str, float, float]] = []
    for question in config["questions"]:
        number = int(question["id"])
        col, row = (number - 1) // qpc, (number - 1) % qpc
        x0 = col_x0 + col * col_width
        y = start_y + row * pitch_y
        qlabels.append((str(number), x0, y + 1.0))
        for oi, option in enumerate(options):
            x = x0 + 10.0 + oi * opt_pitch
            bubbles.append(Bubble("answer", str(number), option, x, y))
            if row == 0:
                olabs.append((option, x, start_y - 2.7))
    # Header table: [name/test details][subject code][booklet code][roll number]
    digits = int(sheet["roll_number_digits"])
    multi_set = len(sheet["set_codes"]) > 1
    roll_w = 6.2 + (digits - 1) * V3_DIGIT_COL_PITCH + 4.0
    subject_w = 6.2 + V3_DIGIT_COL_PITCH + 4.0
    booklet_w = 15.0 if multi_set else 0.0
    name_w = (right - left) - roll_w - subject_w - booklet_w
    if name_w < 60.0:
        raise ValueError("Too many roll number digits for the sheet header")
    subject_x = left + name_w
    booklet_x = subject_x + subject_w
    roll_x = booklet_x + booklet_w
    for position in range(2):
        x = subject_x + 6.2 + position * V3_DIGIT_COL_PITCH
        for digit in range(10):
            bubbles.append(Bubble("subject", str(position), str(digit), x, V3_DIGIT_Y0 + digit * V3_DIGIT_PITCH))
    roll_cols = []
    for position in range(digits):
        x = roll_x + 6.2 + position * V3_DIGIT_COL_PITCH
        roll_cols.append(x)
        for digit in range(10):
            bubbles.append(Bubble("roll", str(position), str(digit), x, V3_DIGIT_Y0 + digit * V3_DIGIT_PITCH))
    if multi_set:
        for idx, code in enumerate(sheet["set_codes"]):
            bubbles.append(Bubble("set", "0", str(code), booklet_x + 9.0, V3_DIGIT_Y0 + idx * V3_DIGIT_PITCH))
    name_box = None
    identity = [("ROLL NUMBER", roll_x, V3_TABLE_TOP)]
    if "name" in sheet.get("fields", []):
        name_box = (left + 2.0, 32.5, name_w - 4.0, 10.0, 22)
        identity.append(("NAME", left + 2.0, 38.0))
    regions = {
        "left": left, "right": right, "name_w": name_w, "subject_x": subject_x, "subject_w": subject_w,
        "booklet_x": booklet_x, "booklet_w": booklet_w, "roll_x": roll_x, "roll_w": roll_w,
        "roll_cols": roll_cols, "start_y": start_y, "pitch_y": pitch_y, "col_x0": col_x0,
        "col_width": col_width, "panel_x": left, "panel_w": V3_PANEL, "bottom": bottom,
    }
    marker = float(sheet.get("corner_mark_size_mm", 8))
    return SheetLayout(width, height, marker, tuple(bubbles), tuple(qlabels), tuple(olabs), tuple(identity),
                       cols, qpc, 3, name_box, 1.8, regions)


def create_layout(config: dict[str, Any]) -> SheetLayout:
    sheet = config["sheet"]
    if int(sheet.get("answer_grid_version", 1)) >= 3:
        return _create_layout_v3(config)
    width, height = float(sheet["width_mm"]), float(sheet["height_mm"])
    cols = int(sheet["columns"])
    qpc = int(sheet["questions_per_column"])
    if width < 150 or height < 180:
        raise ValueError("Sheet size is too small for the current printable layout (minimum 150 x 180 mm)")
    if cols > 4:
        raise ValueError("The standard sheet layout supports up to 4 question columns")
    if len(sheet["set_codes"]) > 8:
        raise ValueError("The standard sheet layout supports up to 8 test set codes")
    if len(config["questions"]) > cols * qpc:
        raise ValueError(f"Sheet capacity is {cols * qpc} questions; this test has {len(config['questions'])}")
    margin = 18.0
    start_y = 100.0
    bottom = height - 17.0
    # Keep short tests compact near the header instead of stretching their rows
    # across the full page. Dense tests still use all available vertical space.
    pitch_y = min(9.0, (bottom - start_y) / qpc)
    if pitch_y < 4.0:
        raise ValueError("Questions do not fit on the page; reduce questions per column or increase page height")
    col_width = (width - 2 * margin) / cols
    options = list(map(str, sheet["options"]))
    # Saved exams without a version use the original printed grid coordinates;
    # newly created exams use compact coordinates to support four columns.
    option_start = (12.0 if int(sheet.get("answer_grid_version", 1)) >= 2
                    else 12.0 + (len(options) - 1) * 5.5)
    if option_start + (len(options) - 1) * 5.5 + 1.5 > col_width:
        raise ValueError("Too many options for the configured number of sheet columns")
    bubbles: list[Bubble] = []
    qlabels: list[tuple[str, float, float]] = []
    olabs: list[tuple[str, float, float]] = []
    for question in config["questions"]:
        number = int(question["id"])
        col, row = (number - 1) // qpc, (number - 1) % qpc
        x0 = margin + col * col_width
        y = start_y + row * pitch_y
        qlabels.append((str(number), x0, y + 1.0))
        for oi, option in enumerate(options):
            x = x0 + option_start + oi * 5.5
            bubbles.append(Bubble("answer", str(number), option, x, y))
            if row == 0:
                olabs.append((option, x, start_y - 2.3))
    # Candidate roll number: ten one-of-ten digit rows, one for every position.
    ry = 43.0
    identity: list[tuple[str, float, float]] = [("ROLL NUMBER", margin, ry)]
    digit_start = margin + 30.0
    digit_pitch = min(8.0, (width - margin - digit_start) / max(1, int(sheet["roll_number_digits"])))
    for position in range(int(sheet["roll_number_digits"])):
        x = digit_start + position * digit_pitch
        for digit in range(10):
            bubbles.append(Bubble("roll", str(position), str(digit), x, ry + 3.5 + digit * 4.1))
    set_y = ry + 48.0
    if len(sheet["set_codes"]) > 1:       # a single paper needs no set bubbles
        identity.append(("SET", margin, set_y))
        for idx, code in enumerate(sheet["set_codes"]):
            x = margin + 20 + idx * 14
            bubbles.append(Bubble("set", "0", str(code), x, set_y))
    # Handwritten fields are deliberately reserved for operator verification; this
    # initial release does not claim handwriting recognition.
    if "name" in sheet.get("fields", []):
        identity.append(("NAME", margin, 29.0))
    if "class" in sheet.get("fields", []):
        class_x = margin + width / 2 if "name" in sheet.get("fields", []) else margin
        identity.append(("CLASS", class_x, 29.0))
    marker = float(sheet.get("corner_mark_size_mm", 8))
    return SheetLayout(width, height, marker, tuple(bubbles), tuple(qlabels), tuple(olabs), tuple(identity), cols, qpc)
