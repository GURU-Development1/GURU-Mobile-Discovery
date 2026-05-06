"""
Virtualized message thread: QListView + delegate so only visible rows are painted.
Large threads scroll smoothly; thumbnails are cached per path.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.timezone_utils import get_tz_abbrev_for_timestamp

from PyQt6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListView,
    QProgressBar,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

# Layout metrics (keep in sync between sizeHint and paint)
_MARGIN = 8           # outer margin around each row
_INNER_PAD = 10       # padding inside the bubble
_HEADER_H = 18        # sender/date row, drawn above the bubble
_HEADER_GAP = 4       # gap between header and bubble
_IMG_MAX = 320
_LINE_GAP = 4
_BUBBLE_MAX_FRAC = 0.65       # cap bubble at 65% of viewport width
_BUBBLE_HARD_MAX = 720        # never wider than this regardless of viewport
_BUBBLE_MIN_W = 120           # never narrower than this
_BUBBLE_RADIUS = 10
_CACHE_MAX = 200

# Bubble palette (kept in sync with app/style.py accent)
_OUT_BG = QColor("#4a73b8")
_OUT_BORDER = QColor("#3f64a3")
_OUT_TEXT = QColor("#ffffff")
_IN_BG = QColor("#ffffff")
_IN_BORDER = QColor("#e1e6ed")
_IN_TEXT = QColor("#1f2937")
_HEADER_SENDER = QColor("#3a4150")
_HEADER_DATE = QColor("#8b95a3")


def _mv():
    """Late import to avoid circular import with message_views."""
    import app.message_views as message_views

    return message_views


def _header_for_message(m: dict, timezone_name: str) -> Tuple[str, str]:
    mv = _mv()
    display = (m.get("display_name") or m.get("sender_display_name") or "").strip()
    sid = (m.get("sender_id") or "").strip()
    # Show phone/email next to name only when a resolved contact name differs from the raw handle.
    if display and sid and display.lower() != sid.lower():
        sender = f"{display} ({sid})"
    else:
        sender = display or sid or "?"
    date_str = (m.get("date_formatted") or "").strip() if m.get("date_formatted") else ""
    if not date_str:
        unix_ts = mv._message_unix_timestamp(m)
        if unix_ts is not None:
            date_str = mv._format_date_from_timestamp(unix_ts, timezone_name)
    if not date_str:
        date_str = "(no date)"
    if timezone_name and date_str and date_str != "(no date)":
        unix_ts = mv._message_unix_timestamp(m)
        if unix_ts is not None:
            abbrev = get_tz_abbrev_for_timestamp(unix_ts, timezone_name)
            if abbrev:
                date_str = date_str + f"  ({abbrev})"
    return sender, date_str


def _load_thumbnail(full_path: Path) -> Optional[QPixmap]:
    if not full_path.exists():
        return None
    pix = QPixmap(str(full_path))
    if not pix.isNull():
        if pix.width() > _IMG_MAX or pix.height() > _IMG_MAX:
            return pix.scaled(
                _IMG_MAX,
                _IMG_MAX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return pix
    try:
        from PIL import Image
        import io
        from PyQt6.QtGui import QImage

        data = full_path.read_bytes()
        img = Image.open(io.BytesIO(data))
        if getattr(img, "n_frames", 1) > 1:
            img.seek(0)
        img.thumbnail((_IMG_MAX, _IMG_MAX))
        if img.mode != "RGB":
            img = img.convert("RGB")
        h, w = img.size[1], img.size[0]
        qimg = QImage(img.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg)
    except Exception:
        return None


class ThreadMessageModel(QAbstractListModel):
    """One row per message; delegate reads model.messages[row]."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.messages: List[dict] = []
        self.timezone_name: str = ""

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.messages)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        return None


class ThreadBubbleDelegate(QStyledItemDelegate):
    """Paints message bubble; only invoked for visible rows."""

    def __init__(self, thread_view: "VirtualThreadView", parent=None):
        super().__init__(parent)
        self._view = thread_view
        self._thumb_cache: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._hit_regions: Dict[int, List[Tuple[QRect, Path]]] = {}

    def _cache_put(self, key: str, pix: QPixmap) -> QPixmap:
        self._thumb_cache[key] = pix
        self._thumb_cache.move_to_end(key)
        while len(self._thumb_cache) > _CACHE_MAX:
            self._thumb_cache.popitem(last=False)
        return pix

    def _get_thumb(self, full_path: Path) -> Optional[QPixmap]:
        key = str(full_path.resolve())
        if key in self._thumb_cache:
            self._thumb_cache.move_to_end(key)
            return self._thumb_cache[key]
        pix = _load_thumbnail(full_path)
        if pix is None or pix.isNull():
            return None
        return self._cache_put(key, pix)

    def _bubble_max_width(self, viewport_w: int) -> int:
        inner_w = max(0, viewport_w - _MARGIN * 2)
        return min(int(inner_w * _BUBBLE_MAX_FRAC), _BUBBLE_HARD_MAX)

    def _measure_bubble(
        self, m: dict, viewport_w: int, body_metrics: QFontMetrics
    ) -> Tuple[int, int, int]:
        """
        Compute (bubble_width, bubble_height, text_height) for a message.

        Bubble shrinks to fit content (text + attachments) up to the cap, and
        never narrower than _BUBBLE_MIN_W. Height accounts for body text plus
        each inline thumbnail and each non-inline attachment line.
        """
        mv = _mv()
        ab = self._view.attachment_base
        bubble_max_w = self._bubble_max_width(viewport_w)
        content_max_w = max(40, bubble_max_w - _INNER_PAD * 2)

        text = (m.get("text") or "").strip()
        text_h = 0
        text_w = 0
        if text:
            br = body_metrics.boundingRect(
                QRect(0, 0, content_max_w, 50_000),
                int(Qt.TextFlag.TextWordWrap),
                text,
            )
            text_h = br.height()
            text_w = br.width()

        attach_h = 0
        max_attach_w = 0
        has_attachments = False
        for att in m.get("attachments") or []:
            local_path = att.get("local_path")
            if (
                local_path
                and mv._is_image_attachment(att)
                and ab
                and (ab / local_path).exists()
            ):
                fp = ab / local_path
                pix = self._get_thumb(fp)
                if pix:
                    attach_h += pix.height() + _LINE_GAP
                    max_attach_w = max(max_attach_w, pix.width())
                else:
                    attach_h += 24 + _LINE_GAP
                has_attachments = True
        for att in m.get("attachments") or []:
            local_path = att.get("local_path")
            inline = (
                local_path
                and mv._is_image_attachment(att)
                and ab
                and (ab / local_path).exists()
            )
            if not inline:
                attach_h += 20 + _LINE_GAP
                # Reserve at least a reasonable label width
                max_attach_w = max(max_attach_w, 180)
                has_attachments = True

        desired_content_w = max(text_w, max_attach_w)
        bubble_w = max(_BUBBLE_MIN_W, min(bubble_max_w, desired_content_w + _INNER_PAD * 2))

        body_h = 0
        if text_h:
            body_h += text_h
        if has_attachments:
            if text_h:
                body_h += _LINE_GAP
            body_h += attach_h
        if body_h == 0:
            body_h = body_metrics.height()  # always reserve at least one line

        bubble_h = _INNER_PAD + body_h + _INNER_PAD
        return bubble_w, bubble_h, text_h

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        model = index.model()
        if not isinstance(model, ThreadMessageModel):
            return super().sizeHint(option, index)
        w = option.rect.width()
        if w <= 0:
            w = 400
        row = index.row()
        if row < 0 or row >= len(model.messages):
            return QSize(w, 40)
        m = model.messages[row]
        body_font = QFont(option.font)
        body_font.setBold(False)
        body_metrics = QFontMetrics(body_font)
        _, bubble_h, _ = self._measure_bubble(m, w, body_metrics)
        total_h = _MARGIN + _HEADER_H + _HEADER_GAP + bubble_h + _MARGIN
        return QSize(w, total_h)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        mv = _mv()
        model = index.model()
        if not isinstance(model, ThreadMessageModel):
            return
        row = index.row()
        if row < 0 or row >= len(model.messages):
            return
        m = model.messages[row]
        self._hit_regions[row] = []

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cell = option.rect
        is_outgoing = bool(m.get("is_from_me"))

        # Measure bubble using the same logic as sizeHint
        body_font = QFont(option.font)
        body_font.setBold(False)
        body_metrics = QFontMetrics(body_font)
        bubble_w, bubble_h, _ = self._measure_bubble(m, cell.width(), body_metrics)

        # Position bubble: outgoing aligns right, incoming aligns left
        inner_left = cell.left() + _MARGIN
        inner_right = cell.right() - _MARGIN
        if is_outgoing:
            bubble_left = inner_right - bubble_w + 1
        else:
            bubble_left = inner_left
        header_top = cell.top() + _MARGIN
        bubble_top = header_top + _HEADER_H + _HEADER_GAP

        bubble_rect = QRect(bubble_left, bubble_top, bubble_w, bubble_h)
        # Header spans across the canvas so sender + date never collide on narrow
        # bubbles. Anchored at the bubble's near edge; extends to the far edge.
        if is_outgoing:
            header_rect = QRect(
                inner_left,
                header_top,
                bubble_left + bubble_w - inner_left,
                _HEADER_H,
            )
        else:
            header_rect = QRect(
                bubble_left,
                header_top,
                inner_right - bubble_left + 1,
                _HEADER_H,
            )

        # Header (sender + date) — drawn outside the bubble, on the canvas
        tz = self._view.timezone_name
        ab = self._view.attachment_base
        sender, date_str = _header_for_message(m, tz)

        header_font = QFont(option.font)
        header_font.setBold(True)
        header_font.setPointSizeF(max(8.0, header_font.pointSizeF() - 1))
        painter.setFont(header_font)
        if is_outgoing:
            # Sender on the right (closer to bubble edge), date on the left
            painter.setPen(_HEADER_DATE)
            painter.drawText(
                header_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                date_str,
            )
            painter.setPen(_HEADER_SENDER)
            painter.drawText(
                header_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                sender,
            )
        else:
            painter.setPen(_HEADER_SENDER)
            painter.drawText(
                header_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                sender,
            )
            painter.setPen(_HEADER_DATE)
            painter.drawText(
                header_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                date_str,
            )

        # Bubble background
        bubble_bg = _OUT_BG if is_outgoing else _IN_BG
        bubble_border = _OUT_BORDER if is_outgoing else _IN_BORDER
        body_text_color = _OUT_TEXT if is_outgoing else _IN_TEXT

        path = QPainterPath()
        path.addRoundedRect(QRectF(bubble_rect), _BUBBLE_RADIUS, _BUBBLE_RADIUS)
        painter.fillPath(path, QBrush(bubble_bg))
        painter.strokePath(path, QPen(bubble_border))

        # Body content
        painter.setFont(body_font)
        painter.setPen(body_text_color)
        content_x = bubble_rect.left() + _INNER_PAD
        content_w = bubble_rect.width() - _INNER_PAD * 2
        y = bubble_rect.top() + _INNER_PAD

        text = (m.get("text") or "").strip()
        if text:
            br = body_metrics.boundingRect(
                QRect(0, 0, max(40, content_w), 50_000),
                int(Qt.TextFlag.TextWordWrap),
                text,
            )
            text_rect = QRect(content_x, y, content_w, br.height())
            painter.drawText(
                text_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
                text,
            )
            y += br.height() + _LINE_GAP

        origin = cell.topLeft()
        for att in m.get("attachments") or []:
            local_path = att.get("local_path")
            if (
                local_path
                and mv._is_image_attachment(att)
                and ab
                and (ab / local_path).exists()
            ):
                fp = ab / local_path
                pix = self._get_thumb(fp)
                if pix:
                    painter.drawPixmap(content_x, y, pix)
                    r = QRect(content_x, y, pix.width(), pix.height())
                    self._hit_regions[row].append((r.translated(-origin), fp))
                    y += pix.height() + _LINE_GAP
                else:
                    y += 24 + _LINE_GAP

        for att in m.get("attachments") or []:
            local_path = att.get("local_path")
            show_inline = (
                local_path
                and mv._is_image_attachment(att)
                and ab
                and (ab / local_path).exists()
            )
            if not show_inline:
                name = att.get("transfer_name") or att.get("filename") or "Attachment"
                painter.drawText(
                    QRect(content_x, y, content_w, 20),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    f"[Attachment: {name}]",
                )
                y += 20 + _LINE_GAP

        painter.restore()

    def editorEvent(self, event: QEvent, model, option: QStyleOptionViewItem, index: QModelIndex) -> bool:
        if event.type() != QEvent.Type.MouseButtonRelease:
            return False
        me = event
        if not isinstance(me, QMouseEvent) or me.button() != Qt.MouseButton.LeftButton:
            return False
        view = self.parent()
        if not isinstance(view, QListView):
            return False
        pos = me.position().toPoint()
        vr = view.visualRect(index)
        local = pos - vr.topLeft()
        for r, path in self._hit_regions.get(index.row(), []):
            if r.contains(local):
                self._view.image_clicked.emit(path)
                return True
        return False


class VirtualThreadView(QWidget):
    """Thread UI with QListView virtualization (fast scroll for large chats)."""
    image_clicked = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        loading_page = QWidget()
        loading_layout = QVBoxLayout(loading_page)
        loading_layout.addStretch()
        loading_label = QLabel("Loading conversation…")
        loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_label.setProperty("class", "placeholder")
        loading_label.setWordWrap(True)
        self._loading_bar = QProgressBar()
        self._loading_bar.setRange(0, 0)
        self._loading_bar.setMinimumWidth(280)
        self._loading_bar.setMaximumWidth(420)
        loading_layout.addWidget(loading_label)
        loading_layout.addWidget(self._loading_bar, alignment=Qt.AlignmentFlag.AlignHCenter)
        loading_layout.addStretch()

        self._list = QListView()
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setUniformItemSizes(False)

        self._model = ThreadMessageModel(self)
        self._delegate = ThreadBubbleDelegate(self, self._list)
        self._list.setModel(self._model)
        self._list.setItemDelegate(self._delegate)

        self._stack = QStackedWidget()
        self._stack.addWidget(loading_page)
        self._stack.addWidget(self._list)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)
        self._stack.setCurrentIndex(1)

        self.attachment_base: Optional[Path] = None
        self.timezone_name: str = ""
        self._load_seq: int = 0

    def set_attachment_base_path(self, base: Optional[Path]) -> None:
        self.attachment_base = base

    def set_timezone(self, timezone_name: str) -> None:
        self.timezone_name = timezone_name or ""

    def set_messages(self, messages: List[dict], attachment_base: Optional[Path] = None) -> None:
        self.attachment_base = attachment_base
        self._model.timezone_name = self.timezone_name
        self._model.beginResetModel()
        self._model.messages = list(messages)
        self._model.endResetModel()
        self._loading_bar.setRange(0, 0)
        self._stack.setCurrentIndex(1)
        if messages:
            self._list.scrollToTop()
        QTimer.singleShot(0, self._relayout)

    def begin_chunked_load(self, messages: List[dict], attachment_base: Optional[Path]) -> None:
        """Swap model data; list view only paints visible rows (fast)."""
        self._load_seq += 1
        lid = self._load_seq
        self.attachment_base = attachment_base
        if not messages:
            self._model.beginResetModel()
            self._model.messages = []
            self._model.endResetModel()
            self._loading_bar.setRange(0, 0)
            self._stack.setCurrentIndex(1)
            return

        self._stack.setCurrentIndex(0)
        QTimer.singleShot(0, lambda: self._finish_load(messages, attachment_base, lid))

    def _finish_load(self, messages: List[dict], attachment_base: Optional[Path], load_id: int) -> None:
        if load_id != self._load_seq:
            return
        self.attachment_base = attachment_base
        self._model.timezone_name = self.timezone_name
        self._model.beginResetModel()
        self._model.messages = list(messages)
        self._model.endResetModel()
        self._loading_bar.setRange(0, 0)
        self._stack.setCurrentIndex(1)
        self._list.scrollToTop()
        QTimer.singleShot(0, self._relayout)

    def _relayout(self) -> None:
        self._list.doItemsLayout()
        self._list.viewport().update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._model.rowCount() > 0:
            QTimer.singleShot(0, self._relayout)
