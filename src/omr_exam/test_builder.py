"""Configuration builder for the supported MCQ sheet model."""
from __future__ import annotations

from datetime import date
from typing import Any
from omr_exam.config import validate_config


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
                       institute: str = "") -> dict[str, Any]:
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
    fields = [str(value).strip().lower() for value in (["name", "class"] if fields is None else fields)]
    if len(fields) != len(set(fields)) or any(value not in {"name", "class"} for value in fields):
        raise ValueError("Printed fields may contain name and/or class, at most once each")
    qpc = questions_per_column if questions_per_column is not None else (question_count + columns - 1) // columns
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
                  "fields": fields, "corner_mark_size_mm": 8, "answer_grid_version": 2},
        "questions": questions,
        "sets": {code: {"answer_key": {}} for code in set_codes},
        "scoring": {"correct": correct_points, "incorrect": incorrect_points, "blank": blank_points,
                    "partial_credit": partial_credit or {}, "bonus_questions": bonus_questions or [],
                    "dropped_questions": dropped_questions or []},
        "reader": {"mark_threshold": mark_threshold, "faint_threshold": faint_threshold},
    }
    if institute.strip():
        config["test"]["institute"] = institute.strip()
    validate_config(config)
    return config
