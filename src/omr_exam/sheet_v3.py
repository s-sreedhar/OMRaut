"""QualifyIAS-format page drawing (answer grid version 3).

Header table (name / subject code / booklet code / roll number), left invigilator panel, red
bubbles and an institute footer. Every bubble position comes from ``layout`` so the reader and the
printed page stay in step."""
from __future__ import annotations

from typing import Any

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.utils import simpleSplit

from omr_exam.layout import V3_DIGIT_PITCH, V3_DIGIT_Y0, V3_TABLE_BOTTOM, V3_TABLE_TOP, SheetLayout

MM = 72 / 25.4
BRAND = HexColor("#B40000")          # QualifyIAS logo red
BRAND_LINE = HexColor("#B40000")
BRAND_SOFT = HexColor("#C77B7B")
BRAND_PALE = HexColor("#FBEAEA")
ROW_PALE = HexColor("#FCF1F1")

INSTITUTE_ADDRESS = "First Floor, Building No 1-1-726/1, Gandhi Nagar, Ashok Nagar, Hyderabad, Telangana 500080"
INSTITUTE_CONTACT = "www.qualifyias.com  |  +91 93118 40789"
INSTITUTE_FOUNDER = "Founder: Rajinikanth Sir (AIR 587)"
CAPTION = ("From Aspirant", "to Administrator.")
TAGLINE = "An Institution Built on Consistency, Direction, and Discipline in Preparation."


def draw_page(pdf: Any, layout: SheetLayout, config: dict[str, Any], logo_info: Any, institute_name: str) -> None:
    r = layout.regions
    left, right = r["left"], r["right"]
    page_w = layout.width

    def xy(x: float, y_top: float) -> tuple[float, float]:
        return x * MM, (layout.height - y_top) * MM

    def line(x1: float, y1: float, x2: float, y2: float, width: float = 0.6, color=BRAND_LINE) -> None:
        pdf.setStrokeColor(color); pdf.setLineWidth(width)
        a, b = xy(x1, y1), xy(x2, y2)
        pdf.line(a[0], a[1], b[0], b[1])

    def rect(x: float, y: float, w: float, h: float, *, stroke=BRAND_LINE, fill=None, width: float = 0.6) -> None:
        px, py = xy(x, y + h)
        pdf.setLineWidth(width)
        if stroke is not None:
            pdf.setStrokeColor(stroke)
        if fill is not None:
            pdf.setFillColor(fill)
        pdf.rect(px, py, w * MM, h * MM, stroke=1 if stroke is not None else 0, fill=1 if fill is not None else 0)

    def label(value: str, x: float, y: float, size: float, *, font: str = "Helvetica", color=black,
              align: str = "left") -> None:
        px, py = xy(x, y)
        pdf.setFillColor(color); pdf.setFont(font, size)
        {"left": pdf.drawString, "center": pdf.drawCentredString, "right": pdf.drawRightString}[align](px, py, value)

    def fit(value: str, width_mm: float, size: float, font: str, minimum: float = 5.0) -> tuple[str, float]:
        while size > minimum and pdf.stringWidth(value, font, size) > width_mm * MM:
            size -= 0.25
        while len(value) > 4 and pdf.stringWidth(value, font, size) > width_mm * MM:
            value = value[:-2].rstrip() + "…"
        return value, size

    # ---- Logo, centred at the top -------------------------------------------------
    if logo_info is not None:
        reader, w_px, h_px = logo_info
        scale = min(44.0 / max(1.0, w_px), 15.5 / max(1.0, h_px))
        dw, dh = w_px * scale, h_px * scale
        px, py = xy(page_w / 2 - dw / 2, 4.8 + dh)
        pdf.drawImage(reader, px, py, dw * MM, dh * MM)
    else:
        label("QUALIFY IAS", page_w / 2, 15, 16, font="Helvetica-Bold", color=BRAND, align="center")
    # Website caption either side of the logo.
    label(CAPTION[0], left, 11.5, 10.5, font="Helvetica-BoldOblique", color=BRAND)
    label(CAPTION[1], left, 16.3, 10.5, font="Helvetica-BoldOblique", color=BRAND)
    ty = 9.2
    for part in simpleSplit(TAGLINE, "Helvetica-Oblique", 6.2, 62 * MM):
        label(part, right, ty, 6.2, font="Helvetica-Oblique", align="right")
        ty += 3.2
    label("OMR ANSWER SHEET", page_w / 2, 24.6, 10.5, font="Helvetica-Bold", color=BRAND, align="center")

    # ---- Header table ---------------------------------------------------------------
    top, bottom = V3_TABLE_TOP, V3_TABLE_BOTTOM
    rect(left, top, right - left, 4.5, fill=BRAND_PALE, stroke=None)
    rect(left, top, right - left, bottom - top, width=0.9)
    line(left, top + 4.5, right, top + 4.5, 0.6)
    multi_set = r["booklet_w"] > 0
    for x in [r["subject_x"], *( [r["booklet_x"]] if multi_set else []), r["roll_x"]]:
        line(x, top, x, bottom, 0.6)
    mid = lambda x, w: x + w / 2
    label("NAME / TEST DETAILS", mid(left, r["name_w"]), top + 3.1, 5.4, font="Helvetica-Bold", color=BRAND, align="center")
    label("SUBJECT CODE", mid(r["subject_x"], r["subject_w"]), top + 3.1, 4.6, font="Helvetica-Bold", color=BRAND, align="center")
    if multi_set:
        label("BOOKLET CODE", mid(r["booklet_x"], r["booklet_w"]), top + 3.1, 4.2, font="Helvetica-Bold", color=BRAND, align="center")
    label("ROLL NUMBER", mid(r["roll_x"], r["roll_w"]), top + 3.1, 5.4, font="Helvetica-Bold", color=BRAND, align="center")

    # Candidate name (hand-written, optionally read by OCR) and test details.
    inner_w = r["name_w"] - 6.0
    if layout.name_box is not None:
        bx, by, bw, bh, cells = layout.name_box
        rect(bx, by, bw, bh, stroke=BRAND_LINE, width=0.8)
        for cell in range(1, cells):
            line(bx + bw * cell / cells, by, bx + bw * cell / cells, by + bh, 0.3, BRAND_SOFT)
        label("Write your name in block letters, one letter per box", bx, by + bh + 2.6, 4.4, color=BRAND)
    cx = mid(left, r["name_w"])
    title, size = fit(config["test"]["title"], inner_w, 10.5, "Helvetica-Bold", 6.5)
    label(title, cx, 53.0, size, font="Helvetica-Bold", align="center")
    subjects, size = fit(", ".join(config["test"]["subjects"]), inner_w, 7.5, "Helvetica", 5.5)
    label(subjects, cx, 58.0, size, align="center")
    label(str(config["test"].get("date", "")), cx, 62.4, 6.5, align="center")
    sig_top = 66.0
    rect(left + 2.0, sig_top, r["name_w"] - 4.0, bottom - 2.0 - sig_top, stroke=BRAND_LINE, width=0.6)
    label("SIGNATURE OF CANDIDATE", left + 3.5, sig_top + 3.0, 4.4, font="Helvetica-Bold", color=BRAND)

    # Write-in squares above the subject / booklet / roll bubbles.
    def squares(xs: list[float]) -> None:
        for x in xs:
            rect(x - 2.75, 32.5, 5.5, 5.5, stroke=BRAND_LINE, width=0.7)

    subject_cols = sorted({b.x for b in layout.bubbles if b.group == "subject"})
    squares(subject_cols)
    squares(list(r["roll_cols"]))
    if multi_set:
        squares([r["booklet_x"] + 9.0])
    for group_x in (subject_cols, list(r["roll_cols"])):
        for x in group_x:
            for digit in range(10):
                label(str(digit), x - 4.6, V3_DIGIT_Y0 + digit * V3_DIGIT_PITCH + 0.8, 4.2, color=BRAND)
    if multi_set:
        for b in layout.bubbles:
            if b.group == "set":
                label(b.value, b.x - 4.5, b.y + 0.9, 5, font="Helvetica-Bold", color=BRAND)

    # ---- Answer grid ------------------------------------------------------------------
    start_y, pitch, qpc = r["start_y"], r["pitch_y"], max(1, layout.questions_per_column)
    columns = layout.question_columns
    col_x0, col_w = r["col_x0"], r["col_width"]
    centres = {int(b.index): b.y for b in layout.bubbles if b.group == "answer"}
    box_top = start_y - 4.8
    full_bottom = start_y + (qpc - 1) * pitch + 4.5
    for column in range(columns):
        ids = [q for q in sorted(centres) if (q - 1) // qpc == column]
        if not ids:
            continue
        x0 = col_x0 + column * col_w
        c_bottom = max(centres[q] for q in ids) + 4.5
        for k, q in enumerate(ids):
            if k % 2 == 0:
                h = min(4.8, pitch * 0.62)
                rect(x0 + 0.25, centres[q] - h / 2, col_w - 0.5, h, stroke=None, fill=ROW_PALE)
        rect(x0, box_top, col_w, c_bottom - box_top, width=0.8)
    for option, x, y in layout.option_labels:
        label(option, x, y, 5.6, font="Helvetica-Bold", color=BRAND, align="center")
    for text, x, y in layout.question_labels:
        label(text, x + 1.6, y, 6.2, font="Helvetica-Bold")

    # ---- Left panel: instructions + invigilator signature ------------------------------------
    px0, pw = r["panel_x"], r["panel_w"]
    rect(px0, box_top, pw, full_bottom - box_top, width=0.8)
    rect(px0, box_top, pw, 5.0, fill=BRAND_PALE, stroke=BRAND_LINE, width=0.6)
    label("INSTRUCTIONS", px0 + pw / 2, box_top + 3.5, 5.0, font="Helvetica-Bold", color=BRAND, align="center")
    y = box_top + 9.0
    for n, text in enumerate(("Use a blue or black ball pen only.", "Darken the circle completely.",
                              "Mark only one answer per question.", "Do not erase, overwrite or fold.")):
        for part in simpleSplit(f"{n + 1}. {text}", "Helvetica", 4.6, (pw - 4.0) * MM):
            label(part, px0 + 2.0, y, 4.6)
            y += 2.5
        y += 1.2
    y += 1.5
    cx0 = px0 + 5.0
    pdf.setLineWidth(0.5)
    for k, (caption, kind) in enumerate((("RIGHT", "full"), ("WRONG", "tick"), ("WRONG", "half"))):
        ex, ey = xy(cx0, y + k * 6.2)
        pdf.setStrokeColor(BRAND); pdf.setFillColor(black if kind == "full" else white)
        pdf.circle(ex, ey, 1.8 * MM, stroke=1, fill=1)
        pdf.setStrokeColor(black)
        if kind == "tick":
            pdf.line(ex - 1.1 * MM, ey - 0.1 * MM, ex - 0.2 * MM, ey - 1.0 * MM)
            pdf.line(ex - 0.2 * MM, ey - 1.0 * MM, ex + 1.3 * MM, ey + 1.2 * MM)
        elif kind == "half":
            pdf.setFillColor(black)
            pdf.circle(ex, ey, 0.6 * MM, stroke=0, fill=1)
        label(caption, cx0 + 4.6, y + k * 6.2 + 0.8, 4.6, font="Helvetica-Bold", color=BRAND if k == 0 else black)
    sig_h = min(44.0, full_bottom - (y + 18.0) - 8.0)
    if sig_h >= 20.0:
        sig_bottom = full_bottom - 3.0
        rect(px0 + 9.0, sig_bottom - sig_h, pw - 12.0, sig_h, stroke=BRAND_LINE, width=0.7)
        pdf.saveState()
        tx, ty = xy(px0 + 6.4, sig_bottom - sig_h / 2)
        pdf.translate(tx, ty); pdf.rotate(90)
        pdf.setFillColor(BRAND); pdf.setFont("Helvetica-Bold", 4.6)
        pdf.drawCentredString(0, 0, "SIGNATURE OF INVIGILATOR")
        pdf.restoreState()

    # ---- Bubbles (brand red, strong outline) ---------------------------------------------
    pdf.setStrokeColor(BRAND); pdf.setFillColor(white); pdf.setLineWidth(0.8)
    radius = layout.bubble_radius * MM
    for bubble in layout.bubbles:
        bx, by = xy(bubble.x, bubble.y)
        pdf.circle(bx, by, radius, stroke=1, fill=1)

    # ---- Footer: institute name above the address -------------------------------------------
    rule = layout.height - 15.8
    line(left, rule, right, rule, 0.6, BRAND)
    if institute_name.replace(" ", "").casefold() == "qualifyias":
        institute_name = "Qualify IAS"
    label(institute_name.upper(), page_w / 2, rule + 3.0, 7, font="Helvetica-Bold", color=BRAND, align="center")
    label(INSTITUTE_FOUNDER + "  |  Telegram: t.me/RAJINIKANTHAIR587", page_w / 2, rule + 5.9, 5.4, font="Helvetica-Bold",
          color=BRAND, align="center")
    label(INSTITUTE_ADDRESS, page_w / 2, rule + 8.6, 5.6, align="center")
    label("www.qualifyias.com  |  +91 93118 40789  |  Print at 100% scale. Do not crop or resize this sheet.",
          page_w / 2, rule + 11.2, 4.8, align="center")
