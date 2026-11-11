"""Print-ready PDF answer sheet generation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from omr_exam.layout import create_layout

MM = 72 / 25.4
ACCENT = HexColor("#E6007E")
ACCENT_LINE = HexColor("#E97AAE")
ACCENT_PALE = HexColor("#FFF1F7")
ROW_PALE = HexColor("#FFF8FB")


def generate_sheet_pdf(config: dict[str, Any], output: str | Path, count: int = 1, *,
                       institute: str = "", logo: str | Path | None = None) -> Path:
    """Create one or more blank answer sheets, with exact registration squares.

    The header can carry the institute name and logo; both are optional and sit clear of the
    corner squares and bubble areas, so they never affect reading."""
    if count < 1:
        raise ValueError("count must be at least one")
    layout = create_layout(config)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    page_size = (layout.width * MM, layout.height * MM)
    pdf = canvas.Canvas(str(path), pagesize=page_size, pageCompression=1)
    pdf.setTitle(f"{config['test']['title']} - OMR answer sheet")
    logo_reader = None
    if logo and Path(logo).is_file():
        try:
            from reportlab.lib.utils import ImageReader
            logo_reader = ImageReader(str(logo))
        except Exception:
            logo_reader = None          # an unreadable logo must never block printing the sheets

    def xy(x: float, y_top: float) -> tuple[float, float]:
        return x * MM, (layout.height - y_top) * MM

    def text(value: str, x: float, y: float, size: float = 7) -> None:
        px, py = xy(x, y)
        pdf.setFillColor(black)
        pdf.setFont("Helvetica", size)
        pdf.drawString(px, py, value)

    def box(x: float, y_top: float, width: float, height: float, *, stroke=ACCENT_LINE,
            fill=None, line_width: float = 0.55) -> None:
        px, py = xy(x, y_top + height)
        pdf.setLineWidth(line_width)
        pdf.setStrokeColor(stroke)
        if fill is None:
            pdf.rect(px, py, width * MM, height * MM, stroke=1, fill=0)
        else:
            pdf.setFillColor(fill)
            pdf.rect(px, py, width * MM, height * MM, stroke=1, fill=1)

    for _ in range(count):
        # Square black registration marks have the same known centers in reader and generator.
        marker = layout.marker_size
        corners = ((8, 8), (layout.width - 8, 8), (layout.width - 8, layout.height - 8), (8, layout.height - 8))
        for corner_index, (cx, cy) in enumerate(corners):
            px, py = xy(cx - marker / 2, cy - marker / 2)
            pdf.setFillColor(black)
            pdf.rect(px, py - marker * MM, marker * MM, marker * MM, stroke=0, fill=1)
            # A rotation-invariant 0/1/2/3 white-dot code identifies each corner,
            # so 90/180 degree scanner rotations do not silently mirror answers.
            holes = ((), ((-1.35, -1.35),), ((-1.35, -1.35), (1.35, -1.35)),
                     ((-1.35, -1.35), (1.35, -1.35), (-1.35, 1.35)))[corner_index]
            pdf.setFillColor(white)
            for dx, dy in holes:
                hx, hy = xy(cx + dx, cy + dy)
                pdf.circle(hx, hy, 0.58 * MM, stroke=0, fill=1)
        institute_name = (institute or config["test"].get("institute", "") or "QualifyIAS").strip()
        header_right = layout.width - 18.0
        if logo_reader is not None:
            box_w, box_h = 22.0, 15.0
            px, py = xy(header_right - box_w, 12.5 + box_h)
            pdf.drawImage(logo_reader, px, py, box_w * MM, box_h * MM, preserveAspectRatio=True, anchor="ne", mask="auto")
            header_right -= box_w + 4.0
        else:
            # Vector wordmark keeps the requested institute identity visible before
            # a custom image logo is added in Settings.
            wordmark_x = header_right - 31.0
            cx, cy = xy(wordmark_x + 3.2, 14.4)
            pdf.setStrokeColor(ACCENT); pdf.setLineWidth(1.1); pdf.circle(cx, cy, 2.8 * MM, stroke=1, fill=0)
            pdf.setFillColor(ACCENT); pdf.setFont("Helvetica-Bold", 7)
            pdf.drawCentredString(cx, cy - 2.3, "Q")
            tx, ty = xy(wordmark_x + 7.0, 15.8)
            pdf.setFillColor(ACCENT); pdf.setFont("Helvetica-Bold", 7.4)
            pdf.drawString(tx, ty, "QualifyIAS")
            header_right -= 34.0
        available = (header_right - 18.0) * MM

        def header_line(value: str, y: float, size: float, font: str) -> None:
            while size > 6 and pdf.stringWidth(value, font, size) > available:
                size -= 0.5
            while len(value) > 4 and pdf.stringWidth(value, font, size) > available:
                value = value[:-2].rstrip() + "…"
            px, py = xy(18, y)
            pdf.setFillColor(black)
            pdf.setFont(font, size)
            pdf.drawString(px, py, value)

        px, py = xy(18, 14)
        pdf.setFillColor(ACCENT)
        title_size = 16.0
        while title_size > 10 and pdf.stringWidth("OMR ANSWER SHEET", "Helvetica-Bold", title_size) > available:
            title_size -= 0.5
        pdf.setFont("Helvetica-Bold", title_size)
        pdf.drawString(px, py, "OMR ANSWER SHEET")
        details = [institute_name, config["test"]["title"], config["test"]["class"],
                   config["test"]["subject"], config["test"]["date"]]
        header_line("  |  ".join(details), 22.0, 7.5, "Helvetica")

        # Handwritten identity areas are deliberately not interpreted by the reader.
        for field, x, y in layout.identity_labels:
            if field in {"NAME", "CLASS"}:
                text(field, x, y, 6.5)
                width = (layout.width / 2 - x - 25) if field == "NAME" and "CLASS" in config["sheet"].get("fields", []) else layout.width - x - 23
                box(x + 21, y - 5.0, width, 8.0, stroke=ACCENT_LINE, line_width=0.7)
            elif field == "ROLL NUMBER":
                text(field, x, y, 6.5)
            elif field == "SET":
                text("TEST SET", x, y, 6.5)

        # Box the ID marks and provide short, readable filling instructions.
        roll_digits = int(config["sheet"]["roll_number_digits"])
        roll_x = 48.0
        roll_pitch = min(8.0, (layout.width - 18.0 - roll_x) / max(1, roll_digits))
        roll_right = roll_x + (roll_digits - 1) * roll_pitch + 5.0
        box(17.5, 37.0, roll_right - 17.5, 51.5, stroke=ACCENT_LINE, line_width=0.7)
        instruction_x = max(105.0, roll_right + 5.0)
        instruction_width = layout.width - 18.0 - instruction_x
        if instruction_width >= 45:
            box(instruction_x, 37.0, instruction_width, 51.5, stroke=ACCENT_LINE, line_width=0.7)
            box(instruction_x, 37.0, instruction_width, 7.5, stroke=ACCENT_LINE, fill=ACCENT_PALE, line_width=0.45)
            px, py = xy(instruction_x + 3, 42.3)
            pdf.setFillColor(ACCENT); pdf.setFont("Helvetica-Bold", 7)
            pdf.drawString(px, py, "BEFORE YOU BEGIN")
            instructions = ("Fill one bubble per question using a dark pen.",
                            "Keep marks inside the circles; erase changes cleanly.",
                            "Keep the sheet flat and all corner marks visible.")
            for index, instruction in enumerate(instructions):
                text(f"{index + 1}. {instruction}", instruction_x + 3, 49.0 + index * 6.0, 5.3)
        if len(config["sheet"]["set_codes"]) > 1:
            box(17.5, 85.0, min(83.0, layout.width - 36.0), 12.5, stroke=ACCENT_LINE, line_width=0.7)
        # Print the digit column headings (0-9) beside the roll number grid.
        for position in range(roll_digits):
            x = roll_x + position * roll_pitch
            text(str(position + 1), x - 0.7, 42, 5)
            for digit in range(10):
                text(str(digit), x - 3.2, 47.0 + digit * 4.1, 4)
        for code in config["sheet"]["set_codes"] if len(config["sheet"]["set_codes"]) > 1 else ():
            bubble = next(b for b in layout.bubbles if b.group == "set" and b.value == str(code))
            text(str(code), bubble.x - 1.0, bubble.y - 2, 5)
        for option, x, y in layout.option_labels:
            text(option, x - 0.8, y, 5)
        question_centres: dict[int, float] = {}
        for bubble in layout.bubbles:
            if bubble.group == "answer":
                question_centres.setdefault(int(bubble.index), bubble.y)
        qpc = max(1, int(config["sheet"]["questions_per_column"]))
        columns = int(config["sheet"]["columns"])
        margin = 18.0
        col_width = (layout.width - 2 * margin) / columns
        pitch_y = min(9.0, (layout.height - 17.0 - 100.0) / qpc)
        for column in range(columns):
            question_ids = [int(question["id"]) for question in config["questions"]
                            if (int(question["id"]) - 1) // qpc == column]
            if not question_ids:
                continue
            centres = [question_centres[qid] for qid in question_ids]
            top, bottom = min(centres) - 4.5, max(centres) + 4.5
            x0 = margin + column * col_width
            for row_index, qid in enumerate(question_ids):
                if row_index % 2 == 0:
                    stripe_height = min(5.2, pitch_y * 0.62)
                    px, py = xy(x0 + 0.25, question_centres[qid] + stripe_height / 2)
                    pdf.setFillColor(ROW_PALE)
                    pdf.rect(px, py, (col_width - 0.5) * MM, stripe_height * MM, stroke=0, fill=1)
            box(x0, top, col_width, bottom - top, stroke=ACCENT_LINE, line_width=0.65)
        for question_label, x, y in layout.question_labels:
            text(question_label, x + 1.4, y, 6)
        pdf.setStrokeColor(black)
        pdf.setLineWidth(0.55)
        for bubble in layout.bubbles:
            x, y = xy(bubble.x, bubble.y)
            pdf.setFillColor(white)
            pdf.circle(x, y, 1.55 * MM, stroke=1, fill=1)
        text("Print at 100% scale. Do not crop or resize this sheet.", 18, layout.height - 6.0, 5.5)
        pdf.showPage()
    pdf.save()
    return path
