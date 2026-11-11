"""Velopack update checks and staged updates."""
from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from omr_exam.resources import config_dir

UPDATE_SOURCE_URL = os.environ.get("OMR_UPDATE_SOURCE_URL", "")
AUTOMATIC_CHECK_INTERVAL = timedelta(hours=24)
try:
    _config_path = config_dir() / "update.json"
    UPDATE_SOURCE_URL = UPDATE_SOURCE_URL or json.loads(
        _config_path.read_text(encoding="utf-8-sig")
    ).get("source_url", "")
except (OSError, json.JSONDecodeError):
    pass


def is_valid_update_source(source_url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(source_url)
        hostname = parsed.hostname
        # Feed URLs are embedded in the app binary: reject userinfo and fragments,
        # and require a real host so malformed strings don't pass as secure URLs.
        if not hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
            return False
        parsed.port  # Validate that any explicit port is numeric and in range.
    except (ValueError, TypeError):
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and hostname in {"localhost", "127.0.0.1", "::1"}


def should_check_automatically(last_check: str, *, now: datetime | None = None) -> bool:
    """Allow one quiet startup check per day; malformed or missing timestamps retry."""
    if not last_check:
        return True
    try:
        previous = datetime.fromisoformat(last_check)
    except (TypeError, ValueError):
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc) - previous.astimezone(timezone.utc) >= AUTOMATIC_CHECK_INTERVAL


def check_for_update(source_url: str = UPDATE_SOURCE_URL):
    """Return (manager, update info), or None when updates are not configured/available."""
    if not source_url:
        return None
    if not is_valid_update_source(source_url):
        raise ValueError("Velopack update source must use HTTPS (HTTP is allowed only on localhost for testing).")
    import velopack

    manager = velopack.UpdateManager(source_url)
    update = manager.check_for_updates()
    return (manager, update) if update else None


def download_and_apply(manager, update) -> None:
    """Download the Velopack package and restart into the applied update."""
    manager.download_updates(update)
    manager.apply_updates_and_restart(update)


def run_update_smoke_test(expected_version: str, source_url: str, completion_marker: str | Path) -> None:
    """Exercise a real installed update; the restarted process writes the marker."""
    if not source_url or not is_valid_update_source(source_url):
        raise ValueError("The update smoke test requires a valid HTTP or HTTPS feed URL")
    import velopack

    manager = velopack.UpdateManager(source_url)
    current_version = str(manager.get_current_version())
    if current_version == expected_version:
        Path(completion_marker).write_text(current_version, encoding="utf-8")
        return

    update = manager.check_for_updates()
    if update is None:
        raise RuntimeError(f"No update available; installed version is {current_version}, expected {expected_version}")
    target_version = str(update.TargetFullRelease.Version)
    if target_version != expected_version:
        raise RuntimeError(f"Feed offered {target_version}, expected {expected_version}")
    manager.download_updates(update)
    manager.apply_updates_and_restart_with_args(
        update, ["--update-smoke-test", expected_version, str(completion_marker)])
    raise RuntimeError("Velopack returned before restarting the application")
