"""Locate bundled static resources in source and PyInstaller builds."""
from pathlib import Path


def config_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config"
        if (candidate / "test-config.schema.json").is_file():
            return candidate
    raise FileNotFoundError("Bundled test-config.schema.json was not found")
