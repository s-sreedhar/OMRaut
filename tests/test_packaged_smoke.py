"""The packaged-app self-test (`OMRaut.exe --smoke-test`) runs during every build; keep it green in CI too."""
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("openpyxl")
pytest.importorskip("fitz")


def test_packaged_smoke_test_passes_against_the_current_reader(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from omr_exam.app import _smoke_test

    assert _smoke_test() == 0
