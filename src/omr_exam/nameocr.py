"""Offline handwritten block-letter reader for the NAME box (pure numpy, no OCR engine needed).

The NAME box is printed as one cell per letter, so the name is read cell by cell: every ink blob is
assigned to its cell, normalised, and classified by a tiny CNN (``data/name_model.npz``, trained by
``tools/train_name_model.py`` on synthetic handwriting).  Empty cells between letters become spaces.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MODEL_PATH = Path(__file__).with_name("data") / "name_model.npz"


def normalise_glyph(mask: np.ndarray) -> np.ndarray | None:
    """Centre one glyph (boolean/uint8 ink mask) in a 28x28 float image, MNIST style."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 6:
        return None
    crop = (mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1] > 0).astype(np.float32)
    h, w = crop.shape
    scale = 20.0 / max(h, w)
    crop = cv2.resize(crop, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((28, 28), np.float32)
    ch, cw = crop.shape
    total = float(crop.sum())
    if total <= 0:
        return None
    cy = float((crop.sum(axis=1) * np.arange(ch)).sum() / total)
    cx = float((crop.sum(axis=0) * np.arange(cw)).sum() / total)
    oy = int(np.clip(round(14 - cy), 0, 28 - ch))
    ox = int(np.clip(round(14 - cx), 0, 28 - cw))
    canvas[oy:oy + ch, ox:ox + cw] = crop
    return cv2.GaussianBlur(canvas, (0, 0), 0.7)


@lru_cache(maxsize=1)
def _model() -> dict[str, np.ndarray] | None:
    try:
        with np.load(MODEL_PATH) as data:
            return {key: data[key] for key in data.files}
    except Exception:
        return None


def _conv(x: np.ndarray, w: np.ndarray, b: np.ndarray) -> np.ndarray:
    """3x3 same-padded convolution over a batch: x (N,C,H,W), w (O,C,3,3)."""
    padded = np.pad(x, ((0, 0), (0, 0), (1, 1), (1, 1)))
    windows = sliding_window_view(padded, (3, 3), axis=(2, 3))          # N,C,H,W,3,3
    return np.einsum("nchwij,ocij->nohw", windows, w, optimize=True) + b[None, :, None, None]


def _pool(x: np.ndarray) -> np.ndarray:
    n, c, h, w = x.shape
    return x.reshape(n, c, h // 2, 2, w // 2, 2).max(axis=(3, 5))


def classify(glyphs: list[np.ndarray]) -> list[tuple[str, float, list[tuple[str, float]]]]:
    """Return (best letter, probability, ranked alternatives) for each normalised glyph."""
    model = _model()
    if model is None or not glyphs:
        return []
    x = np.stack(glyphs)[:, None].astype(np.float32)
    x = _pool(np.maximum(_conv(x, model["c1w"], model["c1b"]), 0))
    x = _pool(np.maximum(_conv(x, model["c2w"], model["c2b"]), 0))
    x = np.maximum(x.reshape(len(glyphs), -1) @ model["f1w"].T + model["f1b"], 0)
    logits = x @ model["f2w"].T + model["f2b"]
    logits -= logits.max(axis=1, keepdims=True)
    prob = np.exp(logits)
    prob /= prob.sum(axis=1, keepdims=True)
    out = []
    for row in prob:
        order = np.argsort(row)[::-1][:3]
        out.append((LETTERS[order[0]], float(row[order[0]]), [(LETTERS[i], float(row[i])) for i in order]))
    return out


def extract_glyphs(gray: np.ndarray, cells: int, cell_w_px: float, left_px: float = 0.0) -> list[tuple[int, np.ndarray]]:
    """Split the name strip into (cell index, ink mask) glyphs, ignoring the printed cell dividers."""
    paper = float(np.percentile(gray, 85))
    cutoff = min(165.0, max(70.0, paper - 55.0))
    ink = (gray < cutoff).astype(np.uint8)
    height = gray.shape[0]
    count, labels, stats, centres = cv2.connectedComponentsWithStats(ink, connectivity=8)
    min_area = max(10, int(0.0010 * gray.size))
    boundaries = [left_px + k * cell_w_px for k in range(cells + 1)]
    per_cell: dict[int, np.ndarray] = {}
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if area < min_area:
            continue
        if w <= 6 and h > 0.45 * height and min(abs((x + w / 2) - b) for b in boundaries) <= 3.5:
            continue                                  # printed divider, not a pen stroke
        if h < 0.18 * height and w < 0.18 * height:
            continue                                  # speck
        cell = int(np.clip((centres[i][0] - left_px) // cell_w_px, 0, cells - 1))
        mask = per_cell.setdefault(cell, np.zeros_like(ink))
        mask[labels == i] = 1
    return sorted(per_cell.items())


def read_name(gray: np.ndarray, cells: int, cell_w_px: float, left_px: float = 0.0) -> tuple[str, float]:
    """Read the name strip. Returns (text, lowest letter confidence); ``("", 0.0)`` when blank/unreadable."""
    glyph_cells = extract_glyphs(gray, cells, cell_w_px, left_px)
    kept = [(cell, normalise_glyph(mask), _aspect(mask)) for cell, mask in glyph_cells]
    kept = [(cell, g, a) for cell, g, a in kept if g is not None]
    if not kept:
        return "", 0.0
    results = classify([g for _, g, _a in kept])
    if not results:
        return "", 0.0
    text, previous = "", None
    for (cell, _glyph, aspect), (letter, prob, alts) in zip(kept, results):
        # A bare vertical stroke (no foot, no hook) in a block-letter box is an "I"; the training data
        # (EMNIST) folds lower-case "l" into L, so the model leans L for it.
        if aspect < 0.32 and letter in ("L", "I") and any(a == "I" for a, _ in alts):
            letter = "I"
        if previous is not None and cell - previous > 1 and not text.endswith(" "):
            text += " "
        text += letter
        previous = cell
    return text, min(prob for _letter, prob, _alts in results)


def _aspect(mask: np.ndarray) -> float:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return 1.0
    return float((xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1))
