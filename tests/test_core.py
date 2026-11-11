import json
import csv
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest

from omr_exam.config import load_config, validate_config
from omr_exam.scoring import score_answers
from omr_exam.answer_keys import import_excel_key, parse_pasted_key, validate_answer_key
from omr_exam.database import Repository
from omr_exam.sheets import generate_sheet_pdf
from omr_exam.reports import (class_results, item_analysis, export_csv, export_student_scorecards_pdf,
                              export_report_pdf, export_subject_analysis_csv, subject_analysis, export_xlsx)
from omr_exam.test_builder import create_test_config
from omr_exam.layout import create_layout, maximum_columns_for_options, maximum_questions_per_column

ROOT = Path(__file__).resolve().parents[1]


def test_sample_config_validates():
    config = load_config(ROOT / "examples" / "sample-test.json")
    assert len(config["questions"]) == 5


def test_test_configuration_json_schema_is_valid():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "config" / "test-config.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)


def test_test_builder_customizes_printed_name_and_class_fields():
    base = {"title": "Field test", "test_date": "2026-11-10", "class_name": "10A", "subject": "Math",
            "question_count": 4, "options": ["A", "B", "C", "D"], "roll_number_digits": 4,
            "set_codes": ["A"], "columns": 1}
    for fields, expected in (([], set()), (["name"], {"NAME"}), (["class"], {"CLASS"}),
                             (["name", "class"], {"NAME", "CLASS"})):
        config = create_test_config(**base, fields=fields)
        validate_config(config)
        identity_labels = create_layout(config).identity_labels
        rendered_fields = {name for name, _x, _y in identity_labels}
        assert rendered_fields - {"ROLL NUMBER", "SET"} == expected
        if fields == ["class"]:
            assert next(x for name, x, _y in identity_labels if name == "CLASS") == 18.0


def test_test_builder_rejects_invalid_column_geometry_before_returning_config():
    base = {"title": "Geometry", "test_date": "2026-11-10", "class_name": "10A", "subject": "Math",
            "question_count": 4, "options": ["A", "B", "C", "D"], "roll_number_digits": 4,
            "set_codes": ["A"]}
    for overrides in ({"columns": 0}, {"columns": 4}, {"columns": 1, "questions_per_column": 0},
                      {"columns": 1, "question_count": 50}):
        with pytest.raises(ValueError):
            create_test_config(**{**base, **overrides})


def test_short_test_question_rows_remain_compact_on_the_printed_sheet():
    config = load_config(ROOT / "examples" / "sample-test.json")
    layout = create_layout(config)
    y_positions = {
        question: next(bubble.y for bubble in layout.bubbles
                       if bubble.group == "answer" and bubble.index == str(question))
        for question in range(1, 6)
    }
    assert y_positions == {1: 100.0, 2: 109.0, 3: 118.0, 4: 100.0, 5: 109.0}


def test_sheet_builder_capacity_matches_printable_bubble_geometry():
    assert maximum_columns_for_options(210, 2) == 4
    assert maximum_columns_for_options(210, 4) == 3
    assert maximum_columns_for_options(210, 5) == 2
    assert maximum_questions_per_column(297) == 45


def test_test_builder_accepts_exact_capacity_and_rejects_one_more_row():
    base = {"title": "Capacity", "test_date": "2026-11-10", "class_name": "10A", "subject": "Math",
            "options": ["A", "B", "C", "D"], "roll_number_digits": 4, "set_codes": ["A"],
            "columns": 2}
    at_capacity = create_test_config(**base, question_count=90)
    assert len(at_capacity["questions"]) == 90
    with pytest.raises(ValueError, match="do not fit"):
        create_test_config(**base, question_count=91)


def test_config_rejects_case_colliding_reader_options_and_set_codes():
    config = load_config(ROOT / "examples" / "sample-test.json")
    config["sheet"]["options"][1] = config["sheet"]["options"][0].lower()
    try:
        validate_config(config)
    except ValueError as exc:
        assert "options must be unique without regard to letter case" in str(exc)
    else:
        raise AssertionError("Options that collapse in the reader were accepted")

    config = load_config(ROOT / "examples" / "sample-test.json")
    config["sheet"]["set_codes"][1] = "a"
    config["sets"]["a"] = config["sets"].pop("B")
    try:
        validate_config(config)
    except ValueError as exc:
        assert "set codes must be unique without regard to letter case" in str(exc)
    else:
        raise AssertionError("Set codes that collapse in review entry were accepted")


def test_config_validates_reader_thresholds_against_defaults():
    for reader, expected in (({"faint_threshold": 0.25}, "faint_threshold"),
                             ({"mark_threshold": 0.01}, "mark_threshold")):
        config = load_config(ROOT / "examples" / "sample-test.json")
        config["reader"] = reader
        with pytest.raises(ValueError, match=expected):
            validate_config(config)


def test_config_rejects_duplicate_optional_sheet_fields():
    config = load_config(ROOT / "examples" / "sample-test.json")
    config["sheet"]["fields"] = ["name", "name"]
    with pytest.raises(ValueError):
        validate_config(config)


def test_config_rejects_section_ranges_with_gaps_or_overlaps():
    for ranges in (((1, 2), (4, 5)), ((1, 3), (3, 5)), ((1, 6),)):
        config = load_config(ROOT / "examples" / "sample-test.json")
        config["test"]["sections"] = [
            {"title": f"Section {index}", "subject": "Science",
             "question_start": start, "question_end": end}
            for index, (start, end) in enumerate(ranges, start=1)
        ]
        with pytest.raises(ValueError, match="section ranges"):
            validate_config(config)


def test_config_rejects_question_section_labels_that_disagree_with_ranges():
    config = load_config(ROOT / "examples" / "sample-test.json")
    config["test"]["sections"] = [
        {"title": "Algebra", "subject": "Math", "question_start": 1, "question_end": 2},
        {"title": "Physics", "subject": "Science", "question_start": 3, "question_end": 5},
    ]
    config["questions"][0]["section"] = "Physics"
    with pytest.raises(ValueError, match="section label must match"):
        validate_config(config)


def test_config_rejects_non_finite_json_numbers():
    for path, value in (("correct", float("nan")), ("incorrect", float("inf"))):
        config = load_config(ROOT / "examples" / "sample-test.json")
        config["scoring"][path] = value
        with pytest.raises(ValueError, match="must be finite"):
            validate_config(config)


def test_config_rejects_question_density_that_cannot_fit_sheet(tmp_path):
    config = json.loads((ROOT / "examples" / "sample-test.json").read_text(encoding="utf-8"))
    config["sheet"]["questions_per_column"] = 100
    path = tmp_path / "too-dense.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    try:
        load_config(path)
    except ValueError as exc:
        assert "Questions do not fit on the page" in str(exc)
    else:
        raise AssertionError("A layout that cannot fit was accepted as a valid test config")


def test_scoring():
    config = json.loads((ROOT / "examples" / "sample-test.json").read_text(encoding="utf-8"))
    result = score_answers(config, "A", {1: "B", 2: "C", 3: None})
    assert result["total"] == 3.0
    assert (result["correct"], result["incorrect"], result["blank"]) == (1, 1, 3)


def test_answer_key_import_validation():
    config = load_config(ROOT / "examples" / "sample-test.json")
    parsed = parse_pasted_key("1\tB\n2\tA\n3\tD\n4\tC\n5\tB", config)
    assert parsed == config["sets"]["A"]["answer_key"]
    try:
        validate_answer_key(config, {"1": "Z"})
    except ValueError as exc:
        assert "Invalid answer" in str(exc)
    else:
        raise AssertionError("Invalid answer option was accepted")


def test_dropped_questions_do_not_require_answer_key_entries():
    config = load_config(ROOT / "examples" / "sample-test.json")
    config["scoring"]["dropped_questions"] = [4]
    key = dict(config["sets"]["A"]["answer_key"])
    del key["4"]
    assert validate_answer_key(config, key) == key
    assert parse_pasted_key("B,A,D,,B", config) == key
    score = score_answers(config, "A", {"1": "B", "2": "A", "3": "D", "5": "B"})
    assert score["questions"][3]["status"] == "dropped"


def test_answer_key_rejects_duplicate_normalized_questions():
    config = load_config(ROOT / "examples" / "sample-test.json")
    try:
        validate_answer_key(config, {1: "B", "1": "A", "2": "A", "3": "D", "4": "C", "5": "B"})
    except ValueError as exc:
        assert "appears more than once" in str(exc)
    else:
        raise AssertionError("Duplicate question IDs were accepted")


def test_excel_answer_key_rejects_trailing_cells(tmp_path):
    pytest = __import__("pytest")
    openpyxl = pytest.importorskip("openpyxl")
    config = load_config(ROOT / "examples" / "sample-test.json")
    path = tmp_path / "key.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for answer in ["B", "A", "D", "C", "B", "A"]:
        sheet.append([answer])
    workbook.save(path)
    try:
        import_excel_key(path, config)
    except ValueError as exc:
        assert "exactly 5 answers" in str(exc)
    else:
        raise AssertionError("Excel import silently ignored an extra answer")


def test_excel_answer_key_accepts_common_header_aliases(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    config = load_config(ROOT / "examples" / "sample-test.json")
    path = tmp_path / "key-with-common-headings.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Question Number", "Answer Key", "Notes"])
    for question, answer in ((1, "B"), (2, "A"), (3, "D"), (4, "C"), (5, "B")):
        sheet.append([question, answer, "verified"])
    workbook.save(path)
    assert import_excel_key(path, config) == config["sets"]["A"]["answer_key"]


def test_excel_answer_key_rejects_duplicate_question_headers(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    config = load_config(ROOT / "examples" / "sample-test.json")
    path = tmp_path / "duplicate-headers.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Question", "Q", "Answer"])
    workbook.save(path)
    with pytest.raises(ValueError, match="duplicate question or answer headers"):
        import_excel_key(path, config)


def test_database_audit_recalculate_and_backup(tmp_path):
    config = load_config(ROOT / "examples" / "sample-test.json")
    repository = Repository(tmp_path / "exam.sqlite3")
    repository.set_setting("scan_dpi", "300")
    repository.set_setting("default_correct_points", "5")
    test_id = repository.save_test(config)
    batch_id = repository.create_batch(test_id, "samples")
    read = SimpleNamespace(roll_number="123456", set_code="A", answers={"1": "A", "2": None},
                           flags=[{"kind": "blank", "question": "2"}], requires_review=False,
                           image_path="sheet.png", preview_path="", thresholds={}, marks={})
    score = score_answers(config, "A", read.answers)
    result_id = repository.store_read(batch_id, read, score)
    updated = repository.update_result(result_id, answers={"1": "B", "2": None, "3": None, "4": None, "5": None},
                                       roll_number="123456", set_code="A", config=config)
    assert updated["total"] == 4
    assert len(repository.results_for_batch(batch_id)) == 1
    changed_key = json.loads(json.dumps(config))
    changed_key["sets"]["A"]["answer_key"]["1"] = "A"
    assert repository.update_test_config_and_rescore(test_id, changed_key) == 1
    assert repository.results_for_batch(batch_id)[0]["score"] == -1
    backup_path = repository.backup_to(tmp_path / "backup.sqlite3")
    restored = Repository(tmp_path / "restored.sqlite3")
    restored.restore_from(backup_path)
    assert len(restored.tests()) == 1
    assert restored.get_setting("scan_dpi") == "300"
    assert restored.get_setting("default_correct_points") == "5"
    (tmp_path / "previews").mkdir()
    (tmp_path / "previews" / "sample.txt").write_text("preview")
    (tmp_path / "scans").mkdir()
    (tmp_path / "scans" / "sheet.txt").write_text("scan")
    bundle_path = repository.backup_bundle_to(tmp_path / "full.omrbackup")
    portable = Repository(tmp_path / "portable" / "exam.sqlite3")
    portable.restore_bundle_from(bundle_path)
    assert (tmp_path / "portable" / "previews" / "sample.txt").read_text() == "preview"
    assert (tmp_path / "portable" / "scans" / "sheet.txt").read_text() == "scan"
    assert portable.get_setting("scan_dpi") == "300"


def test_bundle_restore_rolls_back_live_data_when_database_apply_fails(tmp_path, monkeypatch):
    config = load_config(ROOT / "examples" / "sample-test.json")
    source_config = json.loads(json.dumps(config))
    source_config["test"]["title"] = "Backup version"
    source = Repository(tmp_path / "source" / "exam.sqlite3")
    source.save_test(source_config)
    for directory, filename in (("scans", "new-scan.txt"), ("previews", "new-preview.txt")):
        folder = tmp_path / "source" / directory
        folder.mkdir()
        (folder / filename).write_text("from backup")
    bundle = source.backup_bundle_to(tmp_path / "bundle.omrbackup")

    target_config = json.loads(json.dumps(config))
    target_config["test"]["title"] = "Current version"
    target = Repository(tmp_path / "target" / "exam.sqlite3")
    target.save_test(target_config)
    for directory, filename in (("scans", "old-scan.txt"), ("previews", "old-preview.txt")):
        folder = tmp_path / "target" / directory
        folder.mkdir()
        (folder / filename).write_text("current data")

    original_restore = target.restore_from
    restore_calls = 0

    def fail_once(path):
        nonlocal restore_calls
        restore_calls += 1
        if restore_calls == 1:
            raise OSError("simulated database replace failure")
        return original_restore(path)

    monkeypatch.setattr(target, "restore_from", fail_once)
    pytest = __import__("pytest")
    with pytest.raises(OSError, match="simulated database replace failure"):
        target.restore_bundle_from(bundle)

    assert target.tests()[0]["title"] == "Current version"
    assert (tmp_path / "target" / "scans" / "old-scan.txt").read_text() == "current data"
    assert (tmp_path / "target" / "previews" / "old-preview.txt").read_text() == "current data"


def test_fifty_sheet_batch_scores_match_hand_totals_and_csv_export(tmp_path):
    config = load_config(ROOT / "examples" / "sample-test.json")
    repository = Repository(tmp_path / "exam.sqlite3")
    test_id = repository.save_test(config)
    batch_id = repository.create_batch(test_id, "50-sheet verification", expected_pages=50)
    expected_by_roll = {}
    options = config["sheet"]["options"]
    for index in range(1, 51):
        roll = f"{index:06d}"
        set_code = ["A", "B", "C"][(index - 1) % 3]
        key = config["sets"][set_code]["answer_key"]
        answers = {str(question["id"]): key[str(question["id"])] for question in config["questions"]}
        if index % 2 == 0:
            answers["1"] = next(option for option in options if option != key["1"])
        if index % 5 == 0:
            answers["2"] = None
        # +4 per correct response, -1 for the even-index wrong response, and 0 for a blank.
        expected_by_roll[roll] = 20 - (5 if index % 2 == 0 else 0) - (4 if index % 5 == 0 else 0)
        read = SimpleNamespace(roll_number=roll, set_code=set_code, answers=answers, requires_review=False,
                               image_path=f"sheet-{index:03d}.png", preview_path="", thresholds={}, marks={}, flags=[])
        repository.store_read(batch_id, read, score_answers(config, set_code, answers))
    assert repository.finish_batch(batch_id, 50) == "complete"
    records = repository.results_for_batch(batch_id)
    assert len(records) == 50
    assert {record["roll_number"]: record["score"] for record in records} == expected_by_roll
    assert sum(expected_by_roll.values()) == 835
    csv_path = export_csv(config, records, tmp_path / "marklist.csv")
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        exported = list(csv.DictReader(handle))
    assert len(exported) == 50
    assert sum(float(row["total"]) for row in exported) == 835


def test_rekey_and_rescore_roll_back_together_on_invalid_key(tmp_path):
    config = load_config(ROOT / "examples" / "sample-test.json")
    repository = Repository(tmp_path / "exam.sqlite3")
    test_id = repository.save_test(config)
    batch_id = repository.create_batch(test_id, "samples")
    read = SimpleNamespace(roll_number="123456", set_code="A", answers={"1": "B"}, requires_review=False,
                           image_path="sheet.png", preview_path="", thresholds={}, marks={}, flags=[])
    result_id = repository.store_read(batch_id, read, score_answers(config, "A", read.answers))
    before_score = repository.results_for_batch(batch_id)[0]["score"]
    invalid = json.loads(json.dumps(config))
    invalid["test"]["title"] = "Should roll back"
    invalid["sets"]["A"]["answer_key"]["1"] = "Z"
    try:
        repository.update_test_config_and_rescore(test_id, invalid)
    except ValueError as exc:
        assert "unknown option Z" in str(exc)
    else:
        raise AssertionError("Invalid answer key update succeeded")
    assert repository.config_for_test(test_id)["test"]["title"] == config["test"]["title"]
    assert repository.results_for_batch(batch_id)[0]["score"] == before_score
    with sqlite3.connect(repository.path) as db:
        assert db.execute("SELECT COUNT(*) FROM config_audit").fetchone()[0] == 0


def test_review_batch_updates_are_atomic(tmp_path):
    config = load_config(ROOT / "examples" / "sample-test.json")
    repository = Repository(tmp_path / "exam.sqlite3")
    test_id = repository.save_test(config)
    batch_id = repository.create_batch(test_id, "samples")
    read = SimpleNamespace(roll_number="123456", set_code="A", answers={"1": "A"}, requires_review=True,
                           image_path="sheet-1.png", preview_path="", thresholds={}, marks={}, flags=[])
    score = score_answers(config, "A", {"1": "A"})
    first = repository.store_read(batch_id, read, score)
    read.image_path = "sheet-2.png"
    second = repository.store_read(batch_id, read, score)
    before = repository.results_for_batch(batch_id)
    valid_answers = {str(question["id"]): None for question in config["questions"]}
    bad_answers = dict(valid_answers, **{"999": "A"})
    updates = [
        {"id": first, "answers": valid_answers, "roll_number": "123456", "set_code": "A"},
        {"id": second, "answers": bad_answers, "roll_number": "123457", "set_code": "A"},
    ]
    try:
        repository.update_results(updates, config)
    except ValueError as exc:
        assert "unknown question" in str(exc)
    else:
        raise AssertionError("Malformed review batch was accepted")
    after = repository.results_for_batch(batch_id)
    assert [(item["answers"], item["roll_number"], item["review_required"]) for item in after] == [
        (item["answers"], item["roll_number"], item["review_required"]) for item in before
    ]
    with sqlite3.connect(repository.path) as db:
        assert db.execute("SELECT COUNT(*) FROM correction_audit").fetchone()[0] == 0


def test_batch_completion_is_persisted_and_requires_all_pages(tmp_path):
    config = load_config(ROOT / "examples" / "sample-test.json")
    repository = Repository(tmp_path / "exam.sqlite3")
    test_id = repository.save_test(config)
    complete = repository.create_batch(test_id, "complete", expected_pages=2)
    assert repository.batch_status(complete)["status"] == "processing"
    assert repository.finish_batch(complete, 2) == "complete"
    failed = repository.create_batch(test_id, "failed", expected_pages=2)
    assert repository.finish_batch(failed, 1, error_count=1) == "incomplete"
    cancelled = repository.create_batch(test_id, "cancelled", expected_pages=2)
    assert repository.finish_batch(cancelled, 1, cancelled=True) == "incomplete"
    assert {batch["status"] for batch in repository.batches()} == {"complete", "incomplete"}


def test_application_settings_persist_in_sqlite(tmp_path):
    repository = Repository(tmp_path / "exam.sqlite3")
    assert repository.get_setting("scan_dpi", "216") == "216"
    repository.set_setting("scan_dpi", "300")
    repository.set_setting("scan_color_mode", "color")
    assert repository.get_setting("scan_dpi") == "300"
    assert repository.get_setting("scan_color_mode") == "color"


def test_excel_roster_preserves_zero_padded_roll_numbers(tmp_path):
    pytest = __import__("pytest")
    openpyxl = pytest.importorskip("openpyxl")
    repository = Repository(tmp_path / "exam.sqlite3")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Roll Number", "Name", "Class Name"])
    sheet.append([123, "Student One", "10A"])
    sheet["A2"].number_format = "000000"
    path = tmp_path / "students.xlsx"
    workbook.save(path)
    assert repository.import_students_excel(path) == 1
    with sqlite3.connect(repository.path) as db:
        assert db.execute("SELECT name,class_name FROM students WHERE roll_number='000123'").fetchone() == ("Student One", "10A")


def test_student_csv_rejects_duplicate_roll_numbers_without_partial_import(tmp_path):
    repository = Repository(tmp_path / "exam.sqlite3")
    path = tmp_path / "students.csv"
    path.write_text("roll_number,name,class_name\n000123,First,10A\n000123,Duplicate,10A\n", encoding="utf-8")
    try:
        repository.import_students_csv(path)
    except ValueError as exc:
        assert "duplicate roll number" in str(exc)
    else:
        raise AssertionError("Roster accepted duplicate roll numbers")
    with sqlite3.connect(repository.path) as db:
        assert db.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0


def test_student_csv_allows_trailing_blank_header(tmp_path):
    repository = Repository(tmp_path / "exam.sqlite3")
    path = tmp_path / "students.csv"
    path.write_text("roll_number,name,class_name,\n000123,Student One,10A,\n", encoding="utf-8")
    assert repository.import_students_csv(path) == 1
    with sqlite3.connect(repository.path) as db:
        assert db.execute("SELECT name,class_name FROM students WHERE roll_number='000123'").fetchone() == ("Student One", "10A")


def test_student_csv_accepts_common_roster_header_aliases(tmp_path):
    repository = Repository(tmp_path / "exam.sqlite3")
    path = tmp_path / "students.csv"
    path.write_text("Roll No,Student Name,Class\n000123,Student One,10A\n", encoding="utf-8")
    assert repository.import_students_csv(path) == 1
    with sqlite3.connect(repository.path) as db:
        assert db.execute("SELECT name,class_name FROM students WHERE roll_number='000123'").fetchone() == ("Student One", "10A")


def test_roster_reconciliation_clears_resolved_unknown_id_and_preserves_blank_flag(tmp_path):
    config = load_config(ROOT / "examples" / "sample-test.json")
    repository = Repository(tmp_path / "exam.sqlite3")
    test_id = repository.save_test(config)
    batch_id = repository.create_batch(test_id, "samples")
    roster = tmp_path / "roster.csv"
    roster.write_text("roll_number,name\n999999,Other\n", encoding="utf-8")
    repository.import_students_csv(roster)
    read = SimpleNamespace(roll_number="123456", set_code="A", answers={"1": None}, requires_review=False,
                           image_path="sheet.png", preview_path="", thresholds={}, marks={},
                           flags=[{"kind": "blank", "question": "1"}])
    result_id = repository.store_read(batch_id, read, score_answers(config, "A", read.answers))
    repository.reconcile_batch_roster_flags(batch_id)
    row = repository.results_for_batch(batch_id)[0]
    assert bool(row["review_required"]) is True
    assert {flag["kind"] for flag in row["flags"]} == {"blank", "unknown_id"}
    roster.write_text("roll_number,name\n123456,Known Student\n", encoding="utf-8")
    repository.import_students_csv(roster)
    repository.reconcile_batch_roster_flags(batch_id)
    row = repository.results_for_batch(batch_id)[0]
    assert bool(row["review_required"]) is False
    assert [flag["kind"] for flag in row["flags"]] == ["blank"]


def test_pdf_sheet_generation(tmp_path):
    config = load_config(ROOT / "examples" / "sample-test.json")
    output = generate_sheet_pdf(config, tmp_path / "sheet.pdf", count=2)
    assert output.read_bytes().startswith(b"%PDF-")
    assert output.stat().st_size > 1000


def test_scoring_refuses_incomplete_key():
    config = json.loads((ROOT / "examples" / "sample-test.json").read_text(encoding="utf-8"))
    config["sets"]["A"]["answer_key"].pop("5")
    try:
        score_answers(config, "A", {"1": "B"})
    except ValueError as exc:
        assert "incomplete" in str(exc)
    else:
        raise AssertionError("Incomplete key was scored")


def test_scoring_normalizes_key_and_rejects_invalid_options():
    config = json.loads((ROOT / "examples" / "sample-test.json").read_text(encoding="utf-8"))
    config["sets"]["A"]["answer_key"]["1"] = "b"
    assert score_answers(config, "A", {"1": "B"})["questions"][0]["status"] == "correct"
    config["sets"]["A"]["answer_key"]["1"] = "Z"
    try:
        score_answers(config, "A", {"1": "B"})
    except ValueError as exc:
        assert "Invalid answer option" in str(exc)
    else:
        raise AssertionError("Scoring accepted an answer key option absent from the sheet")


def test_scoring_rejects_invalid_or_unknown_student_answers():
    config = load_config(ROOT / "examples" / "sample-test.json")
    for answers, message in [({"1": "Z"}, "invalid response option"), ({"999": "A"}, "unknown question")]:
        try:
            score_answers(config, "A", answers)
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"Scoring accepted malformed response: {answers}")


def test_scoring_honors_partial_bonus_dropped_and_blank_rules():
    config = load_config(ROOT / "examples" / "sample-test.json")
    config["scoring"].update(partial_credit={"2": 1.5}, bonus_questions=[3], dropped_questions=[4])
    result = score_answers(config, "A", {"1": "B", "2": "C", "3": "D", "4": "A", "5": None})
    assert result["total"] == 10.5
    assert (result["correct"], result["incorrect"], result["blank"]) == (2, 1, 1)
    assert result["questions"][2]["points"] == 5
    assert result["questions"][3]["status"] == "dropped"


def test_item_analysis_uses_each_students_answer_set_key():
    config = load_config(ROOT / "examples" / "sample-test.json")
    records = [
        {"roll_number": "000001", "set_code": "A", "answers": {"1": "B", "2": "A", "3": "D", "4": "C", "5": "B"}, "review_required": False},
        {"roll_number": "000002", "set_code": "B", "answers": {"1": "A", "2": "D", "3": "C", "4": "B", "5": "A"}, "review_required": False},
    ]
    analysis = item_analysis(config, records)
    assert all(question["correct_count"] == 2 and question["difficulty"] == 1 for question in analysis)


def test_item_discrimination_does_not_split_students_tied_at_score_cutoff():
    config = load_config(ROOT / "examples" / "sample-test.json")
    answer_key = config["sets"]["A"]["answer_key"]
    records = []
    for roll, wrong_question in (("000001", 1), ("000002", 2), ("000003", 3), ("000004", 4)):
        answers = dict(answer_key)
        qid = str(wrong_question)
        answers[qid] = next(option for option in config["sheet"]["options"] if option != answer_key[qid])
        records.append({"roll_number": roll, "set_code": "A", "answers": answers, "review_required": False})

    analysis = item_analysis(config, records)
    assert {row["total"] for row in class_results(config, records)} == {15.0}
    assert analysis[0]["discrimination"] == 0.0


def test_result_analysis_and_student_scorecard(tmp_path):
    config = load_config(ROOT / "examples" / "sample-test.json")
    records = [
        {"roll_number": "000001", "student_name": "A Student", "class_name": "10A", "set_code": "A",
         "answers": {"1": "B", "2": "A", "3": "D", "4": "C", "5": "B"}, "flags": [], "review_required": False},
        {"roll_number": "000002", "student_name": "B Student", "class_name": "10A", "set_code": "A",
         "answers": {"1": "B", "2": "C", "3": None, "4": "C", "5": "A"}, "flags": [], "review_required": False},
    ]
    ranked = class_results(config, records)
    assert [item["roll_number"] for item in ranked] == ["000001", "000002"]
    assert [item["rank"] for item in ranked] == [1, 2]
    analysis = item_analysis(config, records)
    assert analysis[0]["difficulty"] == 1
    pdf = export_student_scorecards_pdf(config, records, tmp_path / "scorecards.pdf")
    assert pdf.read_bytes().startswith(b"%PDF-")


def test_subject_analysis_matches_section_subject_hand_calculation(tmp_path):
    config = load_config(ROOT / "examples" / "sample-test.json")
    config["test"]["sections"] = [
        {"title": "Algebra", "subject": "Math", "question_start": 1, "question_end": 2},
        {"title": "Physics", "subject": "Science", "question_start": 3, "question_end": 5},
    ]
    for question in config["questions"]:
        question["section"] = "Algebra" if int(question["id"]) <= 2 else "Physics"
    records = [
        {"roll_number": "000001", "set_code": "A", "answers": {"1": "B", "2": "A", "3": "D", "4": "C", "5": "B"}, "review_required": False},
        {"roll_number": "000002", "set_code": "A", "answers": {"1": "A", "2": "A", "3": "D", "4": None, "5": "A"}, "review_required": False},
    ]
    rows = subject_analysis(config, records)
    assert rows == [
        {"subject": "Math", "question_count": 2, "student_count": 2, "average_points_per_student": 5.5},
        {"subject": "Science", "question_count": 3, "student_count": 2, "average_points_per_student": 7.5},
    ]
    path = export_subject_analysis_csv(config, records, tmp_path / "subjects.csv")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        assert len(list(csv.DictReader(handle))) == 2


def test_excel_workbook_includes_result_and_analysis_tabs(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    config = load_config(ROOT / "examples" / "sample-test.json")
    answers = {str(question["id"]): config["sets"]["A"]["answer_key"][str(question["id"])]
               for question in config["questions"]}
    records = [{"roll_number": "000001", "student_name": "Test Student", "class_name": "QA",
                "set_code": "A", "answers": answers, "review_required": False}]

    path = export_xlsx(config, records, tmp_path / "complete-results.xlsx")
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    assert workbook.sheetnames == ["Results", "Item analysis", "Topic analysis", "Subject analysis"]
    assert workbook["Results"]["F2"].value == 20
    assert workbook["Item analysis"]["A1"].value == "Question"
    assert workbook["Topic analysis"]["A1"].value == "Section"
    assert workbook["Subject analysis"]["A1"].value == "Subject"
    assert workbook["Item analysis"].max_row == len(config["questions"]) + 1
    workbook.close()


def test_pdf_report_contains_marklist_and_analysis_sections(tmp_path):
    fitz = pytest.importorskip("fitz")
    config = load_config(ROOT / "examples" / "sample-test.json")
    correct = {str(question["id"]): config["sets"]["A"]["answer_key"][str(question["id"])]
               for question in config["questions"]}
    incorrect = {str(question["id"]): next(option for option in config["sheet"]["options"]
                                            if option != correct[str(question["id"])])
                 for question in config["questions"]}
    records = [
        {"roll_number": "000001", "student_name": "High scorer", "set_code": "A",
         "answers": correct, "review_required": False},
        {"roll_number": "000002", "student_name": "Low scorer", "set_code": "A",
         "answers": incorrect, "review_required": False},
    ]

    path = export_report_pdf(config, records, tmp_path / "analysis-report.pdf")
    with fitz.open(path) as document:
        text = "\n".join(page.get_text() for page in document)
    for expected in ("High scorer", "Low scorer", "Item analysis", "Difficulty",
                     "Topic analysis", "Subject analysis", "Discrimination"):
        assert expected in text


def test_pdf_report_paginates_a_long_item_analysis(tmp_path):
    fitz = pytest.importorskip("fitz")
    config = create_test_config(title="Long Exam", test_date="2026-11-10", class_name="QA", subject="Science",
                                question_count=120, options=["A", "B", "C", "D"], roll_number_digits=6,
                                set_codes=["A"], columns=3)
    answers = {str(question["id"]): "A" for question in config["questions"]}
    config["sets"]["A"]["answer_key"] = answers
    record = {"roll_number": "000001", "student_name": "Long exam sample", "set_code": "A",
              "answers": answers, "review_required": False}

    path = export_report_pdf(config, [record], tmp_path / "long-analysis-report.pdf")
    with fitz.open(path) as document:
        text = "\n".join(page.get_text() for page in document)
        assert len(document) >= 5
        assert "Item analysis" in text and "Topic analysis" in text and "Subject analysis" in text
        assert "120" in text


def test_long_student_scorecard_repeats_identity_on_each_page(tmp_path):
    fitz = pytest.importorskip("fitz")
    config = create_test_config(title="Long Exam", test_date="2026-11-10", class_name="QA", subject="Science",
                                question_count=120, options=["A", "B", "C", "D"], roll_number_digits=6,
                                set_codes=["A"], columns=3)
    answers = {str(question["id"]): "A" for question in config["questions"]}
    config["sets"]["A"]["answer_key"] = answers
    record = {"roll_number": "001234", "student_name": "Long exam student", "set_code": "A",
              "answers": answers, "review_required": False}

    path = export_student_scorecards_pdf(config, [record], tmp_path / "long-scorecard.pdf")
    with fitz.open(path) as document:
        assert len(document) > 1
        for page in document:
            page_text = page.get_text()
            assert "Long exam student" in page_text
            assert "001234" in page_text


def test_official_csv_export_refuses_unresolved_scans(tmp_path):
    config = load_config(ROOT / "examples" / "sample-test.json")
    record = {"roll_number": "000001", "set_code": "A", "answers": {"1": "B"}, "review_required": True}
    try:
        export_csv(config, [record], tmp_path / "should-not-exist.csv")
    except ValueError as exc:
        assert "Official exports require resolved scans" in str(exc)
    else:
        raise AssertionError("Official export included an unresolved scan")
    assert not (tmp_path / "should-not-exist.csv").exists()


def test_csv_export_keeps_untrusted_student_text_literal(tmp_path):
    config = load_config(ROOT / "examples" / "sample-test.json")
    record = {"roll_number": "000001", "student_name": "=HYPERLINK(\"https://bad.example\",\"click\")",
              "class_name": "@SUM(1+1)", "set_code": "A", "answers": {"1": "B"},
              "review_required": False}
    output = export_csv(config, [record], tmp_path / "safe.csv")
    with output.open(newline="", encoding="utf-8-sig") as handle:
        row = next(csv.DictReader(handle))
    assert row["student_name"].startswith("'=")
    assert row["class_name"].startswith("'@")


def test_existing_sqlite_database_is_migrated(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE tests(id INTEGER PRIMARY KEY,title TEXT NOT NULL,config_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE students(roll_number TEXT PRIMARY KEY,name TEXT DEFAULT '',class_name TEXT DEFAULT '');
        CREATE TABLE batches(id INTEGER PRIMARY KEY,test_id INTEGER NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,source_folder TEXT DEFAULT '');
        CREATE TABLE results(id INTEGER PRIMARY KEY,batch_id INTEGER NOT NULL,roll_number TEXT DEFAULT '',set_code TEXT NOT NULL,answers_json TEXT NOT NULL,score REAL NOT NULL,review_required INTEGER DEFAULT 0,image_path TEXT DEFAULT '');
        PRAGMA user_version=1;
        """)
    repository = Repository(path)
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(results)")}
        version = db.execute("PRAGMA user_version").fetchone()[0]
        batch_columns = {row[1] for row in db.execute("PRAGMA table_info(batches)")}
    assert {"preview_path", "thresholds_json", "marks_json", "flags_json", "processed_at"}.issubset(columns)
    assert version == 6
    assert {"expected_pages", "processed_pages", "error_count", "status"}.issubset(batch_columns)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0] == 0
        assert {row[1] for row in db.execute("PRAGMA table_info(batch_log)")} >= {
            "id", "batch_id", "logged_at", "level", "source_file", "message"
        }


def test_batch_log_events_persist_in_order_and_validate_input(tmp_path):
    repository = Repository(tmp_path / "batch-log.sqlite3")
    config = load_config(ROOT / "examples" / "sample-test.json")
    test_id = repository.save_test(config)
    batch_id = repository.create_batch(test_id, "scans", expected_pages=2)

    repository.log_batch_event(batch_id, "error", "Could not decode page", "page-01.png")
    repository.log_batch_event(batch_id, "INFO", "Batch finished", "")
    events = repository.batch_log(batch_id)
    assert [(event["level"], event["source_file"], event["message"]) for event in events] == [
        ("ERROR", "page-01.png", "Could not decode page"),
        ("INFO", "", "Batch finished"),
    ]
    for level, message in (("DEBUG", "message"), ("INFO", "  ")):
        try:
            repository.log_batch_event(batch_id, level, message)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid batch log event was accepted")


def test_repository_enforces_sqlite_foreign_keys_on_every_connection(tmp_path):
    repository = Repository(tmp_path / "foreign-keys.sqlite3")
    with pytest.raises(sqlite3.IntegrityError):
        repository.create_batch(99999, "orphan")
    with pytest.raises(sqlite3.IntegrityError):
        repository.log_batch_event(99999, "ERROR", "Orphan batch log")
