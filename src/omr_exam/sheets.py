"""Print-ready PDF answer sheet generation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib.colors import black, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from omr_exam.layout import create_layout

MM = 72 / 25.4


def generate_sheet_pdf(config: dict[str, Any], output: str | Path, count: int = 1) -> Path:
    """Create one or more blank answer sheets, with exact registration squares."""
    if count < 1:
        raise ValueError("count must be at least one")
    layout = create_layout(config)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    page_size = (layout.width * MM, layout.height * MM)
    pdf = canvas.Canvas(str(path), pagesize=page_size, pageCompression=1)
    pdf.setTitle(f"{config['test']['title']} - OMR answer sheet")

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
        text(config["test"]["title"], 18, 14, 12)
        text(f"{config['test']['class']}  |  {config['test']['subject']}  |  {config['test']['date']}", 18, 20, 7)
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
        for code in config["sheet"]["set_codes"]:
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
