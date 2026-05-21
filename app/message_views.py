"""
Thread view and table view for messages. Data is pre-cached so switching is instant.
Images displayed inline (no filename); emojis as emojis; GIFs as stills.
"""

from __future__ import annotations

import zoneinfo
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
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
    QProxyStyle,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyleFactory,
    QStyleOptionHeader,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QRect, QPoint
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPixmap,
    QPolygon,
    QWheelEvent,
    QBrush,
    QPalette,
)

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
from app.style import icon as load_icon, logo_path as resolve_logo_path
from app.thread_list import VirtualThreadView as ThreadView
from app.timezone_utils import get_tz_abbrev_for_timestamp


def _lock_horizontal_splitter(splitter: QSplitter) -> None:
    """Keep the left pane width fixed; disable drag-to-resize on the splitter handle."""
    splitter.setHandleWidth(0)
    splitter.setChildrenCollapsible(False)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    if splitter.count() >= 2:
        splitter.handle(0).setEnabled(False)


# Default width for fixed left panes (threads chat list, saved-searches tree).
_LEFT_SIDEBAR_WIDTH = 320
# Shared toolbar band height so left/right split panes align on every tab.
_TOOLBAR_ROW_HEIGHT = 36


def _make_side_toolbar_row(
    *,
    leading: Optional[QWidget] = None,
    trailing: Optional[QWidget] = None,
) -> QWidget:
    """Fixed-height row above main content; keeps left sidebar level with right pane."""
    row = QWidget()
    row.setFixedHeight(_TOOLBAR_ROW_HEIGHT)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    if leading is not None:
        lay.addWidget(leading, 1)
    else:
        lay.addStretch(1)
    if trailing is not None:
        lay.addWidget(trailing, 0, Qt.AlignmentFlag.AlignRight)
    return row


_SEARCH_SELECTED_BG = "#d6e4f5"
_SEARCH_SELECTED_TEXT = "#1f2937"


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

    RESIZE_HIT = 12  # pixels around the column boundary used for resize cursor/drag
    FILTER_ZONE = 26
    TEXT_PAD_LEFT = 11
    TEXT_PAD_RIGHT = 6
    SORT_INDICATOR_WIDTH = 16
    CLICK_SLOP = 8

    filter_clicked = pyqtSignal(int)
    sort_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._active_columns: set = set()
        self.setSectionsClickable(False)
        self.setHighlightSections(True)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._pending_click: Optional[tuple[int, str]] = None
        self._press_origin: Optional[QPoint] = None
        self._resize_active = False

    @property
    def _filter_zone(self) -> int:
        return self.FILTER_ZONE

    @property
    def RESIZE_GRIP(self) -> int:
        return self.RESIZE_HIT

    def reserved_trailing_width(self) -> int:
        return self.RESIZE_HIT + self.FILTER_ZONE

    def _header_label(self, logical_index: int) -> str:
        model = self.model()
        if model is None:
            return ""
        value = model.headerData(logical_index, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        return str(value) if value is not None else ""

    def _label_font_metrics(self) -> QFontMetrics:
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)
        return QFontMetrics(font)

    def minimumSectionWidth(self, logical_index: int) -> int:
        label = self._header_label(logical_index)
        text_w = self._label_font_metrics().horizontalAdvance(label)
        return (
            self.TEXT_PAD_LEFT
            + text_w
            + self.SORT_INDICATOR_WIDTH
            + self.TEXT_PAD_RIGHT
            + self.reserved_trailing_width()
        )

    def _resize_handle_index(self, pos_x: int) -> int:
        """Section whose right edge is near pos_x, or -1."""
        half = self.RESIZE_HIT // 2
        for i in range(self.count()):
            if self.isSectionHidden(i):
                continue
            edge = self.sectionPosition(i) + self.sectionSize(i)
            if abs(pos_x - edge) <= half:
                return i
        return -1

    def _zone_at(self, pos_x: int) -> tuple[int, str]:
        handle = self._resize_handle_index(pos_x)
        if handle >= 0:
            return (handle, "resize")
        idx = self.logicalIndexAt(pos_x)
        if idx < 0:
            return (-1, "")
        x0 = self.sectionPosition(idx)
        rel = pos_x - x0
        sz = self.sectionSize(idx)
        trailing = self.reserved_trailing_width()
        if rel >= sz - trailing:
            return (idx, "filter")
        return (idx, "sort")

    def set_active_columns(self, cols: set) -> None:
        self._active_columns = set(cols)
        self.viewport().update()

    def leaveEvent(self, event) -> None:
        if not self._resize_active:
            self.unsetCursor()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resize_active:
            super().mouseMoveEvent(event)
            return
        pos = int(event.position().x())
        if self._resize_handle_index(pos) >= 0:
            self.setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.unsetCursor()
        if self._pending_click and self._press_origin is not None:
            moved = (event.position().toPoint() - self._press_origin).manhattanLength()
            if moved > self.CLICK_SLOP:
                self._pending_click = None

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        pos = int(event.position().x())
        handle = self._resize_handle_index(pos)
        if handle >= 0:
            self._pending_click = None
            self._press_origin = None
            self._resize_active = True
            super().mousePressEvent(event)
            return
        idx, zone = self._zone_at(pos)
        if idx < 0 or zone not in ("filter", "sort"):
            return super().mousePressEvent(event)
        self._pending_click = (idx, zone)
        self._press_origin = event.position().toPoint()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._resize_active:
            self._resize_active = False
            self._pending_click = None
            self._press_origin = None
            super().mouseReleaseEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton and self._pending_click is not None:
            pos = int(event.position().x())
            idx, zone = self._zone_at(pos)
            pending_idx, pending_zone = self._pending_click
            if (
                idx == pending_idx
                and zone == pending_zone
                and self._press_origin is not None
                and (event.position().toPoint() - self._press_origin).manhattanLength() <= self.CLICK_SLOP
            ):
                if zone == "filter":
                    self.filter_clicked.emit(idx)
                elif zone == "sort":
                    self.sort_clicked.emit(idx)
            self._pending_click = None
            self._press_origin = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:
        reserved = self.reserved_trailing_width()
        content_rect = rect.adjusted(0, 0, -reserved, 0)

        opt = QStyleOptionHeader()
        self.initStyleOption(opt)
        opt.rect = content_rect
        opt.section = logicalIndex
        opt.text = self._header_label(logicalIndex)
        opt.textAlignment = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        opt.sortIndicator = QStyleOptionHeader.SortIndicator.SortNone
        self.style().drawControl(QStyle.ControlElement.CE_Header, opt, painter, self)

        grip = self.RESIZE_HIT
        fr_w = max(12, self.FILTER_ZONE - 4)
        fr_left = rect.right() - grip - self.FILTER_ZONE + 2
        fr = QRect(fr_left, rect.top() + 2, fr_w, rect.height() - 4)

        indicator = ""
        use_highlight = logicalIndex in self._active_columns
        if self.isSortIndicatorShown() and self.sortIndicatorSection() == logicalIndex:
            if self.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder:
                indicator = "\u2191"
            else:
                indicator = "\u2193"
        elif use_highlight:
            indicator = "\u23f7"

        if not indicator:
            return

        painter.save()
        pal = self.palette()
        c = pal.color(pal.ColorGroup.Active, pal.ColorRole.Highlight) if use_highlight else pal.color(
            pal.ColorGroup.Active, pal.ColorRole.Mid
        )
        painter.setPen(c)
        painter.drawText(fr, Qt.AlignmentFlag.AlignCenter, indicator)
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
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._table = TableWidgetWithShiftWheel()
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table.setShowGrid(True)
        self._table.setSortingEnabled(False)
        vheader = self._table.verticalHeader()
        vheader.setDefaultSectionSize(30)
        vheader.setMinimumSectionSize(26)

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
        hdr = self._filter_header
        # Minimum data widths for columns whose cell content is typically wider than the header.
        content_mins = [180, 140, 200, 170, 120, 140, 0, 0, 320, 160]
        body_col = 8
        for i in range(self._table.columnCount()):
            header_min = hdr.minimumSectionWidth(i)
            hdr.setMinimumSectionSize(header_min)
            content_min = content_mins[i] if i < len(content_mins) else 0
            if i == body_col:
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
                hdr.resizeSection(i, max(header_min, content_min))
            else:
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                hdr.resizeSection(i, max(header_min, content_min))

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


class SavedSearchesTreeProxyStyle(QProxyStyle):
    """Fusion + global QSS often omit visible tree disclosure markers; draw chevrons here."""

    _CHEVRON_GRAY = QColor("#6b7280")
    _CHEVRON_WHITE = QColor("#ffffff")

    def __init__(self, base_style: QStyle):
        super().__init__(base_style)

    def drawPrimitive(
        self,
        element: QStyle.PrimitiveElement,
        option,
        painter: QPainter,
        widget=None,
    ) -> None:
        if element != QStyle.PrimitiveElement.PE_IndicatorBranch:
            super().drawPrimitive(element, option, painter, widget)
            return

        # Rows with children: Fusion often paints nothing visible here; drawing after super()
        # still leaves no marker. Paint only our disclosure icon (connector stubs are minor).
        if option.state & QStyle.StateFlag.State_Children:
            rect = option.rect
            if rect.width() >= 4 and rect.height() >= 4:
                expanded = bool(option.state & QStyle.StateFlag.State_Open)
                selected = bool(option.state & QStyle.StateFlag.State_Selected)
                color = self._CHEVRON_WHITE if selected else self._CHEVRON_GRAY

                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)

                cx = rect.center().x()
                cy = rect.center().y()
                cell_h = min(rect.height(), 22)
                half_w = max(4, min(8, cell_h // 3))

                if expanded:
                    points = [
                        QPoint(cx - half_w, cy - 4),
                        QPoint(cx + half_w, cy - 4),
                        QPoint(cx, cy + max(4, half_w)),
                    ]
                else:
                    points = [
                        QPoint(cx - 4, cy - half_w),
                        QPoint(cx + max(4, half_w), cy),
                        QPoint(cx - 4, cy + half_w),
                    ]
                painter.drawPolygon(QPolygon(points))
                painter.restore()
            return

        super().drawPrimitive(element, option, painter, widget)


# Extra item data for saved-search run criteria (UserRole holds ("search", id)).
_SEARCH_CRITERIA_ROLE = Qt.ItemDataRole.UserRole + 1


class SavedSearchesTreeDelegate(QStyledItemDelegate):
    """Saved-search rows: full-row highlight when selected; no hover fill when not selected."""

    def __init__(self, tree: QTreeWidget, parent=None):
        super().__init__(parent)
        self._tree = tree

    def _is_search_item(self, index) -> bool:
        item = self._tree.itemFromIndex(index)
        return SavedSearchesTree._item_is_search(item)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        if not self._is_search_item(index):
            super().paint(painter, option, index)
            return
        item = self._tree.itemFromIndex(index)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        selected = item is not None and item.isSelected()
        opt.state &= ~(
            QStyle.StateFlag.State_Selected
            | QStyle.StateFlag.State_MouseOver
            | QStyle.StateFlag.State_HasFocus
        )
        opt.backgroundBrush = QBrush(Qt.GlobalColor.transparent)
        if selected:
            text_color = QColor(_SEARCH_SELECTED_TEXT)
            opt.palette.setColor(QPalette.ColorRole.Text, text_color)
            opt.palette.setColor(QPalette.ColorRole.WindowText, text_color)
        super().paint(painter, opt, index)


class SavedSearchesTree(QTreeWidget):
    """Tree of folders + saved searches. Supports internal drag-drop with persistence."""

    item_moved = pyqtSignal()  # emitted after a successful drop persists a folder_id/parent_id change

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SavedSearchesTree")
        self.setHeaderHidden(True)
        self.setUniformRowHeights(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        # DragDrop (not InternalMove): item widgets detach on internal move; we persist + rebuild.
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._app_data_root: Optional[Path] = None
        self._case_id: Optional[str] = None
        self._drag_source_item: Optional[QTreeWidgetItem] = None
        # Fusion keeps whole-row selection aligned with QSS; proxy paints disclosure chevrons
        # because Fusion ignores branch `image:` in stylesheets on Windows.
        base = QStyleFactory.create("Fusion") or QApplication.style()
        proxy = SavedSearchesTreeProxyStyle(base)
        proxy.setParent(self)
        self.setStyle(proxy)
        self.setItemDelegate(SavedSearchesTreeDelegate(self, self))

    @staticmethod
    def _item_is_search(item: Optional[QTreeWidgetItem]) -> bool:
        if item is None:
            return False
        data = item.data(0, Qt.ItemDataRole.UserRole)
        return isinstance(data, tuple) and data[0] == "search"

    def drawRow(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        item = self.itemFromIndex(index)
        if self._item_is_search(item):
            if item is not None and item.isSelected():
                full = QRect(option.rect)
                full.setLeft(0)
                full.setRight(self.viewport().width())
                painter.fillRect(full, QColor(_SEARCH_SELECTED_BG))
            row_opt = QStyleOptionViewItem(option)
            row_opt.state &= ~(
                QStyle.StateFlag.State_Selected
                | QStyle.StateFlag.State_MouseOver
                | QStyle.StateFlag.State_HasFocus
            )
            super().drawRow(painter, row_opt, index)
            return
        super().drawRow(painter, option, index)

    def set_saved_search_storage(self, p: Optional[Path], case_id: Optional[str]) -> None:
        self._app_data_root = p
        self._case_id = case_id

    def startDrag(self, supportedActions) -> None:
        self._drag_source_item = self.currentItem()
        super().startDrag(supportedActions)

    def dragEnterEvent(self, event) -> None:
        if event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.source() is self:
            event.acceptProposedAction()
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        try:
            if self._app_data_root is None or self._case_id is None:
                event.ignore()
                return
            source = self._drag_source_item or self.currentItem()
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
                if move_folder(self._app_data_root, self._case_id, src_id, target_folder_id) is None:
                    event.ignore()
                    return
            elif src_kind == "search":
                if update_saved_search(self._app_data_root, self._case_id, src_id, folder_id=target_folder_id) is None:
                    event.ignore()
                    return
            else:
                event.ignore()
                return

            event.accept()
            self.item_moved.emit()
        finally:
            self._drag_source_item = None


class MessageViews(QWidget):
    """Tabs: Threads, Table (full backup), Search (saved searches left, results table right)."""
    add_search_requested = pyqtSignal(object)  # default folder id (str or None)
    edit_search_requested = pyqtSignal(str)  # saved search id
    run_saved_search_requested = pyqtSignal(dict)  # criteria dict (e.g. after Save & Search in dialog)
    search_selected = pyqtSignal(dict)  # criteria dict when user selects a saved search
    export_rsmf_requested = pyqtSignal()  # request to export current search results
    export_threads_rsmf_requested = pyqtSignal(list)  # chat rowids to export
    full_table_tab_loaded = pyqtSignal()  # user loaded deferred Table tab; persist preference in meta

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._tabs = QTabWidget()
        # Tab 0: Threads (chat list + thread view)
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left.setMinimumWidth(_LEFT_SIDEBAR_WIDTH)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by chat participant name or phone number")
        self._search.textChanged.connect(self._apply_search)
        left_layout.addWidget(_make_side_toolbar_row(leading=self._search))
        self._chat_list = QListWidget()
        self._chat_list.setMinimumWidth(_LEFT_SIDEBAR_WIDTH - 20)
        self._chat_list.currentRowChanged.connect(self._on_chat_selection)
        self._chat_list.itemChanged.connect(self._on_chat_item_changed)
        left_layout.addWidget(self._chat_list, 1)
        self._splitter.addWidget(left)
        right_pane = QWidget()
        right_pane_layout = QVBoxLayout(right_pane)
        right_pane_layout.setContentsMargins(0, 0, 0, 0)
        right_pane_layout.setSpacing(0)
        self._export_thread_btn = QPushButton("  Export RSMF")
        self._export_thread_btn.setIcon(load_icon("download"))
        self._export_thread_btn.setIconSize(QSize(14, 14))
        self._export_thread_btn.setToolTip(
            "Export checked threads to Relativity Short Message Format (with optional filters)"
        )
        self._export_thread_btn.setEnabled(False)
        self._export_thread_btn.clicked.connect(self._on_export_thread_clicked)
        right_pane_layout.addWidget(_make_side_toolbar_row(trailing=self._export_thread_btn))
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
        self._splitter.setSizes([_LEFT_SIDEBAR_WIDTH, 680])
        _lock_horizontal_splitter(self._splitter)
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
        self._search_splitter = QSplitter(Qt.Orientation.Horizontal)
        search_left = QWidget()
        search_left.setMinimumWidth(_LEFT_SIDEBAR_WIDTH)
        search_left_layout = QVBoxLayout(search_left)
        search_left_layout.setContentsMargins(0, 0, 0, 0)
        search_left_layout.setSpacing(0)
        self._saved_searches_tree = SavedSearchesTree()
        self._saved_searches_tree.setMinimumWidth(_LEFT_SIDEBAR_WIDTH - 20)
        self._saved_searches_tree.item_moved.connect(self._refresh_saved_searches_tree)
        self._saved_searches_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._saved_searches_tree.currentItemChanged.connect(self._on_saved_search_tree_selection)
        search_left_layout.addWidget(_make_side_toolbar_row())
        search_left_layout.addWidget(self._saved_searches_tree, 1)
        self._search_splitter.addWidget(search_left)
        search_right = QWidget()
        search_right_layout = QVBoxLayout(search_right)
        search_right_layout.setContentsMargins(0, 0, 0, 0)
        search_right_layout.setSpacing(0)
        self._export_rsmf_btn = QPushButton("  Export RSMF")
        self._export_rsmf_btn.setIcon(load_icon("download"))
        self._export_rsmf_btn.setIconSize(QSize(14, 14))
        self._export_rsmf_btn.setToolTip("Export current search results to Relativity Short Message Format")
        self._export_rsmf_btn.clicked.connect(self.export_rsmf_requested.emit)
        search_right_layout.addWidget(_make_side_toolbar_row(trailing=self._export_rsmf_btn))
        self._search_empty_state = QWidget()
        search_empty_layout = QVBoxLayout(self._search_empty_state)
        search_empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        search_logo = QLabel()
        lockup_path = resolve_logo_path("guru_logo_lockup")
        if lockup_path.is_file():
            pix = QPixmap(str(lockup_path))
            if not pix.isNull():
                pix = pix.scaled(
                    320,
                    110,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                search_logo.setPixmap(pix)
        search_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        search_empty_layout.addWidget(search_logo)
        search_right_layout.addWidget(self._search_empty_state, 1)
        self._search_table_view = TableView()
        self._search_table_view.setVisible(False)
        search_right_layout.addWidget(self._search_table_view, 1)
        self._search_splitter.addWidget(search_right)
        self._search_splitter.setSizes([_LEFT_SIDEBAR_WIDTH, 680])
        _lock_horizontal_splitter(self._search_splitter)
        search_tab_layout = QVBoxLayout(search_tab)
        search_tab_layout.setContentsMargins(0, 0, 0, 0)
        search_tab_layout.addWidget(self._search_splitter)
        self._tabs.addTab(search_tab, "Search Messages")
        layout.addWidget(self._tabs)
        self._tabs.currentChanged.connect(self._on_search_tab_activated)
        self._app_data_root: Optional[Path] = None
        self._case_id: Optional[str] = None
        self._library_display_name: Optional[str] = None
        self._chats: List[dict] = []
        self._filtered_chats: List[dict] = []
        self._chat_id_to_messages: Dict[int, List[dict]] = {}
        self._all_messages: List[dict] = []
        self._search_results: List[dict] = []
        self._current_search_id: Optional[str] = None
        self._current_search_chunk_24h: bool = False
        self._current_search_name: str = ""
        self._suppress_search_selection_run: bool = False
        self._attachment_base: Optional[Path] = None
        self._current_chat_rowid: Optional[int] = None
        self._checked_chat_ids: Set[int] = set()
        self._rebuilding_chat_list: bool = False
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

    def set_app_data_root(
        self,
        path: Optional[Path],
        case_id: Optional[str] = None,
        library_display_name: Optional[str] = None,
    ) -> None:
        self._app_data_root = path
        self._case_id = case_id
        disp = (library_display_name or "").strip()
        self._library_display_name = disp or None
        self._saved_searches_tree.set_saved_search_storage(path, case_id)
        self._refresh_saved_searches_tree()

    def _on_search_tab_activated(self, index: int) -> None:
        if index == 2:
            self._refresh_saved_searches_tree()

    def _search_criteria_dict(self, s: dict, index: int) -> dict:
        return {
            "to_filter": s.get("to_filter", ""),
            "body_filter": s.get("body_filter", ""),
            "date_from": s.get("date_from", ""),
            "date_to": s.get("date_to", ""),
            "has_attachments": s.get("has_attachments", "any"),
            "hash_filter": s.get("hash_filter", ""),
            "chunk_24h": s.get("chunk_24h", False),
            "thread_ids": s.get("thread_ids") or [],
            "search_name": s.get("name") or "Search results",
            "sequence": s.get("sequence", index + 1),
            "search_id": s.get("id"),
        }

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
        if not self._app_data_root or not self._case_id:
            return

        self._suppress_search_selection_run = True
        try:
            folders = load_folders(
                self._app_data_root,
                self._case_id,
                library_display_name=self._library_display_name,
            )
            searches = load_saved_searches(
                self._app_data_root,
                self._case_id,
                library_display_name=self._library_display_name,
            )

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
                criteria = self._search_criteria_dict(s, i)
                seq = s.get("sequence") or (i + 1)
                name = s.get("name") or "Unnamed"
                item = QTreeWidgetItem()
                item.setText(0, f"[{seq:04d}] {name}")
                item.setIcon(0, load_icon("search"))
                item.setData(0, Qt.ItemDataRole.UserRole, ("search", s.get("id")))
                item.setData(0, _SEARCH_CRITERIA_ROLE, criteria)
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
        finally:
            self._suppress_search_selection_run = False

    def _on_saved_search_tree_selection(
        self,
        current: Optional[QTreeWidgetItem],
        _previous: Optional[QTreeWidgetItem],
    ) -> None:
        if self._suppress_search_selection_run or current is None:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or data[0] != "search":
            return
        criteria = current.data(0, _SEARCH_CRITERIA_ROLE)
        if isinstance(criteria, dict):
            self.search_selected.emit(criteria)

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
        if not self._app_data_root or not self._case_id:
            return
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return
        add_folder(self._app_data_root, self._case_id, name, parent_id=parent_id or LIBRARY_ROOT_FOLDER_ID)
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
            # Library root: searches only — no subfolders; cannot rename or delete (see saved_searches).
            if not is_library_root_folder_id(ident):
                menu.addAction(
                    "New subfolder",
                    lambda: self._create_folder(parent_id=ident),
                )
                menu.addSeparator()
                menu.addAction("Rename folder...", lambda: self._on_rename_folder(ident))
                menu.addAction("Delete folder...", lambda: self._on_delete_folder(ident))
        elif kind == "search":
            menu.addAction("Edit Search", lambda: self.edit_search_requested.emit(ident))
            menu.addAction("Rename Search", lambda: self._on_rename_search(ident))
            menu.addSeparator()
            menu.addAction("Delete Search", lambda: self._on_delete_search(ident))
        if menu.isEmpty():
            return
        menu.exec(tree.viewport().mapToGlobal(pos))

    def _on_rename_folder(self, folder_id: str) -> None:
        if is_library_root_folder_id(folder_id):
            return
        if not self._app_data_root or not self._case_id:
            return
        folders = load_folders(self._app_data_root, self._case_id)
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
        rename_folder(self._app_data_root, self._case_id, folder_id, name)
        self._refresh_saved_searches_tree()

    def _on_delete_folder(self, folder_id: str) -> None:
        if is_library_root_folder_id(folder_id):
            return
        if not self._app_data_root or not self._case_id:
            return
        folders = load_folders(self._app_data_root, self._case_id)
        searches = load_saved_searches(self._app_data_root, self._case_id)
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
        _, deleted_searches = delete_folder_cascade(self._app_data_root, self._case_id, folder_id)
        if deleted_searches and self._current_search_id is not None:
            remaining = {s.get("id") for s in load_saved_searches(self._app_data_root, self._case_id)}
            if self._current_search_id not in remaining:
                self.clear_search_results()
        self._refresh_saved_searches_tree()

    def _on_rename_search(self, search_id: str) -> None:
        if not self._app_data_root or not self._case_id:
            return
        searches = load_saved_searches(self._app_data_root, self._case_id)
        current = next((s for s in searches if s.get("id") == search_id), None)
        if current is None:
            return
        name, ok = QInputDialog.getText(
            self,
            "Rename Search",
            "Search name:",
            text=current.get("name") or "",
        )
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return
        update_saved_search(self._app_data_root, self._case_id, search_id, name=name)
        self._refresh_saved_searches_tree()

    def _on_delete_search(self, search_id: Optional[str]) -> None:
        """Delete a saved search and clear its results if currently displayed."""
        if not search_id or not self._app_data_root or not self._case_id:
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
        if delete_saved_search(self._app_data_root, self._case_id, search_id):
            if self._current_search_id == search_id:
                self.clear_search_results()
            self._refresh_saved_searches_tree()

    def _update_export_thread_btn(self) -> None:
        self._export_thread_btn.setEnabled(len(self._checked_chat_ids) >= 1)

    def _on_chat_item_changed(self, item: QListWidgetItem) -> None:
        if self._rebuilding_chat_list:
            return
        rowid = item.data(Qt.ItemDataRole.UserRole)
        if rowid is None:
            return
        cid = int(rowid)
        if item.checkState() == Qt.CheckState.Checked:
            self._checked_chat_ids.add(cid)
        else:
            self._checked_chat_ids.discard(cid)
        self._update_export_thread_btn()

    def _on_chat_selection(self, row: int) -> None:
        if row < 0 or not self._filtered_chats or row >= len(self._filtered_chats):
            return
        chat = self._filtered_chats[row]
        cid = chat.get("rowid")
        self._current_chat_rowid = cid
        msgs = self._chat_id_to_messages.get(cid, [])
        if not msgs:
            self._thread_view.set_messages([], self._attachment_base)
            return
        self._thread_view.begin_chunked_load(msgs, self._attachment_base)
        # Table view always shows all messages; do not change it here

    def _on_export_thread_clicked(self) -> None:
        if not self._checked_chat_ids:
            return
        ordered = [
            ch["rowid"]
            for ch in self._chats
            if ch.get("rowid") in self._checked_chat_ids
        ]
        self.export_threads_rsmf_requested.emit(ordered)

    def get_checked_threads_for_export(self) -> List[dict]:
        """Return {label, messages} dicts for each checked chat rowid."""
        out: List[dict] = []
        for ch in self._chats:
            cid = ch.get("rowid")
            if cid not in self._checked_chat_ids:
                continue
            out.append({
                "label": ch.get("label") or f"Chat {cid}",
                "messages": list(self._chat_id_to_messages.get(cid, [])),
            })
        return out

    def get_chat_id_to_label(self) -> Dict[int, str]:
        return {ch.get("rowid"): ch.get("label") or "" for ch in self._chats if ch.get("rowid") is not None}

    def get_chats_for_search_picker(self) -> List[dict]:
        """Thread rows for saved-search picker (same labels as sidebar)."""
        return list(self._chats)

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
        self._rebuilding_chat_list = True
        try:
            for ch in self._filtered_chats:
                cid = ch.get("rowid")
                item = QListWidgetItem(f"{ch['label']} ({ch['count']})")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setData(Qt.ItemDataRole.UserRole, cid)
                if cid in self._checked_chat_ids:
                    item.setCheckState(Qt.CheckState.Checked)
                else:
                    item.setCheckState(Qt.CheckState.Unchecked)
                self._chat_list.addItem(item)
        finally:
            self._rebuilding_chat_list = False
        self._update_export_thread_btn()
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
        self._checked_chat_ids = set()
        self._update_export_thread_btn()
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
        self._current_search_chunk_24h = False
        self._current_search_name = ""
        self._search_table_view.set_messages([], self._timezone_name)
        self._search_table_view.setVisible(False)
        self._search_empty_state.setVisible(True)
        if self._filtered_chats:
            self._chat_list.setCurrentRow(0)
            self._on_chat_selection(0)

    def show_thread_view(self) -> None:
        self._splitter.widget(0).setVisible(True)
        self._splitter.setSizes([_LEFT_SIDEBAR_WIDTH, 680])
        self._tabs.setCurrentIndex(0)

    def show_table_view(self) -> None:
        if self._tabs.isTabVisible(self._table_tab_index):
            self._tabs.setCurrentIndex(self._table_tab_index)

    def set_search_results(
        self,
        messages: List[dict],
        search_id: Optional[str] = None,
        *,
        chunk_24h: bool = False,
        search_name: str = "",
    ) -> None:
        """Populate the Search tab's table and switch to Search tab. Does not affect the Table tab."""
        sorted_msgs = sorted(
            messages,
            key=lambda x: (x.get("date_timestamp") or 0, x.get("rowid") or 0),
        )
        self._search_results = sorted_msgs
        self._current_search_id = search_id
        self._current_search_chunk_24h = bool(chunk_24h)
        self._current_search_name = (search_name or "").strip()
        self._search_table_view.set_messages(sorted_msgs, self._timezone_name)
        self._search_empty_state.setVisible(False)
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
        self._current_search_chunk_24h = False
        self._current_search_name = ""
        self._search_table_view.set_messages([], getattr(self, "_timezone_name", ""))
        self._search_table_view.setVisible(False)
        self._search_empty_state.setVisible(True)

    def get_search_results(self) -> List[dict]:
        """Return current search results for RSMF export."""
        return self._search_results

    def get_current_search_chunk_24h(self) -> bool:
        """Whether the active search results used 24-hour conversation chunking."""
        return self._current_search_chunk_24h

    def get_current_search_name(self) -> str:
        return self._current_search_name

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
