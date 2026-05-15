"""
Thread view and table view for messages. Data is pre-cached so switching is instant.
Images displayed inline (no filename); emojis as emojis; GIFs as stills.
"""

from __future__ import annotations

import zoneinfo
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyleFactory,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal, QRect
from PyQt6.QtGui import QPainter, QPixmap, QWheelEvent

from app.saved_searches import (
    LIBRARY_ROOT_FOLDER_ID,
    add_folder,
    delete_folder_cascade,
    delete_saved_search,
    descendant_search_count,
    is_library_root_folder_id,
    load_folders,
    load_saved_searches,
    move_folder,
    rename_folder,
    update_saved_search,
    walk_folders_depth_first,
)
from app.style import icon as load_icon
from app.thread_list import VirtualThreadView as ThreadView
from app.timezone_utils import get_tz_abbrev_for_timestamp


class TableWidgetWithShiftWheel(QTableWidget):
    """Shift + mouse wheel scrolls horizontally (common spreadsheet behavior)."""

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            bar = self.horizontalScrollBar()
            pd = event.pixelDelta()
            if pd.x() != 0 or pd.y() != 0:
                delta = pd.x() if pd.x() != 0 else pd.y()
            else:
                ad = event.angleDelta()
                delta = ad.y() if ad.y() != 0 else ad.x()
            bar.setValue(bar.value() - delta)
            event.accept()
            return
        super().wheelEvent(event)


class LightboxWidget(QWidget):
    """Full-area overlay: dark semi-transparent background, centered image. Click anywhere to close."""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 0.82);")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setStyleSheet("background: transparent;")
        self._img_label.setScaledContents(False)
        self._img_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._layout.addWidget(self._img_label, 0, Qt.AlignmentFlag.AlignCenter)
        self._current_pix = None

    def set_pixmap(self, pixmap: QPixmap) -> None:
        if pixmap is None or pixmap.isNull():
            self._img_label.clear()
            self._current_pix = None
            return
        self._current_pix = pixmap
        self._update_scaled_pixmap()

    def set_image_path(self, path: Path) -> None:
        """Load image from path and show in lightbox (full resolution)."""
        if path is None or not path.exists():
            self.set_pixmap(QPixmap())
            return
        pix = QPixmap(str(path))
        if pix.isNull():
            self.set_pixmap(QPixmap())
            return
        self.set_pixmap(pix)

    def _update_scaled_pixmap(self) -> None:
        if self._current_pix is None or self._current_pix.isNull():
            return
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        max_w = int(w * 0.85)
        max_h = int(h * 0.85)
        scaled = self._current_pix.scaled(
            max_w, max_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._img_label.setPixmap(scaled)
        self._img_label.setFixedSize(scaled.size())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scaled_pixmap()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


def _is_image_attachment(att: dict) -> bool:
    return att.get("is_image") or (att.get("local_path") or "").endswith(
        (".png", ".jpg", ".jpeg", ".gif", ".heic", ".bmp", ".webp")
    )


def _format_date_from_timestamp(ts: float, timezone_name: str = "") -> str:
    """Format Unix timestamp for display: m/dd/yyyy hh:mm:ss AM/PM (e.g. 1/11/2018 6:10:03 PM)."""
    if ts is None:
        return ""
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return ""
    # If value is huge, it may be Apple timestamp (ns or seconds since 2001) stored as date_timestamp by mistake
    if ts > 1e15:
        ts = _apple_date_to_unix(ts) or 0
        if ts <= 0:
            return ""
    try:
        if timezone_name and timezone_name.strip():
            tz = zoneinfo.ZoneInfo(timezone_name.strip())
        else:
            tz = zoneinfo.ZoneInfo("UTC")
        dt = datetime.fromtimestamp(ts, tz=tz)
        h12 = dt.hour % 12 or 12
        return f"{dt.month}/{dt.day:02d}/{dt.year} {h12}:{dt.minute:02d}:{dt.second:02d} {dt.strftime('%p')}"
    except Exception:
        try:
            dt = datetime.utcfromtimestamp(ts)
            h12 = dt.hour % 12 or 12
            return f"{dt.month}/{dt.day:02d}/{dt.year} {h12}:{dt.minute:02d}:{dt.second:02d} {dt.strftime('%p')}"
        except Exception:
            return ""


# Apple epoch offset for fallback when only "date" (Apple timestamp) is in cache
_APPLE_EPOCH_OFFSET = 978307200


def _apple_date_to_unix(apple_date) -> Optional[float]:
    """Convert Apple timestamp (seconds since 2001) or nanoseconds to Unix. Returns None if invalid."""
    if apple_date is None:
        return None
    try:
        val = float(apple_date)
        # Apple nanosecond timestamps are typically 1e17–1e18; seconds are 1e8–1e10
        if val > 1e15:
            val = val / 1_000_000_000.0
        return val + _APPLE_EPOCH_OFFSET
    except (TypeError, ValueError):
        return None


def _message_unix_timestamp(m: dict) -> Optional[float]:
    """Get Unix timestamp from a message dict (handles date_timestamp, date/Apple, and byte keys)."""
    ts = m.get("date_timestamp") if isinstance(m, dict) else None
    if ts is not None:
        try:
            f = float(ts)
            # Cached date_timestamp is sometimes wrongly the raw Apple timestamp (e.g. 5e17)
            if f <= 1e15:
                return f
        except (TypeError, ValueError):
            pass
    raw_date = m.get("date") if isinstance(m, dict) else None
    return _apple_date_to_unix(raw_date)


class FilterHeaderView(QHeaderView):
    """Header with sort (left) and filter (right) click zones; right edge reserved for resize."""

    RESIZE_GRIP = 6
    filter_clicked = pyqtSignal(int)
    sort_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._filter_zone = 24
        self._active_columns: set = set()
        self.setSectionsClickable(True)
        self.setHighlightSections(True)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def set_active_columns(self, cols: set) -> None:
        self._active_columns = set(cols)
        self.viewport().update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        pos = int(event.position().x())
        idx = self.logicalIndexAt(pos)
        if idx < 0:
            return super().mousePressEvent(event)
        x0 = self.sectionPosition(idx)
        rel = pos - x0
        sz = self.sectionSize(idx)
        grip = self.RESIZE_GRIP
        if rel >= sz - grip:
            return super().mousePressEvent(event)
        if rel >= sz - grip - self._filter_zone:
            self.filter_clicked.emit(idx)
            event.accept()
            return
        self.sort_clicked.emit(idx)
        event.accept()

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:
        super().paintSection(painter, rect, logicalIndex)
        grip = self.RESIZE_GRIP
        fr_w = max(12, self._filter_zone - 4)
        fr_left = rect.right() - grip - self._filter_zone + 2
        fr = QRect(fr_left, rect.top() + 2, fr_w, rect.height() - 4)
        painter.save()
        pal = self.palette()
        c = pal.color(pal.ColorGroup.Active, pal.ColorRole.Highlight) if logicalIndex in self._active_columns else pal.color(
            pal.ColorGroup.Active, pal.ColorRole.Mid
        )
        painter.setPen(c)
        painter.drawText(fr, Qt.AlignmentFlag.AlignCenter, "⏷")
        painter.restore()


class ColumnFilterDialog(QDialog):
    def __init__(self, column_title: str, initial: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Filter — {column_title}")
        self._edit = QLineEdit()
        self._edit.setText(initial)
        self._edit.setPlaceholderText("Filter… (substring, case-insensitive)")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Show rows where \"{column_title}\" contains:"))
        lay.addWidget(self._edit)
        clear_btn = QPushButton("Clear filter")
        clear_btn.clicked.connect(lambda: self._edit.clear())
        row = QHBoxLayout()
        row.addWidget(clear_btn)
        row.addStretch()
        lay.addLayout(row)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def text(self) -> str:
        return self._edit.text()


class TableView(QWidget):
    """Table of messages with column sort (3-state) and per-column filters."""

    _HEADER_LABELS = [
        "Message Sent Date",
        "From",
        "Recipient",
        "Conversation ID",
        "Message ID",
        "Control Number",
        "Is Deleted",
        "Attachments",
        "Body",
        "Hash",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._table = TableWidgetWithShiftWheel()
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table.setShowGrid(True)
        self._table.setSortingEnabled(False)

        hdr = FilterHeaderView(self._table)
        self._table.setHorizontalHeader(hdr)
        hdr.sort_clicked.connect(self._on_header_sort)
        hdr.filter_clicked.connect(self._on_header_filter)
        self._filter_header = hdr

        self._table.setColumnCount(len(self._HEADER_LABELS))
        self._table.setHorizontalHeaderLabels(list(self._HEADER_LABELS))
        self._apply_default_column_widths()

        layout.addWidget(self._table)
        self._columns = [
            "date_formatted", "display_name", "chat_display_name", "conversation_id",
            "message_id", "control_number", "is_deleted", "attachment_count", "message body", "hash",
        ]
        self._messages_original: List[dict] = []
        self._timezone_name = ""
        self._filters: List[str] = [""] * len(self._columns)
        self._sort_col: Optional[int] = None
        self._sort_cycle = 0  # 0=default, 1=asc, 2=desc

    def _apply_default_column_widths(self) -> None:
        hdr = self._table.horizontalHeader()
        widths = [180, 140, 200, 170, 80, 120, 70, 80, 420, 160]
        for i, w in enumerate(widths):
            if i < self._table.columnCount():
                hdr.resizeSection(i, w)

    def _row_strings_for_message(self, m: dict) -> List[str]:
        tz = self._timezone_name
        to_str = m.get("to_display") or m.get("chat_display_name") or m.get("chat_identifier") or ""
        att_count = len(m.get("attachments") or [])
        body = (m.get("text") or "").replace("\n", " ")[:200]
        date_display = (m.get("date_formatted") or "").strip()
        if not date_display:
            unix_ts = _message_unix_timestamp(m)
            if unix_ts is not None:
                date_display = _format_date_from_timestamp(unix_ts, tz)
        if not date_display:
            date_display = "(no date)"
        if tz and date_display != "(no date)":
            unix_ts = _message_unix_timestamp(m)
            if unix_ts is not None:
                abbrev = get_tz_abbrev_for_timestamp(unix_ts, tz)
                if abbrev:
                    date_display = date_display + f" ({abbrev})"
        conv_id = m.get("conversation_id") or ""
        msg_id = m.get("message_id") or ""
        ctrl_num = m.get("control_number") or ""
        is_del = "Yes" if m.get("is_deleted") else ""
        return [
            date_display,
            m.get("display_name") or "",
            to_str,
            conv_id,
            msg_id,
            ctrl_num,
            is_del,
            str(att_count),
            body,
            m.get("hash") or "",
        ]

    def _passes_filters(self, m: dict) -> bool:
        texts = self._row_strings_for_message(m)
        for col, filt in enumerate(self._filters):
            f = (filt or "").strip().lower()
            if not f:
                continue
            if f not in (texts[col] or "").lower():
                return False
        return True

    def _default_key(self, m: dict) -> tuple:
        return (m.get("date_timestamp") or 0, m.get("rowid") or 0)

    def _sort_key(self, m: dict, col: int):
        if col == 0:
            return (0, _message_unix_timestamp(m) or 0)
        if col == 7:
            return (0, len(m.get("attachments") or []))
        if col == 6:
            return (0, 1 if m.get("is_deleted") else 0)
        return (1, (self._row_strings_for_message(m)[col] or "").lower())

    def _apply_sort(self, msgs: List[dict]) -> List[dict]:
        if self._sort_cycle == 0 or self._sort_col is None:
            return sorted(msgs, key=self._default_key)
        rev = self._sort_cycle == 2
        c = self._sort_col
        return sorted(msgs, key=lambda m: self._sort_key(m, c), reverse=rev)

    def _rebuild(self, progress_callback: Optional[Callable[[int, str], None]] = None) -> None:
        msgs = [m for m in self._messages_original if self._passes_filters(m)]
        msgs = self._apply_sort(msgs)
        self._fill_rows(msgs, progress_callback=progress_callback)
        active = {i for i, f in enumerate(self._filters) if (f or "").strip()}
        self._filter_header.set_active_columns(active)

    def _fill_rows(
        self,
        messages: List[dict],
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> None:
        n = len(messages)
        self._table.setUpdatesEnabled(False)
        try:
            if progress_callback:
                progress_callback(0, f"Building table… 0/{n}")
                QApplication.processEvents()
            self._table.setRowCount(n)
            self._table.setColumnCount(len(self._columns))
            self._table.setHorizontalHeaderLabels(list(self._HEADER_LABELS))
            for row, m in enumerate(messages):
                parts = self._row_strings_for_message(m)
                for col in range(len(parts)):
                    self._table.setItem(row, col, QTableWidgetItem(parts[col]))
                if n > 0 and (row + 1) % 1000 == 0:
                    if progress_callback:
                        pct = min(99, int(100 * (row + 1) / n))
                        progress_callback(pct, f"Building table… {row + 1}/{n}")
                    QApplication.processEvents()
            if progress_callback:
                if n > 0:
                    progress_callback(100, f"Building table… {n}/{n}")
                else:
                    progress_callback(100, "Done.")
                QApplication.processEvents()
        finally:
            self._table.setUpdatesEnabled(True)

    def set_messages(
        self,
        messages: List[dict],
        timezone_name: str = "",
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> None:
        self._messages_original = list(messages)
        self._timezone_name = timezone_name or ""
        self._filters = [""] * len(self._columns)
        self._sort_col = None
        self._sort_cycle = 0
        self._table.horizontalHeader().setSortIndicatorShown(False)
        self._rebuild(progress_callback=progress_callback)

    def _on_header_sort(self, col: int) -> None:
        if self._sort_col != col:
            self._sort_col = col
            self._sort_cycle = 1
        else:
            self._sort_cycle = (self._sort_cycle + 1) % 3
            if self._sort_cycle == 0:
                self._sort_col = None
        hdr = self._table.horizontalHeader()
        if self._sort_cycle == 0 or self._sort_col is None:
            hdr.setSortIndicatorShown(False)
        else:
            hdr.setSortIndicatorShown(True)
            order = Qt.SortOrder.AscendingOrder if self._sort_cycle == 1 else Qt.SortOrder.DescendingOrder
            hdr.setSortIndicator(self._sort_col, order)
        self._rebuild()

    def _on_header_filter(self, col: int) -> None:
        if col < 0 or col >= len(self._HEADER_LABELS):
            return
        dlg = ColumnFilterDialog(self._HEADER_LABELS[col], self._filters[col], self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        t = dlg.text()
        self._filters[col] = t
        self._rebuild()


class SavedSearchesTree(QTreeWidget):
    """Tree of folders + saved searches. Supports internal drag-drop with persistence."""

    item_moved = pyqtSignal()  # emitted after a successful drop persists a folder_id/parent_id change

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._app_data_root: Optional[Path] = None
        # Native Windows style paints branch decorations in a rect separate from QSS-styled
        # ::item backgrounds (often leaves a white gutter beside rounded selections).
        # Fusion honors show-decoration-selected + QSS more uniformly.
        _fusion = QStyleFactory.create("Fusion")
        if _fusion is not None:
            _fusion.setParent(self)
            self.setStyle(_fusion)

    def set_app_data_root(self, p: Optional[Path]) -> None:
        self._app_data_root = p

    def dropEvent(self, event) -> None:
        if self._app_data_root is None:
            event.ignore()
            return
        source = self.currentItem()
        if source is None:
            event.ignore()
            return
        src_data = source.data(0, Qt.ItemDataRole.UserRole)
        if not src_data or not isinstance(src_data, tuple):
            event.ignore()
            return
        src_kind, src_id = src_data

        # The library root folder is fixed at the top and cannot be reparented.
        if src_kind == "folder" and is_library_root_folder_id(src_id):
            event.ignore()
            return

        target = self.itemAt(event.position().toPoint())
        indicator = self.dropIndicatorPosition()

        def _parent_folder_id(item) -> str:
            parent = item.parent()
            if parent is None:
                return LIBRARY_ROOT_FOLDER_ID
            pd = parent.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(pd, tuple) and pd[0] == "folder":
                return pd[1]
            return LIBRARY_ROOT_FOLDER_ID

        if target is None:
            target_folder_id = LIBRARY_ROOT_FOLDER_ID
        else:
            tgt_data = target.data(0, Qt.ItemDataRole.UserRole)
            tgt_kind = tgt_data[0] if isinstance(tgt_data, tuple) else None
            tgt_id = tgt_data[1] if isinstance(tgt_data, tuple) else None
            if (
                indicator == QAbstractItemView.DropIndicatorPosition.OnItem
                and tgt_kind == "folder"
            ):
                target_folder_id = tgt_id
            else:
                target_folder_id = _parent_folder_id(target)

        if src_kind == "folder":
            if move_folder(self._app_data_root, src_id, target_folder_id) is None:
                event.ignore()
                return
        elif src_kind == "search":
            if update_saved_search(self._app_data_root, src_id, folder_id=target_folder_id) is None:
                event.ignore()
                return
        else:
            event.ignore()
            return

        event.accept()
        self.item_moved.emit()


class MessageViews(QWidget):
    """Tabs: Threads, Table (full backup), Search (saved searches left, results table right)."""
    add_search_requested = pyqtSignal(object)  # default folder id (str or None)
    run_saved_search_requested = pyqtSignal(dict)  # criteria dict
    export_rsmf_requested = pyqtSignal()  # request to export current search results
    export_thread_rsmf_requested = pyqtSignal(int)  # request to export a specific thread (chat rowid)
    full_table_tab_loaded = pyqtSignal()  # user loaded deferred Table tab; persist preference in meta

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()
        # Tab 0: Threads (chat list + thread view)
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by chat participant name or phone number")
        self._search.textChanged.connect(self._apply_search)
        left_layout.addWidget(self._search)
        self._chat_list = QListWidget()
        self._chat_list.setMinimumWidth(180)
        self._chat_list.currentRowChanged.connect(self._on_chat_selection)
        left_layout.addWidget(self._chat_list)
        self._splitter.addWidget(left)
        right_pane = QWidget()
        right_pane_layout = QVBoxLayout(right_pane)
        right_pane_layout.setContentsMargins(0, 0, 0, 0)
        thread_export_row = QHBoxLayout()
        thread_export_row.addStretch()
        self._export_thread_btn = QPushButton("  Export RSMF")
        self._export_thread_btn.setIcon(load_icon("download"))
        self._export_thread_btn.setIconSize(QSize(14, 14))
        self._export_thread_btn.setToolTip(
            "Export this thread to Relativity Short Message Format (with optional filters)"
        )
        self._export_thread_btn.setEnabled(False)
        self._export_thread_btn.clicked.connect(self._on_export_thread_clicked)
        thread_export_row.addWidget(self._export_thread_btn)
        right_pane_layout.addLayout(thread_export_row)
        self._stack = QStackedWidget()
        self._thread_view = ThreadView()
        self._stack.addWidget(self._thread_view)
        self._view_stack = QStackedWidget()
        self._view_stack.addWidget(self._stack)
        self._lightbox = LightboxWidget(self._view_stack)
        self._lightbox.clicked.connect(self._hide_lightbox)
        self._view_stack.addWidget(self._lightbox)
        self._thread_view.image_clicked.connect(self._show_lightbox)
        right_pane_layout.addWidget(self._view_stack, 1)
        self._splitter.addWidget(right_pane)
        self._splitter.setSizes([200, 600])
        self._tabs.addTab(self._splitter, "Threads View")
        # Tab 1: Table view (hidden until populated if import deferred Table tab)
        table_tab = QWidget()
        table_layout = QVBoxLayout(table_tab)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self._table_view = TableView()
        table_layout.addWidget(self._table_view, 1)
        self._table_tab_index = self._tabs.addTab(table_tab, "Table View")
        # Tab 2: Search — left = saved searches tree (right-click folders to add searches/subfolders), right = results table
        search_tab = QWidget()
        search_splitter = QSplitter(Qt.Orientation.Horizontal)
        search_left = QWidget()
        search_left_layout = QVBoxLayout(search_left)
        search_left_layout.setContentsMargins(0, 0, 0, 0)
        self._saved_searches_tree = SavedSearchesTree()
        self._saved_searches_tree.setMinimumWidth(200)
        self._saved_searches_tree.item_moved.connect(self._refresh_saved_searches_tree)
        self._saved_searches_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        search_left_layout.addWidget(self._saved_searches_tree)
        search_splitter.addWidget(search_left)
        search_right = QWidget()
        search_right_layout = QVBoxLayout(search_right)
        export_row = QHBoxLayout()
        export_row.addStretch()
        self._export_rsmf_btn = QPushButton("  Export RSMF")
        self._export_rsmf_btn.setIcon(load_icon("download"))
        self._export_rsmf_btn.setIconSize(QSize(14, 14))
        self._export_rsmf_btn.setToolTip("Export current search results to Relativity Short Message Format")
        self._export_rsmf_btn.clicked.connect(self.export_rsmf_requested.emit)
        export_row.addWidget(self._export_rsmf_btn)
        search_right_layout.addLayout(export_row)
        self._search_placeholder = QLabel(
            "Right-click the Saved searches folder (or any folder) to create a new search or subfolder."
        )
        self._search_placeholder.setProperty("class", "placeholder")
        self._search_placeholder.setWordWrap(True)
        search_right_layout.addWidget(self._search_placeholder)
        self._search_table_view = TableView()
        self._search_table_view.setVisible(False)
        search_right_layout.addWidget(self._search_table_view)
        search_splitter.addWidget(search_right)
        search_splitter.setSizes([200, 600])
        search_tab_layout = QVBoxLayout(search_tab)
        search_tab_layout.setContentsMargins(0, 0, 0, 0)
        search_tab_layout.addWidget(search_splitter)
        self._tabs.addTab(search_tab, "Search Messages")
        layout.addWidget(self._tabs)
        self._tabs.currentChanged.connect(self._on_search_tab_activated)
        self._app_data_root: Optional[Path] = None
        self._chats: List[dict] = []
        self._filtered_chats: List[dict] = []
        self._chat_id_to_messages: Dict[int, List[dict]] = {}
        self._all_messages: List[dict] = []
        self._search_results: List[dict] = []
        self._current_search_id: Optional[str] = None
        self._attachment_base: Optional[Path] = None
        self._current_chat_rowid: Optional[int] = None
        self._table_tab_deferred: bool = False

    def is_table_tab_deferred(self) -> bool:
        return self._table_tab_deferred

    def _set_table_tab_visible(self, visible: bool) -> None:
        if not visible and self._tabs.currentIndex() == self._table_tab_index:
            self._tabs.setCurrentIndex(0)
        self._tabs.setTabVisible(self._table_tab_index, visible)

    def _show_lightbox(self, path) -> None:
        p = Path(path) if path else None
        if p and p.exists():
            self._lightbox.set_image_path(p)
            self._view_stack.setCurrentWidget(self._lightbox)

    def _hide_lightbox(self) -> None:
        self._view_stack.setCurrentWidget(self._stack)

    def set_app_data_root(self, path: Optional[Path]) -> None:
        self._app_data_root = path
        self._saved_searches_tree.set_app_data_root(path)

    def _on_search_tab_activated(self, index: int) -> None:
        if index == 2:
            self._refresh_saved_searches_tree()

    def _build_search_row_widget(self, search: dict, criteria: dict) -> QWidget:
        """Build the inline label + Run + Delete buttons for a saved-search tree row."""
        name = search.get("name") or "Unnamed"
        seq = search.get("sequence") or 0
        search_id = search.get("id")
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 2, 2, 2)
        lbl = QLabel(f"[{seq:04d}] {name}")
        lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lbl.setWordWrap(True)
        row_layout.addWidget(lbl, 1)
        run_btn = QPushButton()
        run_btn.setIcon(load_icon("search"))
        run_btn.setIconSize(QSize(16, 16))
        run_btn.setProperty("class", "icon-btn")
        run_btn.setToolTip("Run search")
        run_btn.setFixedSize(28, 28)
        run_btn.clicked.connect(lambda checked=False, c=criteria: self.run_saved_search_requested.emit(c))
        row_layout.addWidget(run_btn, 0)
        delete_btn = QPushButton()
        delete_btn.setIcon(load_icon("trash"))
        delete_btn.setIconSize(QSize(16, 16))
        delete_btn.setProperty("class", "icon-btn")
        delete_btn.setToolTip("Delete search")
        delete_btn.setFixedSize(28, 28)
        delete_btn.clicked.connect(lambda checked=False, sid=search_id: self._on_delete_search(sid))
        row_layout.addWidget(delete_btn, 0)
        return row

    def _refresh_saved_searches_tree(self) -> None:
        tree = self._saved_searches_tree
        # Preserve expanded folder ids and the current selection across rebuilds.
        expanded: set = set()
        selected_data = None

        def collect_expanded(item: QTreeWidgetItem) -> None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, tuple) and data[0] == "folder" and item.isExpanded():
                expanded.add(data[1])
            for i in range(item.childCount()):
                collect_expanded(item.child(i))

        root = tree.invisibleRootItem()
        for i in range(root.childCount()):
            collect_expanded(root.child(i))
        current = tree.currentItem()
        if current is not None:
            selected_data = current.data(0, Qt.ItemDataRole.UserRole)

        tree.clear()
        if not self._app_data_root:
            return

        folders = load_folders(self._app_data_root)
        searches = load_saved_searches(self._app_data_root)

        folder_items: Dict[str, QTreeWidgetItem] = {}
        for folder, depth in walk_folders_depth_first(folders):
            fid = folder.get("id")
            item = QTreeWidgetItem()
            item.setText(0, folder.get("name") or "Unnamed folder")
            item.setIcon(0, load_icon("folder"))
            item.setData(0, Qt.ItemDataRole.UserRole, ("folder", fid))
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsDragEnabled
                | Qt.ItemFlag.ItemIsDropEnabled
            )
            parent_id = folder.get("parent_id")
            parent_item = folder_items.get(parent_id) if parent_id else None
            if parent_item is None:
                tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            folder_items[fid] = item

        for i, s in enumerate(searches):
            criteria = {
                "to_filter": s.get("to_filter", ""),
                "body_filter": s.get("body_filter", ""),
                "date_from": s.get("date_from", ""),
                "date_to": s.get("date_to", ""),
                "has_attachments": s.get("has_attachments", "any"),
                "hash_filter": s.get("hash_filter", ""),
                "chunk_24h": s.get("chunk_24h", False),
                "search_name": s.get("name") or "Search results",
                "sequence": s.get("sequence", i + 1),
                "search_id": s.get("id"),
            }
            item = QTreeWidgetItem()
            item.setData(0, Qt.ItemDataRole.UserRole, ("search", s.get("id")))
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsDragEnabled
            )
            folder_id = s.get("folder_id")
            parent_item = folder_items.get(folder_id) if folder_id else None
            if parent_item is None:
                tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            tree.setItemWidget(item, 0, self._build_search_row_widget(s, criteria))

        # Restore expansion state for folders that still exist; expand all by default on first render.
        def restore_expansion(item: QTreeWidgetItem) -> None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, tuple) and data[0] == "folder":
                fid = data[1]
                item.setExpanded(fid in expanded if expanded else True)
            for i in range(item.childCount()):
                restore_expansion(item.child(i))

        for i in range(tree.invisibleRootItem().childCount()):
            restore_expansion(tree.invisibleRootItem().child(i))

        # Restore selection if possible.
        if selected_data:
            def find_by_data(item: QTreeWidgetItem) -> Optional[QTreeWidgetItem]:
                if item.data(0, Qt.ItemDataRole.UserRole) == selected_data:
                    return item
                for j in range(item.childCount()):
                    found = find_by_data(item.child(j))
                    if found is not None:
                        return found
                return None

            for i in range(tree.invisibleRootItem().childCount()):
                found = find_by_data(tree.invisibleRootItem().child(i))
                if found is not None:
                    tree.setCurrentItem(found)
                    break

    def get_selected_folder_id(self) -> str:
        """Return the folder id of the currently selected folder, the parent folder of
        the selected search, or the library root if nothing useful is selected."""
        item = self._saved_searches_tree.currentItem()
        if item is None:
            return LIBRARY_ROOT_FOLDER_ID
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple):
            return LIBRARY_ROOT_FOLDER_ID
        kind, ident = data
        if kind == "folder":
            return ident or LIBRARY_ROOT_FOLDER_ID
        parent = item.parent()
        if parent is None:
            return LIBRARY_ROOT_FOLDER_ID
        pdata = parent.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(pdata, tuple) and pdata[0] == "folder":
            return pdata[1] or LIBRARY_ROOT_FOLDER_ID
        return LIBRARY_ROOT_FOLDER_ID

    def _create_folder(self, parent_id: Optional[str]) -> None:
        if not self._app_data_root:
            return
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return
        add_folder(self._app_data_root, name, parent_id=parent_id or LIBRARY_ROOT_FOLDER_ID)
        self._refresh_saved_searches_tree()

    def _on_tree_context_menu(self, pos) -> None:
        tree = self._saved_searches_tree
        item = tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple):
            return
        kind, ident = data
        menu = QMenu(tree)
        if kind == "folder":
            menu.addAction(
                "New search",
                lambda: self.add_search_requested.emit(ident),
            )
            menu.addAction(
                "New subfolder",
                lambda: self._create_folder(parent_id=ident),
            )
            if not is_library_root_folder_id(ident):
                menu.addSeparator()
                menu.addAction("Rename folder...", lambda: self._on_rename_folder(ident))
                menu.addAction("Delete folder...", lambda: self._on_delete_folder(ident))
        elif kind == "search":
            menu.addAction("Move to folder...", lambda: self._on_move_search(ident))
        if menu.isEmpty():
            return
        menu.exec(tree.viewport().mapToGlobal(pos))

    def _on_rename_folder(self, folder_id: str) -> None:
        if not self._app_data_root:
            return
        folders = load_folders(self._app_data_root)
        current = next((f for f in folders if f.get("id") == folder_id), None)
        if current is None:
            return
        name, ok = QInputDialog.getText(
            self,
            "Rename folder",
            "Folder name:",
            text=current.get("name") or "",
        )
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return
        rename_folder(self._app_data_root, folder_id, name)
        self._refresh_saved_searches_tree()

    def _on_delete_folder(self, folder_id: str) -> None:
        if not self._app_data_root:
            return
        folders = load_folders(self._app_data_root)
        searches = load_saved_searches(self._app_data_root)
        target = next((f for f in folders if f.get("id") == folder_id), None)
        if target is None:
            return
        n_searches = descendant_search_count(folders, searches, folder_id)
        name = target.get("name") or "Unnamed folder"
        if n_searches > 0:
            msg = (
                f"Delete folder \"{name}\"? This will also permanently delete "
                f"{n_searches} saved search{'es' if n_searches != 1 else ''} inside it."
            )
        else:
            msg = f"Delete folder \"{name}\"?"
        ok = QMessageBox.question(
            self,
            "Delete folder",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes
        if not ok:
            return
        _, deleted_searches = delete_folder_cascade(self._app_data_root, folder_id)
        if deleted_searches and self._current_search_id is not None:
            remaining = {s.get("id") for s in load_saved_searches(self._app_data_root)}
            if self._current_search_id not in remaining:
                self.clear_search_results()
        self._refresh_saved_searches_tree()

    def _on_move_search(self, search_id: str) -> None:
        if not self._app_data_root:
            return
        folders = load_folders(self._app_data_root)
        labels: List[str] = []
        ids: List[str] = []
        for folder, depth in walk_folders_depth_first(folders):
            labels.append(("    " * depth) + (folder.get("name") or "Unnamed folder"))
            ids.append(folder.get("id"))
        if not labels:
            return
        choice, ok = QInputDialog.getItem(
            self,
            "Move search",
            "Move to folder:",
            labels,
            0,
            False,
        )
        if not ok:
            return
        try:
            target_id = ids[labels.index(choice)]
        except ValueError:
            return
        update_saved_search(self._app_data_root, search_id, folder_id=target_id)
        self._refresh_saved_searches_tree()

    def _on_delete_search(self, search_id: Optional[str]) -> None:
        """Delete a saved search and clear its results if currently displayed."""
        if not search_id or not self._app_data_root:
            return
        ok = QMessageBox.question(
            self,
            "Delete search",
            "Remove this search? Results from this search will be cleared.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes
        if not ok:
            return
        if delete_saved_search(self._app_data_root, search_id):
            if self._current_search_id == search_id:
                self.clear_search_results()
            self._refresh_saved_searches_tree()

    def _on_chat_selection(self, row: int) -> None:
        if row < 0 or not self._filtered_chats or row >= len(self._filtered_chats):
            self._export_thread_btn.setEnabled(False)
            return
        chat = self._filtered_chats[row]
        cid = chat.get("rowid")
        self._current_chat_rowid = cid
        self._export_thread_btn.setEnabled(cid is not None)
        msgs = self._chat_id_to_messages.get(cid, [])
        if not msgs:
            self._thread_view.set_messages([], self._attachment_base)
            return
        self._thread_view.begin_chunked_load(msgs, self._attachment_base)
        # Table view always shows all messages; do not change it here

    def _on_export_thread_clicked(self) -> None:
        if self._current_chat_rowid is None:
            return
        self.export_thread_rsmf_requested.emit(int(self._current_chat_rowid))

    def get_messages_for_chat(self, chat_rowid: int) -> List[dict]:
        """Return messages belonging to a specific chat rowid."""
        return self._chat_id_to_messages.get(chat_rowid, [])

    def get_messages_for_current_chat(self) -> List[dict]:
        """Return messages belonging to the currently selected chat thread (or empty)."""
        if self._current_chat_rowid is None:
            return []
        return self._chat_id_to_messages.get(self._current_chat_rowid, [])

    def get_current_chat_label(self) -> str:
        """Return the display label of the currently selected chat, or empty string."""
        if self._current_chat_rowid is None:
            return ""
        for ch in self._filtered_chats:
            if ch.get("rowid") == self._current_chat_rowid:
                return ch.get("label") or ""
        return ""

    def _apply_search(self) -> None:
        """Filter chat list by search text and refresh the list."""
        q = (self._search.text() or "").strip().lower()
        if not q:
            self._filtered_chats = list(self._chats)
        else:
            self._filtered_chats = [
                c for c in self._chats
                if q in (c.get("label") or "").lower()
                or q in (c.get("chat_identifier") or "").lower()
            ]
        self._chat_list.clear()
        for ch in self._filtered_chats:
            self._chat_list.addItem(f"{ch['label']} ({ch['count']})")
        if self._filtered_chats and self._current_chat_rowid is not None:
            for i, ch in enumerate(self._filtered_chats):
                if ch.get("rowid") == self._current_chat_rowid:
                    self._chat_list.setCurrentRow(i)
                    break

    def _label_sort_key(self, label: str) -> tuple:
        """Sort key: names (starting with letter) A-Z first, then digits/phones."""
        s = (label or "").strip()
        is_name = bool(s and s[0].isalpha())
        return (0 if is_name else 1, s.upper() if is_name else s)

    def set_attachment_base(self, path: Optional[Path]) -> None:
        self._attachment_base = path
        self._thread_view.set_attachment_base_path(path)

    def populate_full_table_tab(self, progress_callback: Optional[Callable[[int, str], None]] = None) -> None:
        """Fill the main Table tab from cached messages (after import with Table tab deferred)."""
        all_sorted = sorted(
            self._all_messages,
            key=lambda x: (x.get("date_timestamp") or 0, x.get("rowid") or 0),
        )
        self._set_table_tab_visible(True)
        self._table_view.set_messages(all_sorted, self._timezone_name, progress_callback=progress_callback)
        self._table_tab_deferred = False
        self._tabs.setCurrentIndex(self._table_tab_index)
        self.full_table_tab_loaded.emit()

    def set_data(
        self,
        chats: List[dict],
        messages: List[dict],
        attachment_base: Optional[Path] = None,
        timezone_name: str = "",
        populate_table: bool = True,
    ) -> None:
        # Pulse the event loop occasionally so the progress overlay's indeterminate bar
        # keeps animating during this long synchronous setup.
        n_msg = len(messages)
        pulse_every = max(1, n_msg // 30) if n_msg else 1
        # Repair timestamps so viewer always has date_timestamp and date_formatted
        for i, m in enumerate(messages):
            if m.get("date_timestamp") is None and m.get("date") is not None:
                m["date_timestamp"] = _apple_date_to_unix(m["date"])
            ts = m.get("date_timestamp")
            # Normalize: if date_timestamp is huge, it was likely stored as Apple timestamp by mistake
            if ts is not None:
                try:
                    f = float(ts)
                    if f > 1e15:
                        m["date_timestamp"] = _apple_date_to_unix(ts) or _apple_date_to_unix(m.get("date"))
                except (TypeError, ValueError):
                    pass
            if not m.get("date_formatted") and m.get("date_timestamp") is not None:
                m["date_formatted"] = _format_date_from_timestamp(m["date_timestamp"], timezone_name)
            if i % pulse_every == 0:
                QApplication.processEvents()

        self._all_messages = messages
        self._attachment_base = attachment_base
        self._thread_view.set_attachment_base_path(attachment_base)
        self._thread_view.set_timezone(timezone_name)
        self._timezone_name = timezone_name or ""
        self._chat_id_to_messages = {}
        chat_id_to_display = {c.get("rowid"): (c.get("display_name") or c.get("chat_identifier") or "") for c in chats}
        for i, m in enumerate(messages):
            cid = m.get("chat_id")
            m["chat_display_name"] = chat_id_to_display.get(cid, m.get("chat_identifier") or "")
            if cid is not None:
                self._chat_id_to_messages.setdefault(cid, []).append(m)
            if i % pulse_every == 0:
                QApplication.processEvents()
        for cid in self._chat_id_to_messages:
            self._chat_id_to_messages[cid].sort(key=lambda x: (x.get("date_timestamp") or 0, x.get("rowid") or 0))
        self._chats = []
        for c in chats:
            cid = c.get("rowid")
            label = c.get("display_name") or c.get("chat_identifier") or f"Chat {cid}"
            count = len(self._chat_id_to_messages.get(cid, []))
            self._chats.append({
                "rowid": cid,
                "label": label,
                "count": count,
                "chat_identifier": c.get("chat_identifier") or "",
            })
        self._chats.sort(key=lambda ch: self._label_sort_key(ch["label"]))
        self._filtered_chats = list(self._chats)
        self._current_chat_rowid = None
        self._export_thread_btn.setEnabled(False)
        self._apply_search()
        self._thread_view.set_messages([], attachment_base)
        # Table view: all messages, sorted by date (optional skip for faster import / open)
        all_sorted = sorted(
            self._all_messages,
            key=lambda x: (x.get("date_timestamp") or 0, x.get("rowid") or 0),
        )
        if populate_table:
            QApplication.processEvents()
            self._table_view.set_messages(all_sorted, self._timezone_name)
            QApplication.processEvents()
            self._table_tab_deferred = False
            self._set_table_tab_visible(True)
        else:
            self._table_view.set_messages([], self._timezone_name)
            self._table_tab_deferred = True
            self._set_table_tab_visible(False)
        # Clear Search tab when loading new backup
        self._search_results = []
        self._current_search_id = None
        self._search_table_view.set_messages([], self._timezone_name)
        self._search_table_view.setVisible(False)
        self._search_placeholder.setVisible(True)
        if self._filtered_chats:
            self._chat_list.setCurrentRow(0)
            self._on_chat_selection(0)

    def show_thread_view(self) -> None:
        self._splitter.widget(0).setVisible(True)
        self._splitter.setSizes([200, 600])
        self._tabs.setCurrentIndex(0)

    def show_table_view(self) -> None:
        if self._tabs.isTabVisible(self._table_tab_index):
            self._tabs.setCurrentIndex(self._table_tab_index)

    def set_search_results(self, messages: List[dict], search_id: Optional[str] = None) -> None:
        """Populate the Search tab's table and switch to Search tab. Does not affect the Table tab."""
        sorted_msgs = sorted(
            messages,
            key=lambda x: (x.get("date_timestamp") or 0, x.get("rowid") or 0),
        )
        self._search_results = sorted_msgs
        self._current_search_id = search_id
        self._search_table_view.set_messages(sorted_msgs, self._timezone_name)
        self._search_placeholder.setVisible(False)
        self._search_table_view.setVisible(True)
        self._tabs.setCurrentIndex(2)

    def refresh_saved_searches_list(self) -> None:
        """Reload the saved searches tree (e.g. after saving a new search from the dialog)."""
        if self._tabs.currentIndex() == 2:
            self._refresh_saved_searches_tree()

    def show_search_tab(self) -> None:
        """Switch to the Search tab."""
        self._tabs.setCurrentIndex(2)

    def get_all_messages(self) -> List[dict]:
        """Return the full message list for the current backup (for running search)."""
        return self._all_messages

    def clear_search_results(self) -> None:
        """Clear the Search tab's table and show placeholder."""
        self._search_results = []
        self._current_search_id = None
        self._search_table_view.set_messages([], getattr(self, "_timezone_name", ""))
        self._search_table_view.setVisible(False)
        self._search_placeholder.setVisible(True)

    def get_search_results(self) -> List[dict]:
        """Return current search results for RSMF export."""
        return self._search_results

    def show_chat(self, chat_rowid: int) -> None:
        msgs = self._chat_id_to_messages.get(chat_rowid, [])
        self._thread_view.begin_chunked_load(msgs, self._attachment_base)
        # Table view always shows all messages; do not switch it to this chat

    def show_all_messages(self) -> None:
        self._thread_view.begin_chunked_load(self._all_messages, self._attachment_base)
        if not self._table_tab_deferred:
            all_sorted = sorted(
                self._all_messages,
                key=lambda x: (x.get("date_timestamp") or 0, x.get("rowid") or 0),
            )
            self._table_view.set_messages(all_sorted, getattr(self, "_timezone_name", ""))

    def get_chats(self) -> List[dict]:
        return self._chats

    def get_chat_id_to_messages(self) -> Dict[int, List[dict]]:
        return self._chat_id_to_messages
