"""
License activation dialog. Modal; used both as a launch gate and as a "change key"
entry from the Help menu.
"""

from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from app.license_service import LicenseService, LicenseStatus
from app.style import logo_path as resolve_logo_path


class _ActivateWorker(QThread):
    finished_ok = pyqtSignal(object, str)  # (LicenseStatus, message)

    def __init__(self, service: LicenseService, key: str) -> None:
        super().__init__()
        self._service = service
        self._key = key

    def run(self) -> None:
        status, msg = self._service.activate(self._key)
        self.finished_ok.emit(status, msg)


class LicenseDialog(QDialog):
    """Modal activation dialog with key entry, threaded activation, and inline errors."""

    def __init__(
        self,
        service: LicenseService,
        parent=None,
        title: str = "Activate GURU Mobile Discovery",
        prefilled_key: str = "",
        allow_close_without_activation: bool = True,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._worker: Optional[_ActivateWorker] = None
        self._allow_close = allow_close_without_activation

        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setWindowFlag(Qt.WindowType.MSWindowsFixedSizeDialogHint, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 18)
        outer.setSpacing(14)

        logo_label = QLabel()
        lockup = resolve_logo_path("guru_logo_lockup")
        if lockup.is_file():
            pix = QPixmap(str(lockup))
            if not pix.isNull():
                pix = pix.scaled(
                    340, 110,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                logo_label.setPixmap(pix)
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(logo_label)

        intro = QLabel(
            "Enter your license key to activate this device. "
            "Each license can be active on one device at a time."
        )
        intro.setAlignment(Qt.AlignmentFlag.AlignCenter)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        key_label = QLabel("License key:")
        outer.addWidget(key_label)

        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("GMD-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX")
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._key_edit.setFont(mono)
        if prefilled_key:
            self._key_edit.setText(prefilled_key)
        outer.addWidget(self._key_edit)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)
        outer.addWidget(self._progress)

        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: #b3261e;")
        self._error_label.setVisible(False)
        outer.addWidget(self._error_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._activate_btn = QPushButton("Activate")
        self._activate_btn.setDefault(True)
        self._activate_btn.clicked.connect(self._on_activate_clicked)
        close_text = "Cancel" if allow_close_without_activation else "Quit"
        self._cancel_btn = QPushButton(close_text)
        self._cancel_btn.setProperty("class", "secondary")
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._activate_btn)
        btn_row.addWidget(self._cancel_btn)
        outer.addLayout(btn_row)

        self._key_edit.setFocus()

        self.adjustSize()
        self.setFixedSize(self.size())

    # ---------- handlers ----------

    def _on_activate_clicked(self) -> None:
        key = self._key_edit.text().strip()
        if not key:
            self._show_error("Enter a license key.")
            return
        self._set_busy(True)
        self._worker = _ActivateWorker(self._service, key)
        self._worker.finished_ok.connect(self._on_activate_result)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_activate_result(self, status: object, message: str) -> None:
        self._worker = None
        self._set_busy(False)
        if status == LicenseStatus.VALID:
            self.accept()
            return
        self._show_error(message or "Activation failed.")

    # ---------- helpers ----------

    def _set_busy(self, busy: bool) -> None:
        self._progress.setVisible(busy)
        self._activate_btn.setEnabled(not busy)
        self._key_edit.setEnabled(not busy)
        if busy:
            self._error_label.setVisible(False)

    def _show_error(self, text: str) -> None:
        self._error_label.setText(text)
        self._error_label.setVisible(True)

    # Disable close-via-X when the dialog gates launch, so the only escape is "Quit".
    def closeEvent(self, event):  # type: ignore[override]
        if not self._allow_close and self.result() != QDialog.DialogCode.Accepted:
            event.accept()
            self.reject()
        else:
            super().closeEvent(event)


def request_activation(
    service: LicenseService,
    parent=None,
    *,
    prefilled_key: str = "",
    launch_gate: bool = False,
) -> Tuple[bool, str]:
    """Convenience: open the dialog and return (success, last_key)."""
    dlg = LicenseDialog(
        service,
        parent=parent,
        prefilled_key=prefilled_key,
        allow_close_without_activation=not launch_gate,
    )
    ok = dlg.exec() == QDialog.DialogCode.Accepted
    return ok, dlg._key_edit.text().strip()
