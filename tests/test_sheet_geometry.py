"""Printed answer sheets must never have overlapping text or text running over box outlines."""
from __future__ import annotations

import pytest

from omr_exam.sheets import generate_sheet_pdf
from omr_exam.templates import TEMPLATES, build_from_template

fitz = pytest.importorskip("fitz")


def _edges(page):
    edges = []
    for drawing in page.get_drawings():
        if drawing.get("color") is None:          # fill-only shapes (row stripes, corner marks) have no outline
            continue
        for item in drawing["items"]:
            if item[0] == "re":
                r = item[1]
                edges += [fitz.Rect(r.x0, r.y0, r.x1, r.y0), fitz.Rect(r.x0, r.y1, r.x1, r.y1),
                          fitz.Rect(r.x0, r.y0, r.x0, r.y1), fitz.Rect(r.x1, r.y0, r.x1, r.y1)]
            elif item[0] == "l":
                a, b = item[1], item[2]
                edges.append(fitz.Rect(min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y)))
    return edges


def _problems(path):
    out = []
    doc = fitz.open(str(path))
    for page in doc:
        words = [(fitz.Rect(w[:4]), w[4]) for w in page.get_text("words")]
        edges = _edges(page)
        for i, (a, ta) in enumerate(words):
            core = fitz.Rect(a.x0 + 0.3, a.y0 + 1.0, a.x1 - 0.3, a.y1 - 1.0)   # ignore font side-bearings
            for b, tb in words[i + 1:]:
                if core.intersects(fitz.Rect(b.x0 + 0.3, b.y0 + 1.0, b.x1 - 0.3, b.y1 - 1.0)):
                    out.append(f"text overlap: {ta!r} / {tb!r}")
            for e in edges:
                if abs(e.width) < 0.01 or abs(e.height) < 0.01:
                    e = fitz.Rect(e.x0 - 0.05, e.y0 - 0.05, e.x1 + 0.05, e.y1 + 0.05)
                if core.intersects(e):
                    out.append(f"text crosses an outline: {ta!r} at {tuple(round(v) for v in a)}")
                    break
            if a.x0 < 0 or a.y0 < 0 or a.x1 > page.rect.x1 or a.y1 > page.rect.y1:
                out.append(f"text off page: {ta!r}")
    return out


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.name)
def test_every_template_prints_without_overlaps(template, tmp_path):
    subjects = ["Physics", "Chemistry"] if template.question_count >= 25 else ["Science"]
    config = build_from_template(template, title="Sreedhar Testign New", class_name="Mitakshra",
                                 subjects=subjects, fields=["name", "class"])
    assert _problems(generate_sheet_pdf(config, tmp_path / "a.pdf")) == []


def test_long_text_logo_and_field_variants_do_not_overlap(tmp_path):
    import cv2
    import numpy as np

    logo = tmp_path / "logo.png"
    cv2.imwrite(str(logo), np.full((90, 200, 3), 120, np.uint8))
    long_title = "An extremely long mock examination title with many words " * 2
    for fields in ([], ["name"], ["class"], ["name", "class"]):
        for use_logo in (False, True):
            config = build_from_template(TEMPLATES[1], title=long_title, class_name="Class 10 Section A",
                                         subjects=["Economy", "Polity", "Science & Technology"], fields=fields)
            path = generate_sheet_pdf(config, tmp_path / "b.pdf", institute="A Very Long Institute Name Pvt Ltd",
                                      logo=logo if use_logo else None)
            assert _problems(path) == [], (fields, use_logo)


def test_qualifyias_format_header_footer_and_fields(tmp_path):
    config = build_from_template(TEMPLATES[7], title="FLT 1", class_name="UPSC", subjects=["GS"])
    path = generate_sheet_pdf(config, tmp_path / "q.pdf")
    text = fitz.open(str(path))[0].get_text()
    for expected in ("Gandhi Nagar", "Hyderabad, Telangana 500080", "QUALIFY IAS", "SUBJECT CODE",
                     "BOOKLET CODE", "ROLL NUMBER", "SIGNATURE OF INVIGILATOR"):
        assert expected in text
    assert "CLASS" not in text
    page = fitz.open(str(path))[0]
    assert page.rect.width == pytest.approx(595.28, abs=0.5) and page.rect.height == pytest.approx(841.89, abs=0.5)
    assert len([b for b in __import__("omr_exam.layout", fromlist=["x"]).create_layout(config).bubbles
                if b.group == "subject"]) == 20
