"""
Search dialog: filter by Recipient, Body, date range; optional 24h chunking.
Saved criteria always use attachments=any and no hash filter (UI removed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QLabel,
    QCheckBox,
    QComboBox,
    QMessageBox,
    QDateEdit,
    QCalendarWidget,
    QAbstractSpinBox,
    QWidget,
    QListWidget,
    QListWidgetItem,
)
from PyQt6.QtCore import QDate, Qt, QSize, pyqtSignal

from app.saved_searches import (
    LIBRARY_ROOT_FOLDER_ID,
    LIBRARY_ROOT_FOLDER_NAME,
    add_saved_search,
    load_folders,
    update_saved_search,
    walk_folders_depth_first,
)
from app.style import icon as load_icon


def _criteria_from_form(
    to_filter: str,
    body_filter: str,
    date_from: str,
    date_to: str,
    chunk_24h: bool,
    search_name: str,
    thread_ids: List[int],
) -> Dict[str, Any]:
    return {
        "to_filter": to_filter.strip(),
        "body_filter": body_filter.strip(),
        "date_from": date_from.strip(),
        "date_to": date_to.strip(),
        "has_attachments": "any",
        "hash_filter": "",
        "chunk_24h": bool(chunk_24h),
        "thread_ids": thread_ids,
        "search_name": (search_name or "Search results").strip() or "Search results",
    }


class SearchDialog(QDialog):
    """Dialog to define search criteria, save, and run (results show in Search Messages tab)."""

    run_search_requested = pyqtSignal(dict)  # criteria dict

    def __init__(
        self,
        app_data_root: Path,
        case_id: str,
        parent=None,
        default_folder_id: Optional[str] = None,
        library_display_name: Optional[str] = None,
        edit_search: Optional[Dict[str, Any]] = None,
        chats: Optional[List[dict]] = None,
    ):
        super().__init__(parent)
        self._app_data_root = Path(app_data_root)
        self._case_id = case_id
        self._edit_search_id = (edit_search or {}).get("id")
        self._chats = list(chats or [])
        self.setWindowTitle("Edit search" if self._edit_search_id else "Search messages")
        self.setMinimumWidth(480)
        self.setWindowFlag(Qt.WindowType.MSWindowsFixedSizeDialogHint, True)
        layout = QVBoxLayout(self)

        form = QFormLayout()

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Name for saving this search")
        form.addRow("Search name:", self._name_edit)

        self._folder_combo = QComboBox()
        folders = load_folders(
            self._app_data_root,
            self._case_id,
            library_display_name=library_display_name,
        )
        select_index = 0
        target_id = (edit_search or {}).get("folder_id") or default_folder_id or LIBRARY_ROOT_FOLDER_ID
        for i, (folder, depth) in enumerate(walk_folders_depth_first(folders)):
            label = ("    " * depth) + (folder.get("name") or "Unnamed folder")
            self._folder_combo.addItem(label, folder.get("id"))
            if folder.get("id") == target_id:
                select_index = i
        if self._folder_combo.count() == 0:
            # Defensive fallback: library root should always exist after load_folders,
            # but guarantee at least one selectable entry so the combo isn't empty.
            root_label = (library_display_name or "").strip() or LIBRARY_ROOT_FOLDER_NAME
            self._folder_combo.addItem(root_label, LIBRARY_ROOT_FOLDER_ID)
        self._folder_combo.setCurrentIndex(select_index)
        form.addRow("Folder:", self._folder_combo)

        self._to_edit = QLineEdit()
        self._to_edit.setPlaceholderText(
            "Contact name or phone number (comma-separated for multiple)"
        )
        self._to_edit.setToolTip(
            "Comma-separated names or numbers. Only conversations where all listed participants "
            "appear together are included."
        )
        form.addRow("Recipient:", self._to_edit)

        self._threads_list = QListWidget()
        self._threads_list.setMaximumHeight(140)
        self._threads_list.setToolTip(
            "Optional: limit search to selected threads. Leave all unchecked to search every thread."
        )
        for ch in self._chats:
            cid = ch.get("rowid")
            label = ch.get("label") or f"Chat {cid}"
            count = ch.get("count", 0)
            item = QListWidgetItem(f"{label} ({count})")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.ItemDataRole.UserRole, cid)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._threads_list.addItem(item)
        form.addRow("Threads:", self._threads_list)

        self._body_edit = QLineEdit()
        self._body_edit.setPlaceholderText("Message body contains...")
        form.addRow("Body:", self._body_edit)

        date_row = QHBoxLayout()
        _sentinel = QDate(1900, 1, 1)

        def _configure_date_edit(w: QDateEdit) -> None:
            w.setCalendarPopup(False)
            w.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            w.setDisplayFormat("yyyy-MM-dd")
            w.setMinimumDate(_sentinel)
            w.setSpecialValueText("—")
            w.setDate(_sentinel)

        self._date_from_edit = QDateEdit()
        _configure_date_edit(self._date_from_edit)

        self._date_cal_from = QPushButton()
        self._date_cal_from.setProperty("class", "icon-btn")
        self._date_cal_from.setFixedSize(28, 28)
        self._date_cal_from.setIcon(load_icon("calendar"))
        self._date_cal_from.setIconSize(QSize(16, 16))
        self._date_cal_from.setToolTip("Pick date")
        self._date_cal_from.clicked.connect(
            lambda: self._open_calendar_popup(self._date_from_edit, self._date_cal_from)
        )

        self._date_clear_from = QPushButton("\u00d7")
        self._date_clear_from.setProperty("class", "icon-btn")
        self._date_clear_from.setFixedSize(28, 28)
        self._date_clear_from.setToolTip("Clear start date (no lower bound)")
        self._date_clear_from.clicked.connect(lambda: self._clear_date_edit(self._date_from_edit))

        self._date_to_edit = QDateEdit()
        _configure_date_edit(self._date_to_edit)

        self._date_cal_to = QPushButton()
        self._date_cal_to.setProperty("class", "icon-btn")
        self._date_cal_to.setFixedSize(28, 28)
        self._date_cal_to.setIcon(load_icon("calendar"))
        self._date_cal_to.setIconSize(QSize(16, 16))
        self._date_cal_to.setToolTip("Pick date")
        self._date_cal_to.clicked.connect(
            lambda: self._open_calendar_popup(self._date_to_edit, self._date_cal_to)
        )

        self._date_clear_to = QPushButton("\u00d7")
        self._date_clear_to.setProperty("class", "icon-btn")
        self._date_clear_to.setFixedSize(28, 28)
        self._date_clear_to.setToolTip("Clear end date (no upper bound)")
        self._date_clear_to.clicked.connect(lambda: self._clear_date_edit(self._date_to_edit))

        date_row.addWidget(QLabel("From:"))
        date_row.addWidget(self._date_from_edit)
        date_row.addWidget(self._date_cal_from)
        date_row.addWidget(self._date_clear_from)
        date_row.addWidget(QLabel("To:"))
        date_row.addWidget(self._date_to_edit)
        date_row.addWidget(self._date_cal_to)
        date_row.addWidget(self._date_clear_to)
        date_row.addStretch()
        form.addRow("Date range:", date_row)

        self._chunk_24h_cb = QCheckBox("Group results by 24-hour chunks (midnight to midnight)")
        self._chunk_24h_cb.setChecked(False)
        form.addRow("", self._chunk_24h_cb)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_run_btn = QPushButton("Save && Search")
        save_run_btn.setDefault(True)
        save_run_btn.clicked.connect(self._on_save_and_search)
        close_btn = QPushButton("Close")
        close_btn.setProperty("class", "secondary")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_run_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self.adjustSize()
        self.setFixedSize(self.size())

        if edit_search:
            self._name_edit.setText(edit_search.get("name") or "")
            self._to_edit.setText(edit_search.get("to_filter") or "")
            self._body_edit.setText(edit_search.get("body_filter") or "")
            self._set_date_edit_from_ymd(self._date_from_edit, edit_search.get("date_from") or "")
            self._set_date_edit_from_ymd(self._date_to_edit, edit_search.get("date_to") or "")
            self._chunk_24h_cb.setChecked(bool(edit_search.get("chunk_24h")))
            selected_ids = {int(t) for t in (edit_search.get("thread_ids") or []) if t is not None}
            for i in range(self._threads_list.count()):
                item = self._threads_list.item(i)
                cid = item.data(Qt.ItemDataRole.UserRole)
                if cid is not None and int(cid) in selected_ids:
                    item.setCheckState(Qt.CheckState.Checked)

    def _set_date_edit_from_ymd(self, w: QDateEdit, ymd: str) -> None:
        text = (ymd or "").strip()
        if not text:
            w.setDate(w.minimumDate())
            return
        parsed = QDate.fromString(text, "yyyy-MM-dd")
        if parsed.isValid():
            w.setDate(parsed)
        else:
            w.setDate(w.minimumDate())

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
        """Use minimum date as sentinel for 'unset' (shows special value text)."""
        w.setDate(w.minimumDate())

    def _date_to_ymd(self, w: QDateEdit) -> str:
        d = w.date()
        if d == w.minimumDate():
            return ""
        return d.toString("yyyy-MM-dd")

    def _selected_thread_ids(self) -> List[int]:
        ids: List[int] = []
        for i in range(self._threads_list.count()):
            item = self._threads_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                cid = item.data(Qt.ItemDataRole.UserRole)
                if cid is not None:
                    ids.append(int(cid))
        return ids

    def _get_criteria(self) -> Dict[str, Any]:
        return _criteria_from_form(
            self._to_edit.text(),
            self._body_edit.text(),
            self._date_to_ymd(self._date_from_edit),
            self._date_to_ymd(self._date_to_edit),
            self._chunk_24h_cb.isChecked(),
            self._name_edit.text(),
            self._selected_thread_ids(),
        )

    def _on_save_and_search(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Save search", "Enter a name for this search.")
            return
        criteria = self._get_criteria()
        folder_id = self._folder_combo.currentData() or LIBRARY_ROOT_FOLDER_ID
        if self._edit_search_id:
            item = update_saved_search(
                self._app_data_root,
                self._case_id,
                self._edit_search_id,
                name=name,
                to_filter=criteria["to_filter"],
                body_filter=criteria["body_filter"],
                date_from=criteria["date_from"],
                date_to=criteria["date_to"],
                has_attachments=criteria["has_attachments"],
                hash_filter=criteria["hash_filter"],
                chunk_24h=criteria["chunk_24h"],
                thread_ids=criteria["thread_ids"],
                folder_id=folder_id,
            )
            if item is None:
                QMessageBox.warning(self, "Edit search", "Could not update this search.")
                return
        else:
            item = add_saved_search(
                self._app_data_root,
                self._case_id,
                name=name,
                to_filter=criteria["to_filter"],
                body_filter=criteria["body_filter"],
                date_from=criteria["date_from"],
                date_to=criteria["date_to"],
                has_attachments=criteria["has_attachments"],
                hash_filter=criteria["hash_filter"],
                chunk_24h=criteria["chunk_24h"],
                thread_ids=criteria["thread_ids"],
                folder_id=folder_id,
            )
        run_criteria = {
            "to_filter": item["to_filter"],
            "body_filter": item["body_filter"],
            "date_from": item["date_from"],
            "date_to": item["date_to"],
            "has_attachments": item["has_attachments"],
            "hash_filter": item["hash_filter"],
            "chunk_24h": item["chunk_24h"],
            "thread_ids": item.get("thread_ids") or [],
            "search_name": item["name"],
            "sequence": item["sequence"],
            "search_id": item["id"],
        }
        self.run_search_requested.emit(run_criteria)
