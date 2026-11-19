"""Image normalization, registration mark detection, and OMR bubble classification."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from omr_exam.layout import Bubble, create_layout
from omr_exam.ocr import recognize_name_detail


@dataclass
class ReadResult:
    image_path: str
    answers: dict[str, str | None]
    roll_number: str | None
    set_code: str | None
    marks: dict[str, dict[str, float]]
    flags: list[dict[str, Any]] = field(default_factory=list)
    aligned_image: np.ndarray | None = None
    preview_path: str = ""
    thresholds: dict[str, float | str] = field(default_factory=dict)
    student_name: str = ""
    subject_code: str = ""

    @property
    def requires_review(self) -> bool:
        # A blank response is a valid answer and remains visible as an informational
        # flag. Ambiguous/faint/unreadable/identity marks require operator review.
        return any(flag.get("kind") != "blank" for flag in self.flags)


def _gray(image: np.ndarray) -> np.ndarray:
    if image is None or image.size == 0:
        raise ValueError("Image is empty or unreadable")
    if image.ndim == 2:
        return image
    if image.ndim != 3:
        raise ValueError("Image must have two spatial dimensions and an optional color channel")
    if image.shape[2] == 1:
        return image[:, :, 0]
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    if image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    raise ValueError(f"Unsupported image channel count: {image.shape[2]}")


def _marker_candidates(binary: np.ndarray, gray: np.ndarray) -> list[dict[str, Any]]:
    """Solid, roughly square dark blobs: potential corner registration marks (any rotation)."""
    h, w = gray.shape[:2]
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    area_floor = (min(h, w) * 0.012) ** 2
    found: list[dict[str, Any]] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < area_floor or area > h * w * 0.03:
            continue
        (rcx, rcy), (rw, rh), _angle = cv2.minAreaRect(contour)
        if rw < 4 or rh < 4 or not 0.72 <= rw / rh <= 1.39 or area / (rw * rh) < 0.8:
            continue
        # Registration squares are solid black; hollow write-in squares, table cells and
        # letters are rejected by requiring the interior to be mostly ink.
        mask = np.zeros(gray.shape, np.uint8)
        cv2.drawContours(mask, [contour], -1, 255, -1)
        if float(binary[mask > 0].mean()) / 255.0 < 0.8:
            continue
        found.append({"center": np.array([rcx, rcy], np.float32), "side": float((rw + rh) / 2),
                      "contour": contour, "mask": None})
    return found


def _best_quad(cands: list[dict[str, Any]], w: int, h: int, config: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """Choose the four candidates that best form the sheet's registration rectangle."""
    from itertools import combinations
    pool = sorted(cands, key=lambda c: -c["side"])[:14]
    sheet_aspect = None
    if config is not None:
        sw, sh = float(config["sheet"]["width_mm"]), float(config["sheet"]["height_mm"])
        sheet_aspect = max(sw, sh) / min(sw, sh)
    best, best_area = None, 0.0
    for combo in combinations(pool, 4):
        sides = [c["side"] for c in combo]
        if max(sides) / min(sides) > 1.45:
            continue
        pts = np.array([c["center"] for c in combo], np.float32)
        hull = cv2.convexHull(pts)
        if len(hull) != 4:
            continue
        area = float(cv2.contourArea(hull))
        if area < w * h * 0.12 or area <= best_area:
            continue
        ordered = hull.reshape(4, 2)
        edges = [float(np.linalg.norm(ordered[i] - ordered[(i + 1) % 4])) for i in range(4)]
        if min(edges[0], edges[2]) / max(edges[0], edges[2]) < 0.6 or min(edges[1], edges[3]) / max(edges[1], edges[3]) < 0.6:
            continue
        if sheet_aspect is not None:
            long_e, short_e = max(edges), min(edges)
            pair = (sum(sorted(edges)[2:]) / 2) / max(sum(sorted(edges)[:2]) / 2, 1e-6)
            if not (sheet_aspect * 0.72 <= pair <= sheet_aspect * 1.38):
                continue
        best, best_area = combo, area
    return list(best) if best else None


def _count_dots(gray: np.ndarray, cand: dict[str, Any]) -> int:
    """Count the white code dots inside one registration square, independent of its rotation."""
    side = cand["side"]
    mask = np.zeros(gray.shape, np.uint8)
    cv2.drawContours(mask, [cand["contour"]], -1, 255, -1)
    k = max(1, round(side * 0.16))
    core = cv2.erode(mask, np.ones((2 * k + 1, 2 * k + 1), np.uint8))
    if core.sum() == 0:
        return 0
    smooth = cv2.GaussianBlur(gray, (3, 3), 0) if side > 14 else gray
    body = float(np.percentile(smooth[core > 0], 35))
    ring = cv2.dilate(mask, np.ones((int(side * 0.9) | 1, int(side * 0.9) | 1), np.uint8)) & ~cv2.dilate(
        mask, np.ones((int(side * 0.35) | 1, int(side * 0.35) | 1), np.uint8))
    paper = float(np.percentile(gray[ring > 0], 85)) if ring.any() else 255.0
    cutoff = body + 0.38 * max(paper - body, 70.0)
    white = ((smooth > cutoff) & (core > 0)).astype(np.uint8)
    n, _labels, stats, _c = cv2.connectedComponentsWithStats(white, connectivity=8)
    expected = np.pi * (0.58 / 8.0 * side) ** 2
    lo, hi = max(2.0, expected * 0.2), max(6.0, expected * 4.0)
    return sum(1 for i in range(1, n) if lo <= stats[i, cv2.CC_STAT_AREA] <= hi)


def _threshold_variants(gray: np.ndarray):
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    yield cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    block = (max(gray.shape) // 8) | 1
    yield cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, 12)
    yield cv2.threshold(blurred, 90, 255, cv2.THRESH_BINARY_INV)[1]


def _orient_by_grid(gray: np.ndarray, quad: list[dict[str, Any]], config: dict[str, Any]) -> np.ndarray | None:
    """Fallback when the corner dot codes are unreadable (heavy blur, very low resolution): try every
    rotation/flip of the four marks and keep the one where the printed bubble outlines line up."""
    layout = create_layout(config)
    pts = np.array([c["center"] for c in quad], np.float32)
    centre = pts.mean(axis=0)
    order = np.argsort(np.arctan2(pts[:, 1] - centre[1], pts[:, 0] - centre[0]))
    ring = pts[order]                                   # clockwise in image coordinates
    mm = 4.0
    width_mm, height_mm = float(config["sheet"]["width_mm"]), float(config["sheet"]["height_mm"])
    inset = float(config["sheet"].get("corner_mark_size_mm", 8))
    dst = np.array([[inset, inset], [width_mm - inset, inset], [width_mm - inset, height_mm - inset],
                    [inset, height_mm - inset]], np.float32) * mm
    bubbles = [b for b in layout.bubbles if b.group == "answer"][::3]
    angles = np.linspace(0, 2 * np.pi, 10, endpoint=False)
    scores = []
    for flip in (False, True):
        base = ring[::-1] if flip else ring
        for shift in range(4):
            src = np.roll(base, -shift, axis=0)
            try:
                warped = cv2.warpPerspective(gray, cv2.getPerspectiveTransform(src, dst),
                                             (round(width_mm * mm), round(height_mm * mm)))
            except cv2.error:
                continue
            warped = cv2.GaussianBlur(warped, (0, 0), 1.0).astype(np.float32)
            total = 0.0
            for bub in bubbles:
                cx, cy = bub.x * mm, bub.y * mm
                def at(r: float) -> float:
                    xs = np.clip(np.round(cx + r * mm * np.cos(angles)).astype(int), 0, warped.shape[1] - 1)
                    ys = np.clip(np.round(cy + r * mm * np.sin(angles)).astype(int), 0, warped.shape[0] - 1)
                    return float(warped[ys, xs].mean())
                total += at(layout.bubble_radius + 1.1) - at(layout.bubble_radius)
            scores.append((total / max(1, len(bubbles)), src))
    scores.sort(key=lambda item: -item[0])
    if not scores or scores[0][0] < 10 or (len(scores) > 1 and scores[0][0] < scores[1][0] * 1.6):
        return None
    return scores[0][1].astype(np.float32)


def _locate_sheet(gray: np.ndarray, config: dict[str, Any] | None = None) -> np.ndarray:
    """Return the four registration centres ordered by their printed 0/1/2/3 dot codes
    (top-left, top-right, bottom-right, bottom-left of the *printed* sheet), whatever the
    scan's rotation, flip, tilt, scale, or lighting."""
    h, w = gray.shape[:2]
    if min(h, w) < 500:
        raise ValueError("Scan resolution is too low; rescan at 150 DPI or higher")
    problem = "Could not find all four corner marks; check sheet, crop, or scan quality"
    fallback = None
    for binary in _threshold_variants(gray):
        cands = _marker_candidates(binary, gray)
        if len(cands) < 4:
            continue
        quad = _best_quad(cands, w, h, config)
        if quad is None:
            problem = "Corner marks do not form a complete sheet outline"
            continue
        by_code: dict[int, np.ndarray] = {}
        for cand in quad:
            code = _count_dots(gray, cand)
            if code in by_code or code > 3:
                break
            by_code[code] = cand["center"]
        else:
            if set(by_code) == {0, 1, 2, 3}:
                return np.array([by_code[i] for i in range(4)], dtype=np.float32)
        problem = "Corner orientation marks are unreadable; generate a fresh sheet or rescan without cropping"
        fallback = fallback or quad
    if fallback is not None and config is not None:
        oriented = _orient_by_grid(gray, fallback, config)
        if oriented is not None:
            return oriented
    raise ValueError(problem)


def _fiducials(gray: np.ndarray) -> np.ndarray:
    return _locate_sheet(gray)


def align_sheet(image: np.ndarray, config: dict[str, Any]) -> np.ndarray:
    gray = _gray(image)
    points = _locate_sheet(gray, config)
    width_mm = float(config["sheet"]["width_mm"])
    height_mm = float(config["sheet"]["height_mm"])
    marker_mm = float(config["sheet"].get("corner_mark_size_mm", 8))
    pixels_per_mm = 8
    width, height = round(width_mm * pixels_per_mm), round(height_mm * pixels_per_mm)
    # Registration square centres are printed 8 mm from the page edges.
    inset = marker_mm
    destination = np.array([
        [inset * pixels_per_mm, inset * pixels_per_mm],
        [(width_mm - inset) * pixels_per_mm, inset * pixels_per_mm],
        [(width_mm - inset) * pixels_per_mm, (height_mm - inset) * pixels_per_mm],
        [inset * pixels_per_mm, (height_mm - inset) * pixels_per_mm],
    ], dtype=np.float32)
    transform = cv2.getPerspectiveTransform(points, destination)
    return cv2.warpPerspective(gray, transform, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def flatten_illumination(aligned: np.ndarray) -> np.ndarray:
    """Divide out slowly varying paper brightness (shadows, curled pages, uneven lamps).

    The paper level is the local *median* of a 4x downscaled copy (about 10 mm window): marks cover well under
    half of any window so they are ignored, and unlike a max/closing estimate a steep shadow gradient does not
    bias it upward and turn shaded paper into "ink"."""
    small = cv2.resize(aligned, (max(8, aligned.shape[1] // 4), max(8, aligned.shape[0] // 4)), interpolation=cv2.INTER_AREA)
    background = cv2.medianBlur(small, 21)
    background = cv2.GaussianBlur(cv2.resize(background, (aligned.shape[1], aligned.shape[0]), interpolation=cv2.INTER_LINEAR), (0, 0), 6)
    flat = aligned.astype(np.float32) * (255.0 / np.maximum(background.astype(np.float32), 60.0))
    return np.clip(flat, 0, 255).astype(np.uint8)


FAINT_NOISE_FLOOR = 0.07
# A properly filled bubble is dark across the whole circle (>=0.97 on real scans). Half-moons, crescents
# and partly filled marks measure 0.55-0.90, so anything under this is sent to review.
FULL_FILL_MIN = 0.93
OVERFLOW_MAX = 0.25
_ANGLES = np.linspace(0, 2 * np.pi, 48, endpoint=False)


def _bubble_metrics(aligned: np.ndarray, bubble: Bubble, width_mm: float, height_mm: float,
                    pixel_threshold: float, strong_threshold: float, radius_mm: float = 1.9) -> dict[str, float]:
    sx, sy = aligned.shape[1] / width_mm, aligned.shape[0] / height_mm
    cx, cy = round(bubble.x * sx), round(bubble.y * sy)
    reach = round((radius_mm + 1.3) * sx)
    x1, x2 = max(0, cx - reach), min(aligned.shape[1], cx + reach + 1)
    y1, y2 = max(0, cy - reach), min(aligned.shape[0], cy + reach + 1)
    crop = aligned[y1:y2, x1:x2]
    empty = {"ratio": 0.0, "mean": 255.0, "strong": 0.0, "full": 0.0, "overflow": 0.0, "arcs": 0.0}
    if crop.size == 0:
        return empty
    yy, xx = np.ogrid[y1 - cy:y2 - cy, x1 - cx:x2 - cx]
    dist = np.sqrt(xx * xx + yy * yy) / sx          # millimetres from the bubble centre
    dark = crop < pixel_threshold
    # Core sample well inside the printed outline (selection metric).
    core = dist <= 0.95
    samples = crop[core]
    if samples.size == 0:
        return empty
    ratio = float(np.mean(samples < pixel_threshold))
    strong = float(np.mean(samples < strong_threshold))
    # Whole interior of the bubble: a properly filled bubble is dark everywhere, a tick/cross is not.
    inner = dist <= radius_mm * 0.82
    # Judge coverage with the stricter "strong ink" level: the generous mark cutoff counts blurred or
    # anti-aliased edges as ink, which hides half-moons at low resolution or under blur.
    solid = crop < min(strong_threshold + 15.0, pixel_threshold)
    full = float(solid[inner].mean()) if inner.any() else ratio
    # Ink spilling outside the outline (overfill, scribble, smudge). Skip the upper sector where the
    # printed A/B/C/D header and the neighbouring row's outline may sit.
    ang = np.arctan2(-yy, xx) * np.ones_like(dist)
    ring = (dist >= radius_mm + 0.5) & (dist <= radius_mm + 1.2) & ~((ang > 0.5) & (ang < np.pi - 0.5))
    ink = crop < (pixel_threshold + strong_threshold) / 2.0      # real ink, not shading residue
    overflow = float(ink[ring].mean()) if ring.any() else 0.0
    # Number of separate dark arcs just inside the outline: 1 for a filled/half-filled bubble,
    # 2+ for a cross, tick or scribble.
    px = np.clip(np.round(cx - x1 + 0.0 + (radius_mm * 0.5 * sx) * np.cos(_ANGLES)).astype(int), 0, crop.shape[1] - 1)
    py = np.clip(np.round(cy - y1 - (radius_mm * 0.5 * sy) * np.sin(_ANGLES)).astype(int), 0, crop.shape[0] - 1)
    on = dark[py, px]
    arcs = float(np.sum(on & ~np.roll(on, 1))) if not on.all() else 1.0
    return {"ratio": ratio, "mean": float(samples.mean()), "strong": strong, "full": full,
            "overflow": overflow, "arcs": arcs}


def _darkness(aligned: np.ndarray, bubble: Bubble, width_mm: float, height_mm: float, pixel_threshold: float) -> tuple[float, float]:
    m = _bubble_metrics(aligned, bubble, width_mm, height_mm, pixel_threshold, pixel_threshold)
    return m["ratio"], m["mean"]


def read_image(image: str | Path | np.ndarray, config: dict[str, Any], *, mark_threshold: float | None = None, faint_threshold: float | None = None) -> ReadResult:
    path = str(image) if not isinstance(image, np.ndarray) else "<memory>"
    source = cv2.imread(str(image), cv2.IMREAD_COLOR) if not isinstance(image, np.ndarray) else image
    flags: list[dict[str, Any]] = []
    try:
        aligned = align_sheet(source, config)
    except (ValueError, cv2.error) as exc:
        return ReadResult(path, {}, None, None, {}, [{"kind": "unreadable", "message": str(exc)}])
    layout = create_layout(config)
    # Measure marks on an illumination-flattened copy so shadows, curled pages and uneven lamps do not
    # turn paper into "ink"; the untouched aligned image is kept for previews and name OCR.
    measure = flatten_illumination(aligned)
    # A fresh Otsu cutoff for every sheet adapts to scanner brightness and paper tone.
    # Clamp it to avoid extreme cutoffs on nearly blank or bilevel scans.
    otsu_cutoff, _ = cv2.threshold(measure, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    pixel_threshold = float(np.clip(otsu_cutoff + 80, 135, 230))
    strong_threshold = float(np.clip(otsu_cutoff + 20, 95, 175))
    observed: dict[str, dict[str, float]] = {}
    shape: dict[str, dict[str, dict[str, float]]] = {}
    active_questions = len(config["questions"])
    for bubble in layout.bubbles:
        # The printed sheet may have more rows than this test uses; unused rows are ignored entirely
        # (never read, flagged, or scored as blank/incorrect).
        if bubble.group == "answer" and int(bubble.index) > active_questions:
            continue
        group = f"{bubble.group}:{bubble.index}"
        m = _bubble_metrics(measure, bubble, layout.width, layout.height, pixel_threshold, strong_threshold,
                            layout.bubble_radius)
        observed.setdefault(group, {})[bubble.value] = m["ratio"]
        observed[group][f"_mean:{bubble.value}"] = m["mean"]
        observed[group][f"_strong:{bubble.value}"] = m["strong"]
        shape.setdefault(group, {})[bubble.value] = m
    # Confidence cutoffs can be overridden per test. Otherwise the fixed baseline is
    # conservative and every partial, half-filled, or light mark becomes a review flag.
    threshold = float(mark_threshold if mark_threshold is not None else config.get("reader", {}).get("mark_threshold", 0.24))
    faint = float(faint_threshold if faint_threshold is not None else config.get("reader", {}).get("faint_threshold", 0.02))
    if not 0 < faint < threshold < 1:
        raise ValueError("Require 0 < faint_threshold < mark_threshold < 1")
    # Scanner/JPEG noise and printed outlines leak up to ~5% "ink" into an unmarked bubble, so a
    # configured sensitivity below that would flag clean sheets. Real faint marks are well above it.
    faint = min(max(faint, FAINT_NOISE_FLOOR), threshold * 0.8)
    partial_floor = min(0.85, max(threshold + 0.42, 0.72))
    light_floor = 0.62
    decoded: dict[str, str | None] = {}
    scores: dict[str, dict[str, float]] = {}
    for group, values in observed.items():
        ratios = {k: v for k, v in values.items() if not k.startswith(("_mean:", "_strong:"))}
        means = {k.removeprefix("_mean:"): v for k, v in values.items() if k.startswith("_mean:")}
        strong_ratios = {k.removeprefix("_strong:"): v for k, v in values.items() if k.startswith("_strong:")}
        scores[group] = ratios
        selected = [key for key, value in ratios.items() if value >= threshold and means.get(key, 255) < pixel_threshold]
        weak = [(key, value) for key, value in ratios.items()
                if faint <= value < threshold or (value >= faint and means.get(key, 255) >= pixel_threshold)]
        decoded[group] = selected[0] if len(selected) == 1 else None
        kind = group.split(":", 1)[0]
        index = group.split(":", 1)[1]
        weak_values = [key for key, _ in weak]
        if kind == "subject":            # reported through the subject-code check below
            continue
        if len(selected) > 1:
            flags.append({"kind": "multiple", "group": kind, "question": index,
                          "values": [*selected, *weak_values], "selected_values": selected,
                          "weak_values": weak_values})
        elif len(selected) == 0 and weak:
            flags.append({"kind": "faint", "group": kind, "question": index,
                          "values": weak_values, "weak_values": weak_values,
                          "darkness": max(value for _, value in weak)})
        elif len(selected) == 0 and kind == "answer":
            flags.append({"kind": "blank", "question": index})
        elif len(selected) == 1:
            choice = selected[0]
            sel_ratio = ratios[choice]
            sel_strong = strong_ratios.get(choice, sel_ratio)
            detail = shape[group][choice]
            is_partial = sel_ratio < partial_floor or detail["full"] < FULL_FILL_MIN
            is_light = sel_strong < light_floor
            # Crosses, ticks and scribbles darken the centre but leave gaps in the circle;
            # overfilled or smudged marks leave ink outside the printed outline.
            is_cross = detail["arcs"] >= 2 and sel_ratio < 0.9
            is_overflow = detail["overflow"] >= OVERFLOW_MAX
            if weak or is_partial or is_light or is_cross or is_overflow:
                reasons = [r for r, hit in (("partial", is_partial), ("light", is_light), ("cross", is_cross),
                                            ("overflow", is_overflow), ("alternate", bool(weak))) if hit]
                flags.append({"kind": "review", "group": kind, "question": index,
                              "value": choice, "values": [choice, *weak_values],
                              "selected_values": selected, "weak_values": weak_values,
                              "darkness": sel_ratio, "strong_darkness": sel_strong,
                              "reason": "+".join(reasons)})
    answers = {key.split(":", 1)[1]: value for key, value in decoded.items() if key.startswith("answer:")}
    roll = "".join(decoded.get(f"roll:{position}") or "?" for position in range(int(config["sheet"]["roll_number_digits"])))
    if "?" in roll:
        flags.append({"kind": "invalid_id", "message": "Roll number is blank or ambiguous"})
        roll = None
    codes = [str(code) for code in config["sheet"]["set_codes"]]
    if len(codes) == 1:
        set_code = codes[0]                  # single paper: nothing to read
    else:
        set_code = decoded.get("set:0")
        if set_code is None:
            flags.append({"kind": "invalid_set", "message": "Test set is blank or ambiguous"})
    subject_code = None
    subject_groups = sorted(g for g in decoded if g.startswith("subject:"))
    if subject_groups:
        digits = [decoded[g] for g in subject_groups]
        expected_code = str(config.get("test", {}).get("subject_code") or "").strip()
        if all(d is None for d in digits):
            # Entirely blank is only a problem when the test expects a subject code, or when
            # stray ink says somebody tried to fill it in.
            stray = any(v >= faint for g in subject_groups for k, v in observed[g].items() if not k.startswith("_"))
            if stray or expected_code:
                flags.append({"kind": "invalid_subject", "message": "Subject code is blank or ambiguous"})
        elif any(d is None for d in digits):
            flags.append({"kind": "invalid_subject", "message": "Subject code is incomplete or ambiguous"})
        else:
            subject_code = "".join(str(d) for d in digits)
            if expected_code and expected_code != subject_code:
                flags.append({"kind": "invalid_subject",
                              "message": f"Subject code {subject_code} does not match this test ({expected_code})"})
    scanned_name, name_confidence = recognize_name_detail(measure, config, layout)
    thresholds: dict[str, float | str] = {"pixel_intensity": pixel_threshold, "strong_intensity": strong_threshold,
                                          "mark_density": threshold, "partial_density": partial_floor,
                                          "faint_density": faint}
    if subject_code:
        thresholds["subject_code"] = subject_code
    if scanned_name:
        thresholds["name_confidence"] = round(name_confidence, 3)
    return ReadResult(path, answers, roll, set_code, scores, flags, aligned,
                      thresholds=thresholds, student_name=scanned_name, subject_code=subject_code or "")
