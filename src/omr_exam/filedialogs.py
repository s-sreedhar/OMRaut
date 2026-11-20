"""File dialogs that remember where the user last opened or saved something.

Qt's static dialogs start in the process working directory (the app's own folder) unless told otherwise. These
wrappers start in the last folder the user actually picked, across all dialogs and across restarts, and fall back
to Documents when that folder is gone."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import QFileDialog

_KEY = "paths/last_directory"      # most recent folder of any kind: the starting point for a new area
AREAS = ("scans", "exports", "imports", "backups")     # each remembers its own folder


def _area_key(area: str) -> str:
    return f"paths/{area}" if area in AREAS else _KEY


def _settings() -> QSettings:
    return QSettings("OMRaut", "OMRaut")


def _fallback() -> str:
    return QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or str(Path.home())


def last_directory(area: str = "") -> str:
    """The folder last used for this kind of file, else the last folder used for anything, else Documents."""
    for key in (_area_key(area), _KEY):
        try:
            stored = str(_settings().value(key, "") or "")
        except Exception:
            stored = ""
        if stored and Path(stored).is_dir():
            return stored
    return _fallback()


def remember(path: str | Path, area: str = "") -> None:
    """Store the folder of a chosen file (or the chosen folder itself) for this area and as the general default."""
    if not path:
        return
    target = Path(path)
    folder = target if target.is_dir() else target.parent
    if folder.is_dir():
        try:
            settings = _settings()
            settings.setValue(_KEY, str(folder))
            settings.setValue(_area_key(area), str(folder))
        except Exception:
            pass


def open_file(parent, caption: str, file_filter: str = "", area: str = "") -> tuple[str, str]:
    path, chosen = QFileDialog.getOpenFileName(parent, caption, last_directory(area), file_filter)
    remember(path, area)
    return path, chosen


def open_files(parent, caption: str, file_filter: str = "", area: str = "") -> tuple[list[str], str]:
    paths, chosen = QFileDialog.getOpenFileNames(parent, caption, last_directory(area), file_filter)
    if paths:
        remember(paths[0], area)
    return paths, chosen


def save_file(parent, caption: str, suggested_name: str, file_filter: str = "", area: str = "exports") -> tuple[str, str]:
    """``suggested_name`` is only a file name; the dialog opens in the last used folder."""
    start = str(Path(last_directory(area)) / Path(suggested_name).name)
    path, chosen = QFileDialog.getSaveFileName(parent, caption, start, file_filter)
    remember(path, area)
    return path, chosen


def choose_folder(parent, caption: str, area: str = "exports") -> str:
    folder = QFileDialog.getExistingDirectory(parent, caption, last_directory(area))
    remember(folder, area)
    return folder
