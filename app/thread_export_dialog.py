"""
Thread export dialog (combined criteria + export configuration).

Used when exporting one or more threads from the Threads view. Collects optional
filters (body, date range, 24-hour chunking), output settings, and runs export
with each thread in its own folder inside the output ZIP.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QDate, Qt, QSize
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QCalendarWidget,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.export_dialog import ExportWorker
from app.rsmf_export import ThreadExportSpec
from app.search_logic import expand_results_to_full_threads, run_search
from app.style import icon as load_icon


class ThreadExportDialog(QDialog):
    """Export one or more threads to RSMF with shared optional filters."""

    def __init__(
        self,
        threads: List[ThreadExportSpec],
        attachment_base: Optional[Path],
        custodian: str = "",
        timezone_name: str = "",
        parent=None,
        backup_path: Optional[str] = None,
        passphrase: Optional[str] = None,
    ):
        super().__init__(parent)
        self._threads = list(threads)
        self._attachment_base = attachment_base
        self._custodian = custodian or ""
        self._timezone_name = timezone_name or ""
        self._backup_path = backup_path
        self._passphrase = passphrase
        self._worker: Optional[ExportWorker] = None
        self._progress: Optional[QProgressDialog] = None

        count = len(self._threads)
        self.setWindowTitle("Export threads to RSMF" if count > 1 else "Export thread to RSMF")
        self.setMinimumWidth(520)
        self.setWindowFlag(Qt.WindowType.MSWindowsFixedSizeDialogHint, True)
        layout = QVBoxLayout(self)

        if count == 1:
            label = self._threads[0].get("label") or "Thread"
            header = QLabel(f"Export thread: {label}")
        else:
            header = QLabel(f"Export {count} threads (each to its own folder in the ZIP)")
        header.setWordWrap(True)
        layout.addWidget(header)

        layout.addWidget(QLabel("Export location:"))
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
        layout.addLayout(loc_row)

        export_form = QFormLayout()
        self._version_combo = QComboBox()
        self._version_combo.addItem("RSMF 1", "1.0.0")
        self._version_combo.addItem("RSMF 2", "2.0.0")
        self._version_combo.setCurrentIndex(0)
        export_form.addRow("RSMF version:", self._version_combo)
        layout.addLayout(export_form)

        layout.addWidget(QLabel("Filters (optional, applied to each thread):"))
        filter_form = QFormLayout()

        self._body_edit = QLineEdit()
        self._body_edit.setPlaceholderText("Message body contains... (optional)")
        filter_form.addRow("Body:", self._body_edit)

        self._sentinel = QDate(1900, 1, 1)

        def _configure_date_edit(w: QDateEdit) -> None:
            w.setCalendarPopup(False)
            w.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            w.setDisplayFormat("yyyy-MM-dd")
            w.setMinimumDate(self._sentinel)
            w.setSpecialValueText("\u2014")
            w.setDate(self._sentinel)

        date_row = QHBoxLayout()

        self._date_from_edit = QDateEdit()
        _configure_date_edit(self._date_from_edit)
        self._date_cal_from = self._make_calendar_btn(self._date_from_edit)
        self._date_clear_from = self._make_clear_btn(self._date_from_edit, "Clear start date (no lower bound)")

        self._date_to_edit = QDateEdit()
        _configure_date_edit(self._date_to_edit)
        self._date_cal_to = self._make_calendar_btn(self._date_to_edit)
        self._date_clear_to = self._make_clear_btn(self._date_to_edit, "Clear end date (no upper bound)")

        date_row.addWidget(QLabel("From:"))
        date_row.addWidget(self._date_from_edit)
        date_row.addWidget(self._date_cal_from)
        date_row.addWidget(self._date_clear_from)
        date_row.addWidget(QLabel("To:"))
        date_row.addWidget(self._date_to_edit)
        date_row.addWidget(self._date_cal_to)
        date_row.addWidget(self._date_clear_to)
        date_row.addStretch()
        filter_form.addRow("Date range:", date_row)

        self._chunk_24h_cb = QCheckBox("Group results by 24-hour chunks (midnight to midnight)")
        self._chunk_24h_cb.setChecked(False)
        filter_form.addRow("", self._chunk_24h_cb)

        layout.addLayout(filter_form)

        all_msgs = [m for t in self._threads for m in (t.get("messages") or [])]
        layout.addWidget(QLabel("Include in export:"))
        self._include_is_deleted_cb = QCheckBox("Is Deleted")
        has_deleted = any(m.get("is_deleted") for m in all_msgs)
        self._include_is_deleted_cb.setChecked(has_deleted)
        layout.addWidget(self._include_is_deleted_cb)
        self._include_attachments_cb = QCheckBox("Attachments inside RSMF")
        self._include_attachments_cb.setChecked(True)
        layout.addWidget(self._include_attachments_cb)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        export_btn = QPushButton("  Export\u2026")
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

        self.adjustSize()
        self.setFixedSize(self.size())

    def _make_calendar_btn(self, target: QDateEdit) -> QPushButton:
        btn = QPushButton()
        btn.setProperty("class", "icon-btn")
        btn.setFixedSize(28, 28)
        btn.setIcon(load_icon("calendar"))
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip("Pick date")
        btn.clicked.connect(lambda: self._open_calendar_popup(target, btn))
        return btn

    def _make_clear_btn(self, target: QDateEdit, tooltip: str) -> QPushButton:
        btn = QPushButton("\u00d7")
        btn.setProperty("class", "icon-btn")
        btn.setFixedSize(28, 28)
        btn.setToolTip(tooltip)
        btn.clicked.connect(lambda: self._clear_date_edit(target))
        return btn

    def _open_calendar_popup(self, date_edit: QDateEdit, anchor: QWidget) -> None:
        cal = QCalendarWidget(self)
        cal.setWindowFlags(Qt.WindowType.Popup)
        cal.setGridVisible(True)
        current = date_edit.date()
        if current == date_edit.minimumDate():
            cal.setSelectedDate(QDate.currentDate())
        else:
            cal.setSelectedDate(current)

        def on_clicked(d: QDate) -> None:
            date_edit.setDate(d)
            cal.close()

        cal.clicked.connect(on_clicked)
        cal.move(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        cal.show()

    def _clear_date_edit(self, w: QDateEdit) -> None:
        w.setDate(w.minimumDate())

    def _date_to_ymd(self, w: QDateEdit) -> str:
        d = w.date()
        if d == w.minimumDate():
            return ""
        return d.toString("yyyy-MM-dd")

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select export folder")
        if path:
            self._path_edit.setText(path)

    def _prepare_thread_messages(self, thread: ThreadExportSpec) -> List[dict]:
        chat_messages = thread.get("messages") or []
        label = thread.get("label") or "Thread"
        body_filter = self._body_edit.text().strip()
        date_from = self._date_to_ymd(self._date_from_edit)
        date_to = self._date_to_ymd(self._date_to_edit)
        chunk_24h = bool(self._chunk_24h_cb.isChecked())

        results = run_search(
            chat_messages,
            to_filter="",
            body_filter=body_filter,
            date_from=date_from,
            date_to=date_to,
            has_attachments="any",
            hash_filter="",
            chunk_24h=chunk_24h,
            timezone_name=self._timezone_name,
            search_name=f"Thread - {label}",
            search_sequence=0,
        )
        if not results:
            return []
        if chunk_24h:
            return expand_results_to_full_threads(
                results,
                chat_messages,
                timezone_name=self._timezone_name,
            )
        return results

    def _on_export(self) -> None:
        output_dir = Path(self._path_edit.text().strip()) if self._path_edit.text().strip() else None
        if not output_dir or not output_dir.exists():
            QMessageBox.warning(self, "Export RSMF", "Select a valid output folder.")
            return

        export_threads: List[ThreadExportSpec] = []
        chat_id_to_label: dict = {}
        for thread in self._threads:
            label = thread.get("label") or "Thread"
            prepared = self._prepare_thread_messages(thread)
            if not prepared:
                continue
            export_threads.append({"label": label, "messages": prepared})
            cid = prepared[0].get("chat_id")
            if cid is not None:
                chat_id_to_label[int(cid)] = label

        if not export_threads:
            QMessageBox.information(
                self,
                "Export RSMF",
                "No messages matched the given filters in the selected thread(s).",
            )
            return

        all_export_msgs = [m for t in export_threads for m in t["messages"]]
        rsmf_version = self._version_combo.currentData() or "1.0.0"
        if len(export_threads) == 1:
            label_part = (export_threads[0]["label"] or "thread").replace(" ", "_")
        else:
            label_part = f"{len(export_threads)}_threads"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_name = f"threads_{label_part}_{timestamp}"

        self._worker = ExportWorker(
            all_export_msgs,
            self._attachment_base,
            output_dir,
            self._custodian,
            rsmf_version=rsmf_version,
            include_is_deleted=self._include_is_deleted_cb.isChecked(),
            include_attachments=self._include_attachments_cb.isChecked(),
            zip_name=zip_name,
            backup_path=self._backup_path,
            passphrase=self._passphrase,
            chat_id_to_label=chat_id_to_label,
        )
        self._progress = QProgressDialog("Exporting RSMF files...", "Cancel", 0, 100, self)
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(0)

        def _on_progress(pct: float, label: str) -> None:
            if self._progress is not None:
                self._progress.setValue(int(pct))
                self._progress.setLabelText(label)

        self._worker.progress.connect(_on_progress)
        self._worker.export_finished.connect(self._on_export_finished)
        self._worker.error.connect(self._on_export_error)
        self._progress.canceled.connect(self._worker.terminate)
        self._worker.start()
        self._progress.exec()

    def _on_export_finished(self, paths: List[Path]) -> None:
        if self._progress is not None:
            self._progress.close()
        w = self._worker
        self._worker = None
        if w:
            w.deleteLater()
        if paths:
            QMessageBox.information(
                self,
                "Export complete",
                f"Wrote {paths[0].name} to the selected folder.",
            )
        else:
            QMessageBox.warning(
                self,
                "Export complete",
                "Export finished but no RSMF archive was produced.",
            )
        self.accept()

    def _on_export_error(self, err: str) -> None:
        if self._progress is not None:
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
