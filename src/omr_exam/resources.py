"""Locate bundled static resources in source and PyInstaller builds."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config"
        if (candidate / "test-config.schema.json").is_file():
            return candidate
    raise FileNotFoundError("Bundled test-config.schema.json was not found")


def default_logo_path() -> Path | None:
    """Locate the bundled default institute logo in source or packaged builds."""
    for parent in Path(__file__).resolve().parents:
        for rel in (("config", "logo.png"), ("docs", "logo.png")):
            candidate = parent.joinpath(*rel)
            if candidate.is_file():
                return candidate
    return None


def resolve_logo_path(logo: str | Path | bool | None = None) -> Path | None:
    """Resolve an explicit logo path, falling back to the bundled default when None."""
    if logo is False or logo == "":
        return None
    if logo is None:
        return default_logo_path()
    candidate = Path(logo)
    return candidate if candidate.is_file() else None


def load_pdf_logo(logo: str | Path | bool | None = None) -> tuple[bytes, int, int] | None:
    """Load and normalize a logo image as opaque white-flattened PNG bytes for PDF embedding."""
    path = resolve_logo_path(logo)
    if path is None:
        return None
    try:
        import cv2
        import numpy as np

        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is not None and image.size > 0:
            if image.ndim == 2:
                bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            elif image.shape[2] == 4:
                alpha = image[:, :, 3:4].astype(np.float32) / 255.0
                fg = image[:, :, :3].astype(np.float32)
                bgr = np.clip(fg * alpha + 255.0 * (1.0 - alpha), 0, 255).astype(np.uint8)
            else:
                bgr = image[:, :, :3].copy()
            # Clean near-white paper backgrounds so the logo blends seamlessly onto white PDF pages.
            corners = (bgr[0, 0], bgr[0, -1], bgr[-1, 0], bgr[-1, -1])
            if all(int(pixel.min()) >= 244 for pixel in corners):
                near_white = bgr.min(axis=2) >= 246
                bgr[near_white] = (255, 255, 255)
            ok, encoded = cv2.imencode(".png", bgr)
            if ok:
                height, width = bgr.shape[:2]
                return encoded.tobytes(), int(width), int(height)
    except Exception:
        pass
    try:
        from reportlab.lib.utils import ImageReader

        raw = path.read_bytes()
        width, height = ImageReader(BytesIO(raw)).getSize()
        return raw, int(width), int(height)
    except Exception:
        return None


def logo_image_reader(logo: str | Path | bool | None = None) -> tuple[Any, int, int] | None:
    """Return a ReportLab ImageReader and pixel dimensions for the resolved logo."""
    loaded = load_pdf_logo(logo)
    if loaded is None:
        return None
    try:
        from reportlab.lib.utils import ImageReader

        raw, width, height = loaded
        return ImageReader(BytesIO(raw)), width, height
    except Exception:
        return None
