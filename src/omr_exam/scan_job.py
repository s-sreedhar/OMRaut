"""Read a batch of scan files: copy, align, decode, score, and store every page.

The pipeline is a plain function so it can run on a worker thread (the desktop app) or
inline (tests and scripts). It only talks to the repository, which opens a fresh SQLite
connection per call, so it is safe to run off the interface thread.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2
from PySide6.QtCore import QThread, Signal

from omr_exam.database import Repository
from omr_exam.ingest import count_scan_pages, scan_pages
from omr_exam.reader import read_image
from omr_exam.scoring import score_answers


@dataclass
class ScanProgress:
    processed: int = 0
    total: int = 0
    needs_review: int = 0
    errors: int = 0
    current_file: str = ""


@dataclass
class ScanOutcome:
    batch_id: int | None = None
    status: str = ""
    processed: int = 0
    expected: int = 0
    needs_review: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)
    fatal: str = ""          # set when nothing could be read at all


def _flag_summary(flags: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{flag.get('kind', 'review')}"
        f"{(' ' + str(flag['group'])) if flag.get('group') else ''}"
        f"{(' Q' + str(flag['question'])) if flag.get('question') else ''}"
        f"{(': ' + ', '.join(map(str, flag['values']))) if flag.get('values') else ''}"
        for flag in flags if flag.get("kind") != "blank"
    ) or "manual review required"


def process_scan_batch(repo: Repository, db_path: Path, test_id: int, config: dict[str, Any], files: list[str | Path], *,
                       scan_dpi: int = 216, color_mode: str = "grayscale",
                       on_progress: Callable[[ScanProgress], None] | None = None,
                       is_cancelled: Callable[[], bool] | None = None,
                       on_batch_created: Callable[[int], None] | None = None) -> ScanOutcome:
    outcome = ScanOutcome()
    progress = ScanProgress()

    def report() -> None:
        if on_progress is not None:
            on_progress(ScanProgress(**progress.__dict__))

    page_counts: list[tuple[str, int]] = []
    for file in files:
        try:
            page_counts.append((str(file), count_scan_pages(file)))
        except Exception as exc:
            outcome.errors.append(f"{Path(file).name}: {exc}")
    expected_pages = sum(count for _, count in page_counts)
    if not expected_pages:
        outcome.fatal = "\n".join(outcome.errors) or "The selected files contained no pages."
        return outcome
    batch_id = repo.create_batch(test_id, str(Path(page_counts[0][0]).parent), expected_pages)
    outcome.batch_id, outcome.expected = batch_id, expected_pages
    if on_batch_created is not None:
        on_batch_created(batch_id)
    for error in outcome.errors:
        repo.log_batch_event(batch_id, "ERROR", error)

    data_dir = Path(db_path).parent
    managed_dir = data_dir / "scans" / f"batch-{batch_id}"
    managed_dir.mkdir(parents=True, exist_ok=True)
    managed_pages: list[tuple[str, int]] = []
    for index, (file, count) in enumerate(page_counts, 1):
        copy_path = managed_dir / f"{index:04d}-{Path(file).name}"
        try:
            shutil.copy2(file, copy_path)
            managed_pages.append((str(copy_path), count))
        except Exception as exc:
            message = f"Could not copy to app data ({exc})"
            outcome.errors.append(f"{Path(file).name}: {message}")
            repo.log_batch_event(batch_id, "ERROR", message, Path(file).name)
    progress.total = sum(count for _, count in managed_pages)
    progress.errors = len(outcome.errors)
    if not progress.total:
        repo.finish_batch(batch_id, 0, error_count=max(1, len(outcome.errors)))
        repo.log_batch_event(batch_id, "ERROR", "No scan files could be copied into app data.")
        outcome.status = "incomplete"
        outcome.fatal = "Selected scans could not be copied to app data.\n" + "\n".join(outcome.errors)
        return outcome

    report()
    processed = 0
    cancelled = False
    for file, _count in managed_pages:
        progress.current_file = Path(file).name.split("-", 1)[-1]
        try:
            for page_label, image_data in scan_pages(file, dpi=scan_dpi, color_mode=color_mode):
                if is_cancelled is not None and is_cancelled():
                    cancelled = True
                    break
                result = read_image(image_data, config)
                if result.requires_review:
                    repo.log_batch_event(batch_id, "WARNING", f"Reader flags: {_flag_summary(result.flags)}", Path(file).name)
                result.thresholds["color_mode"] = color_mode
                if Path(file).suffix.lower() == ".pdf":
                    result.thresholds["pdf_render_dpi"] = float(scan_dpi)
                original_label, page_suffix = (page_label.split("#", 1) + [""])[:2]
                result.image_path = (Path(original_label).relative_to(data_dir).as_posix()
                                     + (f"#{page_suffix}" if page_suffix else ""))
                preview = result.aligned_image if result.aligned_image is not None else image_data
                if preview is not None:
                    preview_dir = data_dir / "previews"
                    preview_dir.mkdir(parents=True, exist_ok=True)
                    preview_path = preview_dir / f"batch-{batch_id}-sheet-{processed + 1}.png"
                    if cv2.imwrite(str(preview_path), preview):
                        result.preview_path = preview_path.relative_to(data_dir).as_posix()
                    else:
                        result.flags.append({"kind": "preview_error", "message": "Could not save scan preview"})
                if result.set_code in config["sets"]:
                    scored = score_answers(config, result.set_code, result.answers)
                else:
                    scored = {"total": 0, "correct": 0, "incorrect": 0, "blank": len(config["questions"]), "questions": []}
                repo.store_read(batch_id, result, scored)
                processed += 1
                progress.processed = processed
                progress.needs_review += int(result.requires_review)
                report()
            if cancelled:
                break
        except Exception as exc:
            outcome.errors.append(f"{Path(file).name}: {exc}")
            repo.log_batch_event(batch_id, "ERROR", str(exc), Path(file).name)
            progress.errors = len(outcome.errors)
            report()

    repo.reconcile_batch_roster_flags(batch_id)
    outcome.status = repo.finish_batch(batch_id, processed, error_count=len(outcome.errors), cancelled=cancelled)
    if cancelled:
        repo.log_batch_event(batch_id, "WARNING", f"Batch cancelled after {processed} of {expected_pages} pages.")
    else:
        repo.log_batch_event(batch_id, "INFO", f"Batch finished as {outcome.status}: {processed}/{expected_pages} pages, "
                                               f"{len(outcome.errors)} file/page errors.")
    outcome.processed, outcome.cancelled = processed, cancelled
    outcome.needs_review = sum(bool(row["review_required"]) for row in repo.results_for_batch(batch_id))
    return outcome


class ScanWorker(QThread):
    """Runs :func:`process_scan_batch` off the interface thread."""

    progressed = Signal(object)
    batch_created = Signal(int)
    completed = Signal(object)

    def __init__(self, repo: Repository, db_path: Path, test_id: int, config: dict[str, Any], files: list[str],
                 scan_dpi: int, color_mode: str, parent=None):
        super().__init__(parent)
        self._arguments = (repo, db_path, test_id, config, files)
        self._options = {"scan_dpi": scan_dpi, "color_mode": color_mode}
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            outcome = process_scan_batch(*self._arguments, **self._options, on_progress=self.progressed.emit,
                                         is_cancelled=lambda: self._cancel, on_batch_created=self.batch_created.emit)
        except Exception as exc:  # never let a worker die silently
            outcome = ScanOutcome(fatal=str(exc))
        self.completed.emit(outcome)
