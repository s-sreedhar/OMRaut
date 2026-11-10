"""One deterministic page layout shared by sheet generation and image reading.

All coordinates are in millimetres from the page's top-left corner.
"""
from __future__ import annotations

from dataclasses import dataclass
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


def maximum_columns_for_options(width_mm: float, option_count: int, *, maximum: int = 4) -> int:
    """Return the widest supported question grid that fits the option bubbles."""
    if option_count < 2 or maximum < 1:
        return 0
    option_start = 12.0 + (option_count - 1) * 5.5
    required_width = option_start + 2.5 * (option_count - 1) + 22.0
    for columns in range(min(maximum, 4), 0, -1):
        column_width = (float(width_mm) - 36.0) / columns
        if required_width <= column_width:
            return columns
    return 0


def maximum_questions_per_column(height_mm: float) -> int:
    available = float(height_mm) - 17.0 - 100.0
    return max(0, floor(available / 4.0))


def create_layout(config: dict[str, Any]) -> SheetLayout:
    sheet = config["sheet"]
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
    option_start = 12.0 + (len(options) - 1) * 5.5
    if option_start + 2.5 * (len(options) - 1) > col_width - 22:
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
                olabs.append((option, x, start_y - 3.0))
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
