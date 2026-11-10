"""Load image files and multi-page PDF/TIFF scans into OpenCV arrays."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

SUPPORTED_SCAN_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".pdf"})


def scan_files_in_folder(folder: str | Path) -> list[Path]:
    """Return supported scan files directly inside a selected batch folder."""
    directory = Path(folder)
    if not directory.is_dir():
        raise ValueError(f"Scan folder does not exist or is not a directory: {directory}")
    return sorted(
        (path for path in directory.iterdir()
         if path.is_file() and path.suffix.lower() in SUPPORTED_SCAN_SUFFIXES),
        key=lambda path: (path.name.casefold(), path.name),
    )


def count_scan_pages(path: str | Path) -> int:
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError("PDF scan import requires PyMuPDF; install the app dependencies") from exc
        with fitz.open(path) as document:
            if document.needs_pass:
                raise ValueError(f"PDF is password-protected: {path.name}")
            return len(document)
    if path.suffix.lower() in {".tif", ".tiff"}:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("TIFF scan import requires OpenCV; install the app dependencies") from exc
        count = int(cv2.imcount(str(path)))
        if count < 1:
            raise ValueError(f"Could not decode TIFF scan: {path.name}")
        return count
    return 1


def scan_pages(path: str | Path, *, dpi: int = 216, color_mode: str = "grayscale") -> Iterator[tuple[str, np.ndarray]]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Scan decoding requires OpenCV and NumPy; install the app dependencies") from exc
    path = Path(path)
    if not 150 <= int(dpi) <= 600:
        raise ValueError("Scan DPI must be between 150 and 600")
    color_mode = str(color_mode).lower()
    if color_mode not in {"grayscale", "color"}:
        raise ValueError("Color mode must be 'grayscale' or 'color'")
    image_flag = cv2.IMREAD_COLOR if color_mode == "color" else cv2.IMREAD_GRAYSCALE
    if path.suffix.lower() == ".pdf":
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError("PDF scan import requires PyMuPDF; install the app dependencies") from exc
        with fitz.open(path) as document:
            if document.needs_pass:
                raise ValueError(f"PDF is password-protected: {path.name}")
            for index, page in enumerate(document, 1):
                colorspace = fitz.csRGB if color_mode == "color" else fitz.csGRAY
                pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), colorspace=colorspace, alpha=False)
                array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                if pix.n == 4:
                    array = cv2.cvtColor(array, cv2.COLOR_RGBA2BGR)
                elif pix.n == 3:
                    array = cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
                elif pix.n == 1:
                    array = array.reshape(pix.height, pix.width)
                yield f"{path}#page={index}", array
        return
    if path.suffix.lower() in {".tif", ".tiff"}:
        count = count_scan_pages(path)
        for index in range(count):
            decoded, frames = cv2.imreadmulti(str(path), index, 1, flags=image_flag)
            if not decoded or len(frames) != 1 or frames[0] is None or frames[0].size == 0:
                raise ValueError(f"Could not decode TIFF page {index + 1}: {path.name}")
            yield f"{path}#page={index + 1}", frames[0]
        return
    image = cv2.imread(str(path), image_flag)
    if image is None or image.size == 0:
        raise ValueError(f"Could not decode image: {path.name}")
    yield str(path), image
