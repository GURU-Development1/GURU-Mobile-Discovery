"""
Preview dialog listing threads selected for multi-thread RSMF export.
"""

from __future__ import annotations

from typing import List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)


class ThreadExportPreviewDialog(QDialog):
    """Read-only preview of threads to export before opening export settings."""

    def __init__(
        self,
        threads: List[dict],
        parent=None,
    ):
        super().__init__(parent)
        self._threads = threads
        self.setWindowTitle("Export threads — preview")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(
                f"{len(threads)} thread(s) selected for export. "
                "Each thread will be written to its own folder inside the ZIP archive."
            )
        )

        self._list = QListWidget()
        for t in threads:
            label = t.get("label") or "Thread"
            count = len(t.get("messages") or [])
            self._list.addItem(f"{label} ({count} messages)")
        layout.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        continue_btn = QPushButton("Continue")
        continue_btn.setDefault(True)
        continue_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("class", "secondary")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(continue_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
