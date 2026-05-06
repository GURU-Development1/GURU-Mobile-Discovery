"""
App data and temp paths. Use ITUNES_PARSER_V2_DATA to store cache/logs on a different drive.

Default resolution (when ITUNES_PARSER_V2_DATA is not set):
- If ~/.itunes_parser_v2 already exists → use it (backward compatibility).
- On Windows otherwise → %LOCALAPPDATA%\\iTunes Parser v2 (standard per-user data for production installs).
- Else → ~/.itunes_parser_v2

The directory is created on first run; an installer does not need to pre-create it.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_ENV_DATA = "ITUNES_PARSER_V2_DATA"
_LEGACY_DIR = ".itunes_parser_v2"
_WIN_APPDATA_SUBDIR = "iTunes Parser v2"


def _default_app_data_root() -> Path:
    """Pick default data root when ITUNES_PARSER_V2_DATA is unset."""
    legacy = Path.home() / _LEGACY_DIR
    if legacy.exists():
        return legacy
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / _WIN_APPDATA_SUBDIR
    return legacy


def get_app_data_root() -> Path:
    """
    Root for cache, logs, cases, and saved searches.

    Override with environment variable **ITUNES_PARSER_V2_DATA** (absolute path) for development,
    portable installs, or storing data on another drive.
    """
    raw = os.environ.get(_ENV_DATA)
    if raw and raw.strip():
        root = Path(raw.strip()).resolve()
    else:
        root = _default_app_data_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_temp_dir_on_data_drive() -> None:
    """
    If ITUNES_PARSER_V2_DATA is set, use a temp subdir under it so temp files (e.g. SQLite)
    don't fill the system drive. Call once at startup (e.g. from main).
    """
    if not os.environ.get(_ENV_DATA):
        return
    tmp = get_app_data_root() / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(tmp)
