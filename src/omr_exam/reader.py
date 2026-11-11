"""Image normalization, registration mark detection, and OMR bubble classification."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from omr_exam.layout import Bubble, create_layout


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


def _fiducials(gray: np.ndarray) -> np.ndarray:
    """Find the four square registration marks, ordered TL, TR, BR, BL."""
    h, w = gray.shape[:2]
    if min(h, w) < 500:
        raise ValueError("Scan resolution is too low; rescan at 150 DPI or higher")
    # Otsu tolerates grayscale/color scans; a slight blur removes scanner speckle.
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[np.ndarray, float]] = []
    area_floor = (min(h, w) * 0.012) ** 2
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < area_floor or area > h * w * 0.03:
            continue
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.04 * perimeter, True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        x, y, cw, ch = cv2.boundingRect(polygon)
        ratio = cw / max(ch, 1)
        if not 0.75 <= ratio <= 1.33 or area / max(cw * ch, 1) < 0.55:
            continue
        center = np.array([x + cw / 2, y + ch / 2], dtype=np.float32)
        candidates.append((center, float(area)))
    if len(candidates) < 4:
        raise ValueError("Could not find all four corner marks; check sheet, crop, or scan quality")
    # Pick the closest valid square to each expected corner. Deduplicate so a nested contour
    # around the same black marker cannot occupy two corner slots.
    corners = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    chosen: list[np.ndarray] = []
    used: set[int] = set()
    for corner in corners:
        ranked = sorted(enumerate(candidates), key=lambda item: np.linalg.norm(item[1][0] - corner))
        match = next(((idx, item) for idx, item in ranked if idx not in used), None)
        if match is None:
            raise ValueError("Registration marks overlap or are ambiguous")
        idx, (point, _) = match
        used.add(idx)
        chosen.append(point)
    points = np.array(chosen, dtype=np.float32)
    # Require a sensible quadrilateral rather than warping a sheet with missing/collapsed corners.
    if cv2.contourArea(points.reshape(-1, 1, 2)) < w * h * 0.35:
        raise ValueError("Corner marks do not form a complete sheet outline")
    return points


def align_sheet(image: np.ndarray, config: dict[str, Any]) -> np.ndarray:
    gray = _gray(image)
    points = _fiducials(gray)
    width_mm = float(config["sheet"]["width_mm"])
    height_mm = float(config["sheet"]["height_mm"])
    marker_mm = float(config["sheet"].get("corner_mark_size_mm", 8))
    # Identify the coded registration squares before warping; this explicitly corrects
    # 90/180 degree rotations and mirrored inputs rather than trusting image orientation.
    h, w = gray.shape[:2]
    scale_normal = min(w / width_mm, h / height_mm)
    scale_rotated = min(w / height_mm, h / width_mm)
    rx = max(2, round(marker_mm * max(scale_normal, scale_rotated) / 2))
    sample_radius = max(1, round(rx * 0.12))
    hole_offset = rx * 0.34
    marker_codes: dict[int, np.ndarray] = {}
    for point in points:
        cx, cy = map(float, point)
        white_count = 0
        for dx, dy in ((-hole_offset, -hole_offset), (hole_offset, -hole_offset),
                       (hole_offset, hole_offset), (-hole_offset, hole_offset)):
            x, y = round(cx + dx), round(cy + dy)
            patch = gray[max(0, y-sample_radius):min(h, y+sample_radius+1), max(0, x-sample_radius):min(w, x+sample_radius+1)]
            if patch.size and float(np.median(patch)) > 175:
                white_count += 1
        # The printed codes contain exactly 0, 1, 2, or 3 white sub-marks. Four means
        # the registration area was missed, obscured, or a legacy sheet is being used.
        code = white_count
        if code in marker_codes:
            raise ValueError("Corner orientation marks are duplicated; check that the sheet was generated by this version")
        marker_codes[code] = point
    if set(marker_codes) != {0, 1, 2, 3}:
        raise ValueError("Corner orientation marks are unreadable; generate a fresh sheet or rescan without cropping")
    points = np.array([marker_codes[i] for i in range(4)], dtype=np.float32)
    # A normalized pixel/mm scale keeps geometry and bubble sampling deterministic.
    pixels_per_mm = 8
    width, height = round(width_mm * pixels_per_mm), round(height_mm * pixels_per_mm)
    marker = marker_mm
    # Registration square centers are printed 8 mm from the page edges, independent
    # of their square size. Keep the values in mm until converting every coordinate.
    inset = marker
    destination = np.array([
        [inset * pixels_per_mm, inset * pixels_per_mm],
        [(width_mm - inset) * pixels_per_mm, inset * pixels_per_mm],
        [(width_mm - inset) * pixels_per_mm, (height_mm - inset) * pixels_per_mm],
        [inset * pixels_per_mm, (height_mm - inset) * pixels_per_mm],
    ], dtype=np.float32)
    transform = cv2.getPerspectiveTransform(points, destination)
    return cv2.warpPerspective(gray, transform, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _darkness(aligned: np.ndarray, bubble: Bubble, width_mm: float, height_mm: float, pixel_threshold: float) -> tuple[float, float]:
    sx, sy = aligned.shape[1] / width_mm, aligned.shape[0] / height_mm
    cx, cy = round(bubble.x * sx), round(bubble.y * sy)
    # Sample inner bubble area, excluding printed outline (radius 0.85 mm).
    radius = max(2, round(0.9 * sx))
    x1, x2 = max(0, cx - radius), min(aligned.shape[1], cx + radius + 1)
    y1, y2 = max(0, cy - radius), min(aligned.shape[0], cy + radius + 1)
    crop = aligned[y1:y2, x1:x2]
    if crop.size == 0:
        return 0.0, 1.0
    _, binary = cv2.threshold(crop, pixel_threshold, 255, cv2.THRESH_BINARY_INV)
    ratio = float(cv2.countNonZero(binary)) / crop.size
    mean_intensity = float(np.mean(crop))
    return ratio, mean_intensity


def read_image(image: str | Path | np.ndarray, config: dict[str, Any], *, mark_threshold: float | None = None, faint_threshold: float | None = None) -> ReadResult:
    path = str(image) if not isinstance(image, np.ndarray) else "<memory>"
    source = cv2.imread(str(image), cv2.IMREAD_COLOR) if not isinstance(image, np.ndarray) else image
    flags: list[dict[str, Any]] = []
    try:
        aligned = align_sheet(source, config)
    except (ValueError, cv2.error) as exc:
        return ReadResult(path, {}, None, None, {}, [{"kind": "unreadable", "message": str(exc)}])
    layout = create_layout(config)
    # A fresh Otsu cutoff for every sheet adapts to scanner brightness and paper tone.
    # Clamp it to avoid extreme cutoffs on nearly blank or bilevel scans.
    otsu_cutoff, _ = cv2.threshold(aligned, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    pixel_threshold = float(np.clip(otsu_cutoff + 80, 135, 230))
    observed: dict[str, dict[str, float]] = {}
    for bubble in layout.bubbles:
        group = f"{bubble.group}:{bubble.index}"
        ratio, mean = _darkness(aligned, bubble, layout.width, layout.height, pixel_threshold)
        observed.setdefault(group, {})[bubble.value] = ratio
        observed[group][f"_mean:{bubble.value}"] = mean
    # Confidence cutoffs can be overridden per test. Otherwise the fixed baseline is
    # conservative and every near-cutoff mark becomes a review flag.
    threshold = float(mark_threshold if mark_threshold is not None else config.get("reader", {}).get("mark_threshold", 0.24))
    faint = float(faint_threshold if faint_threshold is not None else config.get("reader", {}).get("faint_threshold", 0.02))
    if not 0 < faint < threshold < 1:
        raise ValueError("Require 0 < faint_threshold < mark_threshold < 1")
    decoded: dict[str, str | None] = {}
    scores: dict[str, dict[str, float]] = {}
    for group, values in observed.items():
        ratios = {k: v for k, v in values.items() if not k.startswith("_mean:")}
        means = {k.removeprefix("_mean:"): v for k, v in values.items() if k.startswith("_mean:")}
        scores[group] = ratios
        selected = [key for key, value in ratios.items() if value >= threshold and means.get(key, 255) < pixel_threshold]
        weak = [(key, value) for key, value in ratios.items()
                if faint <= value < threshold or (value >= faint and means.get(key, 255) >= pixel_threshold)]
        decoded[group] = selected[0] if len(selected) == 1 else None
        kind = group.split(":", 1)[0]
        index = group.split(":", 1)[1]
        weak_values = [key for key, _ in weak]
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
        elif len(selected) == 1 and (weak or selected[0] in ratios and ratios[selected[0]] < threshold + 0.08):
            flags.append({"kind": "review", "group": kind, "question": index,
                          "value": selected[0], "values": [selected[0], *weak_values],
                          "selected_values": selected, "weak_values": weak_values,
                          "darkness": ratios[selected[0]]})
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
    return ReadResult(path, answers, roll, set_code, scores, flags, aligned,
                      thresholds={"pixel_intensity": pixel_threshold, "mark_density": threshold, "faint_density": faint})
