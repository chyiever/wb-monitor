"""Persist and restore the last-used GUI parameters (``last_params.json``)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict


SETTINGS_FILENAME = "last_params.json"


def default_settings_path() -> Path:
    """Return the settings file path.

    When frozen (PyInstaller exe) the file lives next to the executable so the
    tool stays portable. When running from source it lives in the project root.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / SETTINGS_FILENAME
    return Path(__file__).resolve().parents[2] / SETTINGS_FILENAME


def load_settings() -> Dict[str, Any]:
    path = default_settings_path()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(settings: Dict[str, Any]) -> None:
    """Atomically write settings, silently ignoring write errors."""
    path = default_settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(settings, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
    except OSError:
        pass
