import sys
from types import SimpleNamespace
import pytest

from omr_exam import updater


def test_unconfigured_build_disables_automatic_updates():
    assert updater.check_for_update("") is None


def test_update_source_requires_https_without_embedded_credentials():
    assert updater.is_valid_update_source("https://updates.example/omr")
    assert updater.is_valid_update_source("http://localhost:8000/updates")
    assert not updater.is_valid_update_source("http://updates.example/omr")
    assert not updater.is_valid_update_source("https:///missing-host")
    assert not updater.is_valid_update_source("https://user:password@updates.example/omr")
    assert not updater.is_valid_update_source("https://updates.example/omr#fragment")
    assert not updater.is_valid_update_source("https://updates.example:99999/omr")


def test_velopack_update_is_downloaded_and_applied(monkeypatch):
    update = object()
    calls = []

    class Manager:
        def __init__(self, source):
            calls.append(("source", source))

        def check_for_updates(self):
            calls.append(("check",))
            return update

        def download_updates(self, item):
            calls.append(("download", item))

        def apply_updates_and_restart(self, item):
            calls.append(("apply", item))

    monkeypatch.setitem(sys.modules, "velopack", SimpleNamespace(UpdateManager=Manager))
    result = updater.check_for_update("https://updates.example/omr")
    assert result is not None
    manager, found = result
    assert found is update
    updater.download_and_apply(manager, found)
    assert calls == [("source", "https://updates.example/omr"), ("check",),
                     ("download", update), ("apply", update)]


def test_update_smoke_test_marks_completion_after_restart(monkeypatch, tmp_path):
    class Manager:
        def __init__(self, source):
            assert source == "http://127.0.0.1:8123"

        def get_current_version(self):
            return "0.1.1-ci.7"

    monkeypatch.setitem(sys.modules, "velopack", SimpleNamespace(UpdateManager=Manager))
    marker = tmp_path / "updated.txt"
    updater.run_update_smoke_test("0.1.1-ci.7", "http://127.0.0.1:8123", marker)
    assert marker.read_text(encoding="utf-8") == "0.1.1-ci.7"


def test_update_smoke_test_checks_then_applies_expected_version(monkeypatch, tmp_path):
    update = SimpleNamespace(TargetFullRelease=SimpleNamespace(Version="0.1.1-ci.7"))
    calls = []

    class Manager:
        def __init__(self, source):
            calls.append(("source", source))

        def get_current_version(self):
            return "0.1.0"

        def check_for_updates(self):
            calls.append(("check",))
            return update

        def download_updates(self, value):
            calls.append(("download", value))

        def apply_updates_and_restart(self, value):
            calls.append(("apply", value))
            raise SystemExit(0)

    monkeypatch.setitem(sys.modules, "velopack", SimpleNamespace(UpdateManager=Manager))
    with pytest.raises(SystemExit):
        updater.run_update_smoke_test("0.1.1-ci.7", "http://127.0.0.1:8123", tmp_path / "done.txt")
    assert calls == [("source", "http://127.0.0.1:8123"), ("check",),
                     ("download", update), ("apply", update)]


@pytest.mark.parametrize("update, message", [
    (None, "No update available"),
    (SimpleNamespace(TargetFullRelease=SimpleNamespace(Version="0.1.2")), "Feed offered 0.1.2"),
])
def test_update_smoke_test_rejects_missing_or_wrong_release(monkeypatch, tmp_path, update, message):
    class Manager:
        def __init__(self, source):
            pass

        def get_current_version(self):
            return "0.1.0"

        def check_for_updates(self):
            return update

    monkeypatch.setitem(sys.modules, "velopack", SimpleNamespace(UpdateManager=Manager))
    with pytest.raises(RuntimeError, match=message):
        updater.run_update_smoke_test("0.1.1", "http://127.0.0.1:8123", tmp_path / "done.txt")
    assert not (tmp_path / "done.txt").exists()
