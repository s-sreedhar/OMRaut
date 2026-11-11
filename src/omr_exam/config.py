"""Loading and validation for the shared test JSON format."""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any
from omr_exam.resources import config_dir

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:  # Keep config loading usable in minimal/offline deployments.
    Draft202012Validator = None
    FormatChecker = None

SCHEMA_PATH = config_dir() / "test-config.schema.json"


def _reject_non_finite_numbers(value: Any, path: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"Invalid test configuration: {path} must be finite")
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_non_finite_numbers(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_non_finite_numbers(child, f"{path}[{index}]")


def validate_config(config: dict[str, Any]) -> None:
    _reject_non_finite_numbers(config)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if Draft202012Validator is not None:
        errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(config), key=lambda e: list(map(str, e.path)))
        if errors:
            details = "; ".join(f"{'.'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors)
            raise ValueError(f"Invalid test configuration: {details}")
    else:
        _validate_without_jsonschema(config)
    for label, values in (("options", config["sheet"]["options"]),
                          ("set codes", config["sheet"]["set_codes"])):
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError(f"Sheet {label} must contain non-empty strings")
        if any(value != value.strip() for value in values):
            raise ValueError(f"Sheet {label} must not have leading or trailing whitespace")
        normalized = [value.casefold() for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"Sheet {label} must be unique without regard to letter case")
    question_ids = [str(item["id"]) for item in config["questions"]]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("Question IDs must be unique")
    if sorted(map(int, question_ids)) != list(range(1, len(question_ids) + 1)):
        raise ValueError("Question IDs must be consecutive and start at 1; they define printed bubble positions")
    sections = config["test"].get("sections", [])
    if sections:
        expected_start = 1
        section_for_question: dict[int, str] = {}
        for section in sorted(sections, key=lambda item: item["question_start"]):
            start, end = int(section["question_start"]), int(section["question_end"])
            if start != expected_start or end < start:
                raise ValueError("Test section ranges must cover each question exactly once without gaps or overlaps")
            title = str(section["title"]).strip()
            if not title:
                raise ValueError("Test section titles must not be empty")
            for question_id in range(start, end + 1):
                section_for_question[question_id] = title
            expected_start = end + 1
        if expected_start != len(question_ids) + 1:
            raise ValueError("Test section ranges must cover every configured question")
        for question in config["questions"]:
            question_id = int(question["id"])
            if str(question["section"]).strip() != section_for_question[question_id]:
                raise ValueError(f"Question {question_id} section label must match its configured section range")
    questions = set(question_ids)
    for set_code, set_config in config["sets"].items():
        for question, answer in set_config["answer_key"].items():
            if question not in questions:
                raise ValueError(f"Answer key {set_code} references unknown question {question}")
            if answer not in config["sheet"]["options"]:
                raise ValueError(f"Answer key {set_code}, question {question}: unknown option {answer}")
    if set(config["sets"]) != set(config["sheet"]["set_codes"]):
        raise ValueError("Configured set codes must match the answer key set names")
    for qid in config["scoring"].get("bonus_questions", []) + config["scoring"].get("dropped_questions", []):
        if str(qid) not in questions:
            raise ValueError(f"Scoring rule references unknown question {qid}")
    for qid in config["scoring"].get("partial_credit", {}):
        if str(qid) not in questions:
            raise ValueError(f"Partial credit references unknown question {qid}")
    reader = config.get("reader", {})
    mark_threshold = float(reader.get("mark_threshold", 0.24))
    faint_threshold = float(reader.get("faint_threshold", 0.02))
    if not 0 < faint_threshold < mark_threshold < 1:
        raise ValueError("Require 0 < faint_threshold < mark_threshold < 1, including reader defaults")
    # Reject tests that satisfy the JSON contract but cannot fit the shared print/read geometry.
    from omr_exam.layout import create_layout
    create_layout(config)


def _validate_without_jsonschema(config: dict[str, Any]) -> None:
    """Small structural fallback used only if jsonschema is unavailable."""
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ValueError("Invalid test configuration: schema_version must be 1")
    required = {"test", "sheet", "questions", "sets", "scoring"}
    if not required.issubset(config):
        raise ValueError(f"Invalid test configuration: missing {', '.join(sorted(required-set(config)))}")
    test, sheet, scoring = config["test"], config["sheet"], config["scoring"]
    for key in ("title", "class", "subject", "date"):
        if not isinstance(test.get(key), str) or not test[key].strip():
            raise ValueError(f"Invalid test configuration: test.{key} is required")
    try:
        date.fromisoformat(test["date"])
    except ValueError as exc:
        raise ValueError("Invalid test configuration: test.date must be YYYY-MM-DD") from exc
    for key in ("width_mm", "height_mm", "questions_per_column", "columns", "roll_number_digits"):
        if key not in sheet or float(sheet[key]) <= 0:
            raise ValueError(f"Invalid test configuration: sheet.{key} must be positive")
    for key in ("options", "set_codes", "fields"):
        if not isinstance(sheet.get(key), list):
            raise ValueError(f"Invalid test configuration: sheet.{key} must be an array")
    if any(not isinstance(value, str) or value not in {"name", "class"} for value in sheet["fields"]) or len(sheet["fields"]) != len(set(sheet["fields"])):
        raise ValueError("Invalid test configuration: fields must contain unique name/class values")
    if len(sheet["options"]) < 2 or len(set(sheet["options"])) != len(sheet["options"]):
        raise ValueError("Invalid test configuration: options must contain at least two unique values")
    if not isinstance(config["questions"], list) or not config["questions"]:
        raise ValueError("Invalid test configuration: questions must be a non-empty array")
    if not isinstance(config["sets"], dict) or not config["sets"]:
        raise ValueError("Invalid test configuration: sets must be a non-empty object")
    if not {"correct", "incorrect", "blank"}.issubset(scoring):
        raise ValueError("Invalid test configuration: scoring needs correct, incorrect, blank")


def load_config(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_config(data)
    return data


def uses_sets(config: dict[str, Any]) -> bool:
    """True when students get different papers (two or more sets).

    A test with a single paper prints no "SET" bubbles; the reader simply assumes its only set.
    """
    return len(config["sheet"]["set_codes"]) > 1
