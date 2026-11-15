"""Offline name-field OCR using Windows built-in WinRT OCR when available."""
from __future__ import annotations

import atexit
import base64
import re
import subprocess
import sys
import threading
from typing import Any

import cv2
import numpy as np

from omr_exam.layout import SheetLayout

_LOCK = threading.Lock()
_WORKER: subprocess.Popen[str] | None = None
_OCR_DISABLED = False

_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [System.IO.WindowsRuntimeStreamExtensions]
$null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType=WindowsRuntime]
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
function Await-WinRt($op, $resType) {
    $t = $asTaskGeneric.MakeGenericMethod($resType).Invoke($null, @($op))
    $t.Wait()
    return $t.Result
}
$eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $eng) {
    [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime] | Out-Null
    $eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US'))
}
if ($null -eq $eng) {
    exit 2
}
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
while ($null -ne ($line = [Console]::In.ReadLine())) {
    if ($line -eq '__EXIT__') { break }
    if (-not $line) { [Console]::Out.WriteLine(''); [Console]::Out.Flush(); continue }
    try {
        $bytes = [Convert]::FromBase64String($line)
        $ms = New-Object System.IO.MemoryStream(,$bytes)
        $ras = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($ms)
        $dec = Await-WinRt ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bmp = Await-WinRt ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $res = Await-WinRt ($eng.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
        $txt = ($res.Text -replace "[\r\n]+", " ").Trim()
        [Console]::Out.WriteLine($txt)
    } catch {
        [Console]::Out.WriteLine('')
    }
    [Console]::Out.Flush()
}
"""


def _shutdown_worker() -> None:
    global _WORKER
    with _LOCK:
        proc, _WORKER = _WORKER, None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                proc.stdin.write("__EXIT__\n")
                proc.stdin.flush()
                proc.wait(timeout=1.5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


atexit.register(_shutdown_worker)


def _get_worker() -> subprocess.Popen[str] | None:
    global _WORKER, _OCR_DISABLED
    if _OCR_DISABLED or sys.platform != "win32":
        return None
    if _WORKER is not None and _WORKER.poll() is None:
        return _WORKER
    try:
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        _WORKER = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _PS_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            creationflags=creation,
        )
        return _WORKER
    except Exception:
        _OCR_DISABLED = True
        _WORKER = None
        return None


def name_box_mm(config: dict[str, Any], layout: SheetLayout) -> tuple[float, float, float, float, int] | None:
    """Return ``(x_mm, y_mm, width_mm, height_mm, cells)`` of the printed NAME box, or ``None``."""
    fields = config.get("sheet", {}).get("fields", [])
    if "name" not in fields:
        return None
    name_label = next((item for item in layout.identity_labels if item[0] == "NAME"), None)
    if name_label is None:
        return None
    _field, x, y = name_label
    box_x = float(x) + 21.0
    if "class" in fields:
        class_label_x = layout.width / 2.0 + 18.0
        box_w = class_label_x - 7.0 - box_x
    else:
        box_w = layout.width - 18.0 - box_x
    if box_w <= 10.0:
        return None
    return (box_x, float(y) - 5.5, box_w, 10.0, 22)


def _prepare_name_crop(aligned: np.ndarray, box_mm: tuple[float, float, float, float, int],
                       width_mm: float, height_mm: float) -> np.ndarray | None:
    """Crop the NAME box interior, erase thin cell dividers, and return a padded OCR image if non-blank."""
    box_x, box_y, box_w, box_h, cells = box_mm
    sx = aligned.shape[1] / width_mm
    sy = aligned.shape[0] / height_mm
    x1 = max(0, round((box_x + 0.55) * sx))
    x2 = min(aligned.shape[1], round((box_x + box_w - 0.55) * sx))
    y1 = max(0, round((box_y + 0.85) * sy))
    y2 = min(aligned.shape[0], round((box_y + box_h - 0.85) * sy))
    if x2 - x1 < 20 or y2 - y1 < 12:
        return None

    crop = aligned[y1:y2, x1:x2].copy()
    inner_w_mm = box_w - 1.1
    # Remove the subtle vertical cell divider lines where neighboring pixels are paper-white
    # so letter crossbars that span a divider remain intact.
    for cell_idx in range(1, cells):
        gx = round((cell_idx * (box_w / cells) - 0.55) * sx)
        for col in range(max(0, gx - 2), min(crop.shape[1], gx + 3)):
            left_col = max(0, col - 3)
            right_col = min(crop.shape[1] - 1, col + 3)
            clear_mask = (crop[:, left_col] > 165) | (crop[:, right_col] > 165)
            crop[clear_mask, col] = 255

    if inner_w_mm <= 0:
        return None
    paper_floor = float(np.percentile(crop, 85))
    ink_cutoff = min(155.0, max(70.0, paper_floor - 70.0))
    ink_ratio = float((crop < ink_cutoff).mean())
    if ink_ratio < 0.004:
        return None

    scaled = cv2.resize(crop, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    padded = cv2.copyMakeBorder(scaled, 28, 28, 32, 32, cv2.BORDER_CONSTANT, value=255)
    return padded


def _clean_ocr_name(text: str) -> str:
    cleaned = text.replace("|", " ").replace("_", " ")
    cleaned = re.sub(r"[^A-Za-z0-9\s.'-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .'-")
    if len(cleaned) <= 1 and not cleaned.isalpha():
        return ""
    return cleaned


def recognize_name(aligned: np.ndarray, config: dict[str, Any], layout: SheetLayout) -> str:
    """Extract candidate text from the NAME box of an aligned OMR image, or ``''``."""
    box = name_box_mm(config, layout)
    if box is None:
        return ""
    prepared = _prepare_name_crop(aligned, box, layout.width, layout.height)
    if prepared is None:
        return ""
    ok, encoded = cv2.imencode(".png", prepared)
    if not ok:
        return ""
    payload = base64.b64encode(encoded.tobytes()).decode("ascii")
    with _LOCK:
        for attempt in range(2):
            worker = _get_worker()
            if worker is None or worker.stdin is None or worker.stdout is None:
                return ""
            try:
                worker.stdin.write(payload + "\n")
                worker.stdin.flush()
                raw = worker.stdout.readline()
                if raw == "" and worker.poll() is not None:
                    raise EOFError("OCR worker exited")
                return _clean_ocr_name(raw)
            except Exception:
                global _WORKER
                try:
                    worker.kill()
                except Exception:
                    pass
                _WORKER = None
                if attempt == 1:
                    return ""
    return ""
