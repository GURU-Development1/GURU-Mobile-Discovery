"""
Import backup dialog: path (browse), custodian, timezone dropdown, password.
Default timezone is the one detected from backup if any.
Remembers last browsed backup directory for the next time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QComboBox,
    QLabel,
    QFileDialog,
    QDialogButtonBox,
    QSpinBox,
    QCheckBox,
)
from PyQt6.QtCore import Qt, QSize

from app.paths import get_app_data_root
from app.style import icon as load_icon


def _app_data_root() -> Path:
    return get_app_data_root()


def _get_last_backup_browse_dir() -> Optional[str]:
    path = _app_data_root() / "last_backup_browse_dir.txt"
    if not path.exists():
        return None
    try:
        last = path.read_text(encoding="utf-8").strip()
        if last and Path(last).is_dir():
            return last
    except Exception:
        pass
    return None


def _set_last_backup_browse_dir(directory: str) -> None:
    if not directory or not Path(directory).is_dir():
        return
    try:
        path = _app_data_root() / "last_backup_browse_dir.txt"
        path.write_text(directory, encoding="utf-8")
    except Exception:
        pass


def _detect_timezone_from_backup(backup_path: str) -> Optional[str]:
    try:
        import plistlib
        plist_path = Path(backup_path) / "Info.plist"
        if not plist_path.exists():
            return None
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)
        tz = plist.get("Timezone") or plist.get("TimeZone") or plist.get("timezone")
        return tz if isinstance(tz, str) and tz else None
    except Exception:
        return None


def get_timezone_list() -> list[tuple[str, str]]:
    """Return list of (display_string, iana_id) for timezone combo. Display format: (UTC±HH:MM) Standard Name."""
    from datetime import datetime
    import zoneinfo
    # Common zones: (iana_id, display name without offset)
    common = [
        ("UTC", "UTC"),
        ("America/New_York", "Eastern Time (US & Canada)"),
        ("America/Chicago", "Central Time (US & Canada)"),
        ("America/Denver", "Mountain Time (US & Canada)"),
        ("America/Los_Angeles", "Pacific Time (US & Canada)"),
        ("America/Phoenix", "Arizona (US)"),
        ("America/Anchorage", "Alaska (US)"),
        ("Pacific/Honolulu", "Hawaiian Time (US)"),
        ("America/Toronto", "Eastern Time (Canada)"),
        ("America/Vancouver", "Pacific Time (Canada)"),
        ("Europe/London", "GMT (UK)"),
        ("Europe/Dublin", "Ireland"),
        ("Europe/Paris", "Central European Time"),
        ("Europe/Berlin", "Central Europe"),
        ("Europe/Amsterdam", "Netherlands"),
        ("Europe/Moscow", "Moscow Time"),
        ("Asia/Dubai", "Gulf Time (Dubai)"),
        ("Asia/Kolkata", "India Standard Time"),
        ("Asia/Singapore", "Singapore"),
        ("Asia/Tokyo", "Japan Standard Time"),
        ("Australia/Sydney", "Eastern Australia"),
        ("Australia/Perth", "Western Australia"),
        ("Pacific/Auckland", "New Zealand"),
    ]
    now = datetime.now(zoneinfo.ZoneInfo("UTC"))
    out: list[tuple[str, str]] = []
    for iana_id, name in common:
        try:
            tz = zoneinfo.ZoneInfo(iana_id)
            dt = now.astimezone(tz)
            utcoff = dt.utcoffset()
            if utcoff is None:
                offset_str = "UTC"
            else:
                total_sec = int(utcoff.total_seconds())
                sign = "+" if total_sec >= 0 else "-"
                h, r = divmod(abs(total_sec), 3600)
                m = r // 60
                offset_str = f"(UTC{sign}{h:02d}:{m:02d})"
            display = f"{offset_str} {name}"
            out.append((display, iana_id))
        except Exception:
            out.append((f"{iana_id}", iana_id))
    return out


class ImportBackupDialog(QDialog):
    def __init__(
        self,
        parent=None,
        default_path: str = "",
        detected_timezone: Optional[str] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Import iTunes Backup")
        layout = QVBoxLayout(self)

        form = QFormLayout()

        # Backup path + browse
        path_layout = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Select backup folder...")
        if default_path:
            self.path_edit.setText(default_path)
        path_layout.addWidget(self.path_edit)
        browse_btn = QPushButton("  Browse...")
        browse_btn.setIcon(load_icon("folder-open"))
        browse_btn.setIconSize(QSize(14, 14))
        browse_btn.setProperty("class", "secondary")
        browse_btn.clicked.connect(self._browse)
        path_layout.addWidget(browse_btn)
        form.addRow("Backup path:", path_layout)

        self.backup_name_edit = QLineEdit()
        self.backup_name_edit.setPlaceholderText("Display name for this backup in the case tree")
        form.addRow("Backup name:", self.backup_name_edit)

        self.custodian_edit = QLineEdit()
        self.custodian_edit.setPlaceholderText("Name to show as owner in messages")
        form.addRow("Custodian:", self.custodian_edit)

        self.timezone_combo = QComboBox()
        self.timezone_combo.setEditable(True)
        tz_list = get_timezone_list()
        for display, iana_id in tz_list:
            self.timezone_combo.addItem(display, iana_id)
        if detected_timezone:
            idx = self.timezone_combo.findData(detected_timezone)
            if idx >= 0:
                self.timezone_combo.setCurrentIndex(idx)
            else:
                self.timezone_combo.setCurrentIndex(0)
        else:
            self.timezone_combo.setCurrentIndex(0)
        form.addRow("Time zone:", self.timezone_combo)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText('Required if backup uses "Encrypt local backup" in iTunes/Finder')
        self.password_edit.setToolTip(
            "Enter the same password you set for encrypted iTunes/Finder backups. "
            "Leave empty only for unencrypted backups. Encrypted backups cannot be read without it."
        )
        form.addRow("Backup password:", self.password_edit)

        self.control_prefix_edit = QLineEdit()
        self.control_prefix_edit.setPlaceholderText("e.g. LEVAN")
        form.addRow("Control Number Prefix:", self.control_prefix_edit)

        self.control_padding_spin = QSpinBox()
        self.control_padding_spin.setRange(1, 12)
        self.control_padding_spin.setValue(6)
        self.control_padding_spin.setToolTip("Number of digits (e.g. 6 = 000001)")
        form.addRow("Control Number Padding:", self.control_padding_spin)

        self.populate_table_check = QCheckBox("Populate Table tab with all messages (full grid)")
        self.populate_table_check.setChecked(False)
        self.populate_table_check.setToolTip(
            "If unchecked, the main Table tab stays empty until you load it later "
            "(Threads and Search still work). Uncheck on large backups to open faster."
        )
        form.addRow("", self.populate_table_check)

        self.extract_attachments_check = QCheckBox("Extract and cache image attachments")
        self.extract_attachments_check.setChecked(False)
        self.extract_attachments_check.setToolTip(
            "If unchecked, attachment filenames appear in messages but images are not shown inline. "
            "You can extract later via File > Extract Attachments."
        )
        form.addRow("", self.extract_attachments_check)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _detect_timezone(self, backup_path: str) -> None:
        tz = _detect_timezone_from_backup(backup_path)
        if tz:
            idx = self.timezone_combo.findData(tz)
            if idx >= 0:
                self.timezone_combo.setCurrentIndex(idx)
            else:
                self.timezone_combo.setCurrentText(tz)

    def _browse(self) -> None:
        start = (
            self.path_edit.text().strip()
            or _get_last_backup_browse_dir()
            or str(Path.home())
        )
        path = QFileDialog.getExistingDirectory(self, "Select backup folder", start)
        if path:
            self.path_edit.setText(path)
            _set_last_backup_browse_dir(path)
            self._detect_timezone(path)

    def get_path(self) -> str:
        return self.path_edit.text().strip()

    def get_backup_name(self) -> str:
        return self.backup_name_edit.text().strip()

    def get_custodian(self) -> str:
        return self.custodian_edit.text().strip()

    def get_timezone(self) -> str:
        # Prefer stored IANA id (currentData) when user picked from list; else use typed text
        data = self.timezone_combo.currentData()
        if data and isinstance(data, str):
            return data.strip() or "UTC"
        text = self.timezone_combo.currentText().strip()
        return text or "UTC"

    def get_password(self) -> Optional[str]:
        p = self.password_edit.text()
        return p if p else None

    def get_control_prefix(self) -> str:
        return self.control_prefix_edit.text().strip()

    def get_control_padding(self) -> int:
        return self.control_padding_spin.value()

    def get_populate_table_tab(self) -> bool:
        return self.populate_table_check.isChecked()

    def get_extract_attachments(self) -> bool:
        return self.extract_attachments_check.isChecked()
