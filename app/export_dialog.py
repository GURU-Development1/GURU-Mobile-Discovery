"""
Export RSMF dialog: export location, custodian, RSMF version, and optional field toggles.
Exports search results to Relativity Short Message Format (.rsmf) files.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QComboBox,
    QLabel,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QProgressDialog,
)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal

from app.rsmf_export import export_search_results_to_rsmf
from app.style import icon as load_icon


class ExportWorker(QThread):
    """Background thread for RSMF export."""

    progress = pyqtSignal(float, str)
    # Do not name this "finished" — that shadows QThread.finished and breaks thread lifecycle.
    export_finished = pyqtSignal(list)  # list of output paths
    error = pyqtSignal(str)

    def __init__(
        self,
        messages: List[dict],
        attachment_base: Optional[Path],
        output_dir: Path,
        custodian: str,
        rsmf_version: str = "1.0.0",
        include_control_number: bool = True,
        include_is_deleted: bool = True,
        include_attachments: bool = True,
    ):
        super().__init__()
        self._messages = messages
        self._attachment_base = attachment_base
        self._output_dir = output_dir
        self._custodian = custodian
        self._rsmf_version = rsmf_version
        self._include_control_number = include_control_number
        self._include_is_deleted = include_is_deleted
        self._include_attachments = include_attachments

    def run(self) -> None:
        try:
            def on_progress(pct: float, label: str) -> None:
                self.progress.emit(pct, label)
            paths = export_search_results_to_rsmf(
                self._messages,
                self._attachment_base,
                self._output_dir,
                custodian=self._custodian,
                rsmf_version=self._rsmf_version,
                include_control_number=self._include_control_number,
                include_is_deleted=self._include_is_deleted,
                include_attachments=self._include_attachments,
                progress_cb=on_progress,
            )
            self.export_finished.emit(paths)
        except Exception as e:
            self.error.emit(str(e))


class ExportRsmfDialog(QDialog):
    """Dialog to configure and run RSMF export for search results."""
    export_completed = pyqtSignal(list)  # output paths

    def __init__(
        self,
        messages: List[dict],
        attachment_base: Optional[Path],
        custodian: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._messages = messages
        self._attachment_base = attachment_base
        self._custodian = custodian
        self.setWindowTitle("Export RSMF")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        # Export location
        loc_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Select output folder...")
        loc_row.addWidget(self._path_edit)
        browse_btn = QPushButton("  Browse...")
        browse_btn.setIcon(load_icon("folder-open"))
        browse_btn.setIconSize(QSize(14, 14))
        browse_btn.setProperty("class", "secondary")
        browse_btn.clicked.connect(self._browse)
        loc_row.addWidget(browse_btn)
        layout.addWidget(QLabel("Export location:"))
        layout.addLayout(loc_row)

        # Custodian override and RSMF version
        form = QFormLayout()
        self._custodian_edit = QLineEdit()
        self._custodian_edit.setPlaceholderText("Backup custodian (e.g. device owner)")
        self._custodian_edit.setText(custodian)
        form.addRow("Custodian:", self._custodian_edit)
        self._version_combo = QComboBox()
        self._version_combo.addItem("RSMF 1", "1.0.0")
        self._version_combo.addItem("RSMF 2", "2.0.0")
        self._version_combo.setCurrentIndex(0)
        form.addRow("RSMF version:", self._version_combo)
        layout.addLayout(form)

        # Optional field toggles
        layout.addWidget(QLabel("Include in export:"))
        self._include_control_number_cb = QCheckBox("Control Number")
        self._include_control_number_cb.setChecked(True)
        layout.addWidget(self._include_control_number_cb)
        self._include_is_deleted_cb = QCheckBox("Is Deleted")
        has_deleted = any(m.get("is_deleted") for m in messages)
        self._include_is_deleted_cb.setChecked(has_deleted)
        layout.addWidget(self._include_is_deleted_cb)
        self._include_attachments_cb = QCheckBox("Attachments inside RSMF")
        self._include_attachments_cb.setChecked(True)
        layout.addWidget(self._include_attachments_cb)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        export_btn = QPushButton("  Export")
        export_btn.setIcon(load_icon("download"))
        export_btn.setIconSize(QSize(14, 14))
        export_btn.setDefault(True)
        export_btn.clicked.connect(self._on_export)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("class", "secondary")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(export_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select export folder")
        if path:
            self._path_edit.setText(path)

    def _on_export(self) -> None:
        output_dir = Path(self._path_edit.text().strip()) if self._path_edit.text().strip() else None
        if not output_dir or not output_dir.exists():
            QMessageBox.warning(
                self,
                "Export RSMF",
                "Select a valid output folder.",
            )
            return
        if not self._messages:
            QMessageBox.warning(
                self,
                "Export RSMF",
                "No messages to export.",
            )
            return

        custodian = self._custodian_edit.text().strip() or self._custodian
        rsmf_version = self._version_combo.currentData() or "1.0.0"
        self._worker = ExportWorker(
            self._messages,
            self._attachment_base,
            output_dir,
            custodian,
            rsmf_version=rsmf_version,
            include_control_number=self._include_control_number_cb.isChecked(),
            include_is_deleted=self._include_is_deleted_cb.isChecked(),
            include_attachments=self._include_attachments_cb.isChecked(),
        )
        self._progress = QProgressDialog("Exporting RSMF files...", "Cancel", 0, 100, self)
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(0)
        def _on_progress(pct: float, label: str) -> None:
            self._progress.setValue(int(pct))
            self._progress.setLabelText(label)
        self._worker.progress.connect(_on_progress)
        self._worker.export_finished.connect(self._on_export_finished)
        self._worker.error.connect(self._on_export_error)
        self._progress.canceled.connect(self._worker.terminate)
        self._worker.start()
        self._progress.exec()

    def _on_export_finished(self, paths: List[Path]) -> None:
        self._progress.close()
        w = self._worker
        self._worker = None
        if w:
            w.deleteLater()
        self.export_completed.emit(paths)
        QMessageBox.information(
            self,
            "Export complete",
            f"Exported {len(paths)} RSMF file(s) to the selected folder.",
        )
        self.accept()

    def _on_export_error(self, err: str) -> None:
        self._progress.close()
        w = self._worker
        self._worker = None
        if w:
            w.deleteLater()
        QMessageBox.critical(
            self,
            "Export failed",
            f"Export failed: {err}",
        )
