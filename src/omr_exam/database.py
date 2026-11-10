"""Local SQLite store and schema bootstrap."""
from __future__ import annotations

import sqlite3
import json
import csv
import hashlib
import shutil
import tempfile
import zipfile
import re
import os
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS tests (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, config_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS students (
 roll_number TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', class_name TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS batches (
 id INTEGER PRIMARY KEY, test_id INTEGER NOT NULL REFERENCES tests(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, source_folder TEXT NOT NULL DEFAULT '',
 expected_pages INTEGER NOT NULL DEFAULT 0, processed_pages INTEGER NOT NULL DEFAULT 0,
 error_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'complete'
);
CREATE TABLE IF NOT EXISTS batch_log (
 id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
 logged_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, level TEXT NOT NULL,
 source_file TEXT NOT NULL DEFAULT '', message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS batch_log_batch_id ON batch_log(batch_id,id);
CREATE TABLE IF NOT EXISTS results (
 id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
 roll_number TEXT NOT NULL DEFAULT '', set_code TEXT NOT NULL, answers_json TEXT NOT NULL,
 score REAL NOT NULL DEFAULT 0, review_required INTEGER NOT NULL DEFAULT 0, image_path TEXT NOT NULL DEFAULT '',
 preview_path TEXT NOT NULL DEFAULT '', thresholds_json TEXT NOT NULL DEFAULT '{}', marks_json TEXT NOT NULL DEFAULT '{}',
 flags_json TEXT NOT NULL DEFAULT '[]', processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(batch_id, image_path)
);
CREATE INDEX IF NOT EXISTS results_batch_roll ON results(batch_id, roll_number);
CREATE TABLE IF NOT EXISTS correction_audit (
 id INTEGER PRIMARY KEY, result_id INTEGER NOT NULL REFERENCES results(id) ON DELETE CASCADE,
 question_id TEXT NOT NULL, old_answer TEXT, new_answer TEXT, changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS config_audit (
 id INTEGER PRIMARY KEY, test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
 old_config_json TEXT NOT NULL, new_config_json TEXT NOT NULL, changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS app_settings (
 key TEXT PRIMARY KEY, value TEXT NOT NULL
);
"""
SCHEMA_VERSION = 5


def _roster_header(value: Any) -> str:
    normalized = re.sub(r"[\s\-]+", "_", str(value).strip().lower())
    aliases = {
        "roll": "roll_number", "roll_no": "roll_number", "rollno": "roll_number",
        "student_id": "roll_number", "admission_no": "roll_number",
        "student_name": "name", "full_name": "name",
        "class": "class_name", "classroom": "class_name",
    }
    return aliases.get(normalized, normalized)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class _ClosingConnection(sqlite3.Connection):
    """Connection whose ``with`` block commits/rolls back and then closes it.

    The stock sqlite3 context manager leaves the file handle open, which keeps the
    database locked on Windows until garbage collection.
    """

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _database_connection(path: str | Path) -> sqlite3.Connection:
    """Open a connection with schema constraints enforced for this connection."""
    connection = sqlite3.connect(path, factory=_ClosingConnection)
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize_database(path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _database_connection(target) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"Database schema {version} is newer than this app supports ({SCHEMA_VERSION})")
        connection.executescript(SCHEMA)
        batch_columns = {row[1] for row in connection.execute("PRAGMA table_info(batches)")}
        if "expected_pages" not in batch_columns:
            connection.execute("ALTER TABLE batches ADD COLUMN expected_pages INTEGER NOT NULL DEFAULT 0")
        if "processed_pages" not in batch_columns:
            connection.execute("ALTER TABLE batches ADD COLUMN processed_pages INTEGER NOT NULL DEFAULT 0")
        if "error_count" not in batch_columns:
            connection.execute("ALTER TABLE batches ADD COLUMN error_count INTEGER NOT NULL DEFAULT 0")
        if "status" not in batch_columns:
            # Existing batches predate explicit completion tracking and are retained as complete.
            connection.execute("ALTER TABLE batches ADD COLUMN status TEXT NOT NULL DEFAULT 'complete'")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(results)")}
        if "preview_path" not in columns:
            connection.execute("ALTER TABLE results ADD COLUMN preview_path TEXT NOT NULL DEFAULT ''")
        if "thresholds_json" not in columns:
            connection.execute("ALTER TABLE results ADD COLUMN thresholds_json TEXT NOT NULL DEFAULT '{}'")
        if "marks_json" not in columns:
            connection.execute("ALTER TABLE results ADD COLUMN marks_json TEXT NOT NULL DEFAULT '{}'")
        if "flags_json" not in columns:
            connection.execute("ALTER TABLE results ADD COLUMN flags_json TEXT NOT NULL DEFAULT '[]'")
        if "processed_at" not in columns:
            connection.execute("ALTER TABLE results ADD COLUMN processed_at TEXT NOT NULL DEFAULT ''")
            connection.execute("UPDATE results SET processed_at=CURRENT_TIMESTAMP WHERE processed_at=''")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS results_batch_image ON results(batch_id,image_path)")
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


class Repository:
    """Small transactional repository for tests, batches, scans, and corrections."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        initialize_database(path)

    def save_test(self, config: dict[str, Any]) -> int:
        with _database_connection(self.path) as db:
            cursor = db.execute("INSERT INTO tests(title,config_json) VALUES (?,?)", (config["test"]["title"], json.dumps(config, ensure_ascii=False)))
            return int(cursor.lastrowid)

    def get_setting(self, key: str, default: str = "") -> str:
        with _database_connection(self.path) as db:
            row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with _database_connection(self.path) as db:
            db.execute("INSERT INTO app_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, str(value)))

    def tests(self) -> list[dict[str, Any]]:
        with _database_connection(self.path) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT id,title,created_at FROM tests ORDER BY created_at DESC,id DESC")]

    def config_for_test(self, test_id: int) -> dict[str, Any]:
        with _database_connection(self.path) as db:
            row = db.execute("SELECT config_json FROM tests WHERE id=?", (test_id,)).fetchone()
        if row is None:
            raise KeyError(f"Test {test_id} not found")
        return json.loads(row[0])

    def update_test_config(self, test_id: int, config: dict[str, Any]) -> None:
        self.update_test_config_and_rescore(test_id, config)

    def update_test_config_and_rescore(self, test_id: int, config: dict[str, Any]) -> int:
        """Save a new key/rules and all dependent scores as one transaction."""
        from omr_exam.scoring import score_answers
        from omr_exam.config import validate_config
        validate_config(config)
        new_json = json.dumps(config, ensure_ascii=False)
        with _database_connection(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT config_json FROM tests WHERE id=?", (test_id,)).fetchone()
            if old is None:
                raise KeyError(f"Test {test_id} not found")
            rows = db.execute("SELECT r.id,r.answers_json,r.set_code FROM results r JOIN batches b ON b.id=r.batch_id WHERE b.test_id=?", (test_id,)).fetchall()
            recalculated = []
            for result_id, answers_json, set_code in rows:
                if set_code not in config.get("sets", {}):
                    raise ValueError(f"Cannot re-score result {result_id}: test set {set_code!r} is missing from the new configuration")
                scored = score_answers(config, set_code, json.loads(answers_json))
                recalculated.append((scored["total"], result_id))
            db.execute("INSERT INTO config_audit(test_id,old_config_json,new_config_json) VALUES (?,?,?)",
                       (test_id, old[0], new_json))
            db.execute("UPDATE tests SET title=?,config_json=? WHERE id=?",
                       (config["test"]["title"], new_json, test_id))
            db.executemany("UPDATE results SET score=? WHERE id=?", recalculated)
            return len(recalculated)

    def create_batch(self, test_id: int, source_folder: str, expected_pages: int = 0) -> int:
        if expected_pages < 0:
            raise ValueError("Expected page count cannot be negative")
        with _database_connection(self.path) as db:
            cursor = db.execute("INSERT INTO batches(test_id,source_folder,expected_pages,status) VALUES (?,?,?,'processing')",
                                (test_id, source_folder, expected_pages))
            return int(cursor.lastrowid)

    def finish_batch(self, batch_id: int, processed_pages: int, *, error_count: int = 0,
                     cancelled: bool = False) -> str:
        if processed_pages < 0 or error_count < 0:
            raise ValueError("Processed pages and error count cannot be negative")
        with _database_connection(self.path) as db:
            row = db.execute("SELECT expected_pages FROM batches WHERE id=?", (batch_id,)).fetchone()
            if row is None:
                raise KeyError(f"Batch {batch_id} not found")
            expected_pages = int(row[0])
            complete = not cancelled and error_count == 0 and processed_pages == expected_pages
            status = "complete" if complete else "incomplete"
            db.execute("UPDATE batches SET processed_pages=?,error_count=?,status=? WHERE id=?",
                       (processed_pages, error_count, status, batch_id))
            return status

    def log_batch_event(self, batch_id: int, level: str, message: str, source_file: str = "") -> int:
        level = str(level).upper()
        if level not in {"INFO", "WARNING", "ERROR"}:
            raise ValueError("Batch log level must be INFO, WARNING, or ERROR")
        message = str(message).strip()
        if not message:
            raise ValueError("Batch log message cannot be empty")
        with _database_connection(self.path) as db:
            cursor = db.execute("INSERT INTO batch_log(batch_id,level,source_file,message) VALUES (?,?,?,?)",
                                (batch_id, level, str(source_file), message))
            return int(cursor.lastrowid)

    def batch_log(self, batch_id: int) -> list[dict[str, Any]]:
        with _database_connection(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT id,logged_at,level,source_file,message FROM batch_log WHERE batch_id=? ORDER BY id",
                              (batch_id,)).fetchall()
            return [dict(row) for row in rows]

    def batch_status(self, batch_id: int) -> dict[str, Any]:
        with _database_connection(self.path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT id,expected_pages,processed_pages,error_count,status FROM batches WHERE id=?", (batch_id,)).fetchone()
            if row is None:
                raise KeyError(f"Batch {batch_id} not found")
            return dict(row)

    def batches(self, test_id: int | None = None) -> list[dict[str, Any]]:
        with _database_connection(self.path) as db:
            db.row_factory = sqlite3.Row
            if test_id is None:
                rows = db.execute("SELECT b.id,b.test_id,t.title,b.created_at,b.source_folder,b.expected_pages,b.processed_pages,b.error_count,b.status,COUNT(r.id) AS sheet_count FROM batches b JOIN tests t ON t.id=b.test_id LEFT JOIN results r ON r.batch_id=b.id GROUP BY b.id ORDER BY b.id DESC LIMIT 100").fetchall()
            else:
                rows = db.execute("SELECT b.id,b.test_id,t.title,b.created_at,b.source_folder,b.expected_pages,b.processed_pages,b.error_count,b.status,COUNT(r.id) AS sheet_count FROM batches b JOIN tests t ON t.id=b.test_id LEFT JOIN results r ON r.batch_id=b.id WHERE b.test_id=? GROUP BY b.id ORDER BY b.id DESC LIMIT 100", (test_id,)).fetchall()
            return [dict(row) for row in rows]

    def recalculate_test_results(self, test_id: int, config: dict[str, Any]) -> int:
        from omr_exam.scoring import score_answers
        count = 0
        with _database_connection(self.path) as db:
            rows = db.execute("SELECT r.id,r.answers_json,r.set_code FROM results r JOIN batches b ON b.id=r.batch_id WHERE b.test_id=?", (test_id,)).fetchall()
            for result_id, answers_raw, set_code in rows:
                if set_code in config["sets"]:
                    scored = score_answers(config, set_code, json.loads(answers_raw))
                    db.execute("UPDATE results SET score=? WHERE id=?", (scored["total"], result_id))
                    count += 1
        return count

    def store_read(self, batch_id: int, result: Any, score_result: dict[str, Any]) -> int:
        with _database_connection(self.path) as db:
            db.execute("""INSERT INTO results(batch_id,roll_number,set_code,answers_json,score,review_required,image_path,preview_path,thresholds_json,marks_json,flags_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(batch_id,image_path) DO UPDATE SET
                roll_number=excluded.roll_number,set_code=excluded.set_code,answers_json=excluded.answers_json,
                score=excluded.score,review_required=excluded.review_required,preview_path=excluded.preview_path,
                thresholds_json=excluded.thresholds_json,marks_json=excluded.marks_json,
                flags_json=excluded.flags_json,processed_at=CURRENT_TIMESTAMP""",
                (batch_id, result.roll_number or "", result.set_code or "", json.dumps(result.answers, ensure_ascii=False),
                 float(score_result.get("total", 0)), int(bool(result.requires_review)), result.image_path,
                 result.preview_path, json.dumps(result.thresholds, ensure_ascii=False),
                 json.dumps(result.marks, ensure_ascii=False),
                 json.dumps(result.flags, ensure_ascii=False)))
            row = db.execute("SELECT id FROM results WHERE batch_id=? AND image_path=?", (batch_id, result.image_path)).fetchone()
            return int(row[0])

    def set_result_flags(self, result_id: int, flags: list[dict[str, Any]], *, review_required: bool) -> None:
        with _database_connection(self.path) as db:
            db.execute("UPDATE results SET flags_json=?,review_required=? WHERE id=?",
                       (json.dumps(flags, ensure_ascii=False), int(review_required), result_id))

    def reconcile_batch_roster_flags(self, batch_id: int) -> None:
        """Refresh duplicate/unknown ID flags from the current batch and roster."""
        records = self.results_for_batch(batch_id)
        has_roster = self.has_students()
        roll_counts: dict[str, int] = {}
        for record in records:
            roll = str(record.get("roll_number") or "")
            if roll:
                roll_counts[roll] = roll_counts.get(roll, 0) + 1
        identity_kinds = {"duplicate_id", "unknown_id"}
        for record in records:
            roll = str(record.get("roll_number") or "")
            flags = [flag for flag in record["flags"] if flag.get("kind") not in identity_kinds]
            if roll and roll_counts.get(roll, 0) > 1:
                flags.append({"kind": "duplicate_id", "message": f"Roll number {roll} appears more than once in this batch"})
            elif roll and has_roster and not self.student_exists(roll):
                flags.append({"kind": "unknown_id", "message": f"Roll number {roll} is not in the imported student roster"})
            has_identity_issue = any(flag.get("kind") in identity_kinds for flag in flags)
            previous_nonidentity_issues = [flag for flag in record["flags"]
                                           if flag.get("kind") not in identity_kinds | {"blank"}]
            review_required = has_identity_issue or (bool(record["review_required"]) and
                                                     (bool(previous_nonidentity_issues) or not record["flags"]))
            self.set_result_flags(int(record["id"]), flags, review_required=review_required)

    def students(self) -> list[dict[str, Any]]:
        with _database_connection(self.path) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT roll_number,name,class_name FROM students ORDER BY roll_number")]

    def save_student(self, roll_number: str, name: str, class_name: str = "") -> None:
        roll_number = roll_number.strip()
        if not roll_number:
            raise ValueError("Roll number is required")
        if not name.strip():
            raise ValueError("Student name is required")
        with _database_connection(self.path) as db:
            db.execute("INSERT INTO students(roll_number,name,class_name) VALUES (?,?,?) ON CONFLICT(roll_number) DO UPDATE SET name=excluded.name,class_name=excluded.class_name",
                       (roll_number, name.strip(), class_name.strip()))

    def delete_student(self, roll_number: str) -> None:
        with _database_connection(self.path) as db:
            db.execute("DELETE FROM students WHERE roll_number=?", (roll_number,))

    def student_exists(self, roll_number: str) -> bool:
        with _database_connection(self.path) as db:
            return db.execute("SELECT 1 FROM students WHERE roll_number=?", (roll_number,)).fetchone() is not None

    def has_students(self) -> bool:
        with _database_connection(self.path) as db:
            return db.execute("SELECT EXISTS(SELECT 1 FROM students LIMIT 1)").fetchone()[0] == 1

    def results_for_batch(self, batch_id: int) -> list[dict[str, Any]]:
        with _database_connection(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT r.*,s.name AS student_name,s.class_name FROM results r LEFT JOIN students s ON s.roll_number=r.roll_number WHERE r.batch_id=? ORDER BY r.roll_number,r.image_path", (batch_id,)).fetchall()
            results = [dict(row) for row in rows]
        for row in results:
            row["answers"] = json.loads(row.pop("answers_json"))
            row["flags"] = json.loads(row.pop("flags_json"))
            row["thresholds"] = json.loads(row.pop("thresholds_json"))
            row["marks"] = json.loads(row.pop("marks_json"))
        return results

    def correct_answer(self, result_id: int, question_id: str, new_answer: str | None) -> None:
        with _database_connection(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT answers_json FROM results WHERE id=?", (result_id,)).fetchone()
            if row is None:
                raise KeyError(f"Result {result_id} not found")
            answers = json.loads(row[0])
            old = answers.get(question_id)
            answers[question_id] = new_answer
            db.execute("INSERT INTO correction_audit(result_id,question_id,old_answer,new_answer) VALUES (?,?,?,?)",
                       (result_id, question_id, old, new_answer))
            db.execute("UPDATE results SET answers_json=?,review_required=1 WHERE id=?", (json.dumps(answers), result_id))
            db.commit()

    def update_result(self, result_id: int, *, answers: dict[str, str | None], roll_number: str,
                      set_code: str, config: dict[str, Any]) -> dict[str, Any]:
        return self.update_results([{"id": result_id, "answers": answers, "roll_number": roll_number,
                                     "set_code": set_code}], config)[result_id]

    def update_results(self, updates: list[dict[str, Any]], config: dict[str, Any]) -> dict[int, dict[str, Any]]:
        """Apply a review batch atomically so a failed row cannot leave partial scores."""
        from omr_exam.scoring import score_answers
        result_ids = [int(item["id"]) for item in updates]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("A result appears more than once in this review batch")
        scored_by_id: dict[int, dict[str, Any]] = {}
        with _database_connection(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            for item in updates:
                result_id = int(item["id"])
                answers = item["answers"]
                roll_number = str(item["roll_number"])
                set_code = str(item["set_code"])
                row = db.execute("SELECT answers_json,set_code,roll_number FROM results WHERE id=?", (result_id,)).fetchone()
                if row is None:
                    raise KeyError(f"Result {result_id} not found")
                previous = json.loads(row[0])
                scored = score_answers(config, set_code, answers)
                for question_id, new_answer in answers.items():
                    old_answer = previous.get(str(question_id))
                    if old_answer != new_answer:
                        db.execute("INSERT INTO correction_audit(result_id,question_id,old_answer,new_answer) VALUES (?,?,?,?)",
                                   (result_id, str(question_id), old_answer, new_answer))
                if row[2] != roll_number:
                    db.execute("INSERT INTO correction_audit(result_id,question_id,old_answer,new_answer) VALUES (?,?,?,?)",
                               (result_id, "__roll_number__", row[2], roll_number))
                if row[1] != set_code:
                    db.execute("INSERT INTO correction_audit(result_id,question_id,old_answer,new_answer) VALUES (?,?,?,?)",
                               (result_id, "__set_code__", row[1], set_code))
                db.execute("UPDATE results SET roll_number=?,set_code=?,answers_json=?,score=?,review_required=0 WHERE id=?",
                           (roll_number, set_code, json.dumps(answers, ensure_ascii=False), scored["total"], result_id))
                scored_by_id[result_id] = scored
        return scored_by_id

    def backup_to(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = _database_connection(self.path)
        backup = _database_connection(target)
        try:
            source.backup(backup)
        finally:
            backup.close()
            source.close()
        return target

    def restore_from(self, source_path: str | Path) -> None:
        candidate = Path(source_path)
        if candidate.resolve() == Path(self.path).resolve():
            raise ValueError("Choose a separate backup file to restore")
        source = _database_connection(candidate)
        target = _database_connection(self.path)
        try:
            integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(f"Backup database failed integrity check: {integrity}")
            tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"tests", "batches", "results"}.issubset(tables):
                raise ValueError("Selected file is not an OMR Exam App database backup")
            source.backup(target)
        finally:
            target.close()
            source.close()
        initialize_database(self.path)

    def backup_bundle_to(self, destination: str | Path) -> Path:
        """Back up the database plus managed scan images and aligned previews."""
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="omr-backup-") as temporary:
            stage = Path(temporary)
            self.backup_to(stage / "exam.sqlite3")
            data_dir = Path(self.path).parent
            for directory in ("scans", "previews"):
                source_dir = data_dir / directory
                if source_dir.is_dir():
                    shutil.copytree(source_dir, stage / directory)
            checksums = {}
            for item in stage.rglob("*"):
                if item.is_file():
                    relative = item.relative_to(stage).as_posix()
                    digest = _sha256_file(item)
                    checksums[relative] = {"size": item.stat().st_size, "sha256": digest}
            (stage / "manifest.json").write_text(json.dumps({"format": 1, "files": checksums}, indent=2), encoding="utf-8")
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for item in stage.rglob("*"):
                    if item.is_file():
                        archive.write(item, item.relative_to(stage).as_posix())
        return target

    def restore_bundle_from(self, bundle_path: str | Path) -> None:
        bundle = Path(bundle_path)
        with zipfile.ZipFile(bundle, "r") as archive:
            total_size = 0
            archive_files = set()
            seen_names = set()
            for info in archive.infolist():
                member = Path(info.filename)
                if member.is_absolute() or member.drive or ".." in member.parts or info.external_attr >> 16 & 0o170000 == 0o120000:
                    raise ValueError("Backup contains an unsafe file path")
                if info.filename in seen_names:
                    raise ValueError("Backup contains duplicate archive entries")
                seen_names.add(info.filename)
                if not info.is_dir():
                    archive_files.add(info.filename)
                total_size += info.file_size
            if total_size > 10_000_000_000:
                raise ValueError("Backup is too large to restore safely")
            data_dir = Path(self.path).parent
            data_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="omr-restore-", dir=data_dir) as temporary:
                stage = Path(temporary)
                archive.extractall(stage)
                manifest_path = stage / "manifest.json"
                if not manifest_path.is_file():
                    raise ValueError("Backup bundle has no manifest")
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("format") != 1 or not isinstance(manifest.get("files"), dict):
                    raise ValueError("Unsupported backup format")
                if archive_files - {"manifest.json"} != set(manifest["files"]):
                    raise ValueError("Backup manifest does not match the archive contents")
                for relative, metadata in manifest["files"].items():
                    member_path = Path(relative)
                    if member_path.is_absolute() or member_path.drive or ".." in member_path.parts:
                        raise ValueError("Backup manifest contains an unsafe path")
                    file_path = stage / member_path
                    if not isinstance(metadata, dict) or not file_path.is_file() or file_path.stat().st_size != int(metadata["size"]):
                        raise ValueError(f"Backup file is missing or has the wrong size: {relative}")
                    digest = _sha256_file(file_path)
                    if digest != metadata["sha256"]:
                        raise ValueError(f"Backup checksum failed: {relative}")
                database = stage / "exam.sqlite3"
                if "exam.sqlite3" not in manifest["files"]:
                    raise ValueError("Backup does not contain a database")
                # Validate all content before changing the active database or media.
                connection = _database_connection(database)
                try:
                    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Backup database failed integrity check")
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if not {"tests", "batches", "results"}.issubset(tables):
                        raise ValueError("Backup database is not an OMR Exam App database")
                finally:
                    connection.close()
                transaction = Path(temporary)
                replacements: dict[str, Path] = {}
                previous: dict[str, Path] = {}
                # Copy and validate all media before touching the live database or folders.
                for name in ("scans", "previews"):
                    restored = stage / name
                    if restored.is_dir():
                        replacement = transaction / f"new-{name}"
                        shutil.copytree(restored, replacement)
                        replacements[name] = replacement
                    previous[name] = transaction / f"old-{name}"
                old_database = transaction / "previous.sqlite3"
                self.backup_to(old_database)
                moved_old: set[str] = set()
                moved_new: set[str] = set()
                database_restore_started = False
                try:
                    for name in ("scans", "previews"):
                        destination = data_dir / name
                        if destination.exists():
                            os.replace(destination, previous[name])
                            moved_old.add(name)
                        replacement = replacements.get(name)
                        if replacement is not None:
                            os.replace(replacement, destination)
                            moved_new.add(name)
                    database_restore_started = True
                    self.restore_from(database)
                except Exception:
                    for name in reversed(("scans", "previews")):
                        destination = data_dir / name
                        if name in moved_new and destination.exists():
                            if destination.is_dir() and not destination.is_symlink():
                                shutil.rmtree(destination)
                            else:
                                destination.unlink()
                        if name in moved_old and previous[name].exists():
                            os.replace(previous[name], destination)
                    if database_restore_started:
                        self.restore_from(old_database)
                    raise

    def import_students_csv(self, path: str | Path) -> int:
        with Path(path).open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError("Student CSV is missing a header row")
            normalized_headers = [_roster_header(name) if name else "" for name in reader.fieldnames]
            populated_headers = [name for name in normalized_headers if name]
            if len(populated_headers) != len(set(populated_headers)):
                raise ValueError("Student CSV has duplicate column headers")
            fields = {name: original for name, original in zip(normalized_headers, reader.fieldnames) if name}
            if "roll_number" not in fields or "name" not in fields:
                raise ValueError("Student CSV needs roll_number and name columns; class_name is optional")
            rows = []
            seen: set[str] = set()
            for row in reader:
                roll = (row.get(fields["roll_number"]) or "").strip()
                name = (row.get(fields["name"]) or "").strip()
                class_name = (row.get(fields["class_name"]) or "").strip() if "class_name" in fields else ""
                if roll:
                    if roll in seen:
                        raise ValueError(f"Student CSV contains duplicate roll number {roll}")
                    seen.add(roll)
                    rows.append((roll, name, class_name))
                elif name or class_name:
                    raise ValueError("Student CSV row has student data but no roll_number")
        if not rows:
            raise ValueError("Student CSV did not contain any roll numbers")
        with _database_connection(self.path) as db:
            db.executemany("INSERT INTO students(roll_number,name,class_name) VALUES (?,?,?) ON CONFLICT(roll_number) DO UPDATE SET name=excluded.name,class_name=excluded.class_name", rows)
        return len(rows)

    def import_students_excel(self, path: str | Path, *, sheet_name: str | None = None) -> int:
        """Import a roster workbook with columns roll_number, name, and optional class_name."""
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise RuntimeError("Excel roster import requires openpyxl; install the app dependencies") from exc
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            if sheet_name is not None:
                if sheet_name not in workbook.sheetnames:
                    raise ValueError(f"Worksheet {sheet_name!r} was not found")
                sheet = workbook[sheet_name]
            else:
                sheet = workbook.active
            iterator = sheet.iter_rows()
            header = next(iterator, None)
            if not header:
                raise ValueError("Student workbook is missing a header row")
            header_names = [_roster_header(cell.value) if cell.value is not None else "" for cell in header]
            populated_headers = [name for name in header_names if name]
            if len(populated_headers) != len(set(populated_headers)):
                raise ValueError("Student workbook has duplicate column headers")
            fields = {name: index for index, name in enumerate(header_names) if name}
            if "roll_number" not in fields or "name" not in fields:
                raise ValueError("Student workbook needs roll_number and name columns; class_name is optional")
            rows: list[tuple[str, str, str]] = []
            seen: set[str] = set()
            for row_number, cells in enumerate(iterator, start=2):
                values = [cell.value for cell in cells]
                roll_index = fields["roll_number"]
                cell = cells[roll_index] if roll_index < len(cells) else None
                raw_roll = cell.value if cell is not None else None
                if raw_roll is None or str(raw_roll).strip() == "":
                    has_other_data = any(value is not None and str(value).strip() for index, value in enumerate(values) if index != roll_index)
                    if has_other_data:
                        raise ValueError(f"Student workbook row {row_number} has data but no roll number")
                    continue
                if isinstance(raw_roll, bool):
                    raise ValueError(f"Student workbook row {row_number} has a boolean roll number")
                if isinstance(raw_roll, (int, float)):
                    if not float(raw_roll).is_integer():
                        raise ValueError(f"Student workbook row {row_number} has a non-integer roll number")
                    roll = str(int(raw_roll))
                    number_format = cell.number_format if cell is not None else ""
                    if re.fullmatch(r"0+", number_format or ""):
                        roll = roll.zfill(len(number_format))
                else:
                    roll = str(raw_roll).strip()
                name_value = values[fields["name"]] if fields["name"] < len(values) else None
                class_value = values[fields["class_name"]] if "class_name" in fields and fields["class_name"] < len(values) else None
                name = str(name_value).strip() if name_value is not None else ""
                class_name = str(class_value).strip() if class_value is not None else ""
                if roll in seen:
                    raise ValueError(f"Student workbook row {row_number} repeats roll number {roll}")
                seen.add(roll)
                rows.append((roll, name, class_name))
        finally:
            workbook.close()
        if not rows:
            raise ValueError("Student workbook did not contain any roll numbers")
        with _database_connection(self.path) as db:
            db.executemany("INSERT INTO students(roll_number,name,class_name) VALUES (?,?,?) ON CONFLICT(roll_number) DO UPDATE SET name=excluded.name,class_name=excluded.class_name", rows)
        return len(rows)
