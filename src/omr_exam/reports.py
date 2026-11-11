"""Result exports and question analysis from saved response records."""
from __future__ import annotations

import csv
import re
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from omr_exam.scoring import score_answers

_SPREADSHEET_FORMULA = re.compile(r"^[\s]*[=+@-]")


def _safe_spreadsheet_text(value: Any) -> Any:
    """Force untrusted text to remain literal when a report is opened in a spreadsheet."""
    if isinstance(value, str) and _SPREADSHEET_FORMULA.match(value):
        return "'" + value
    return value


def _require_exportable(config: dict[str, Any], records: list[dict[str, Any]]) -> None:
    rows = class_results(config, records)
    unresolved = [row.get("roll_number", "unknown roll") for row in rows
                  if row["review_required"] or row.get("total") is None]
    if unresolved:
        raise ValueError(f"Official exports require resolved scans; unresolved sheet(s): {', '.join(map(str, unresolved[:10]))}")


def class_results(config: dict[str, Any], records: list[dict[str, Any]], set_code: str | None = None) -> list[dict[str, Any]]:
    output = []
    for record in records:
        code = set_code or record.get("set_code")
        if code not in config["sets"]:
            output.append({**record, "total": None, "correct": 0, "incorrect": 0, "blank": 0, "review_required": True})
            continue
        scored = score_answers(config, code, record.get("answers", {}))
        output.append({**record, **scored, "review_required": bool(record.get("review_required"))})
    output.sort(key=lambda row: (-(row.get("total") or 0), row.get("roll_number", "")))
    previous_score = object()
    current_rank = 0
    for position, row in enumerate(output, 1):
        score = row.get("total")
        if score != previous_score:
            current_rank = position
            previous_score = score
        row["rank"] = current_rank
    maximum = max_score(config)
    scored = [row["total"] for row in output if row.get("total") is not None]
    for row in output:
        total = row.get("total")
        if total is None:
            row["percent"], row["percentile"] = None, None
            continue
        row["percent"] = round(total / maximum * 100, 1) if maximum else None
        below = sum(score < total for score in scored)
        equal = sum(score == total for score in scored)
        row["percentile"] = round((below + 0.5 * equal) / len(scored) * 100, 1)
    return output


def source_file_details(record: dict[str, Any]) -> tuple[str, str, str]:
    """Return the original uploaded basename, optional PDF page, and recorded import time."""
    raw_path = str(record.get("image_path") or "")
    source_path, marker, page_suffix = raw_path.partition("#page=")
    source_path = source_path.split("#", 1)[0]
    filename = os.path.basename(source_path) if source_path else "Not recorded"
    page = page_suffix.split("#", 1)[0] if marker else ""
    imported_at = str(record.get("uploaded_at") or record.get("processed_at") or "Not recorded")
    return filename, page, imported_at


def _source_line(record: dict[str, Any]) -> str:
    filename, page, imported_at = source_file_details(record)
    page_text = f" (PDF page {page})" if page else ""
    return f"Uploaded file: {filename}{page_text}  |  Imported at (UTC): {imported_at}"


def max_score(config: dict[str, Any]) -> float:
    """Highest total a student can earn: every counted question right, bonus questions worth one extra."""
    rules = config["scoring"]
    dropped = {str(q) for q in rules.get("dropped_questions", [])}
    bonus = {str(q) for q in rules.get("bonus_questions", [])}
    return sum(float(rules["correct"]) + (1.0 if str(q["id"]) in bonus else 0.0)
               for q in config["questions"] if str(q["id"]) not in dropped)


def _eligible(config: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in class_results(config, records) if not row["review_required"] and row.get("total") is not None]


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "stdev": None, "high": None, "low": None}
    ordered = sorted(values)
    n = len(ordered)
    mean = sum(ordered) / n
    median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    stdev = (sum((v - mean) ** 2 for v in ordered) / n) ** 0.5
    return {"mean": round(mean, 2), "median": round(median, 2), "stdev": round(stdev, 2),
            "high": ordered[-1], "low": ordered[0]}


def summary_stats(config: dict[str, Any], records: list[dict[str, Any]], pass_percent: float = 40.0) -> dict[str, Any]:
    """Whole-test figures: spread of scores, pass rate, and KR-20 reliability of the question set."""
    rows = _eligible(config, records)
    maximum = max_score(config)
    result: dict[str, Any] = {"students": len(rows), "maximum": maximum, "pass_percent": pass_percent,
                              **_stats([row["total"] for row in rows])}
    result["mean_percent"] = round(result["mean"] / maximum * 100, 1) if rows and maximum else None
    result["pass_rate"] = (round(sum((row["percent"] or 0) >= pass_percent for row in rows) / len(rows) * 100, 1)
                           if rows else None)
    reliability = None
    if len(rows) > 1:
        counted = [i for i, q in enumerate(rows[0]["questions"]) if q["status"] != "dropped"]
        k = len(counted)
        totals = [sum(row["questions"][i]["status"] == "correct" for i in counted) for row in rows]
        mean = sum(totals) / len(totals)
        variance = sum((t - mean) ** 2 for t in totals) / len(totals)
        if k > 1 and variance > 0:
            pq = 0.0
            for i in counted:
                p = sum(row["questions"][i]["status"] == "correct" for row in rows) / len(rows)
                pq += p * (1 - p)
            reliability = round(k / (k - 1) * (1 - pq / variance), 3)
    result["reliability"] = reliability
    return result


def distractor_analysis(config: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For each question, how many students chose each option, which wrong option attracted most, and blanks."""
    rows = _eligible(config, records)
    options = [str(o) for o in config["sheet"]["options"]]
    single = len(config["sheet"]["set_codes"]) == 1
    dropped = {str(q) for q in config["scoring"].get("dropped_questions", [])}
    output = []
    for question in config["questions"]:
        qid = int(question["id"])
        item = {"question": qid, "topic": question.get("topic", ""), "key": "", "blank": 0, "top_wrong": "",
                "top_wrong_count": 0, "dropped": str(qid) in dropped, **{f"option_{o}": 0 for o in options}}
        wrong: dict[str, int] = defaultdict(int)
        if not item["dropped"]:
            for row in rows:
                entry = next((q for q in row["questions"] if q["question"] == qid), None)
                if entry is None:
                    continue
                item["key"] = (entry.get("expected") or "") if single else "by set"
                if entry["answer"] is None:
                    item["blank"] += 1
                    continue
                item[f"option_{entry['answer']}"] = item.get(f"option_{entry['answer']}", 0) + 1
                if entry["status"] != "correct":
                    wrong[entry["answer"]] += 1
        if wrong:
            item["top_wrong"], item["top_wrong_count"] = max(sorted(wrong.items()), key=lambda pair: pair[1])
        output.append(item)
    return output


def class_summary(config: dict[str, Any], records: list[dict[str, Any]], pass_percent: float = 40.0) -> list[dict[str, Any]]:
    """Average, spread and pass rate for each class."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _eligible(config, records):
        groups[str(row.get("class_name") or "Unassigned")].append(row)
    output = []
    for name in sorted(groups, key=str.casefold):
        members = groups[name]
        output.append({"class_name": name, "students": len(members), **_stats([row["total"] for row in members]),
                       "pass_rate": round(sum((row["percent"] or 0) >= pass_percent for row in members) / len(members) * 100, 1)})
    return output


def set_summary(config: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-paper comparison; only meaningful when the test has more than one set."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _eligible(config, records):
        groups[str(row.get("set_code"))].append(row)
    return [{"set_code": code, "students": len(rows), **_stats([row["total"] for row in rows])}
            for code, rows in sorted(groups.items())]


def score_bands(config: dict[str, Any], records: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """How many students fall in each ten-percent band of the maximum score."""
    labels = [f"{low}-{low + 10}%" for low in range(0, 100, 10)]
    counts = [0] * 10
    for row in _eligible(config, records):
        percent = max(0.0, min(100.0, row["percent"] or 0.0))
        counts[min(9, int(percent // 10))] += 1
    return list(zip(labels, counts))


def item_analysis(config: dict[str, Any], records: list[dict[str, Any]], set_code: str | None = None) -> list[dict[str, Any]]:
    rows = class_results(config, records, set_code)
    eligible = [row for row in rows if not row["review_required"] and row.get("total") is not None]
    n = len(eligible)
    dropped = {str(q) for q in config["scoring"].get("dropped_questions", [])}
    if n == 0:
        return [{"question": question["id"], "correct_count": 0, "attempted": 0, "difficulty": None,
                 "discrimination": None, "topic": question.get("topic", ""), "dropped": str(question["id"]) in dropped}
                for question in config["questions"]]
    group_size = max(1, round(n * 0.27))
    # Include every student tied at a cutoff. Splitting tied totals by roll number
    # would make discrimination depend on arbitrary roster ordering.
    high_cutoff = eligible[group_size - 1]["total"]
    low_cutoff = eligible[-group_size]["total"]
    high = [row for row in eligible if row["total"] >= high_cutoff]
    low = [row for row in eligible if row["total"] <= low_cutoff]
    result = []
    for question in config["questions"]:
        qid = str(question["id"])
        if qid in dropped:
            result.append({"question": int(qid), "correct_count": 0, "attempted": 0, "difficulty": None,
                           "discrimination": None, "topic": question.get("topic", ""), "dropped": True})
            continue
        attempted = sum(row["answers"].get(qid) not in (None, "") for row in eligible)
        def is_correct(row: dict[str, Any]) -> bool:
            code = set_code or row.get("set_code")
            expected = config["sets"].get(code, {}).get("answer_key", {}).get(qid)
            return expected is not None and row["answers"].get(qid) == expected
        correct = sum(is_correct(row) for row in eligible)
        high_rate = sum(is_correct(row) for row in high) / len(high)
        low_rate = sum(is_correct(row) for row in low) / len(low)
        result.append({"question": int(qid), "correct_count": correct, "attempted": attempted,
                       "difficulty": round(correct / n, 4), "discrimination": round(high_rate - low_rate, 4),
                       "topic": question.get("topic", ""), "dropped": False})
    return result


def export_csv(config: dict[str, Any], records: list[dict[str, Any]], output: str | Path) -> Path:
    _require_exportable(config, records)
    rows = class_results(config, records)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    question_ids = [str(question["id"]) for question in config["questions"]]
    columns = ["rank", "roll_number", "student_name", "class_name", "set_code", "total", "percent", "percentile", "correct", "incorrect", "blank", "review_required", "source_file", "page", "imported_at_utc", *[f"Q{qid}" for qid in question_ids]]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            output_row = dict(row)
            output_row["source_file"], output_row["page"], output_row["imported_at_utc"] = source_file_details(row)
            output_row.update({f"Q{qid}": row["answers"].get(qid, "") or "" for qid in question_ids})
            for field in ("roll_number", "student_name", "class_name", "set_code", "source_file", *[f"Q{qid}" for qid in question_ids]):
                output_row[field] = _safe_spreadsheet_text(output_row.get(field))
            writer.writerow(output_row)
    return path


def export_xlsx(config: dict[str, Any], records: list[dict[str, Any]], output: str | Path) -> Path:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError as exc:
        raise RuntimeError("Excel export requires openpyxl") from exc
    _require_exportable(config, records)
    rows = class_results(config, records)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Results"
    headers = ["Rank", "Roll number", "Name", "Class", "Set", "Score", "Percent", "Percentile", "Correct", "Incorrect", "Blank", "Needs review", "Source file", "PDF page", "Imported at (UTC)"] + [f"Q{q['id']}" for q in config["questions"]]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="17324D")
    for row in rows:
        filename, page, imported_at = source_file_details(row)
        sheet.append([row.get("rank"), _safe_spreadsheet_text(row.get("roll_number")), _safe_spreadsheet_text(row.get("student_name", "")), _safe_spreadsheet_text(row.get("class_name", "")), _safe_spreadsheet_text(row.get("set_code")), row.get("total"), row.get("percent"), row.get("percentile"), row.get("correct"), row.get("incorrect"), row.get("blank"), "Yes" if row.get("review_required") else "No", _safe_spreadsheet_text(filename), page, imported_at] + [_safe_spreadsheet_text(row["answers"].get(str(q["id"]))) for q in config["questions"]])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    options = [str(o) for o in config["sheet"]["options"]]
    summary = summary_stats(config, records)
    summary_sheet = workbook.create_sheet("Summary", 1)
    summary_sheet.append(["Measure", "Value"])
    for label_text, value in (("Test", config["test"]["title"]), ("Students scored", summary["students"]),
                              ("Maximum score", summary["maximum"]), ("Average score", summary["mean"]),
                              ("Average percent", summary["mean_percent"]), ("Median", summary["median"]),
                              ("Standard deviation", summary["stdev"]), ("Highest", summary["high"]), ("Lowest", summary["low"]),
                              (f"Pass rate (at {summary['pass_percent']:g}%)", summary["pass_rate"]),
                              ("Reliability (KR-20)", summary["reliability"])):
        summary_sheet.append([label_text, _safe_spreadsheet_text(value)])
    summary_sheet.append([])
    summary_sheet.append(["Score band", "Students"])
    for band, count in score_bands(config, records):
        summary_sheet.append([band, count])
    for cell in summary_sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="17324D")
    summary_sheet.column_dimensions["A"].width = 30
    summary_sheet.column_dimensions["B"].width = 26
    analyses = (
        ("Class summary", ["Class", "Students", "Average", "Median", "Std dev", "Highest", "Lowest", "Pass rate %"],
         class_summary(config, records), ("class_name", "students", "mean", "median", "stdev", "high", "low", "pass_rate")),
        ("Distractors", ["Question", "Topic", "Key", *options, "Blank", "Most chosen wrong", "Times chosen"],
         [{**row, **{f"o_{o}": row.get(f"option_{o}") for o in options}} for row in distractor_analysis(config, records)],
         ("question", "topic", "key", *[f"o_{o}" for o in options], "blank", "top_wrong", "top_wrong_count")),
        ("Item analysis", ["Question", "Topic", "Correct", "Attempted", "Difficulty", "Discrimination", "Dropped"],
         item_analysis(config, records),
         ("question", "topic", "correct_count", "attempted", "difficulty", "discrimination", "dropped")),
        ("Topic analysis", ["Section", "Topic", "Questions", "Students", "Average points per student"],
         topic_analysis(config, records),
         ("section", "topic", "question_count", "student_count", "average_points_per_student")),
        ("Subject analysis", ["Subject", "Questions", "Students", "Average points per student"],
         subject_analysis(config, records),
         ("subject", "question_count", "student_count", "average_points_per_student")),
    )
    if len(config["sheet"]["set_codes"]) > 1:
        analyses = (*analyses, ("Set comparison", ["Set", "Students", "Average", "Median", "Std dev", "Highest", "Lowest"],
                                set_summary(config, records), ("set_code", "students", "mean", "median", "stdev", "high", "low")))
    for title, headers, data, keys in analyses:
        analysis_sheet = workbook.create_sheet(title)
        analysis_sheet.append(headers)
        for cell in analysis_sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="17324D")
        for row in data:
            analysis_sheet.append([_safe_spreadsheet_text(row.get(key)) for key in keys])
        analysis_sheet.freeze_panes = "A2"
        if analysis_sheet.max_row > 1:
            analysis_sheet.auto_filter.ref = analysis_sheet.dimensions
        for column in analysis_sheet.columns:
            letter = column[0].column_letter
            max_length = max(len(str(cell.value or "")) for cell in column)
            analysis_sheet.column_dimensions[letter].width = min(max(max_length + 2, 12), 34)
    workbook.save(path)
    return path


def export_report_pdf(config: dict[str, Any], records: list[dict[str, Any]], output: str | Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet
    from xml.sax.saxutils import escape
    _require_exportable(config, records)
    rows = class_results(config, records)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), title=f"{config['test']['title']} results")
    _colors, styles = _pdf_styles()
    data = [["Rank", "Roll", "Student", "Class", "Set", "Score", "Correct", "Wrong", "Blank", "Source file", "Imported at (UTC)"]]
    for row in rows:
        filename, _page, imported_at = source_file_details(row)
        data.append([row.get("rank"), row.get("roll_number"), row.get("student_name", ""), row.get("class_name", ""),
                     row.get("set_code"), row.get("total"), row.get("correct"), row.get("incorrect"), row.get("blank"),
                     Paragraph(escape(filename), styles["ReportSmall"]), Paragraph(escape(imported_at), styles["ReportSmall"])])
    table = Table(data, repeatRows=1, colWidths=[28, 50, 90, 42, 28, 42, 38, 38, 34, 145, 96])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324d")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#aab5bf")), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f8")]), ("FONTSIZE", (0, 0), (-1, -1), 8), ("PADDING", (0, 0), (-1, -1), 5)]))
    content = [Paragraph(escape(config["test"]["title"]), styles["Title"]),
               Paragraph(escape(f"{config['test']['class']} · {', '.join(config['test']['subjects'])} · {config['test']['date']}"), styles["Normal"]),
               Spacer(1, 12), table, PageBreak()]

    def add_analysis(title: str, headers: list[str], rows: list[list[Any]], widths: list[int]) -> None:
        content.extend([Paragraph(escape(title), styles["Heading1"]), Spacer(1, 6)])
        analysis_table = Table([headers, *rows], repeatRows=1, colWidths=widths)
        analysis_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324d")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#aab5bf")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f8")]),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]))
        content.extend([analysis_table, Spacer(1, 14)])

    items = item_analysis(config, records)
    add_analysis("Item analysis", ["Q", "Topic", "Correct", "Attempted", "Difficulty", "Discrimination", "Dropped"],
                 [[row["question"], Paragraph(escape(str(row["topic"] or "")), styles["BodyText"]),
                   row["correct_count"], row["attempted"],
                   "—" if row["difficulty"] is None else f"{row['difficulty']:.0%}",
                   "—" if row["discrimination"] is None else f"{row['discrimination']:+.0%}",
                   "Yes" if row["dropped"] else "No"] for row in items],
                 [30, 150, 55, 55, 80, 90, 55])
    topics = topic_analysis(config, records)
    add_analysis("Topic analysis", ["Section", "Topic", "Questions", "Students", "Average points / student"],
                 [[Paragraph(escape(str(row["section"] or "")), styles["BodyText"]),
                   Paragraph(escape(str(row["topic"] or "")), styles["BodyText"]), row["question_count"],
                   row["student_count"], row["average_points_per_student"]] for row in topics],
                 [130, 180, 70, 70, 150])
    subjects = subject_analysis(config, records)
    add_analysis("Subject analysis", ["Subject", "Questions", "Students", "Average points / student"],
                 [[Paragraph(escape(str(row["subject"])), styles["BodyText"]), row["question_count"],
                   row["student_count"], row["average_points_per_student"]] for row in subjects],
                 [210, 90, 90, 210])
    doc.build(content)
    return path


def export_item_analysis_csv(config: dict[str, Any], records: list[dict[str, Any]], output: str | Path) -> Path:
    _require_exportable(config, records)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = item_analysis(config, records)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["question", "topic", "correct_count", "attempted", "difficulty", "discrimination", "dropped"])
        writer.writeheader()
        writer.writerows({**row, "topic": _safe_spreadsheet_text(row.get("topic"))} for row in rows)
    return path


def topic_analysis(config: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = class_results(config, records)
    question_meta = {str(question["id"]): question for question in config["questions"]}
    aggregates: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row["review_required"] or row.get("total") is None:
            continue
        by_id = {str(item["question"]): item for item in row["questions"]}
        for question_id, meta in question_meta.items():
            group = (str(meta.get("section", "")), str(meta.get("topic", "")))
            record = aggregates.setdefault(group, {"section": group[0], "topic": group[1], "question_ids": set(), "student_scores": defaultdict(float)})
            record["question_ids"].add(question_id)
            record["student_scores"][row.get("roll_number", "")] += float(by_id.get(question_id, {}).get("points", 0))
    output = []
    for record in sorted(aggregates.values(), key=lambda x: (x["section"], x["topic"])):
        scores = record["student_scores"]
        output.append({"section": record["section"], "topic": record["topic"], "question_count": len(record["question_ids"]),
                       "student_count": len(scores), "average_points_per_student": round(sum(scores.values()) / len(scores), 3) if scores else 0})
    return output


def export_topic_analysis_csv(config: dict[str, Any], records: list[dict[str, Any]], output: str | Path) -> Path:
    _require_exportable(config, records)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = topic_analysis(config, records)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["section", "topic", "question_count", "student_count", "average_points_per_student"])
        writer.writeheader()
        writer.writerows({**row, "section": _safe_spreadsheet_text(row.get("section")),
                          "topic": _safe_spreadsheet_text(row.get("topic"))} for row in rows)
    return path


def subject_analysis(config: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize earned points by each question section's configured subject."""
    eligible = [row for row in class_results(config, records)
                if not row["review_required"] and row.get("total") is not None]
    sections = config["test"].get("sections", [])
    fallback_subject = (config["test"].get("subjects") or ["General"])[0]
    subject_by_section = {str(section.get("title", "")): str(section.get("subject") or section.get("title") or fallback_subject)
                          for section in sections}
    subject_by_question: dict[str, str] = {}
    for question in config["questions"]:
        qid = str(question["id"])
        section_name = str(question.get("section", ""))
        match = next((item for item in sections
                      if int(item["question_start"]) <= int(qid) <= int(item["question_end"])), None)
        if match:
            subject = str(match.get("subject") or match.get("title") or fallback_subject)
        else:
            subject = subject_by_section.get(section_name, fallback_subject)
        subject_by_question[qid] = subject
    question_ids_by_subject: dict[str, set[str]] = defaultdict(set)
    for qid, subject in subject_by_question.items():
        question_ids_by_subject[subject].add(qid)
    totals_by_subject: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for index, row in enumerate(eligible):
        student = str(row.get("roll_number") or f"__row_{index}")
        for item in row["questions"]:
            subject = subject_by_question.get(str(item["question"]))
            if subject is not None:
                totals_by_subject[subject][student] += float(item["points"])
    output = []
    for subject in sorted(question_ids_by_subject):
        scores = totals_by_subject[subject]
        output.append({"subject": subject, "question_count": len(question_ids_by_subject[subject]),
                       "student_count": len(scores),
                       "average_points_per_student": round(sum(scores.values()) / len(scores), 3) if scores else 0})
    return output


def export_subject_analysis_csv(config: dict[str, Any], records: list[dict[str, Any]], output: str | Path) -> Path:
    _require_exportable(config, records)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = subject_analysis(config, records)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["subject", "question_count", "student_count", "average_points_per_student"])
        writer.writeheader()
        writer.writerows({**row, "subject": _safe_spreadsheet_text(row.get("subject"))} for row in rows)
    return path


def export_student_scorecards_pdf(config: dict[str, Any], records: list[dict[str, Any]], output: str | Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet
    from xml.sax.saxutils import escape
    rows = class_results(config, records)
    _require_exportable(config, records)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=A4, title=f"{config['test']['title']} student scorecards")
    styles = getSampleStyleSheet()
    content = []
    question_meta = {int(q["id"]): q for q in config["questions"]}
    for index, row in enumerate(rows):
        if index:
            content.append(PageBreak())
        name = row.get("student_name") or "Student"
        identity = f"{name} · Roll {row.get('roll_number', '')} · {row.get('class_name') or config['test']['class']}" + (f" · Set {row.get('set_code')}" if len(config["sheet"]["set_codes"]) > 1 else "")
        content.extend([Paragraph(escape(config["test"]["title"]), styles["Title"]),
                        Paragraph(escape(f"{', '.join(config['test']['subjects'])} · {config['test']['date']}"), styles["Normal"]),
                        Paragraph(escape(f"Score {row.get('total')} · Rank {row.get('rank')} · Correct {row.get('correct')} · Incorrect {row.get('incorrect')} · Blank {row.get('blank')}"), styles["Normal"]), Spacer(1, 12)])
        content.append(Paragraph(escape(_source_line(row)), styles["Normal"]))
        table_rows = [[Paragraph(escape(identity), styles["Heading3"]), "", "", "", "", ""],
                      ["Q", "Section / topic", "Student", "Key", "Result", "Points"]]
        for item in row["questions"]:
            meta = question_meta[item["question"]]
            label = " / ".join(filter(None, [str(meta.get("section", "")), str(meta.get("topic", ""))]))
            table_rows.append([item["question"], label, item.get("answer") or "Blank", item.get("expected") or "—", item["status"], item["points"]])
        table = Table(table_rows, repeatRows=2, colWidths=[28, 150, 55, 40, 55, 42])
        table.setStyle(TableStyle([("SPAN", (0, 0), (-1, 0)), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf0f6")),
                                   ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#17324d")),
                                   ("TEXTCOLOR", (0, 1), (-1, 1), colors.white),
                                   ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#aab5bf")),
                                   ("ROWBACKGROUNDS", (0, 2), (-1, -1), [colors.white, colors.HexColor("#f1f5f8")]),
                                   ("FONTSIZE", (0, 0), (-1, -1), 7), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                   ("PADDING", (0, 0), (-1, -1), 4)]))
        content.append(table)
    doc.build(content)
    return path


def _subject_for_question(config: dict[str, Any]) -> dict[str, str]:
    fallback = (config["test"].get("subjects") or ["General"])[0]
    sections = config["test"].get("sections", [])
    result: dict[str, str] = {}
    for question in config["questions"]:
        qid = str(question["id"])
        section_name = str(question.get("section", ""))
        match = next((section for section in sections
                      if int(section["question_start"]) <= int(qid) <= int(section["question_end"])), None)
        if match:
            result[qid] = str(match.get("subject") or match.get("title") or fallback)
        else:
            result[qid] = str(next((section.get("subject") or section.get("title")
                                    for section in sections if section.get("title") == section_name), fallback))
    return result


def _student_subject_scores(row: dict[str, Any], question_subject: dict[str, str]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for answer in row.get("questions", []):
        totals[question_subject.get(str(answer["question"]), "General")] += float(answer.get("points", 0))
    return dict(totals)


def _pdf_styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportSmall", parent=styles["BodyText"], fontName="Helvetica", fontSize=7, leading=8))
    styles.add(ParagraphStyle(name="ReportMuted", parent=styles["BodyText"], textColor=colors.HexColor("#526477"), fontSize=8, leading=10))
    return colors, styles


def _styled_table(data, widths=None, *, repeat=1, font_size=7):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    result = Table(data, colWidths=widths, repeatRows=repeat, hAlign="LEFT")
    result.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CFD8E3")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6F9")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("PADDING", (0, 0), (-1, -1), 3.5),
    ]))
    return result


def export_subject_score_list_pdf(config: dict[str, Any], records: list[dict[str, Any]], output: str | Path) -> Path:
    """Ranked mark list with per-subject scores, matching the supplied section-wise list."""
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from xml.sax.saxutils import escape

    _require_exportable(config, records)
    rows = class_results(config, records)
    subjects = list(dict.fromkeys(_subject_for_question(config).values()))
    subject_by_question = _subject_for_question(config)
    path = Path(output); path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=22, rightMargin=22, topMargin=24, bottomMargin=24,
                            title=f"{config['test']['title']} subject-wise mark list")
    colors, styles = _pdf_styles()
    content = [Paragraph("SUBJECT-WISE MARK LIST", styles["Title"]),
               Paragraph(escape(f"{config['test']['title']}  |  {config['test']['class']}  |  {', '.join(subjects)}  |  {config['test']['date']}"), styles["Normal"]),
               Spacer(1, 10)]
    headers = ["Rank", "Roll number", "Candidate name", "Uploaded file", "Imported at (UTC)", *subjects, "Total", "%"]
    data = [headers]
    for row in rows:
        by_subject = _student_subject_scores(row, subject_by_question)
        filename, _page, imported_at = source_file_details(row)
        data.append([row.get("rank"), row.get("roll_number"), row.get("student_name") or "", *
                     [filename, imported_at], *[round(by_subject.get(subject, 0), 2) for subject in subjects], row.get("total"), row.get("percent")])
    first_widths = [34, 58, 118, 128, 92]
    widths = first_widths + [max(46, min(64, 430 / max(1, len(subjects))))] * len(subjects) + [52, 42]
    content.append(_styled_table(data, widths, repeat=1, font_size=7))
    content.extend([Spacer(1, 8), Paragraph(escape(f"Students: {len(rows)}  |  Maximum score: {max_score(config):g}  |  Rank ties share the same rank."), styles["ReportMuted"])])
    doc.build(content)
    return path


def export_candidate_response_pdf(config: dict[str, Any], records: list[dict[str, Any]], output: str | Path) -> Path:
    """Per-candidate response analysis with topic, key, answer, result, and item rates."""
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    from xml.sax.saxutils import escape

    _require_exportable(config, records)
    rows = class_results(config, records)
    eligible = _eligible(config, records)
    stats_by_set = {code: {item["question"]: item for item in item_analysis(config, records, code)}
                    for code in config["sets"]}
    question_meta = {int(question["id"]): question for question in config["questions"]}
    q_subject = _subject_for_question(config)
    path = Path(output); path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=20, rightMargin=20, topMargin=22, bottomMargin=22,
                            title=f"{config['test']['title']} candidate response analysis")
    colors, styles = _pdf_styles()
    content = []
    for index, row in enumerate(rows):
        if index:
            content.append(PageBreak())
        content.extend([Paragraph("QUESTION RESPONSE ANALYSIS", styles["Title"]),
                        Paragraph(escape(f"{config['test']['title']}  |  {config['test']['date']}  |  {q_subject.get('1', ', '.join(config['test']['subjects']))}"), styles["Normal"]),
                        Spacer(1, 5),
                        Paragraph(escape(f"{row.get('student_name') or 'Student'}  |  Roll {row.get('roll_number')}  |  Class {row.get('class_name') or config['test']['class']}  |  Set {row.get('set_code')}"), styles["Heading3"]),
                        Paragraph(escape(f"Score {row.get('total'):g}/{max_score(config):g} ({row.get('percent')}%)  |  Rank {row.get('rank')}  |  Percentile {row.get('percentile')}%  |  Class average {summary_stats(config, records)['mean']}"), styles["ReportMuted"]),
                        Spacer(1, 7)])
        content.append(Paragraph(escape(_source_line(row)), styles["ReportMuted"]))
        headers = ["Q", "ATT%", "RIGHT%", "WRONG%", "SUBJECT", "TOPIC / SECTION", "KEY", "ANSWER", "RESULT", "MARKS"]
        data = [headers]
        status_counts = defaultdict(int)
        for answer in row["questions"]:
            qid = int(answer["question"]); meta = question_meta[qid]
            rates = stats_by_set[row["set_code"]].get(qid, {})
            denominator = max(1, len(eligible))
            attempted = int(rates.get("attempted", 0)); correct = int(rates.get("correct_count", 0))
            status_counts[answer["status"]] += 1
            expected = config["sets"][row["set_code"]]["answer_key"].get(str(qid))
            data.append([qid, f"{attempted / denominator:.0%}", f"{correct / denominator:.0%}",
                         f"{max(0, attempted - correct) / denominator:.0%}", q_subject.get(str(qid), ""),
                         " / ".join(filter(None, [str(meta.get("section", "")), str(meta.get("topic", ""))])),
                         expected or "-", answer.get("answer") or "Blank",
                         {"correct": "Correct", "incorrect": "Wrong", "blank": "Left", "dropped": "Dropped", "bonus": "Bonus"}.get(answer["status"], answer["status"]),
                         answer.get("points", 0)])
        widths = [24, 37, 44, 44, 74, 150, 32, 42, 48, 42]
        content.append(_styled_table(data, widths, repeat=1, font_size=6.6))
        subject_points = _student_subject_scores(row, q_subject)
        subject_data = [["Subject", "Score"]] + [[subject, subject_points.get(subject, 0)] for subject in dict.fromkeys(q_subject.values())]
        content.extend([Spacer(1, 7), _styled_table(subject_data, [180, 58], font_size=7),
                        Spacer(1, 5), Paragraph(escape(f"Correct: {status_counts['correct']}  |  Wrong: {status_counts['incorrect']}  |  Left: {status_counts['blank']}  |  Total: {row.get('total'):g}"), styles["ReportMuted"])])
    doc.build(content)
    return path


def export_subsection_pdf(config: dict[str, Any], records: list[dict[str, Any]], output: str | Path) -> Path:
    """Per-candidate summary by section/topic, including question numbers by outcome."""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    from xml.sax.saxutils import escape

    _require_exportable(config, records)
    rows = class_results(config, records)
    meta = {int(question["id"]): question for question in config["questions"]}
    path = Path(output); path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=30, rightMargin=30, topMargin=28, bottomMargin=28,
                            title=f"{config['test']['title']} sub-section report")
    colors, styles = _pdf_styles()
    content = []
    for index, row in enumerate(rows):
        if index:
            content.append(PageBreak())
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for answer in row["questions"]:
            question = meta[int(answer["question"])]
            key = (str(question.get("section", "")), str(question.get("topic", "")))
            group = groups.setdefault(key, {"questions": [], "correct": [], "wrong": [], "left": [], "points": 0.0})
            group["questions"].append(str(answer["question"]))
            outcome = "correct" if answer["status"] in {"correct", "bonus"} else "wrong" if answer["status"] == "incorrect" else "left"
            group[outcome].append(str(answer["question"]))
            group["points"] += float(answer.get("points", 0))
        content.extend([Paragraph("SUB-SECTION PERFORMANCE", styles["Title"]),
                        Paragraph(escape(f"{config['test']['title']}  |  {row.get('student_name') or 'Student'} [{row.get('roll_number')}]  |  Rank {row.get('rank')}  |  Score {row.get('total')}"), styles["Normal"]), Spacer(1, 10)])
        content.append(Paragraph(escape(_source_line(row)), styles["ReportMuted"]))
        data = [["Section", "Topic", "Qs", "Correct", "Wrong", "Unattempted", "Score"]]
        for (section, topic), group in sorted(groups.items()):
            data.append([section, topic or "-", len(group["questions"]), len(group["correct"]), len(group["wrong"]), len(group["left"]), round(group["points"], 2)])
        content.append(_styled_table(data, [90, 115, 35, 50, 45, 68, 48], font_size=7))
        content.extend([Spacer(1, 8), Paragraph("Question numbers by result", styles["Heading2"]), Spacer(1, 4)])
        qdata = [["Section / topic", "Correct", "Wrong", "Unattempted"]]
        for (section, topic), group in sorted(groups.items()):
            qdata.append([f"{section} / {topic}".strip(" /"), ", ".join(group["correct"]) or "-",
                          ", ".join(group["wrong"]) or "-", ", ".join(group["left"]) or "-"])
        content.append(_styled_table(qdata, [100, 95, 95, 105], font_size=6.8))
    doc.build(content)
    return path


def export_item_statistics_pdf(config: dict[str, Any], records: list[dict[str, Any]], output: str | Path) -> Path:
    """Expanded test item report with score statistics, difficulty, discrimination, and distractors."""
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from xml.sax.saxutils import escape

    _require_exportable(config, records)
    rows = _eligible(config, records)
    stats = summary_stats(config, records)
    items = item_analysis(config, records)
    distractors = {row["question"]: row for row in distractor_analysis(config, records)}
    meta = {int(question["id"]): question for question in config["questions"]}
    path = Path(output); path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=22, rightMargin=22, topMargin=24, bottomMargin=24,
                            title=f"{config['test']['title']} expanded item statistics")
    colors, styles = _pdf_styles()
    score_summary = (f"Students {stats['students']}  |  Mean {stats['mean']}  |  Median {stats['median']}  |  SD {stats['stdev']}  |  "
                     f"High {stats['high']}  |  Low {stats['low']}  |  Pass rate {stats['pass_rate']}%  |  KR-20 {stats['reliability']}")
    content = [Paragraph("TEST ITEM ANALYSIS", styles["Title"]),
               Paragraph(escape(f"{config['test']['title']}  |  {', '.join(config['test']['subjects'])}  |  {config['test']['date']}"), styles["Normal"]),
               Spacer(1, 6), Paragraph(escape(score_summary), styles["ReportMuted"]), Spacer(1, 10)]
    headers = ["Q", "Section", "Topic", "Key", "Correct", "Attempted", "Difficulty", "Discrimination", *[f"{o} picks" for o in config["sheet"]["options"]], "Blank"]
    data = [headers]
    for item in items:
        qid = int(item["question"]); topic = meta[qid]
        distribution = distractors[qid]
        expected = (config["sets"][next(iter(config["sets"]))]["answer_key"].get(str(qid))
                    if len(config["sets"]) == 1 else None)
        data.append([qid, topic.get("section", ""), topic.get("topic", ""), expected or "by set",
                     item["correct_count"], item["attempted"], "-" if item["difficulty"] is None else f"{item['difficulty']:.0%}",
                     "-" if item["discrimination"] is None else f"{item['discrimination']:+.0%}",
                     *[distribution.get(f"option_{option}", 0) for option in config["sheet"]["options"]], distribution.get("blank", 0)])
    available_width = 798
    widths = [24, 80, 100, 42, 45, 48, 54, 66] + [min(42, max(32, available_width / (len(config["sheet"]["options"]) + 8))) for _ in config["sheet"]["options"]] + [38]
    content.append(_styled_table(data, widths, font_size=6.7))
    doc.build(content)
    return path


def export_assessment_pdf(config: dict[str, Any], records: list[dict[str, Any]], output: str | Path) -> Path:
    """Individual summary pages with subject performance against class average and topper."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Flowable
    from xml.sax.saxutils import escape

    _require_exportable(config, records)
    rows = class_results(config, records)
    eligible = _eligible(config, records)
    stats = summary_stats(config, records)
    subject_map = _subject_for_question(config)
    subjects = list(dict.fromkeys(subject_map.values()))
    maxima = defaultdict(float)
    for question in config["questions"]:
        qid = str(question["id"])
        if qid not in {str(item) for item in config["scoring"].get("dropped_questions", [])}:
            maxima[subject_map[qid]] += float(config["scoring"]["correct"])

    class_subjects = {row.get("roll_number"): _student_subject_scores(row, subject_map) for row in eligible}

    class ComparisonBars(Flowable):
        def __init__(self, student, width=520):
            super().__init__(); self.student = student; self.width = width
            self.height = max(58, len(subjects) * 54 + 22)

        def draw(self):
            canvas = self.canv
            canvas.setFont("Helvetica", 8)
            colors_ = {"student": "#087E73", "average": "#4C78A8", "topper": "#D08A22"}
            y = self.height - 16
            for subject in subjects:
                values = [class_subjects.get(row.get("roll_number"), {}).get(subject, 0) for row in eligible]
                average = sum(values) / len(values) if values else 0
                top = max(values, default=0)
                mine = self.student.get(subject, 0)
                maximum = max(1.0, maxima.get(subject, 0))
                canvas.setFillColorRGB(.12, .18, .25); canvas.drawString(0, y, subject[:24])
                for offset, (label_, value, colour) in enumerate((("You", mine, colors_["student"]), ("Avg", average, colors_["average"]), ("Top", top, colors_["topper"]))):
                    bar_y = y - 11 - offset * 11
                    canvas.setFillColorRGB(.9, .92, .94); canvas.rect(92, bar_y, self.width - 140, 6, fill=1, stroke=0)
                    canvas.setFillColor(HexColor(colour)); canvas.rect(92, bar_y, (self.width - 140) * max(0, value) / maximum, 6, fill=1, stroke=0)
                    canvas.setFillColorRGB(.28, .34, .4); canvas.drawString(4, bar_y, label_)
                    canvas.drawRightString(self.width, bar_y, f"{value:.2f} / {maximum:.2f}")
                y -= 54

    path = Path(output); path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=38, rightMargin=38, topMargin=30, bottomMargin=30,
                            title=f"{config['test']['title']} assessment reports")
    colors, styles = _pdf_styles()
    content = []
    for index, row in enumerate(rows):
        if index:
            content.append(PageBreak())
        by_subject = _student_subject_scores(row, subject_map)
        content.extend([Paragraph("TEST PERFORMANCE ANALYSIS", styles["Title"]),
                        Paragraph(escape(f"{config['test']['title']}  |  {', '.join(subjects)}  |  {config['test']['date']}"), styles["Normal"]),
                        Spacer(1, 8), Paragraph(escape(f"{row.get('student_name') or 'Student'} [{row.get('roll_number')}]  |  Class {row.get('class_name') or config['test']['class']}"), styles["Heading2"]),
                        Paragraph(escape(f"Score {row.get('total')}/{max_score(config)} ({row.get('percent')}%)  |  Rank {row.get('rank')}  |  Percentile {row.get('percentile')}%"), styles["Heading3"]),
                        Paragraph(escape(f"Candidates {stats['students']}  |  Average {stats['mean']}  |  High {stats['high']}  |  Low {stats['low']}  |  Pass rate {stats['pass_rate']}%"), styles["ReportMuted"]),
                        Spacer(1, 10), Paragraph("Subject analysis: your score compared with the class average and highest score", styles["Heading2"]),
                        ComparisonBars(by_subject), Spacer(1, 8)])
        content.append(Paragraph(escape(_source_line(row)), styles["ReportMuted"]))
        table_data = [["Subject", "Your score", "Maximum", "Class average", "Top score"]]
        for subject in subjects:
            values = [class_subjects.get(candidate.get("roll_number"), {}).get(subject, 0) for candidate in eligible]
            table_data.append([subject, round(by_subject.get(subject, 0), 2), round(maxima.get(subject, 0), 2),
                               round(sum(values) / len(values), 2) if values else 0, round(max(values, default=0), 2)])
        content.append(_styled_table(table_data, [130, 75, 65, 85, 75], font_size=7))
    doc.build(content)
    return path


def export_distractor_analysis_csv(config: dict[str, Any], records: list[dict[str, Any]], output: str | Path) -> Path:
    _require_exportable(config, records)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    options = [str(o) for o in config["sheet"]["options"]]
    fields = ["question", "topic", "key", *[f"option_{o}" for o in options], "blank", "top_wrong", "top_wrong_count"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({**row, "topic": _safe_spreadsheet_text(row.get("topic"))} for row in distractor_analysis(config, records))
    return path


#: Every export, in the order shown to the user: key -> (title, description, file name)
EXPORTS: dict[str, tuple[str, str, str]] = {
    "xlsx": ("Excel workbook", "Marklist plus summary, class and question analysis sheets", "marklist.xlsx"),
    "csv": ("Marklist (CSV)", "Ranked scores and every answer, for any spreadsheet", "marklist.csv"),
    "pdf": ("Results report (PDF)", "Printable ranked list with the question analysis", "results-report.pdf"),
    "subject-list": ("Subject-wise mark list (PDF)", "Candidate ranks with scores for each subject", "subject-wise-mark-list.pdf"),
    "cards": ("Student scorecards (PDF)", "One page per student, question by question", "student-scorecards.pdf"),
    "responses": ("Candidate response analysis (PDF)", "One question-by-question report per student", "candidate-response-analysis.pdf"),
    "assessment": ("Assessment reports (PDF)", "Individual performance against class averages and toppers", "assessment-reports.pdf"),
    "sections": ("Sub-section report (PDF)", "Each student’s correct, wrong, and unattempted questions by topic", "sub-section-report.pdf"),
    "item-report": ("Expanded item statistics (PDF)", "Test statistics, item difficulty, discrimination, and option counts", "item-statistics.pdf"),
    "item": ("Item analysis (CSV)", "Difficulty and discrimination of each question", "item-analysis.csv"),
    "distractor": ("Distractor analysis (CSV)", "How often each option was chosen, per question", "distractor-analysis.csv"),
    "topic": ("Topic analysis (CSV)", "Average points per topic", "topic-analysis.csv"),
    "subject": ("Subject analysis (CSV)", "Average points per subject", "subject-analysis.csv"),
}


def export_function(kind: str):
    return {"xlsx": export_xlsx, "csv": export_csv, "pdf": export_report_pdf, "cards": export_student_scorecards_pdf,
            "subject-list": export_subject_score_list_pdf, "responses": export_candidate_response_pdf,
            "assessment": export_assessment_pdf, "sections": export_subsection_pdf,
            "item-report": export_item_statistics_pdf,
            "item": export_item_analysis_csv, "distractor": export_distractor_analysis_csv,
            "topic": export_topic_analysis_csv, "subject": export_subject_analysis_csv}[kind]


def export_all(config: dict[str, Any], records: list[dict[str, Any]], folder: str | Path,
               kinds: Iterable[str] | None = None) -> list[Path]:
    """Write several exports into one folder in a single step."""
    _require_exportable(config, records)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    return [export_function(kind)(config, records, folder / EXPORTS[kind][2])
            for kind in (list(kinds) if kinds is not None else list(EXPORTS))]
