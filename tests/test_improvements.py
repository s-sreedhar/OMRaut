"""End-to-end verification for partial/light fill review flags, top Name OCR, DB roll lookup, and Mark List PDF."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtWidgets import QApplication

from omr_exam.database import Repository
from omr_exam.layout import create_layout
from omr_exam.ocr import name_box_mm
from omr_exam.reader import read_image
from omr_exam.reports import EXPORTS, export_function, export_mark_list_pdf
from omr_exam.review import COL_NAME, COL_ROLL
from omr_exam.scoring import score_answers
from omr_exam.sheets import generate_sheet_pdf
from omr_exam.templates import TEMPLATES, build_from_template
from omr_exam.test_builder import create_test_config
from omr_exam.ui import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def _render_sheet(config, tmp_path: Path):
    pdf_path = generate_sheet_pdf(config, tmp_path / "sheet.pdf")
    doc = pymupdf.open(pdf_path)
    try:
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(300 / 72, 300 / 72), alpha=False)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).copy()
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    finally:
        doc.close()
    return gray, create_layout(config)


def test_partial_half_and_light_fills_detected_and_flagged_for_review(tmp_path):
    config = create_test_config(
        title="Mark Review QA", test_date="2026-11-10", class_name="10A", subject="Math",
        question_count=4, options=["A", "B", "C", "D"], roll_number_digits=4, set_codes=["A"], columns=1,
    )
    image, layout = _render_sheet(config, tmp_path)
    sx, sy = image.shape[1] / layout.width, image.shape[0] / layout.height

    def center(group, index, value):
        b = next(x for x in layout.bubbles if x.group == group and x.index == str(index) and x.value == str(value))
        return round(b.x * sx), round(b.y * sy)

    # Full roll number 1234, with position 0 half-filled to verify roll review flagging too
    for pos, digit in enumerate("1234"):
        cx, cy = center("roll", pos, digit)
        r = round(1.05 * min(sx, sy))
        if pos == 0:
            # Left half-filled black
            cv2.ellipse(image, (cx, cy), (r, r), 0, 90, 270, 0, -1)
        else:
            cv2.circle(image, (cx, cy), r, 0, -1)

    # Q1: clean full solid dark fill -> confident
    cv2.circle(image, center("answer", 1, "B"), round(1.05 * min(sx, sy)), 0, -1)
    # Q2: half-filled dark bubble -> detected as "C" and flagged "review" with partial reason
    r = round(1.05 * min(sx, sy))
    cv2.ellipse(image, center("answer", 2, "C"), (r, r), 0, 90, 270, 0, -1)
    # Q3: full bubble lightly shaded (light gray 180) -> detected as "D" and flagged "review" with light reason
    cv2.circle(image, center("answer", 3, "D"), r, 180, -1)
    # Q4: small faint partial dot -> flagged "faint"
    cv2.circle(image, center("answer", 4, "A"), round(0.42 * min(sx, sy)), 175, -1)

    result = read_image(image, config)
    assert result.requires_review
    assert result.answers["1"] == "B"
    assert not any(f.get("question") == "1" for f in result.flags)

    assert result.answers["2"] == "C"
    q2_flag = next(f for f in result.flags if f.get("group") == "answer" and f.get("question") == "2")
    assert q2_flag["kind"] == "review"
    assert "partial" in q2_flag.get("reason", "")

    assert result.answers["3"] == "D"
    q3_flag = next(f for f in result.flags if f.get("group") == "answer" and f.get("question") == "3")
    assert q3_flag["kind"] == "review"
    assert "light" in q3_flag.get("reason", "")

    assert result.answers["4"] is None
    assert any(f.get("kind") == "faint" and f.get("question") == "4" for f in result.flags)

    # Roll digit 0 half-filled must also flag roll:0 for review
    assert any(f.get("group") == "roll" and f.get("question") == "0" and f.get("kind") == "review" for f in result.flags)


def test_top_name_box_and_roll_scanned_together(tmp_path):
    config = create_test_config(
        title="Name OCR QA", test_date="2026-11-10", class_name="10A", subject="Science",
        question_count=4, options=["A", "B", "C", "D"], roll_number_digits=6, set_codes=["A"], columns=1,
    )
    image, layout = _render_sheet(config, tmp_path)
    sx, sy = image.shape[1] / layout.width, image.shape[0] / layout.height

    # Blank NAME box yields empty scanned name
    blank_res = read_image(image, config)
    assert blank_res.student_name == ""

    # Draw block characters inside the 22 NAME cells at the top of the sheet
    box = name_box_mm(config, layout)
    assert box is not None
    bx, by, bw, bh, cells = box
    pil = Image.fromarray(image)
    draw = ImageDraw.Draw(pil)
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = ImageFont.truetype(str(font_path), round(5.5 * sy)) if font_path.is_file() else ImageFont.load_default(round(5.5 * sy))
    for idx, ch in enumerate("ASHA RAO"):
        if ch == " ":
            continue
        cx = (bx + (idx + 0.5) * (bw / cells)) * sx
        cy = (by + 0.52 * bh) * sy
        draw.text((cx, cy), ch, fill=10, font=font, anchor="mm")
    filled = np.array(pil)

    for pos, digit in enumerate("004321"):
        b = next(x for x in layout.bubbles if x.group == "roll" and x.index == str(pos) and x.value == digit)
        cv2.circle(filled, (round(b.x * sx), round(b.y * sy)), round(1.05 * min(sx, sy)), 0, -1)
    q1b = next(x for x in layout.bubbles if x.group == "answer" and x.index == "1" and x.value == "B")
    cv2.circle(filled, (round(q1b.x * sx), round(q1b.y * sy)), round(1.05 * min(sx, sy)), 0, -1)

    res = read_image(filled, config)
    assert res.roll_number == "004321"
    assert res.answers["1"] == "B"
    assert "ASHA" in res.student_name.upper()
    assert "RAO" in res.student_name.upper()


def test_existing_database_roll_picks_db_name_and_ui_updates(tmp_path):
    _app()
    config = create_test_config(
        title="DB Lookup QA", test_date="2026-11-10", class_name="10A", subject="Science",
        question_count=3, options=["A", "B", "C", "D"], roll_number_digits=6, set_codes=["A"], columns=1,
    )
    config["sets"]["A"]["answer_key"] = {"1": "A", "2": "B", "3": "C"}
    db_path = tmp_path / "lookup.sqlite3"
    repo = Repository(db_path)
    repo.save_student("000123", "Asha Rao", "10A")
    repo.save_student("00921863", "Meera Nair", "10A")
    test_id = repo.save_test(config)
    batch_id = repo.create_batch(test_id, "lookup-batch", expected_pages=3)

    from omr_exam.reader import ReadResult
    # 1. Exact DB roll match -> DB name overrides scanned name
    r1 = ReadResult("s1.png", {"1": "A", "2": "B", "3": "C"}, "000123", "A", {},
                    flags=[{"kind": "review", "group": "answer", "question": "2", "value": "B", "values": ["B"]},
                           {"kind": "review", "group": "roll", "question": "0", "value": "0", "values": ["0"]}],
                    student_name="OCR NOISE")
    # 2. Numeric leading-zero DB match ("921863" vs "00921863") -> DB name picked!
    r2 = ReadResult("s2.png", {"1": "A", "2": "B", "3": "C"}, "921863", "A", {}, [], student_name="")
    # 3. Unknown roll -> falls back to scanned_name
    r3 = ReadResult("s3.png", {"1": "A", "2": "B", "3": "C"}, "000777", "A", {}, [], student_name="KIRAN KUMAR")

    for r in (r1, r2, r3):
        repo.store_read(batch_id, r, score_answers(config, "A", r.answers))
    repo.reconcile_batch_roster_flags(batch_id)

    rows = {row["roll_number"]: row for row in repo.results_for_batch(batch_id)}
    assert rows["000123"]["student_name"] == "Asha Rao"
    assert rows["921863"]["student_name"] == "Meera Nair"
    assert not any(f["kind"] == "unknown_id" for f in rows["921863"]["flags"])
    assert rows["000777"]["student_name"] == "KIRAN KUMAR"
    assert any(f["kind"] == "unknown_id" for f in rows["000777"]["flags"])

    window = MainWindow(db_path)
    try:
        window.refresh_tests()
        window._open_batch(repo.batches(test_id)[0])
        window.show_page("review")
        page = window.review_page
        # Row 0 is 000123 -> DB name shown in COL_NAME & name_edit
        assert page.table.item(0, COL_NAME).text() == "Asha Rao"
        # Verify partial/review roll highlights COL_ROLL in grid
        assert page.table.item(0, COL_ROLL).background().color().name() != "#ffffff"
        unknown_row = next(i for i, rec in enumerate(page.records) if rec["roll_number"] == "000777")
        page.select_row(unknown_row)  # 000777 (KIRAN KUMAR)
        assert page.name_edit.text() == "KIRAN KUMAR"
        # Changing roll to an existing DB roll picks DB name immediately
        page.roll_edit.setText("000123")
        page._roll_edited("000123")
        assert page.name_edit.text() == "Asha Rao"
        assert page.table.item(unknown_row, COL_NAME).text() == "Asha Rao"
    finally:
        window.close()


def test_mark_list_pdf_matches_expected_format_and_dense_ranks(tmp_path):
    assert "mark-list" in EXPORTS
    assert export_function("mark-list") is export_mark_list_pdf

    config = create_test_config(
        title="ECONOMICS_22", test_date="2025-09-22", class_name="12A", subject="Economics",
        question_count=50, options=["A", "B", "C", "D"], roll_number_digits=8, set_codes=["A"], columns=2,
    )
    # 50 questions * +4 = 200 max marks, -1 wrong
    config["scoring"] = {"correct": 4.0, "incorrect": -1.0, "blank": 0.0, "bonus_questions": [], "dropped_questions": [], "partial_credit": {}}
    key = {str(q["id"]): "A" for q in config["questions"]}
    config["sets"]["A"]["answer_key"] = key

    def make_answers(right: int, wrong: int) -> dict[str, str | None]:
        ans = {}
        for i in range(1, 51):
            if i <= right:
                ans[str(i)] = "A"
            elif i <= right + wrong:
                ans[str(i)] = "B"
            else:
                ans[str(i)] = None
        return ans

    records = [
        {"roll_number": "921863", "student_name": "Topper", "set_code": "A", "answers": make_answers(32, 4), "review_required": False},    # 124.00, 62.00%, Rank 1
        {"roll_number": "46070263", "student_name": "Tie One", "set_code": "A", "answers": make_answers(10, 12), "review_required": False}, # 28.00, 14.00%, Rank 2
        {"roll_number": "1422760", "student_name": "Tie Two", "set_code": "A", "answers": make_answers(10, 12), "review_required": False},  # 28.00, 14.00%, Rank 2
        {"roll_number": "45997512", "student_name": "Next", "set_code": "A", "answers": make_answers(8, 5), "review_required": False},      # 27.00, 13.50%, Rank 3 (dense!)
        {"roll_number": "1410106", "student_name": "", "set_code": "A", "answers": make_answers(1, 6), "review_required": False},           # -2.00, 0.00%, Rank 4
    ]

    # Add enough extra records to span multiple pages and confirm header repeats
    for idx in range(45):
        records.append({
            "roll_number": f"4600{idx:04d}", "student_name": f"Student {idx}", "set_code": "A",
            "answers": make_answers(5, 2), "review_required": False,
        })

    pdf_path = export_mark_list_pdf(config, records, tmp_path / "mark-list.pdf")
    assert pdf_path.is_file()
    doc = pymupdf.open(pdf_path)
    try:
        assert len(doc) >= 2
        page0 = doc[0].get_text()
        page1 = doc[1].get_text()
        for page_text in (page0, page1):
            assert "Mark List" in page_text
            assert "ECONOMICS_22 - 22/09/2025 (Max. Marks: 200)" in page_text
            assert "Class : 12A" in page_text
            assert "Total" in page_text
            for col in ("Roll No", "Name", "R", "W", "B", "Marks", "%", "Rank"):
                assert col in page_text
        full_text = "\n".join(doc[i].get_text() for i in range(len(doc)))
        assert "124.00" in full_text and "62.00" in full_text
        assert "-2.00" in full_text and "0.00" in full_text
        # Dense rank: Tie One & Tie Two both rank 2, Next ("45997512", 27.00) must be rank 3
        lines = [line.strip() for line in full_text.splitlines() if line.strip()]
        idx_next = lines.index("45997512")
        assert lines[idx_next + 7] == "3"
    finally:
        doc.close()


def test_omr_sheet_and_exam_pdfs_embed_aligned_default_logo(tmp_path):
    """OMR sheets and every exam PDF export embed the bundled logo cleanly aligned at the top."""
    import pymupdf
    from omr_exam.reports import PDF_EXPORT_KINDS, export_all
    from omr_exam.resources import default_logo_path

    default_logo = default_logo_path()
    assert default_logo is not None and default_logo.is_file()

    config = build_from_template(
        TEMPLATES[1], title="ECONOMICS_22", class_name="GS-1",
        subjects=["Economics", "Polity"], test_date="2025-09-22", fields=["name", "class"],
    )
    for code in config["sets"]:
        config["sets"][code]["answer_key"] = {str(q["id"]): "A" for q in config["questions"]}

    # 1. OMR sheet embeds default logo at top-right clear of corner squares & NAME/CLASS boxes
    omr_pdf = generate_sheet_pdf(config, tmp_path / "omr.pdf")
    doc = pymupdf.open(omr_pdf)
    try:
        page = doc[0]
        images = page.get_images(full=True)
        assert len(images) == 1
        rects = page.get_image_rects(images[0][0])
        assert len(rects) == 1
        r = rects[0]
        mm = 72.0 / 25.4
        # QualifyIAS format: logo centred at the top, clear of the corner squares and the header table (27mm)
        assert abs((r.x0 + r.x1) / 2 - 105.0 * mm) < 1.0 * mm
        assert 3.0 * mm <= r.y0 < r.y1 <= 22.0 * mm
    finally:
        doc.close()

    # 2. All exam PDF exports embed the logo cleanly in top-right header without overlapping text
    records = [
        {"roll_number": "46024436", "student_name": "Aarav Sharma", "class_name": "GS-1",
         "set_code": "A", "review_required": False,
         "answers": {str(q["id"]): "A" for q in config["questions"]}},
        {"roll_number": "46034336", "student_name": "Diya Nair", "class_name": "GS-1",
         "set_code": "A", "review_required": False,
         "answers": {str(q["id"]): ("A" if int(q["id"]) <= 12 else "B") for q in config["questions"]}},
    ]
    out_dir = tmp_path / "exports"
    written = export_all(config, records, out_dir, kinds=sorted(PDF_EXPORT_KINDS))
    assert len(written) == len(PDF_EXPORT_KINDS)
    for pdf_path in written:
        assert pdf_path.is_file() and pdf_path.stat().st_size > 0
        pdf_doc = pymupdf.open(pdf_path)
        try:
            logo_pages = 0
            for page_idx in range(len(pdf_doc)):
                p = pdf_doc[page_idx]
                imgs = p.get_images(full=True)
                if imgs:
                    logo_pages += 1
                    rect = p.get_image_rects(imgs[0][0])[0]
                    assert rect.y0 < p.rect.height * 0.25
                    assert rect.x1 > p.rect.width * 0.70
                    for word in p.get_text("words"):
                        w_rect = pymupdf.Rect(word[:4])
                        assert not rect.intersects(w_rect), f"{pdf_path.name} logo overlaps text {word[4]!r}"
                elif page_idx == 0 or pdf_path.name == "mark-list.pdf":
                    raise AssertionError(f"{pdf_path.name} page {page_idx} missing header logo")
            if pdf_path.name in {"student-scorecards.pdf", "candidate-response-analysis.pdf", "assessment-reports.pdf", "sub-section-report.pdf"}:
                assert logo_pages >= len(records)
        finally:
            pdf_doc.close()


def test_ui_sheet_and_exam_pdf_downloads_append_pdf_extension_and_keep_logo(tmp_path, monkeypatch):
    """Downloading OMR sheets or exam PDFs from UI uses default logo and ensures .pdf output files."""
    pytest.importorskip("PySide6")
    import pymupdf
    from PySide6.QtWidgets import QApplication, QFileDialog
    from omr_exam.resources import default_logo_path
    from omr_exam.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(str(tmp_path / "ui.sqlite3"))
    try:
        config = build_from_template(TEMPLATES[0], title="GS Mock 1", class_name="Batch A", subject="Polity")
        for code in config["sets"]:
            config["sets"][code]["answer_key"] = {str(q["id"]): "A" for q in config["questions"]}
        test_id = window.repo.save_test(config)
        window.refresh_tests(test_id)

        _inst, logo = window.sheet_branding()
        assert logo == default_logo_path()

        sheet_target_no_ext = tmp_path / "my_sheet_download"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **kw: (str(sheet_target_no_ext), "PDF (*.pdf)")))
        import omr_exam.ui as ui_mod
        monkeypatch.setattr(ui_mod, "confirm", lambda *a, **kw: False)
        window.make_sheets()

        expected_sheet = tmp_path / "my_sheet_download.pdf"
        assert expected_sheet.is_file()
        sheet_doc = pymupdf.open(expected_sheet)
        assert len(sheet_doc[0].get_images(full=True)) == 1
        sheet_doc.close()

        window.result_records = [{
            "roll_number": "1001", "student_name": "Rohan Verma", "class_name": "Batch A",
            "set_code": "A", "review_required": False,
            "answers": {str(q["id"]): "A" for q in config["questions"]},
        }]
        report_target_no_ext = tmp_path / "mark_list_download"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **kw: (str(report_target_no_ext), "PDF (*.pdf)")))
        window.export_results("mark-list", checked=True)

        expected_report = tmp_path / "mark_list_download.pdf"
        assert expected_report.is_file()
        rep_doc = pymupdf.open(expected_report)
        assert len(rep_doc[0].get_images(full=True)) == 1
        rep_doc.close()
    finally:
        window.close()
        app.processEvents()
