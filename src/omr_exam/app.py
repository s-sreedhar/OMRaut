"""PySide6 desktop application entry point."""
from __future__ import annotations

import sys
import json
import sqlite3
import tempfile
import os
from contextlib import closing
from pathlib import Path

# Velopack must process install/update lifecycle arguments before Qt starts.
if __name__ == "__main__":
    import velopack
    velopack.App().run()

if "--smoke-test" in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication

from omr_exam.ui import MainWindow


def _smoke_test() -> int:
    """Exercise packaged resources, imports, and a disposable SQLite database."""
    from omr_exam.resources import config_dir
    from omr_exam.database import Repository
    from omr_exam.config import validate_config
    from omr_exam.ingest import scan_pages
    from omr_exam.layout import create_layout
    from omr_exam.reader import read_image
    from omr_exam.sheets import generate_sheet_pdf
    from omr_exam.test_builder import create_test_config
    from omr_exam.updater import UPDATE_SOURCE_URL, is_valid_update_source

    bundled = config_dir()
    schema = json.loads((bundled / "test-config.schema.json").read_text(encoding="utf-8"))
    json.loads((bundled / "update.json").read_text(encoding="utf-8-sig"))
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise RuntimeError("Bundled test configuration schema is invalid")
    if UPDATE_SOURCE_URL and not is_valid_update_source(UPDATE_SOURCE_URL):
        raise RuntimeError("Bundled Velopack update source must use HTTPS (HTTP is allowed only on localhost for testing)")
    # Import optional runtime modules that PyInstaller must carry into the bundle.
    import cv2  # noqa: F401
    import fitz  # noqa: F401
    from openpyxl import Workbook
    import reportlab  # noqa: F401
    import jsonschema  # noqa: F401
    with tempfile.TemporaryDirectory(prefix="omr-exam-smoke-") as temporary:
        temporary_path = Path(temporary)
        database_path = temporary_path / "smoke.sqlite3"
        repository = Repository(database_path)
        with closing(sqlite3.connect(database_path)) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Packaged SQLite initialization failed its integrity check")
        config = create_test_config(title="Package smoke", test_date="2026-01-01", class_name="QA", subject="QA",
                                    question_count=4, options=["A", "B", "C", "D"], roll_number_digits=4,
                                    set_codes=["A"], columns=1)
        config["sets"]["A"]["answer_key"] = {str(question["id"]): "A" for question in config["questions"]}
        validate_config(config)
        test_id = repository.save_test(config)
        repository.create_batch(test_id, "smoke", expected_pages=1)
        pdf_path = generate_sheet_pdf(config, temporary_path / "smoke.pdf")
        pages = list(scan_pages(pdf_path))
        if len(pages) != 1:
            raise RuntimeError("Packaged PDF generation and scan ingestion smoke test failed")
        image = pages[0][1].copy()
        layout = create_layout(config)
        sx, sy = image.shape[1] / layout.width, image.shape[0] / layout.height

        def mark(group: str, index: str, value: str) -> None:
            bubble = next(item for item in layout.bubbles
                          if item.group == group and item.index == index and item.value == value)
            center = round(bubble.x * sx), round(bubble.y * sy)
            cv2.circle(image, center, round(1.05 * min(sx, sy)), 0, -1)

        expected_answers = {str(question["id"]): "A" for question in config["questions"]}
        for question in expected_answers:
            mark("answer", question, "A")
        for position, digit in enumerate("1234"):
            mark("roll", str(position), digit)
        mark("set", "0", "A")
        read_result = read_image(image, config)
        if (read_result.answers != expected_answers or read_result.roll_number != "1234"
                or read_result.set_code != "A" or read_result.requires_review):
            raise RuntimeError(
                "Packaged generator-to-reader smoke test failed: "
                f"answers={read_result.answers}, roll={read_result.roll_number}, "
                f"set={read_result.set_code}, flags={read_result.flags}"
            )
        workbook = Workbook()
        workbook.active.title = "Smoke"
        workbook.save(temporary_path / "smoke.xlsx")
        application = QApplication(["OMRExamApp", "--smoke-test"])
        window = MainWindow(str(database_path))
        window.show()
        application.processEvents()
        if not window.isVisible():
            raise RuntimeError("Packaged desktop window did not start")
        if window.dashboard_tests_table.rowCount() != 1 or window.dashboard_batches_table.rowCount() != 1:
            raise RuntimeError("Dashboard did not load the saved test and batch from SQLite")
        window.open_dashboard_test(0)
        window.open_dashboard_batch(0)
        if window.active_test_id != test_id or window.batch_id is None:
            raise RuntimeError("Dashboard could not open a saved test and batch")
        window.close()
        application.processEvents()
    return 0


def main() -> int:
    if "--smoke-test" in sys.argv:
        return _smoke_test()
    if "--update-smoke-test" in sys.argv:
        index = sys.argv.index("--update-smoke-test")
        arguments = sys.argv[index + 1:]
        if len(arguments) != 2:
            raise SystemExit("--update-smoke-test requires an expected version and completion-marker path")
        from omr_exam.updater import UPDATE_SOURCE_URL, run_update_smoke_test
        run_update_smoke_test(arguments[0], UPDATE_SOURCE_URL, arguments[1])
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName("OMR Exam App")
    app.setOrganizationName("OMR Exam App")
    data_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    window = MainWindow(data_dir + "/omr-exam.sqlite3")
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
