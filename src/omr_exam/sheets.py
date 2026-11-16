"""Print-ready PDF answer sheet generation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from omr_exam.layout import create_layout
from omr_exam.resources import logo_image_reader

MM = 72 / 25.4
# QualifyIAS brand palette (from qualifyias.com / the logo): deep red and black.
ACCENT = HexColor("#B40000")
ACCENT_LINE = HexColor("#C77B7B")
ACCENT_PALE = HexColor("#FBEAEA")
ROW_PALE = HexColor("#FCF3F3")
INSTITUTE_ADDRESS = "First Floor, Building No 1-1-726/1, Gandhi Nagar, Ashok Nagar, Hyderabad, Telangana 500080"
INSTITUTE_CONTACT = "www.qualifyias.com  |  +91 93118 40789"


def generate_sheet_pdf(config: dict[str, Any], output: str | Path, count: int = 1, *,
                       institute: str = "", logo: str | Path | bool | None = None) -> Path:
    """Create one or more blank answer sheets, with exact registration squares.

    The header carries the institute name and logo (defaulting to the bundled logo when omitted);
    both sit clear of the corner squares and bubble areas, so they never affect reading."""
    if count < 1:
        raise ValueError("count must be at least one")
    layout = create_layout(config)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    page_size = (layout.width * MM, layout.height * MM)
    pdf = canvas.Canvas(str(path), pagesize=page_size, pageCompression=1)
    pdf.setTitle(f"{config['test']['title']} - OMR answer sheet")
    logo_info = logo_image_reader(logo)

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
        if layout.version >= 3:
            from omr_exam.sheet_v3 import draw_page
            draw_page(pdf, layout, config, logo_info,
                      (institute or config["test"].get("institute", "") or "Qualify IAS").strip())
            pdf.showPage()
            continue
        header_left = 18.0
        header_right = layout.width - 18.0
        if logo_info is not None:
            logo_reader, logo_w_px, logo_h_px = logo_info
            box_w, box_h = 40.0, 17.0
            scale = min(box_w / max(1.0, logo_w_px), box_h / max(1.0, logo_h_px))
            drawn_w = logo_w_px * scale
            drawn_h = logo_h_px * scale
            # Top-left, clear of the corner squares (x<12) and the NAME/CLASS boxes (y>=23.5).
            px, py = xy(header_left, 5.5 + drawn_h)
            pdf.drawImage(logo_reader, px, py, drawn_w * MM, drawn_h * MM)
            header_left += drawn_w + 5.0
        else:
            cx, cy = xy(header_left + 3.2, 14.4)
            pdf.setStrokeColor(ACCENT); pdf.setLineWidth(1.1); pdf.circle(cx, cy, 2.8 * MM, stroke=1, fill=0)
            pdf.setFillColor(ACCENT); pdf.setFont("Helvetica-Bold", 7)
            pdf.drawCentredString(cx, cy - 2.3, "Q")
            tx, ty = xy(header_left + 7.0, 15.8)
            pdf.setFillColor(ACCENT); pdf.setFont("Helvetica-Bold", 7.4)
            pdf.drawString(tx, ty, "QualifyIAS")
            header_left += 36.0
        available = (header_right - header_left) * MM

        def header_line(value: str, y: float, size: float, font: str) -> None:
            while size > 6 and pdf.stringWidth(value, font, size) > available:
                size -= 0.5
            while len(value) > 4 and pdf.stringWidth(value, font, size) > available:
                value = value[:-2].rstrip() + "…"
            px, py = xy(header_left, y)
            pdf.setFillColor(black)
            pdf.setFont(font, size)
            pdf.drawString(px, py, value)

        px, py = xy(header_left, 13)
        pdf.setFillColor(ACCENT)
        title_size = 16.0
        while title_size > 10 and pdf.stringWidth("OMR ANSWER SHEET", "Helvetica-Bold", title_size) > available:
            title_size -= 0.5
        pdf.setFont("Helvetica-Bold", title_size)
        pdf.drawString(px, py, "OMR ANSWER SHEET")
        header_line(config["test"]["title"], 18.2, 8.5, "Helvetica-Bold")
        header_line("  |  ".join(d for d in (institute_name, config["test"]["class"], ", ".join(config["test"]["subjects"]),
                                             config["test"]["date"]) if d), 21.4, 6.8, "Helvetica")

        # Handwritten identity areas are deliberately not interpreted by the reader.
        for field, x, y in layout.identity_labels:
            if field in {"NAME", "CLASS"}:
                text(field, x, y, 6.5)
                box_x = x + 21.0
                if field == "NAME" and "class" in config["sheet"].get("fields", []):
                    class_label_x = layout.width / 2 + 18.0
                    width = class_label_x - 7.0 - box_x
                else:
                    width = layout.width - 18.0 - box_x
                box(box_x, y - 5.5, width, 10.0, stroke=ACCENT_LINE, line_width=0.9)
                # The reference form asks candidates to print names in block
                # letters. Internal guides keep handwriting aligned and legible.
                cells = 22 if field == "NAME" else 8
                pdf.setStrokeColor(ACCENT_LINE)
                pdf.setLineWidth(0.3)
                for cell in range(1, cells):
                    px, py = xy(box_x + width * cell / cells, y - 5.5)
                    pdf.line(px, py, px, py - 10.0 * MM)
            elif field == "ROLL NUMBER":
                text(field, x + 2.5, y, 6.5)        # inset so the label never touches the panel border
            elif field == "SET":
                text("TEST SET", x + 2.5, y, 6.5)

        # Box the ID marks and provide short, readable filling instructions.
        roll_digits = int(config["sheet"]["roll_number_digits"])
        # Take the digit columns from the shared layout so labels always sit on their bubbles.
        roll_cols = sorted({float(b.x) for b in layout.bubbles if b.group == "roll"})
        roll_x = roll_cols[0] if roll_cols else 48.0
        roll_right = (roll_cols[-1] if roll_cols else roll_x) + 5.0
        # End the roll-number panel above the set row; their outlines used to
        # overlap even though the bubbles themselves were separate.
        box(17.5, 37.0, roll_right - 17.5, 49.0, stroke=ACCENT_LINE, line_width=0.7)
        instruction_x = max(105.0, roll_right + 5.0)
        instruction_width = layout.width - 18.0 - instruction_x
        if instruction_width >= 45:
            box(instruction_x, 37.0, instruction_width, 49.0, stroke=ACCENT_LINE, line_width=0.7)
            box(instruction_x, 37.0, instruction_width, 7.5, stroke=ACCENT_LINE, fill=ACCENT_PALE, line_width=0.45)
            px, py = xy(instruction_x + 3, 42.3)
            pdf.setFillColor(ACCENT); pdf.setFont("Helvetica-Bold", 7)
            pdf.drawString(px, py, "BEFORE YOU BEGIN")
            instructions = ("Fill one bubble per question using a dark pen.",
                            "Keep marks inside the circles; erase changes cleanly.",
                            "Keep the sheet flat and all corner marks visible.")
            fit = 5.3                       # shrink, never overflow, on narrow sheets
            while fit > 4.0 and max(pdf.stringWidth(f"3. {t}", "Helvetica", fit) for t in instructions) > (instruction_width - 6.0) * MM:
                fit -= 0.2
            for index, instruction in enumerate(instructions):
                text(f"{index + 1}. {instruction}", instruction_x + 3, 49.0 + index * 6.0, fit)
        if len(config["sheet"]["set_codes"]) > 1:
            # Leave a visible gutter between roll digits, set bubbles, and Q1.
            box(17.5, 87.0, min(83.0, layout.width - 36.0), 7.0, stroke=ACCENT_LINE, line_width=0.7)
        # Print the digit column headings (0-9) beside the roll number grid.
        for position, x in enumerate(roll_cols):
            text(str(position + 1), x - 0.7, 42, 5)
            for digit in range(10):
                text(str(digit), x - 3.2, 47.0 + digit * 4.1, 4)
        for code in config["sheet"]["set_codes"] if len(config["sheet"]["set_codes"]) > 1 else ():
            bubble = next(b for b in layout.bubbles if b.group == "set" and b.value == str(code))
            text(str(code), bubble.x - 1.0, bubble.y - 2, 5)
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
            top, bottom = min(centres) - 4.8, max(centres) + 4.5
            x0 = margin + column * col_width
            for row_index, qid in enumerate(question_ids):
                if row_index % 2 == 0:
                    stripe_height = min(4.8, pitch_y * 0.62)
                    px, py = xy(x0 + 0.25, question_centres[qid] + stripe_height / 2)
                    pdf.setFillColor(ROW_PALE)
                    pdf.rect(px, py, (col_width - 0.5) * MM, stripe_height * MM, stroke=0, fill=1)
            box(x0, top, col_width, bottom - top, stroke=ACCENT_LINE, line_width=0.65)
        for option, x, y in layout.option_labels:      # drawn after the row stripes so they are never covered
            text(option, x - 0.8, y, 5)
        for question_label, x, y in layout.question_labels:
            text(question_label, x + 1.4, y, 6)
        pdf.setStrokeColor(black)
        pdf.setLineWidth(0.55)
        for bubble in layout.bubbles:
            x, y = xy(bubble.x, bubble.y)
            pdf.setFillColor(white)
            pdf.circle(x, y, 1.55 * MM, stroke=1, fill=1)
        pdf.setStrokeColor(ACCENT); pdf.setLineWidth(0.5)
        lx0, ly = xy(18, layout.height - 12.2); lx1, _ = xy(layout.width - 18, 0)
        pdf.line(lx0, ly, lx1, ly)
        pdf.setFillColor(ACCENT); pdf.setFont("Helvetica-Bold", 5.6)
        pdf.drawCentredString(layout.width / 2 * MM, xy(0, layout.height - 9.4)[1], INSTITUTE_ADDRESS)
        pdf.setFillColor(black); pdf.setFont("Helvetica", 5.2)
        pdf.drawCentredString(layout.width / 2 * MM, xy(0, layout.height - 6.4)[1], INSTITUTE_CONTACT)
        pdf.setFont("Helvetica", 4.6)
        pdf.drawCentredString(layout.width / 2 * MM, xy(0, layout.height - 3.6)[1], "Print at 100% scale. Do not crop or resize this sheet.")
        pdf.showPage()
    pdf.save()
    return path
