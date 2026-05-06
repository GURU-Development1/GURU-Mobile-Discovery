"""
Logging setup for iTunes Parser v2.
Writes to app data root (see app.paths); default %USERPROFILE%\\.itunes_parser_v2\\logs\\itunes_parser_v2.log.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from app.paths import get_app_data_root

LOG_NAME = "itunes_parser_v2"
_configured = False


def configure_logging() -> None:
    """Configure the itunes_parser_v2 logger once. Safe to call multiple times; skips if already configured."""
    global _configured
    if _configured:
        return
    log_dir = get_app_data_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "itunes_parser_v2.log"

    logger = logging.getLogger(LOG_NAME)
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        _configured = True
        return

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    _configured = True


def get_logger() -> logging.Logger:
    """Return the itunes_parser_v2 logger. Configures logging on first use if not already done."""
    configure_logging()
    return logging.getLogger(LOG_NAME)
