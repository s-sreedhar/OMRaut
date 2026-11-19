"""Roster: typed or imported students carry an email, export round-trips, names come from the roll number."""
import csv
import json

import pytest

from omr_exam.database import Repository


def test_email_saved_imported_and_never_erased_by_blank_import(tmp_path):
    repo = Repository(tmp_path / "db.sqlite3")
    repo.save_student("101", "Asha Verma", "10A", "asha@example.com")
    assert repo.students()[0]["email"] == "asha@example.com"

    blank = tmp_path / "blank.json"
    blank.write_text(json.dumps({"students": [{"roll_number": "101", "name": "Asha V", "class_name": "10A"}]}))
    repo.import_students_json(blank)
    assert repo.students()[0]["name"] == "Asha V" and repo.students()[0]["email"] == "asha@example.com"

    data = tmp_path / "new.json"
    data.write_text(json.dumps([{"roll": "102", "student_name": "Bala Rao", "e_mail": "bala@example.com"}]))
    repo.import_students_json(data)
    assert {s["roll_number"]: s["email"] for s in repo.students()} == {"101": "asha@example.com", "102": "bala@example.com"}

    sheet = tmp_path / "r.csv"
    sheet.write_text("roll_number,name,email,class\n103,Chitra,chitra@example.com,10B\n", encoding="utf-8")
    repo.import_students_csv(sheet)
    assert repo.find_student_by_roll("103")["email"] == "chitra@example.com"


def test_bad_email_is_rejected(tmp_path):
    repo = Repository(tmp_path / "db.sqlite3")
    with pytest.raises(ValueError, match="valid email"):
        repo.save_student("1", "A", "", "not-an-email")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"roll_number": "2", "name": "B", "email": "nope"}]))
    with pytest.raises(ValueError, match="valid email"):
        repo.import_students_json(bad)


def test_old_database_gains_email_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE students (roll_number TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', class_name TEXT NOT NULL DEFAULT '')")
    connection.execute("INSERT INTO students VALUES ('7','Old Student','9A')")
    connection.commit(); connection.close()
    repo = Repository(path)
    assert repo.students()[0]["email"] == "" and repo.students()[0]["name"] == "Old Student"
