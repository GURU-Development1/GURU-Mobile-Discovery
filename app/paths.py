"""
App data and temp paths. Use GURU_MOBILE_DISCOVERY_DATA to store cache/logs on a different drive.

The legacy environment variable **ITUNES_PARSER_V2_DATA** is still accepted.

Default resolution (when neither env var is set):
- If ~/.itunes_parser_v2 already exists → use it (backward compatibility).
- On Windows: if %LOCALAPPDATA%\\iTunes Parser v2 exists → use it; else → %LOCALAPPDATA%\\GURU Mobile Discovery
- Else → ~/.itunes_parser_v2 (created on first run if needed)

The directory is created on first run; an installer does not need to pre-create it.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_ENV_DATA = "GURU_MOBILE_DISCOVERY_DATA"
_ENV_DATA_LEGACY = "ITUNES_PARSER_V2_DATA"
_LEGACY_DIR = ".itunes_parser_v2"
_WIN_APPDATA_SUBDIR = "GURU Mobile Discovery"
_LEGACY_WIN_APPDATA_SUBDIR = "iTunes Parser v2"


def _env_data_override() -> str | None:
    raw = os.environ.get(_ENV_DATA) or os.environ.get(_ENV_DATA_LEGACY)
    if raw and raw.strip():
        return raw.strip()
    return None


def _default_app_data_root() -> Path:
    """Pick default data root when no data-directory env var is set."""
    legacy = Path.home() / _LEGACY_DIR
    if legacy.exists():
        return legacy
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            base = Path(local)
            legacy_win = base / _LEGACY_WIN_APPDATA_SUBDIR
            if legacy_win.exists():
                return legacy_win
            return base / _WIN_APPDATA_SUBDIR
    return legacy


def get_app_data_root() -> Path:
    """
    Root for cache, logs, cases, and saved searches.

    Override with environment variable **GURU_MOBILE_DISCOVERY_DATA** (absolute path), or the legacy
    **ITUNES_PARSER_V2_DATA**, for development, portable installs, or storing data on another drive.
    """
    raw = _env_data_override()
    if raw:
        root = Path(raw).resolve()
    else:
        root = _default_app_data_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_temp_dir_on_data_drive() -> None:
    """
    If GURU_MOBILE_DISCOVERY_DATA or ITUNES_PARSER_V2_DATA is set, use a temp subdir under it so
    temp files (e.g. SQLite) don't fill the system drive. Call once at startup (e.g. from main).
    """
    if not _env_data_override():
        return
    tmp = get_app_data_root() / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(tmp)
