"""Regenerate the labeled synthetic raster used by the reader test harness."""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

from omr_exam.config import load_config
from omr_exam.layout import create_layout
from omr_exam.sheets import generate_sheet_pdf


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ANSWERS = {"1": "B", "2": "A", "3": "D", "4": "C", "5": "B"}
EXPECTED_ROLL = "123456"
EXPECTED_SET = "A"


def generate(output: Path) -> Path:
    config = load_config(ROOT / "examples" / "sample-test.json")
    layout = create_layout(config)
    with tempfile.TemporaryDirectory(prefix="omr-sample-fixture-") as directory:
        pdf_path = Path(directory) / "sample.pdf"
        generate_sheet_pdf(config, pdf_path)
        document = pymupdf.open(pdf_path)
        try:
            pixmap = document[0].get_pixmap(matrix=pymupdf.Matrix(300 / 72, 300 / 72), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        finally:
            document.close()

    draw = ImageDraw.Draw(image)
    scale_x, scale_y = image.width / layout.width, image.height / layout.height
    radius = 1.05 * min(scale_x, scale_y)
    marked = []
    for question, answer in EXPECTED_ANSWERS.items():
        marked.append(next(bubble for bubble in layout.bubbles
                           if bubble.group == "answer" and bubble.index == question and bubble.value == answer))
    for position, digit in enumerate(EXPECTED_ROLL):
        marked.append(next(bubble for bubble in layout.bubbles
                           if bubble.group == "roll" and bubble.index == str(position) and bubble.value == digit))
    marked.append(next(bubble for bubble in layout.bubbles
                       if bubble.group == "set" and bubble.value == EXPECTED_SET))
    for bubble in marked:
        center_x, center_y = bubble.x * scale_x, bubble.y * scale_y
        draw.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), fill="black")

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG", optimize=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "tests" / "samples" / "sample-generated-001.png")
    args = parser.parse_args()
    print(f"Generated synthetic reader fixture: {generate(args.output.resolve())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
