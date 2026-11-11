"""Answer-key authoring and import helpers with strict question/option matching."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any


def validate_answer_key(config: dict[str, Any], answer_key: dict[str, str], *, complete: bool = True) -> dict[str, str]:
    valid_questions = {str(question["id"]) for question in config["questions"]}
    valid_options = {str(option).strip().upper(): str(option) for option in config["sheet"]["options"]}
    normalized: dict[str, str] = {}
    for question, answer in answer_key.items():
        q = str(question).strip()
        if answer is None or not str(answer).strip():
            continue
        if q in normalized:
            raise ValueError(f"Question {q} appears more than once in answer key")
        value = str(answer).strip().upper()
        normalized[q] = valid_options.get(value, value)
    unknown_questions = set(normalized) - valid_questions
    if unknown_questions:
        raise ValueError(f"Answer key contains unknown question(s): {', '.join(sorted(unknown_questions))}")
    invalid_answers = {question: answer for question, answer in normalized.items() if answer not in set(valid_options.values())}
    if invalid_answers:
        detail = ", ".join(f"Q{question}={answer}" for question, answer in sorted(invalid_answers.items()))
        raise ValueError(f"Invalid answer option(s); allowed: {', '.join(sorted(valid_options))}. Found: {detail}")
    dropped = {str(question) for question in config.get("scoring", {}).get("dropped_questions", [])}
    missing = valid_questions - dropped - set(normalized)
    if complete and missing:
        raise ValueError(f"Answer key is incomplete; missing question(s): {', '.join(sorted(missing, key=int))}")
    return normalized


def parse_pasted_key(text: str, config: dict[str, Any]) -> dict[str, str]:
    """Accept rows like `1\tA`, or a single row/list `A, B, C`, in question order."""
    stripped = text.strip()
    if not stripped:
        return {}
    questions = [str(item["id"]) for item in config["questions"]]
    options = {str(value).upper() for value in config["sheet"]["options"]}
    rows = list(csv.reader(io.StringIO(stripped), delimiter="\t" if "\t" in stripped else ","))
    if rows and len(rows[0]) >= 2 and rows[0][0].strip().lower() in {"question", "question number", "q"} and rows[0][1].strip().lower() in {"answer", "key", "option"}:
        rows = rows[1:]
    pairs: dict[str, str] = {}
    pair_mode = all(len(row) >= 2 and row[0].strip().isdigit() for row in rows)
    if pair_mode:
        for row in rows:
            q, answer = row[0].strip(), row[1].strip().upper()
            if q in pairs:
                raise ValueError(f"Question {q} appears more than once in pasted key")
            if answer and answer not in options:
                raise ValueError(f"Question {q}: {answer!r} is not a valid option")
            if answer:
                pairs[q] = answer
    else:
        tokens = [cell.strip().upper() for row in rows for cell in row]
        if len(tokens) != len(questions):
            raise ValueError(f"Expected {len(questions)} answers in question order; found {len(tokens)}")
        pairs = {q: a for q, a in zip(questions, tokens) if a}
        for q, answer in pairs.items():
            if answer not in options:
                raise ValueError(f"Question {q}: {answer!r} is not a valid option")
    return validate_answer_key(config, pairs)


def parse_compact_key(text: str, config: dict[str, Any]) -> dict[str, str]:
    """Parse a typed answer string such as `ABCDDCBA` or `A B C D` (one answer per question, in order).

    Also accepts `1-A 2-B` / `1:A, 2:B` style pairs. Every character is checked
    against the test's options and the count must match the question count exactly.
    """
    stripped = text.strip()
    if not stripped:
        return {}
    options = {str(value).upper() for value in config["sheet"]["options"]}
    questions = [str(item["id"]) for item in config["questions"]]
    if any(len(option) != 1 for option in options):
        return parse_pasted_key(text, config)
    import re
    pair_matches = re.findall(r"(\d+)\s*[-:=.)]?\s*([A-Za-z])", stripped)
    if pair_matches and re.fullmatch(r"(?:\s*\d+\s*[-:=.)]?\s*[A-Za-z]\s*[,;]?)+", stripped):
        return parse_pasted_key(chr(10).join(f"{q}{chr(9)}{a}" for q, a in pair_matches), config)
    letters = [character.upper() for character in stripped if not character.isspace() and character not in ",;"]
    bad = sorted({character for character in letters if character not in options})
    if bad:
        raise ValueError(f"Not a valid option: {', '.join(bad)}. Allowed: {', '.join(sorted(options))}")
    if len(letters) != len(questions):
        raise ValueError(f"You typed {len(letters)} answers but the test has {len(questions)} questions")
    return validate_answer_key(config, dict(zip(questions, letters)))


def import_excel_key(path: str | Path, config: dict[str, Any], *, sheet_name: str | None = None) -> dict[str, str]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("Excel import requires openpyxl; install the app dependencies") from exc
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook[sheet_name] if sheet_name else workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            raise ValueError("The selected worksheet is empty")
        first = [str(value).strip().casefold() if value is not None else "" for value in rows[0]]
        question_headers = {"question", "question number", "question no", "q"}
        answer_headers = {"answer", "answer key", "key", "option"}
        question_columns = [index for index, value in enumerate(first) if value in question_headers]
        answer_columns = [index for index, value in enumerate(first) if value in answer_headers]
        if question_columns and answer_columns:
            if len(question_columns) != 1 or len(answer_columns) != 1:
                raise ValueError("Excel key has duplicate question or answer headers")
            qi, ai = question_columns[0], answer_columns[0]
            pairs = {}
            for row in rows[1:]:
                if len(row) <= max(qi, ai) or row[qi] is None or row[ai] is None:
                    continue
                question, answer = str(row[qi]).strip(), str(row[ai]).strip().upper()
                if question in pairs:
                    raise ValueError(f"Question {question} appears more than once in the Excel key")
                pairs[question] = answer
        else:
            questions = [str(q["id"]) for q in config["questions"]]
            values = [value for row in rows for value in row]
            while values and values[-1] is None:
                values.pop()
            if len(values) != len(questions):
                raise ValueError(f"Expected exactly {len(questions)} answers in question order; found {len(values)} cells")
            pairs = {question: str(value).strip().upper() for question, value in zip(questions, values)
                     if value is not None and str(value).strip()}
    finally:
        workbook.close()
    return validate_answer_key(config, pairs)


def import_master_sheet(image: Any, config: dict[str, Any], set_code: str) -> dict[str, str]:
    """Read a completed key sheet; roll-number bubbles are irrelevant to key entry."""
    from omr_exam.reader import read_image

    code = str(set_code).strip()
    if code not in {str(value) for value in config["sheet"]["set_codes"]}:
        raise ValueError(f"Unknown test set: {code}")
    result = read_image(image, config)
    # A master key is identified by its set and answers. A teacher need not fill
    # the student ID, and accidental ID marks cannot alter the key being imported.
    issues = [flag for flag in result.flags
              if flag.get("kind") not in {"blank", "invalid_id"}
              and flag.get("group") != "roll"]
    if issues:
        summary = ", ".join(
            f"{flag.get('kind', 'review')}"
            f"{(' on Q' + str(flag['question'])) if flag.get('question') else ''}"
            for flag in issues
        )
        raise ValueError(f"Master sheet has uncertain answer or set marks: {summary}")
    if result.set_code != code:
        raise ValueError(f"Selected set {code}, but the master sheet reads as set {result.set_code or 'unknown'}")
    return validate_answer_key(config, result.answers)


def set_answer_key(config: dict[str, Any], set_code: str, answers: dict[str, str]) -> dict[str, Any]:
    code = str(set_code).strip()
    if code not in {str(value) for value in config["sheet"]["set_codes"]}:
        raise ValueError(f"Unknown test set: {code}")
    valid = validate_answer_key(config, answers)
    updated = json.loads(json.dumps(config))
    updated["sets"][code]["answer_key"] = valid
    return updated


def write_sample_key(config: dict[str, Any], path: str | Path) -> Path:
    """Write an example answer key for this test that the key dialog can import again.

    The file type follows the extension: .json (all sets), .xlsx (Question / Answer columns) or
    .txt (one line, the form typed into 'Type the whole key'). The answers are only placeholders.
    """
    import json

    path = Path(path)
    options = [str(o) for o in config["sheet"]["options"]]
    ids = [str(q["id"]) for q in config["questions"]]
    dropped = {str(q) for q in config.get("scoring", {}).get("dropped_questions", [])}
    sample = {qid: options[index % len(options)] for index, qid in enumerate(ids) if qid not in dropped}
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Answer key"
        sheet.append(["Question", "Answer"])
        for qid, answer in sample.items():
            sheet.append([int(qid), answer])
        sheet.column_dimensions["A"].width = 12
        sheet.column_dimensions["B"].width = 12
        workbook.save(path)
    elif suffix == ".txt":
        path.write_text("".join(sample.values()) + "\n", encoding="utf-8")
    else:
        payload = {"format": "omr-answer-keys-v1", "test_title": config["test"]["title"],
                   "sets": {str(code): dict(sample) for code in config["sheet"]["set_codes"]},
                   "topics": {qid: "" for qid in sample}}
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
