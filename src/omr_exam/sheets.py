"""Print-ready PDF answer sheet generation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib.colors import black, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from omr_exam.layout import create_layout

MM = 72 / 25.4


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
        institute_name = (institute or config["test"].get("institute", "")).strip()
        header_right = layout.width - 18.0
        if logo_reader is not None:
            box_w, box_h = 22.0, 15.0
            px, py = xy(header_right - box_w, 12.5 + box_h)
            pdf.drawImage(logo_reader, px, py, box_w * MM, box_h * MM, preserveAspectRatio=True, anchor="ne", mask="auto")
            header_right -= box_w + 4.0
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

        details = f"{config['test']['class']}  |  {config['test']['subject']}  |  {config['test']['date']}"
        if institute_name or logo_reader is not None:
            if institute_name:
                header_line(institute_name, 14.0, 11.5, "Helvetica-Bold")
            header_line(config["test"]["title"], 20.0 if institute_name else 14.0, 9.5 if institute_name else 12, "Helvetica")
            header_line(details, 25.0 if institute_name else 20.0, 7, "Helvetica")
        else:
            header_line(config["test"]["title"], 14.0, 12, "Helvetica")
            header_line(details, 20.0, 7, "Helvetica")
        for field, x, y in layout.identity_labels:
            text(field, x, y, 7)
            if field in {"NAME", "CLASS"}:
                px, py = xy(x + 22, y + 1.5)
                width = (layout.width / 2 - x - 25) if field == "NAME" and "CLASS" in config["sheet"].get("fields", []) else layout.width - x - 23
                pdf.setStrokeColor(black)
                pdf.setLineWidth(0.4)
                pdf.line(px, py, px + width * MM, py)
        # Print the digit column headings (0-9) beside the roll number grid.
        roll_x = 48.0
        pitch = min(8.0, (layout.width - 18.0 - roll_x) / max(1, int(config["sheet"]["roll_number_digits"])))
        for position in range(int(config["sheet"]["roll_number_digits"])):
            x = roll_x + position * pitch
            text(str(position + 1), x - 0.7, 42, 5)
            for digit in range(10):
                text(str(digit), x - 3.2, 47.0 + digit * 4.1, 4)
        for code in config["sheet"]["set_codes"] if len(config["sheet"]["set_codes"]) > 1 else ():
            bubble = next(b for b in layout.bubbles if b.group == "set" and b.value == str(code))
            text(str(code), bubble.x - 1.0, bubble.y - 2, 5)
        for option, x, y in layout.option_labels:
            text(option, x - 0.8, y, 5)
        for label, x, y in layout.question_labels:
            text(label, x, y, 6)
        pdf.setStrokeColor(black)
        pdf.setLineWidth(0.55)
        for bubble in layout.bubbles:
            x, y = xy(bubble.x, bubble.y)
            pdf.setFillColor(white)
            pdf.circle(x, y, 1.55 * MM, stroke=1, fill=1)
        pdf.showPage()
    pdf.save()
    return path
