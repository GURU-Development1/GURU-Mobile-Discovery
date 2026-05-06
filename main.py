"""
Entry point for iTunes Parser v2.
Launches the PyQt6 app (parser is self-contained in app.backup_parser).
"""

import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from app.logging_config import configure_logging
from app.paths import ensure_temp_dir_on_data_drive
from app.main_window import MainWindow
from app.style import get_stylesheet, init_assets_root
from app.version import __version__


def _bundle_root() -> Path:
    """Project root when running from source; PyInstaller extract dir when frozen."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _app_icon_path() -> Path:
    return _bundle_root() / "assets" / "app.ico"


def main() -> int:
    configure_logging()
    ensure_temp_dir_on_data_drive()
    app = QApplication(sys.argv)
    app.setApplicationName("GURU Mobile Discovery")
    app.setApplicationVersion(__version__)
    init_assets_root(_bundle_root() / "assets")
    app.setStyleSheet(get_stylesheet(_bundle_root() / "assets"))
    icon_path = _app_icon_path()
    icon = QIcon(str(icon_path)) if icon_path.is_file() else QIcon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    win = MainWindow()
    if not icon.isNull():
        win.setWindowIcon(icon)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
