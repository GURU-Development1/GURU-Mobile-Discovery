"""
Entry point for GURU Mobile Discovery.
Launches the PyQt6 app (iOS backup parsing in app.backup_parser).
"""

import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QDialog

from app.logging_config import configure_logging
from app.paths import ensure_temp_dir_on_data_drive, get_app_data_root
from app.main_window import MainWindow
from app.style import get_stylesheet, init_assets_root
from app.version import __version__
from app.license_service import LicenseService, LicenseStatus
from app.license_dialog import LicenseDialog


def _bundle_root() -> Path:
    """Project root when running from source; PyInstaller extract dir when frozen."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _app_icon_path() -> Path:
    return _bundle_root() / "assets" / "app.ico"


def _ensure_licensed(app: QApplication, license_service: LicenseService) -> bool:
    """Validate cached license; prompt for a new one if invalid. Return True if licensed."""
    license_service.load()
    status = LicenseStatus.NOT_ACTIVATED
    if license_service.is_cached():
        status, _ = license_service.revalidate()
    if status == LicenseStatus.VALID:
        return True

    prefilled = license_service.cached_key() if license_service.is_cached() else ""
    dlg = LicenseDialog(
        license_service,
        parent=None,
        prefilled_key=prefilled,
        allow_close_without_activation=False,
    )
    return dlg.exec() == QDialog.DialogCode.Accepted


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

    license_service = LicenseService(get_app_data_root())
    if not _ensure_licensed(app, license_service):
        return 0

    win = MainWindow(license_service=license_service)
    if not icon.isNull():
        win.setWindowIcon(icon)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
