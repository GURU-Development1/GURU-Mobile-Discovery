"""
Search dialog: filter by Recipient, Body, date range; optional 24h chunking.
Saved criteria always use attachments=any and no hash filter (UI removed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QLabel,
    QCheckBox,
    QMessageBox,
    QDateEdit,
)
from PyQt6.QtCore import QDate, pyqtSignal

from app.saved_searches import add_saved_search


def _criteria_from_form(
    to_filter: str,
    body_filter: str,
    date_from: str,
    date_to: str,
    chunk_24h: bool,
    search_name: str,
) -> Dict[str, Any]:
    return {
        "to_filter": to_filter.strip(),
        "body_filter": body_filter.strip(),
        "date_from": date_from.strip(),
        "date_to": date_to.strip(),
        "has_attachments": "any",
        "hash_filter": "",
        "chunk_24h": bool(chunk_24h),
        "search_name": (search_name or "Search results").strip() or "Search results",
    }


class SearchDialog(QDialog):
    """Dialog to define search criteria, save, and run (results show in Search Messages tab)."""

    run_search_requested = pyqtSignal(dict)  # criteria dict

    def __init__(self, app_data_root: Path, parent=None):
        super().__init__(parent)
        self._app_data_root = Path(app_data_root)
        self.setWindowTitle("Search messages")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)

        form = QFormLayout()

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Name for saving this search")
        form.addRow("Search name:", self._name_edit)

        self._to_edit = QLineEdit()
        self._to_edit.setPlaceholderText(
            "Contact name or phone number (comma-separated for multiple)"
        )
        self._to_edit.setToolTip(
            "Comma-separated names or numbers. Only conversations where all listed participants "
            "appear together are included."
        )
        form.addRow("Recipient:", self._to_edit)

        self._body_edit = QLineEdit()
        self._body_edit.setPlaceholderText("Message body contains...")
        form.addRow("Body:", self._body_edit)

        date_row = QHBoxLayout()
        _sentinel = QDate(1900, 1, 1)
        self._date_from_edit = QDateEdit()
        self._date_from_edit.setCalendarPopup(True)
        self._date_from_edit.setDisplayFormat("yyyy-MM-dd")
        self._date_from_edit.setMinimumDate(_sentinel)
        self._date_from_edit.setSpecialValueText("—")
        self._date_from_edit.setDate(_sentinel)
        self._date_clear_from = QPushButton("Clear")
        self._date_clear_from.setToolTip("Clear start date (no lower bound)")
        self._date_clear_from.setFixedWidth(52)
        self._date_clear_from.clicked.connect(lambda: self._clear_date_edit(self._date_from_edit))

        self._date_to_edit = QDateEdit()
        self._date_to_edit.setCalendarPopup(True)
        self._date_to_edit.setDisplayFormat("yyyy-MM-dd")
        self._date_to_edit.setMinimumDate(_sentinel)
        self._date_to_edit.setSpecialValueText("—")
        self._date_to_edit.setDate(_sentinel)
        self._date_clear_to = QPushButton("Clear")
        self._date_clear_to.setToolTip("Clear end date (no upper bound)")
        self._date_clear_to.setFixedWidth(52)
        self._date_clear_to.clicked.connect(lambda: self._clear_date_edit(self._date_to_edit))

        date_row.addWidget(QLabel("From:"))
        date_row.addWidget(self._date_from_edit)
        date_row.addWidget(self._date_clear_from)
        date_row.addWidget(QLabel("To:"))
        date_row.addWidget(self._date_to_edit)
        date_row.addWidget(self._date_clear_to)
        date_row.addStretch()
        form.addRow("Date range:", date_row)

        self._chunk_24h_cb = QCheckBox("Group results by 24-hour chunks (midnight to midnight)")
        self._chunk_24h_cb.setChecked(False)
        form.addRow("", self._chunk_24h_cb)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        save_run_btn = QPushButton("Save & Search")
        save_run_btn.setDefault(True)
        save_run_btn.clicked.connect(self._on_save_and_search)
        btn_row.addWidget(save_run_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _clear_date_edit(self, w: QDateEdit) -> None:
        """Use minimum date as sentinel for 'unset' (shows special value text)."""
        w.setDate(w.minimumDate())

    def _date_to_ymd(self, w: QDateEdit) -> str:
        d = w.date()
        if d == w.minimumDate():
            return ""
        return d.toString("yyyy-MM-dd")

    def _get_criteria(self) -> Dict[str, Any]:
        return _criteria_from_form(
            self._to_edit.text(),
            self._body_edit.text(),
            self._date_to_ymd(self._date_from_edit),
            self._date_to_ymd(self._date_to_edit),
            self._chunk_24h_cb.isChecked(),
            self._name_edit.text(),
        )

    def _on_save_and_search(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Save search", "Enter a name for this search.")
            return
        criteria = self._get_criteria()
        item = add_saved_search(
            self._app_data_root,
            name=name,
            to_filter=criteria["to_filter"],
            body_filter=criteria["body_filter"],
            date_from=criteria["date_from"],
            date_to=criteria["date_to"],
            has_attachments=criteria["has_attachments"],
            hash_filter=criteria["hash_filter"],
            chunk_24h=criteria["chunk_24h"],
        )
        run_criteria = {
            "to_filter": item["to_filter"],
            "body_filter": item["body_filter"],
            "date_from": item["date_from"],
            "date_to": item["date_to"],
            "has_attachments": item["has_attachments"],
            "hash_filter": item["hash_filter"],
            "chunk_24h": item["chunk_24h"],
            "search_name": item["name"],
            "sequence": item["sequence"],
            "search_id": item["id"],
        }
        self.run_search_requested.emit(run_criteria)
