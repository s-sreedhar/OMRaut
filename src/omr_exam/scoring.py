"""Deterministic scoring independent of scanning and user interface."""
from __future__ import annotations

from typing import Any
from omr_exam.answer_keys import validate_answer_key


def score_answers(config: dict[str, Any], set_code: str, answers: dict[int | str, str | None]) -> dict[str, Any]:
    if set_code not in config["sets"]:
        raise ValueError(f"Unknown test set: {set_code}")
    question_ids = {str(question["id"]) for question in config["questions"]}
    option_lookup = {str(option).strip().upper(): str(option) for option in config["sheet"]["options"]}
    normalized_answers: dict[str, str | None] = {}
    for question, answer in answers.items():
        qid = str(question).strip()
        if qid not in question_ids:
            raise ValueError(f"Response contains unknown question: {qid}")
        if qid in normalized_answers:
            raise ValueError(f"Response contains question {qid} more than once")
        if answer is None or not str(answer).strip():
            normalized_answers[qid] = None
            continue
        value = str(answer).strip().upper()
        if value not in option_lookup:
            raise ValueError(f"Question {qid}: invalid response option {value!r}")
        normalized_answers[qid] = option_lookup[value]
    key = validate_answer_key(config, config["sets"][set_code]["answer_key"], complete=False)
    rules = config["scoring"]
    dropped = {str(q) for q in rules.get("dropped_questions", [])}
    missing_key = {str(q["id"]) for q in config["questions"] if str(q["id"]) not in dropped} - set(key)
    if missing_key:
        raise ValueError(f"Answer key {set_code} is incomplete; missing question(s): {', '.join(sorted(missing_key, key=int))}")
    bonus = {str(q) for q in rules.get("bonus_questions", [])}
    total, correct, incorrect, blank = 0.0, 0, 0, 0
    rows = []
    for question in config["questions"]:
        qid = str(question["id"])
        given = normalized_answers.get(qid)
        expected = key.get(qid)
        if qid in dropped:
            points, status = 0.0, "dropped"
        elif given is None or given == "":
            points, status = float(rules["blank"]), "blank"
            blank += 1
        elif expected is not None and given == expected:
            points, status = float(rules["correct"]) + (1.0 if qid in bonus else 0.0), "correct"
            correct += 1
        else:
            points, status = float(rules.get("partial_credit", {}).get(qid, rules["incorrect"])), "incorrect"
            incorrect += 1
        total += points
        rows.append({"question": int(qid), "answer": given, "expected": expected, "status": status, "points": points})
    return {"set_code": set_code, "total": total, "correct": correct, "incorrect": incorrect, "blank": blank, "questions": rows}
