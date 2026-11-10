from pathlib import Path

import pytest

from omr_exam.ingest import scan_files_in_folder


def test_scan_folder_returns_supported_files_in_deterministic_order(tmp_path):
    for name in ("b.PNG", "a.pdf", "c.TIFF", "notes.txt"):
        (tmp_path / name).write_bytes(b"fixture")
    (tmp_path / "subfolder").mkdir()
    (tmp_path / "subfolder" / "nested.jpg").write_bytes(b"fixture")

    assert [path.name for path in scan_files_in_folder(tmp_path)] == ["a.pdf", "b.PNG", "c.TIFF"]


def test_scan_folder_requires_an_existing_directory(tmp_path):
    with pytest.raises(ValueError, match="not a directory"):
        scan_files_in_folder(tmp_path / "missing")


def test_scan_folder_returns_empty_when_no_supported_files(tmp_path):
    (tmp_path / "notes.txt").write_text("not a scan")
    assert scan_files_in_folder(tmp_path) == []
