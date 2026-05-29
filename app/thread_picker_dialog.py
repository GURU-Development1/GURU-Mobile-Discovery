"""Dual-pane thread picker for saved search criteria."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


def _thread_label(chat: dict) -> str:
    label = chat.get("label") or f"Chat {chat.get('rowid')}"
    count = chat.get("count", 0)
    return f"{label} ({count})"


def _sort_chats(chats: List[dict]) -> List[dict]:
    def key(ch: dict) -> tuple:
        label = (ch.get("label") or "").lower()
        count = int(ch.get("count") or 0)
        rowid = int(ch.get("rowid") or 0)
        return (label, -count, rowid)

    return sorted(chats, key=key)


class ThreadPickerDialog(QDialog):
    """Two-pane thread selector. Left = available, right = selected."""

    def __init__(
        self,
        chats: List[dict],
        selected_ids: Optional[List[int]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Select threads")
        self.setMinimumWidth(560)
        self.setMinimumHeight(360)

        self._chats_by_id: Dict[int, dict] = {}
        for ch in chats:
            rowid = ch.get("rowid")
            if rowid is not None:
                self._chats_by_id[int(rowid)] = ch

        selected_set = {int(t) for t in (selected_ids or []) if t is not None}
        self._selected_ids: List[int] = [
            int(t) for t in (selected_ids or []) if t is not None and int(t) in self._chats_by_id
        ]
        available_ids = [cid for cid in self._chats_by_id if cid not in selected_set]

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select threads to include in this saved search"))

        panes = QHBoxLayout()

        self._available_list = QListWidget()
        self._available_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._available_list.itemDoubleClicked.connect(self._move_available_to_selected)

        move_col = QVBoxLayout()
        move_col.addStretch()
        add_btn = QPushButton(">")
        add_btn.setFixedWidth(40)
        add_btn.clicked.connect(self._move_available_to_selected)
        remove_btn = QPushButton("<")
        remove_btn.setFixedWidth(40)
        remove_btn.clicked.connect(self._move_selected_to_available)
        move_col.addWidget(add_btn)
        move_col.addWidget(remove_btn)
        move_col.addStretch()

        self._selected_list = QListWidget()
        self._selected_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._selected_list.itemDoubleClicked.connect(self._move_selected_to_available)

        panes.addWidget(self._available_list, stretch=1)
        panes.addLayout(move_col)
        panes.addWidget(self._selected_list, stretch=1)
        layout.addLayout(panes, stretch=1)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("class", "secondary")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        self._populate_list(self._available_list, available_ids)
        self._populate_list(self._selected_list, self._selected_ids)

    def _populate_list(self, widget: QListWidget, ids: List[int]) -> None:
        widget.clear()
        chats = _sort_chats([self._chats_by_id[cid] for cid in ids if cid in self._chats_by_id])
        for ch in chats:
            cid = int(ch["rowid"])
            item = QListWidgetItem(_thread_label(ch))
            item.setData(Qt.ItemDataRole.UserRole, cid)
            widget.addItem(item)

    def _selected_ids_from_list(self, widget: QListWidget) -> List[int]:
        ids: List[int] = []
        for i in range(widget.count()):
            item = widget.item(i)
            cid = item.data(Qt.ItemDataRole.UserRole)
            if cid is not None:
                ids.append(int(cid))
        return ids

    def _move_available_to_selected(self) -> None:
        moving = self._selected_ids_from_list(self._available_list)
        if not moving:
            return
        moving_set = set(moving)
        self._selected_ids = sorted(set(self._selected_ids) | moving_set)
        available = [cid for cid in self._chats_by_id if cid not in self._selected_ids]
        self._populate_list(self._available_list, available)
        self._populate_list(self._selected_list, self._selected_ids)

    def _move_selected_to_available(self) -> None:
        moving = self._selected_ids_from_list(self._selected_list)
        if not moving:
            return
        moving_set = set(moving)
        self._selected_ids = [cid for cid in self._selected_ids if cid not in moving_set]
        available = [cid for cid in self._chats_by_id if cid not in self._selected_ids]
        self._populate_list(self._available_list, available)
        self._populate_list(self._selected_list, self._selected_ids)

    def selected_thread_ids(self) -> List[int]:
        return list(self._selected_ids)
