"""Configuration builder and live validation helpers for the supported MCQ sheet model."""
from __future__ import annotations

from datetime import date
from typing import Any
from omr_exam.config import validate_config
from omr_exam.layout import maximum_columns_for_options, maximum_questions_per_column


def parse_question_list(text: str, max_question: int | None = None, *, label: str = "Question numbers") -> list[int]:
    """Parse a comma-separated list of question numbers or ranges (e.g. '1-3, 5, 12') with clear validation errors."""
    items = [part.strip() for part in str(text or "").split(",") if part.strip()]
    result: list[int] = []
    seen: set[int] = set()
    for part in items:
        if "-" in part and not part.startswith("-"):
            bounds = part.split("-", 1)
            try:
                start_q = int(bounds[0].strip())
                end_q = int(bounds[1].strip())
            except ValueError:
                raise ValueError(f"{label} must be whole numbers or ranges separated by commas") from None
            if start_q > end_q:
                raise ValueError(f"{label} range start must be <= end (got {part})")
            nums = list(range(start_q, end_q + 1))
        else:
            try:
                nums = [int(part)]
            except ValueError:
                raise ValueError(f"{label} must be whole numbers separated by commas") from None
        for num in nums:
            if num < 1 or (max_question is not None and num > max_question):
                bound = f" between 1 and {max_question}" if max_question is not None else " >= 1"
                raise ValueError(f"{label} must be{bound} (got {num})")
            if num not in seen:
                seen.add(num)
                result.append(num)
    return result


def parse_partial_credit(text: str, max_question: int | None = None) -> dict[str, float]:
    """Parse a comma-separated list of 'question:points' pairs (e.g. '3:2, 7:1.5')."""
    partial_credit: dict[str, float] = {}
    for entry in str(text or "").split(","):
        if not entry.strip():
            continue
        if ":" not in entry:
            raise ValueError("Partial points must look like 3:2, 7:1.5")
        q_raw, pts_raw = entry.split(":", 1)
        try:
            q_num = int(q_raw.strip())
            pts = float(pts_raw.strip())
        except ValueError:
            raise ValueError("Partial points must look like 3:2, 7:1.5") from None
        if q_num < 1 or (max_question is not None and q_num > max_question):
            bound = f" between 1 and {max_question}" if max_question is not None else " >= 1"
            raise ValueError(f"Partial credit question number must be{bound} (got {q_num})")
        partial_credit[str(q_num)] = pts
    return partial_credit


def sheet_capacity_summary(question_count: int, options: list[str] | int = 4, columns: int = 2,
                           width_mm: float = 210, height_mm: float = 297) -> dict[str, Any]:
    """Return layout capacity metrics and human-readable status for the test builder UI."""
    if isinstance(options, int):
        clean_options = [chr(ord("A") + i) for i in range(max(0, options))]
    else:
        clean_options = [str(o).strip().upper() for o in options if str(o).strip()]
    opt_count = max(2, len(clean_options))
    max_cols = maximum_columns_for_options(width_mm, opt_count)
    max_rows = maximum_questions_per_column(height_mm)
    eff_cols = max(1, min(columns, max(1, max_cols)))
    max_total = eff_cols * max_rows
    qpc = (max(1, question_count) + eff_cols - 1) // eff_cols
    fits = max_cols >= 1 and 1 <= columns <= max_cols and qpc <= max_rows and len(clean_options) >= 2
    if max_cols < 1:
        message = "Too many answer options to fit on an A4 sheet."
    elif len(clean_options) < 2 or len(set(clean_options)) != len(clean_options):
        message = "Enter at least two unique answer options (e.g. A, B, C, D)."
    elif not fits:
        message = f"Up to {max_total} questions fit in {eff_cols} column(s) ({max_rows} per column)."
    else:
        message = f"{columns} column{'s' if columns != 1 else ''} × up to {qpc} question{'s' if qpc != 1 else ''}/column (capacity {max_total} in {columns} col)."
    return {
        "max_columns": max(1, max_cols),
        "max_questions_per_column": max_rows,
        "max_questions_total": max_total,
        "questions_per_column": qpc,
        "fits": fits,
        "message": message,
    }


def create_test_config(*, title: str, test_date: str | None, class_name: str,
                       subject: str | None = None, subjects: list[str] | None = None,
                       question_count: int, options: list[str], roll_number_digits: int,
                       set_codes: list[str], fields: list[str] | None = None,
                       width_mm: float = 210, height_mm: float = 297,
                       columns: int = 2, questions_per_column: int | None = None,
                       sections: list[str] | None = None, correct_points: float = 4,
                       incorrect_points: float = -1, blank_points: float = 0,
                       bonus_questions: list[int] | None = None, dropped_questions: list[int] | None = None,
                       partial_credit: dict[str, float] | None = None,
                       mark_threshold: float = 0.24, faint_threshold: float = 0.02,
                       institute: str = "", sheet_questions: int | None = None) -> dict[str, Any]:
    title, class_name = title.strip(), class_name.strip()
    selected_subjects = [str(value).strip() for value in (subjects if subjects is not None else [subject or ""]) if str(value).strip()]
    if not title or not class_name or not selected_subjects:
        raise ValueError("Test title, class, and at least one subject are required")
    if len({value.casefold() for value in selected_subjects}) != len(selected_subjects):
        raise ValueError("Select each test subject only once")
    if question_count < 1:
        raise ValueError("Question count must be positive")
    if not 1 <= columns <= 4:
        raise ValueError("Question columns must be between 1 and 4")
    if questions_per_column is not None and questions_per_column < 1:
        raise ValueError("Questions per column must be positive")
    options = [str(x).strip().upper() for x in options]
    if len(options) < 2 or len(set(options)) != len(options) or any(not x for x in options):
        raise ValueError("Enter at least two unique, non-empty answer options")
    set_codes = [str(x).strip().upper() for x in set_codes] or ["A"]     # no sets = one paper
    if len(set(set_codes)) != len(set_codes) or any(not x for x in set_codes):
        raise ValueError("Enter unique test set codes, or leave sets off for a single paper")
    fields = [str(value).strip().lower() for value in (["name"] if fields is None else fields)]
    if len(fields) != len(set(fields)) or any(value not in {"name", "class"} for value in fields):
        raise ValueError("Printed fields may contain name and/or class, at most once each")
    # `sheet_questions` is the fixed number of rows on the pre-printed sheet; the test may use fewer.
    printed = int(sheet_questions) if sheet_questions else question_count
    if printed < question_count:
        raise ValueError(f"This test uses {question_count} questions but the printed sheet only has {printed}")
    qpc = questions_per_column if questions_per_column is not None else (printed + columns - 1) // columns
    sections = [str(item).strip() for item in (sections or selected_subjects) if str(item).strip()] or selected_subjects
    if len(sections) > question_count:
        raise ValueError("Section count cannot exceed question count")
    section_subjects = {name.casefold(): name for name in selected_subjects}
    if any(section.casefold() not in section_subjects for section in sections):
        raise ValueError("Question sections must use subjects selected for this test")
    if not 0 < faint_threshold < mark_threshold < 1:
        raise ValueError("Require 0 < faint mark threshold < filled mark threshold < 1")
    # Split question ranges evenly among the requested subject sections.
    section_ranges = []
    base, extra = divmod(question_count, len(sections))
    start = 1
    for index, section in enumerate(sections):
        end = start + base + (1 if index < extra else 0) - 1
        section_ranges.append({"title": section, "subject": section_subjects[section.casefold()], "question_start": start, "question_end": end})
        start = end + 1
    questions = []
    for n in range(1, question_count + 1):
        section = next(item["title"] for item in section_ranges if item["question_start"] <= n <= item["question_end"])
        questions.append({"id": n, "section": section, "topic": ""})
    config = {
        "schema_version": 1,
        "test": {"title": title, "date": test_date or date.today().isoformat(), "class": class_name, "subjects": selected_subjects,
                 "sections": section_ranges},
        "sheet": {"width_mm": width_mm, "height_mm": height_mm, "questions_per_column": qpc, "columns": columns,
                  "options": options, "roll_number_digits": roll_number_digits, "set_codes": set_codes,
                  "fields": fields, "corner_mark_size_mm": 8, "answer_grid_version": 3},
        "questions": questions,
        "sets": {code: {"answer_key": {}} for code in set_codes},
        "scoring": {"correct": correct_points, "incorrect": incorrect_points, "blank": blank_points,
                    "partial_credit": partial_credit or {}, "bonus_questions": bonus_questions or [],
                    "dropped_questions": dropped_questions or []},
        "reader": {"mark_threshold": mark_threshold, "faint_threshold": faint_threshold},
    }
    if printed != question_count:
        config["sheet"]["printed_questions"] = printed
    if institute.strip():
        config["test"]["institute"] = institute.strip()
    validate_config(config)
    return config
