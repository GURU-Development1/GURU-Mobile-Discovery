"""
Main window: case tree (left), message views (right). Context menu: Delete, Rename, Import backup.
"""

from __future__ import annotations

import copy
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QSplitter,
    QMenu,
    QMessageBox,
    QInputDialog,
    QPushButton,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QFrame,
    QApplication,
    QDialog,
    QProgressBar,
    QProgressDialog,
    QStyle,
    QStyleFactory,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QProxyStyle,
    QLineEdit,
    QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QRect, QPoint
from PyQt6.QtGui import QAction, QCloseEvent, QMouseEvent, QIcon, QPixmap, QPainter, QColor, QBrush, QPalette, QPolygon

from .import_dialog import ImportBackupDialog
from .search_dialog import SearchDialog
from .export_dialog import ExportRsmfDialog
from .thread_export_dialog import ThreadExportDialog
from .thread_export_preview_dialog import ThreadExportPreviewDialog
from .message_views import MessageViews, SavedSearchesTreeProxyStyle
from .style import icon as load_icon, logo_path as resolve_logo_path
from . import cache
from .import_worker import run_import, extract_attachments_to_cache
from app.paths import get_app_data_root
from app.backup_parser import resolve_display_name, is_placeholder_chat_identifier
from app.backup_parser.parser import backup_appears_encrypted
from app.search_logic import run_search, expand_results_for_rsmf_export
from app.version import __version__


def _app_data_root() -> Path:
    return get_app_data_root()


def _cases_file() -> Path:
    return _app_data_root() / "cases.json"


def _load_cases() -> Dict[str, str]:
    """case_id -> name"""
    import json
    path = _cases_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("cases", {})
    except Exception:
        return {}


def _save_cases(cases: Dict[str, str]) -> None:
    import json
    _cases_file().write_text(json.dumps({"cases": cases}, indent=2), encoding="utf-8")


class CaseTreeProxyStyle(QProxyStyle):
    """Disclosure chevrons for the case tree.

    Two differences from SavedSearchesTreeProxyStyle:
      - The chevron is always painted in a dark gray, even when the case row is
        selected (the saved-searches proxy flips to white on selection).
      - The chevron is nudged a few pixels to the right within its column so it
        sits visually inside the rounded selection pill instead of hugging the
        pill's left edge.
    """

    _CHEVRON_GRAY = QColor("#6b7280")
    _CHEVRON_RIGHT_OFFSET = 5

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
        if not (option.state & QStyle.StateFlag.State_Children):
            super().drawPrimitive(element, option, painter, widget)
            return
        rect = option.rect
        if rect.width() < 4 or rect.height() < 4:
            return
        expanded = bool(option.state & QStyle.StateFlag.State_Open)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(self._CHEVRON_GRAY)
        painter.setPen(Qt.PenStyle.NoPen)

        cx = rect.center().x() + self._CHEVRON_RIGHT_OFFSET
        cy = rect.center().y()
        # Half-size chevron: ~8px wide / ~5px tall instead of ~14px / ~11px.
        cell_h = min(rect.height(), 22)
        half_w = max(2, min(5, cell_h // 6))

        if expanded:
            points = [
                QPoint(cx - half_w, cy - 2),
                QPoint(cx + half_w, cy - 2),
                QPoint(cx, cy + max(2, half_w)),
            ]
        else:
            points = [
                QPoint(cx - 2, cy - half_w),
                QPoint(cx + max(2, half_w), cy),
                QPoint(cx - 2, cy + half_w),
            ]
        painter.drawPolygon(QPolygon(points))
        painter.restore()


class CaseTreeDelegate(QStyledItemDelegate):
    """Paints case/backup item contents without any selection / hover background.

    The rounded pill is drawn by CaseTreeWidget.drawRow per item. The delegate
    just renders the icon and label, with the text forced to white when the row
    is selected so the label stays readable on the blue pill. Clearing
    State_Selected / State_MouseOver here also prevents Qt's QStyle code path
    from painting a stray rectangular selection fill on top of our pill.
    """

    _SELECTED_TEXT = QColor("#ffffff")

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        was_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        opt.state &= ~(
            QStyle.StateFlag.State_Selected
            | QStyle.StateFlag.State_MouseOver
            | QStyle.StateFlag.State_HasFocus
        )
        opt.backgroundBrush = QBrush(Qt.GlobalColor.transparent)
        if was_selected:
            opt.palette.setColor(QPalette.ColorRole.Text, self._SELECTED_TEXT)
            opt.palette.setColor(QPalette.ColorRole.WindowText, self._SELECTED_TEXT)
            opt.palette.setColor(QPalette.ColorRole.HighlightedText, self._SELECTED_TEXT)
        super().paint(painter, opt, index)


class CaseTreeWidget(QTreeWidget):
    """Case/backup tree; empty-area clicks do not clear the current backup selection.

    Visual model:
      - A **case** row paints a single rounded blue pill that spans from the
        viewport's left edge to its right edge, so the disclosure chevron,
        briefcase icon, and case label all live inside one continuous element.
      - A **backup** row paints its own rounded pill that starts at the item
        rect (i.e. just before the phone icon) — there's no chevron column for
        children, so the empty space to the left of the backup pill stays
        transparent. The backup pill therefore reads as a self-contained piece
        offset slightly to the right, sitting visually underneath its parent
        case rather than being tabbed deeply.

    The pill is painted directly in drawRow. The widget's palette also forces
    QPalette.Highlight to transparent across every color group (Active,
    Inactive, Disabled) so Qt's built-in selection painting — which on Windows
    falls back to a dark grey for inactive selections — cannot leak through
    behind our custom pill.
    """

    _SELECTED_BG = QColor("#8fadda")
    _HOVER_BG = QColor("#dde6f3")
    _ROW_RADIUS = 6
    _ROW_HMARGIN = 2  # small inset from the viewport edges
    _ROW_VMARGIN = 1  # vertical breathing room above/below the pill
    _BACKUP_LEFT_INSET = 4  # extra px of pill before the backup's icon

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CaseImportTree")
        self._suppress_restore_selection = False
        base = QStyleFactory.create("Fusion") or QApplication.style()
        proxy = CaseTreeProxyStyle(base)
        proxy.setParent(self)
        self.setStyle(proxy)
        self.setItemDelegate(CaseTreeDelegate(self))
        # Hover must register over the chevron column too, not just the item
        # rect, so the case pill highlights cleanly when the cursor is over the
        # disclosure arrow.
        self.setMouseTracking(True)
        # Force Qt's default selection highlight to transparent across every
        # palette color group so the system selection color (a dark grey on
        # Windows when the tree is unfocused) cannot paint behind our pill.
        pal = self.palette()
        transparent = QColor(0, 0, 0, 0)
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            pal.setColor(group, QPalette.ColorRole.Highlight, transparent)
        self.setPalette(pal)
        # Qt invalidates only each item's visualRect on selection change, but
        # our custom pill is wider than the visualRect (it spans the full row).
        # Without a full-viewport repaint a thin "sliver" of the previous pill
        # can linger to the right of a deselected row until the next paint.
        self.itemSelectionChanged.connect(self.viewport().update)

    def drawRow(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        item = self.itemFromIndex(index)
        selected = item is not None and item.isSelected()
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            is_top_level = item is not None and item.parent() is None
            viewport_rect = self.viewport().rect()
            if is_top_level:
                # Case pill extends to the very left of the viewport so the
                # chevron + briefcase + label are all visually inside one pill.
                left = viewport_rect.left() + self._ROW_HMARGIN
            else:
                # Backup pill starts just before the item rect (after the
                # indent), giving the row a self-contained pill that's offset
                # to the right of its parent case pill.
                item_rect = self.visualRect(index)
                left = max(
                    viewport_rect.left() + self._ROW_HMARGIN,
                    item_rect.left() - self._BACKUP_LEFT_INSET,
                )
            right = viewport_rect.right() - self._ROW_HMARGIN
            pill = QRect(
                left,
                option.rect.y() + self._ROW_VMARGIN,
                max(0, right - left),
                option.rect.height() - 2 * self._ROW_VMARGIN,
            )
            color = self._SELECTED_BG if selected else self._HOVER_BG
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(pill, self._ROW_RADIUS, self._ROW_RADIUS)
            painter.restore()
        super().drawRow(painter, option, index)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._suppress_restore_selection = True
        elif event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if item is None:
                event.accept()
                return
        super().mousePressEvent(event)


class BackupLoadWorker(QThread):
    """Load backup from cache and resolve contacts in background."""

    # Do not name this "finished" — that shadows QThread.finished and breaks thread lifecycle.
    load_result = pyqtSignal(dict)  # result with meta, data, case_id, backup_id
    error = pyqtSignal(str)
    progress = pyqtSignal(int, str)  # 0–100, status label

    def __init__(self, app_data_root: Path, case_id: str, backup_id: str):
        super().__init__()
        self._app_data_root = app_data_root
        self._case_id = case_id
        self._backup_id = backup_id

    def _emit_progress(self, pct: int, label: str) -> None:
        self.progress.emit(max(0, min(100, pct)), label)

    def run(self) -> None:
        try:
            self._emit_progress(5, "Reading backup info…")
            meta = cache.load_backup_meta(self._app_data_root, self._case_id, self._backup_id)
            self._emit_progress(18, "Loading messages from cache…")
            data = cache.load_backup_messages(self._app_data_root, self._case_id, self._backup_id)
            if not data:
                self.error.emit("Could not load cached messages.")
                return
            chats = data.get("chats") or []
            messages = data.get("messages") or []
            n_chats = len(chats)
            n_msg = len(messages)

            self._emit_progress(32, "Processing chats and contacts…")
            contact_map = data.get("contact_map") or {}
            if contact_map:
                for i, c in enumerate(chats):
                    ident = c.get("chat_identifier") or ""
                    if is_placeholder_chat_identifier(ident) and c.get("participant_handle_ids"):
                        names = sorted(
                            set(
                                resolve_display_name(pid, contact_map) or pid
                                for pid in c["participant_handle_ids"]
                                if pid
                            )
                        )
                        c["display_name"] = ", ".join(names) if names else ident
                    else:
                        resolved = resolve_display_name(ident, contact_map)
                        c["display_name"] = resolved or ident
                    if n_chats > 50 and i % max(1, n_chats // 12) == 0:
                        self._emit_progress(32 + int(12 * (i + 1) / n_chats), "Processing chats…")
                self._emit_progress(48, "Resolving senders…")
                for i, m in enumerate(messages):
                    if not m.get("is_from_me"):
                        resolved = resolve_display_name(m.get("sender_id") or "", contact_map)
                        m["display_name"] = resolved or m.get("sender_id") or ""
                    if n_msg > 100 and i % max(1, n_msg // 15) == 0:
                        self._emit_progress(48 + int(17 * (i + 1) / n_msg), "Resolving senders…")
            else:
                for i, c in enumerate(chats):
                    ident = c.get("chat_identifier") or ""
                    if is_placeholder_chat_identifier(ident) and c.get("participant_handle_ids"):
                        c["display_name"] = ", ".join(pid for pid in c["participant_handle_ids"] if pid) or ident
                    else:
                        c["display_name"] = ident
                    if n_chats > 50 and i % max(1, n_chats // 12) == 0:
                        self._emit_progress(32 + int(28 * (i + 1) / n_chats), "Processing chats…")

            self._emit_progress(68, "Building conversation views…")
            chat_by_id = {c.get("rowid"): c for c in chats if c.get("rowid") is not None}
            custodian_name = str((meta or {}).get("custodian") or "").strip()
            for i, m in enumerate(messages):
                cid = m.get("chat_id")
                chat = chat_by_id.get(cid) if cid is not None else None
                if chat:
                    pids = list(chat.get("participant_handle_ids") or [])
                    ident = chat.get("chat_identifier") or ""
                    if ident and ident not in pids and not is_placeholder_chat_identifier(ident):
                        pids.append(ident)
                    m["participant_handle_ids"] = pids
                else:
                    m["participant_handle_ids"] = []
                sender_name = m.get("display_name") or m.get("sender_id") or ""
                if chat and (chat.get("participant_handle_ids") or m.get("participant_handle_ids")):
                    pids = list(m.get("participant_handle_ids") or chat.get("participant_handle_ids") or [])
                    ident = chat.get("chat_identifier") or ""
                    if ident and ident not in pids and not is_placeholder_chat_identifier(ident):
                        pids.append(ident)
                    if contact_map:
                        names = sorted(
                            set(resolve_display_name(p, contact_map) or p for p in pids if p)
                            | {sender_name} | ({custodian_name} if custodian_name else set())
                        )
                    else:
                        names = sorted(set(p for p in pids if p) | {sender_name} | ({custodian_name} if custodian_name else set()))
                    m["to_display"] = ", ".join(names) if names else (chat.get("display_name") or chat.get("chat_identifier") or "")
                else:
                    extra = {custodian_name} if custodian_name else set()
                    fallback = (chat.get("display_name") or chat.get("chat_identifier") or "") if chat else ""
                    m["to_display"] = ", ".join(sorted(extra | {sender_name, fallback} - {""})) if (extra or sender_name or fallback) else fallback
                if n_msg > 200 and i % max(1, n_msg // 20) == 0:
                    self._emit_progress(68 + int(30 * (i + 1) / n_msg), "Preparing message list…")

            self._emit_progress(100, "Opening backup…")
            self.load_result.emit({
                "meta": meta,
                "data": data,
                "case_id": self._case_id,
                "backup_id": self._backup_id,
            })
        except Exception as e:
            self.error.emit(str(e))


class ImportWorker(QThread):
    progress = pyqtSignal(float, str)
    # Do not name this "finished" — that shadows QThread.finished and breaks thread lifecycle.
    import_finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(
        self,
        backup_path: str,
        case_id: str,
        custodian: str,
        timezone_name: str,
        passphrase: Optional[str],
        app_data_root: Path,
        control_prefix: str = "",
        control_padding: int = 6,
        backup_label: str = "",
        populate_table_tab: bool = True,
        extract_attachments: bool = False,
    ):
        super().__init__()
        self.backup_path = backup_path
        self.case_id = case_id
        self.custodian = custodian
        self.timezone_name = timezone_name
        self.passphrase = passphrase
        self.app_data_root = app_data_root
        self.control_prefix = control_prefix
        self.control_padding = control_padding
        self.backup_label = backup_label
        self.populate_table_tab = populate_table_tab
        self.extract_attachments = extract_attachments

    def run(self) -> None:
        try:
            result = run_import(
                self.backup_path,
                self.case_id,
                self.custodian,
                self.timezone_name,
                self.passphrase,
                self.app_data_root,
                lambda pct, label: self.progress.emit(pct, label),
                control_prefix=self.control_prefix,
                control_padding=self.control_padding,
                backup_label=self.backup_label,
                populate_table_tab=self.populate_table_tab,
                extract_attachments=self.extract_attachments,
            )
            self.import_finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class ExtractAttachmentsWorker(QThread):
    progress = pyqtSignal(float, str)
    extract_finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(
        self,
        backup_path: str,
        passphrase: Optional[str],
        app_data_root: Path,
        case_id: str,
        backup_id: str,
    ):
        super().__init__()
        self.backup_path = backup_path
        self.passphrase = passphrase
        self.app_data_root = app_data_root
        self.case_id = case_id
        self.backup_id = backup_id

    def run(self) -> None:
        try:
            extract_attachments_to_cache(
                self.backup_path,
                self.passphrase,
                self.app_data_root,
                self.case_id,
                self.backup_id,
                lambda pct, label: self.progress.emit(pct, label),
            )
            self.extract_finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self, license_service=None):
        super().__init__()
        self._license_service = license_service
        self.setWindowTitle("GURU Mobile Discovery — Cases")
        self.setMinimumSize(1000, 600)
        self.resize(1200, 700)

        file_menu = self.menuBar().addMenu("&File")
        new_case_action = QAction("New Case…", self)
        new_case_action.setShortcut("Ctrl+N")
        new_case_action.triggered.connect(self._add_case)
        file_menu.addAction(new_case_action)
        open_case_action = QAction("Open Case…", self)
        open_case_action.setShortcut("Ctrl+O")
        open_case_action.triggered.connect(self._on_open_case)
        file_menu.addAction(open_case_action)

        options_menu = self.menuBar().addMenu("&Options")
        self._extract_attachments_action = QAction("Extract Attachments…", self)
        self._extract_attachments_action.setToolTip(
            "Extract attachment files from the original backup into the cache (inline images in threads)."
        )
        self._extract_attachments_action.setEnabled(False)
        self._extract_attachments_action.triggered.connect(self._on_extract_attachments)
        options_menu.addAction(self._extract_attachments_action)
        self._populate_table_action = QAction("Populate Table View…", self)
        self._populate_table_action.setToolTip(
            "Build the full message grid for the Table View tab (use if you skipped it during import)."
        )
        self._populate_table_action.setEnabled(False)
        self._populate_table_action.triggered.connect(self._on_populate_table_view)
        options_menu.addAction(self._populate_table_action)

        help_menu = self.menuBar().addMenu("&Help")
        help_action = QAction("Help", self)
        help_action.setShortcut("F1")
        help_action.triggered.connect(self._on_help)
        help_menu.addAction(help_action)
        help_menu.addSeparator()
        change_license_action = QAction("Change License Key…", self)
        change_license_action.triggered.connect(self._on_change_license)
        help_menu.addAction(change_license_action)
        remove_license_action = QAction("Remove License from This Device…", self)
        remove_license_action.triggered.connect(self._on_remove_license)
        help_menu.addAction(remove_license_action)
        help_menu.addSeparator()
        about_action = QAction("About", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

        self._app_data_root = _app_data_root()
        self._cases = _load_cases()
        self._case_id_to_backups: Dict[str, List[str]] = {}  # case_id -> [backup_id, ...]
        for cid in self._cases:
            self._case_id_to_backups[cid] = cache.list_backup_ids(self._app_data_root, cid)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.setObjectName("MainCaseSplitter")

        # Case tree
        self._tree_container = QWidget()
        tree_layout = QVBoxLayout(self._tree_container)
        tree_layout.setContentsMargins(8, 8, 4, 8)
        tree_layout.setSpacing(8)
        cases_header = QHBoxLayout()
        self._cases_heading = QLabel("CASES")
        self._cases_heading.setProperty("class", "section-heading")
        cases_header.addWidget(self._cases_heading)
        cases_header.addStretch()
        self._toggle_cases_btn = QPushButton()
        self._toggle_cases_btn.setProperty("class", "icon-btn")
        self._toggle_cases_btn.setFixedSize(28, 28)
        self._toggle_cases_btn.setIconSize(QSize(14, 14))
        self._toggle_cases_btn.clicked.connect(self._toggle_cases_panel)
        cases_header.addWidget(self._toggle_cases_btn)
        tree_layout.addLayout(cases_header)
        self._add_case_btn = QPushButton("  New Case")
        self._add_case_btn.setIcon(load_icon("plus"))
        self._add_case_btn.setIconSize(QSize(14, 14))
        self._add_case_btn.clicked.connect(self._add_case)
        tree_layout.addWidget(self._add_case_btn)
        self._tree = CaseTreeWidget()
        self._tree.setHeaderLabels(["Cases"])
        self._tree.setHeaderHidden(True)
        self._tree.setItemsExpandable(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(18)
        self._tree.setIconSize(QSize(16, 16))
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree.currentItemChanged.connect(self._on_tree_selection_changed)
        tree_layout.addWidget(self._tree)
        self._main_splitter.addWidget(self._tree_container)
        self._tree.setMinimumWidth(280)
        self._item_to_case_id: Dict[int, str] = {}  # id(item) -> case_id
        self._item_to_backup_id: Dict[int, Optional[str]] = {}  # id(item) -> backup_id or None

        # Right: content area
        right = QWidget()
        right_layout = QVBoxLayout(right)
        # Match the case-tree container's vertical margins (8 top / 8 bottom) so
        # the bottom edge of the right panel lines up with the bottom edge of
        # the cases list instead of sitting a few px lower from Qt's default
        # (11 px) layout margins.
        right_layout.setContentsMargins(4, 8, 8, 8)
        self._stack = QStackedWidget()
        self._placeholder = self._build_empty_placeholder()
        self._stack.addWidget(self._placeholder)
        self._message_panel = QWidget()
        msg_panel_layout = QVBoxLayout(self._message_panel)
        # Drop Qt's default ~11 px layout margins so the tabs fill the right
        # side fully and the panel's bottom edge lines up with the case-tree
        # panel's bottom edge.
        msg_panel_layout.setContentsMargins(0, 0, 0, 0)
        self._message_views = MessageViews()
        self._message_views.add_search_requested.connect(self._open_search_dialog)
        self._message_views.edit_search_requested.connect(self._open_edit_search_dialog)
        self._message_views.run_saved_search_requested.connect(
            lambda criteria: self._run_search(criteria, notify=True)
        )
        self._message_views.search_selected.connect(
            lambda criteria: self._run_search(criteria, notify=False)
        )
        self._message_views.export_rsmf_requested.connect(self._open_export_rsmf_dialog)
        self._message_views.export_threads_rsmf_requested.connect(self._open_export_threads_dialog)
        self._message_views.full_table_tab_loaded.connect(self._on_full_table_tab_loaded)
        msg_panel_layout.addWidget(self._message_views)
        self._stack.addWidget(self._message_panel)
        # Inline import progress panel (replaces popup dialog)
        self._import_progress_panel = QWidget()
        import_progress_layout = QVBoxLayout(self._import_progress_panel)
        import_progress_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._import_progress_label = QLabel("Importing backup...")
        self._import_progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        import_progress_layout.addWidget(self._import_progress_label)
        self._import_progress_bar = QProgressBar()
        self._import_progress_bar.setRange(0, 100)
        self._import_progress_bar.setValue(0)
        self._import_progress_bar.setMinimumWidth(400)
        import_progress_layout.addWidget(self._import_progress_bar, 0, Qt.AlignmentFlag.AlignCenter)
        self._import_warning_label = QLabel("Please do not close the application while the backup is importing.")
        self._import_warning_label.setProperty("class", "placeholder")
        self._import_warning_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        import_progress_layout.addWidget(self._import_warning_label, 0, Qt.AlignmentFlag.AlignCenter)
        self._import_cancel_btn = QPushButton("Cancel")
        self._import_cancel_btn.clicked.connect(self._on_import_cancel)
        import_progress_layout.addWidget(self._import_cancel_btn, 0, Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(self._import_progress_panel)
        # Loading overlay when opening a backup (prevents UI freeze)
        self._loading_overlay = QWidget()
        loading_layout = QVBoxLayout(self._loading_overlay)
        loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label = QLabel("Loading backup...")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(self._loading_label)
        self._loading_bar = QProgressBar()
        self._loading_bar.setRange(0, 100)
        self._loading_bar.setValue(0)
        self._loading_bar.setTextVisible(True)
        self._loading_bar.setFormat("%p%")
        self._loading_bar.setMinimumWidth(300)
        loading_layout.addWidget(self._loading_bar, 0, Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(self._loading_overlay)
        right_layout.addWidget(self._stack)
        self._main_splitter.addWidget(right)
        self._main_splitter.setSizes([320, 880])
        self._main_splitter.setHandleWidth(0)
        self._main_splitter.setCollapsible(0, True)
        self._main_splitter.setCollapsible(1, False)
        if self._main_splitter.count() >= 2:
            self._main_splitter.handle(0).setEnabled(False)
        layout.addWidget(self._main_splitter)

        self._cases_panel_expanded = True
        self._cases_panel_width = 320
        self._sync_cases_panel_toggle()

        self._rebuild_tree()
        self._current_case_id: Optional[str] = None
        self._current_backup_id: Optional[str] = None
        self._current_timezone: str = ""
        self._current_custodian: str = ""
        self._import_worker: Optional[ImportWorker] = None
        self._extract_attachments_worker: Optional[ExtractAttachmentsWorker] = None
        self._load_backup_worker: Optional[BackupLoadWorker] = None
        self._load_backup_timer = QTimer(self)
        self._load_backup_timer.setSingleShot(True)
        self._load_backup_timer.timeout.connect(self._on_load_backup_timer)
        self._pending_backup_item: Optional[QTreeWidgetItem] = None
        # In-memory LRU of last loaded backup payloads (instant re-switch between backups)
        self._BACKUP_CACHE_MAX = 3
        self._backup_load_cache: "OrderedDict[tuple[str, str], dict]" = OrderedDict()
        self._refresh_file_menu_backup_actions()

    def _build_empty_placeholder(self) -> QWidget:
        """Centered logo + heading + subtitle shown when no backup is selected."""
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.setSpacing(14)

        logo_label = QLabel()
        lockup_path = resolve_logo_path("guru_logo_lockup")
        if lockup_path.is_file():
            pix = QPixmap(str(lockup_path))
            if not pix.isNull():
                pix = pix.scaled(
                    320, 110,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                logo_label.setPixmap(pix)
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(logo_label)

        title = QLabel("No backup selected")
        title.setProperty("class", "placeholder-title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)

        subtitle = QLabel("Select a case or backup from the list on the left to view messages.")
        subtitle.setProperty("class", "placeholder-subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        return w

    def _cases_panel_toggle_icon(self, expanded: bool) -> QIcon:
        pixmap = (
            QStyle.StandardPixmap.SP_ArrowLeft if expanded else QStyle.StandardPixmap.SP_ArrowRight
        )
        return self.style().standardIcon(pixmap)

    def _sync_cases_panel_toggle(self) -> None:
        expanded = self._cases_panel_expanded
        self._toggle_cases_btn.setIcon(self._cases_panel_toggle_icon(expanded))
        self._toggle_cases_btn.setToolTip(
            "Hide cases panel" if expanded else "Show cases panel"
        )

    def _toggle_cases_panel(self) -> None:
        self._set_cases_panel_expanded(not self._cases_panel_expanded)

    def _set_cases_panel_expanded(self, expanded: bool) -> None:
        self._cases_panel_expanded = expanded
        self._cases_heading.setVisible(expanded)
        self._add_case_btn.setVisible(expanded)
        self._tree.setVisible(expanded)
        self._sync_cases_panel_toggle()

        sizes = self._main_splitter.sizes()
        total = max(sum(sizes), 1)
        if expanded:
            self._tree_container.setMinimumWidth(280)
            self._tree_container.setMaximumWidth(16777215)
            width = max(self._cases_panel_width, 280)
            self._main_splitter.setSizes([width, max(100, total - width)])
        else:
            if sizes[0] > 60:
                self._cases_panel_width = sizes[0]
            self._tree_container.setMinimumWidth(0)
            self._tree_container.setMaximumWidth(48)
            collapsed_width = 40
            self._main_splitter.setSizes([collapsed_width, max(100, total - collapsed_width)])

    def _refresh_file_menu_backup_actions(self) -> None:
        has_backup = bool(self._current_case_id and self._current_backup_id)
        on_messages = self._stack.currentWidget() == self._message_panel
        self._extract_attachments_action.setEnabled(has_backup and on_messages)
        self._populate_table_action.setEnabled(has_backup and on_messages)

    def _backup_cache_put(self, key: tuple[str, str], value: dict) -> None:
        """Store a deep copy of the load result (pre–set_data mutation)."""
        if key in self._backup_load_cache:
            self._backup_load_cache.move_to_end(key)
        self._backup_load_cache[key] = value
        while len(self._backup_load_cache) > self._BACKUP_CACHE_MAX:
            self._backup_load_cache.popitem(last=False)

    def _backup_cache_invalidate(self, case_id: Optional[str] = None, backup_id: Optional[str] = None) -> None:
        if case_id and backup_id:
            self._backup_load_cache.pop((case_id, backup_id), None)
        elif case_id:
            for k in list(self._backup_load_cache.keys()):
                if k[0] == case_id:
                    del self._backup_load_cache[k]
        else:
            self._backup_load_cache.clear()

    def _show_loading_overlay(self, label: str = "Loading backup…") -> None:
        self._loading_label.setText(label)
        # Indeterminate (sweeping) progress bar until the worker emits its first real percent.
        self._loading_bar.setRange(0, 0)
        self._loading_bar.setValue(0)
        self._stack.setCurrentWidget(self._loading_overlay)

    def _add_case(self) -> None:
        name, ok = QInputDialog.getText(self, "New Case", "Case name:")
        if ok and name.strip():
            case_id = str(uuid.uuid4())
            self._cases[case_id] = name.strip()
            self._case_id_to_backups[case_id] = []
            _save_cases(self._cases)
            self._rebuild_tree()

    def _on_open_case(self) -> None:
        if not self._cases:
            QMessageBox.information(
                self,
                "Open case",
                "No cases yet. Use File > New Case to create one.",
            )
            return
        names = sorted(self._cases.values())
        name, ok = QInputDialog.getItem(self, "Open case", "Select a case:", names, 0, False)
        if not ok or not name:
            return
        target_id = next((cid for cid, n in self._cases.items() if n == name), None)
        if target_id is None:
            return
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if self._item_to_case_id.get(id(item)) == target_id:
                item.setExpanded(True)
                self._tree.setCurrentItem(item)
                break

    def _rebuild_tree(self) -> None:
        self._tree.clear()
        self._item_to_case_id.clear()
        self._item_to_backup_id.clear()
        for case_id, name in self._cases.items():
            case_item = QTreeWidgetItem(self._tree, [name])
            case_item.setData(0, Qt.ItemDataRole.UserRole, ("case", case_id))
            self._item_to_case_id[id(case_item)] = case_id
            self._item_to_backup_id[id(case_item)] = None
            for backup_id in self._case_id_to_backups.get(case_id, []):
                meta = cache.load_backup_meta(self._app_data_root, case_id, backup_id)
                label = meta.get("backup_label") or meta.get("device_name") or meta.get("backup_id") or backup_id
                back_item = QTreeWidgetItem(case_item, [label])
                back_item.setData(0, Qt.ItemDataRole.UserRole, ("backup", case_id, backup_id))
                back_item.setIcon(0, load_icon("phone"))
                self._item_to_case_id[id(back_item)] = case_id
                self._item_to_backup_id[id(back_item)] = backup_id
            case_item.setExpanded(False)
            self._update_case_item_icon(case_item)

    def _find_backup_tree_item(self, case_id: str, backup_id: str) -> Optional[QTreeWidgetItem]:
        for i in range(self._tree.topLevelItemCount()):
            case_item = self._tree.topLevelItem(i)
            if self._item_to_case_id.get(id(case_item)) != case_id:
                continue
            for j in range(case_item.childCount()):
                back_item = case_item.child(j)
                if self._item_to_backup_id.get(id(back_item)) == backup_id:
                    return back_item
        return None

    def _restore_backup_tree_selection(self) -> None:
        if getattr(self._tree, "_suppress_restore_selection", False):
            return
        if not self._current_case_id or not self._current_backup_id:
            return
        item = self._find_backup_tree_item(self._current_case_id, self._current_backup_id)
        if item is None:
            return
        self._tree.blockSignals(True)
        self._tree.setCurrentItem(item)
        self._tree.blockSignals(False)

    def _on_tree_context_menu(self, position) -> None:
        self._load_backup_timer.stop()
        self._pending_backup_item = None
        item = self._tree.itemAt(position)
        if not item:
            return
        case_id = self._item_to_case_id.get(id(item))
        backup_id = self._item_to_backup_id.get(id(item))
        menu = QMenu(self)
        if backup_id:
            view_act = QAction("View messages", self)
            view_act.triggered.connect(lambda: self._show_backup(item))
            menu.addAction(view_act)
            del_back_act = QAction("Delete backup", self)
            del_back_act.triggered.connect(lambda: self._confirm_delete_backup(case_id, backup_id, item))
            menu.addAction(del_back_act)
        else:
            rename_act = QAction("Rename case", self)
            rename_act.triggered.connect(lambda: self._rename_case(item))
            menu.addAction(rename_act)
            import_act = QAction("Import iTunes backup", self)
            import_act.triggered.connect(lambda: self._import_backup(item))
            menu.addAction(import_act)
            delete_act = QAction("Delete case", self)
            delete_act.triggered.connect(lambda: self._confirm_delete_case(item))
            menu.addAction(delete_act)
        try:
            menu.exec(self._tree.mapToGlobal(position))
        finally:
            self._tree._suppress_restore_selection = False

    def _update_case_item_icon(self, item: QTreeWidgetItem) -> None:
        """Set case item briefcase icon and label text."""
        if self._item_to_backup_id.get(id(item)) is not None:
            return
        case_id = self._item_to_case_id.get(id(item))
        if not case_id:
            return
        name = self._cases.get(case_id, "Case")
        item.setText(0, name)
        case_icon = load_icon("briefcase")
        if case_icon.isNull():
            style = QApplication.style()
            case_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        item.setIcon(0, case_icon)

    def _on_tree_selection_changed(self) -> None:
        item = self._tree.currentItem()
        if not item:
            if self._current_case_id and self._current_backup_id:
                self._restore_backup_tree_selection()
            return
        backup_id = self._item_to_backup_id.get(id(item))
        if backup_id:
            case_id = self._item_to_case_id.get(id(item))
            if (
                case_id == self._current_case_id
                and backup_id == self._current_backup_id
                and self._stack.currentWidget() == self._message_panel
            ):
                return
            self._pending_backup_item = item
            self._show_loading_overlay()
            self._load_backup_timer.start(150)
            return
        if self._current_case_id and self._current_backup_id:
            self._restore_backup_tree_selection()
            return
        self._pending_backup_item = None
        self._load_backup_timer.stop()
        self._stack.setCurrentWidget(self._placeholder)
        self._refresh_file_menu_backup_actions()

    def _on_load_backup_timer(self) -> None:
        if self._pending_backup_item is not None:
            item = self._pending_backup_item
            self._pending_backup_item = None
            self._show_backup(item)

    def _on_backup_load_progress(self, pct: int, label: str) -> None:
        # First real progress signal flips the bar from indeterminate (0,0) to determinate (0,100).
        self._loading_bar.setRange(0, 100)
        self._loading_bar.setValue(pct)
        self._loading_label.setText(label)

    def _show_backup(self, item: QTreeWidgetItem) -> None:
        self._load_backup_timer.stop()
        self._pending_backup_item = None
        self._extract_attachments_action.setEnabled(False)
        self._populate_table_action.setEnabled(False)
        case_id = self._item_to_case_id.get(id(item))
        backup_id = self._item_to_backup_id.get(id(item))
        if not case_id or not backup_id:
            return
        self._show_loading_overlay()
        # Let the overlay paint before any blocking work (cache-hit deepcopy or worker setup).
        QApplication.processEvents()
        cache_key = (case_id, backup_id)
        if cache_key in self._backup_load_cache:
            self._backup_load_cache.move_to_end(cache_key)
            self._on_backup_loaded(copy.deepcopy(self._backup_load_cache[cache_key]))
            return
        if not self._stop_load_backup_worker():
            self._stack.setCurrentWidget(self._placeholder)
            QMessageBox.warning(
                self,
                "Load busy",
                "The previous backup load could not be stopped in time. Try again in a moment.",
            )
            return
        self._load_backup_worker = BackupLoadWorker(self._app_data_root, case_id, backup_id)
        self._load_backup_worker.progress.connect(self._on_backup_load_progress)
        self._load_backup_worker.load_result.connect(self._on_backup_loaded)
        self._load_backup_worker.error.connect(self._on_backup_load_error)
        self._load_backup_worker.start()

    def _stop_load_backup_worker(self) -> bool:
        """Stop any in-flight backup load. Returns False if the thread would be orphaned."""
        w = self._load_backup_worker
        if not w:
            return True
        if w.isRunning():
            w.terminate()
            if not w.wait(60_000):
                return False
        w.deleteLater()
        self._load_backup_worker = None
        return True

    def _on_backup_loaded(self, result: dict) -> None:
        w = self._load_backup_worker
        self._load_backup_worker = None
        if w:
            w.deleteLater()
        # Worker is done but the synchronous deepcopy + set_data below can take several seconds
        # for large backups. Flip the bar back to indeterminate sweep so it doesn't look frozen at 100%.
        self._loading_label.setText("Opening backup…")
        self._loading_bar.setRange(0, 0)
        QApplication.processEvents()
        meta = result["meta"]
        data = result["data"]
        case_id = result["case_id"]
        backup_id = result["backup_id"]
        self._current_case_id = case_id
        self._current_backup_id = backup_id
        self._current_timezone = (meta or {}).get("timezone") or ""
        self._current_custodian = (meta or {}).get("custodian") or ""
        attach_base = cache.get_backup_cache_root(self._app_data_root, case_id, backup_id)
        from app.logging_config import get_logger
        att_dir = attach_base / "attachments"
        att_count = len(list(att_dir.iterdir())) if att_dir.exists() else 0
        get_logger().info("show_backup: attach_base=%s attachments_exists=%s attachments_count=%s", attach_base, att_dir.exists(), att_count)
        self._message_views.set_attachment_base(attach_base)
        populate_table = (meta or {}).get("populate_table_tab", True)
        if not isinstance(populate_table, bool):
            populate_table = True
        to_cache = copy.deepcopy(result)
        self._message_views.set_data(
            data.get("chats", []),
            data.get("messages", []),
            attach_base,
            timezone_name=meta.get("timezone") or "",
            populate_table=populate_table,
        )
        self._backup_cache_put((case_id, backup_id), to_cache)
        lib_name = (self._cases.get(case_id) or "").strip() or None
        self._message_views.set_app_data_root(
            self._app_data_root,
            case_id=case_id,
            backup_id=backup_id,
            library_display_name=lib_name,
        )
        self._message_views.show_thread_view()
        self._stack.setCurrentWidget(self._message_panel)
        self._refresh_file_menu_backup_actions()

    def _on_full_table_tab_loaded(self) -> None:
        """After user loads deferred Table tab, persist so reopening this backup pre-populates."""
        case_id = self._current_case_id
        backup_id = self._current_backup_id
        if not case_id or not backup_id:
            return
        cache.update_backup_meta_fields(
            self._app_data_root,
            case_id,
            backup_id,
            {"populate_table_tab": True},
        )
        self._backup_cache_invalidate(case_id, backup_id)
        self._refresh_file_menu_backup_actions()

    def _on_backup_load_error(self, err: str) -> None:
        w = self._load_backup_worker
        self._load_backup_worker = None
        if w:
            w.deleteLater()
        self._loading_bar.setRange(0, 100)
        self._loading_bar.setValue(0)
        self._stack.setCurrentWidget(self._placeholder)
        self._refresh_file_menu_backup_actions()
        QMessageBox.warning(self, "Load failed", err)

    def _on_populate_table_view(self) -> None:
        from app.logging_config import get_logger
        log = get_logger()
        log.info(
            "populate_table_view: clicked. case=%s backup=%s deferred=%s",
            self._current_case_id, self._current_backup_id,
            self._message_views.is_table_tab_deferred(),
        )
        if not self._current_case_id or not self._current_backup_id:
            QMessageBox.warning(self, "Populate Table View", "Select and load a backup first.")
            return
        if not self._message_views.is_table_tab_deferred():
            QMessageBox.information(
                self,
                "Populate Table View",
                "The Table View is already populated for this backup.",
            )
            return
        dlg = QProgressDialog("Preparing table…", None, 0, 100, self)
        dlg.setWindowTitle("Populate Table View")
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.setCancelButton(None)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        QApplication.processEvents()

        def on_progress(pct: int, label: str) -> None:
            dlg.setLabelText(label)
            dlg.setValue(int(max(0, min(100, pct))))
            QApplication.processEvents()

        try:
            self._message_views.populate_full_table_tab(on_progress)
            log.info("populate_table_view: completed")
        except Exception:
            log.exception("populate_table_view: failed")
            QMessageBox.warning(self, "Populate Table View", "Failed to populate the Table tab. See log for details.")
        finally:
            dlg.close()

    def _on_extract_attachments(self) -> None:
        if self._import_worker and self._import_worker.isRunning():
            QMessageBox.information(self, "Busy", "Please wait for the import to finish.")
            return
        if self._extract_attachments_worker and self._extract_attachments_worker.isRunning():
            return
        case_id = self._current_case_id
        backup_id = self._current_backup_id
        if not case_id or not backup_id:
            QMessageBox.warning(self, "Extract attachments", "Select and load a backup first.")
            return
        meta = cache.load_backup_meta(self._app_data_root, case_id, backup_id)
        if not meta:
            QMessageBox.warning(self, "Extract attachments", "Backup metadata not found.")
            return
        if meta.get("attachments_extracted") is True:
            r = QMessageBox.question(
                self,
                "Extract attachments",
                "Attachments were already extracted for this backup. Extract again?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                return
        bp = Path(meta.get("backup_path") or "")
        if not bp.is_dir():
            QMessageBox.warning(
                self,
                "Backup unavailable",
                "The original backup folder was not found at the saved path. "
                "Connect the drive or re-import from the current backup location.",
            )
            return
        passphrase: Optional[str] = None
        if backup_appears_encrypted(bp):
            pw, ok = QInputDialog.getText(
                self,
                "Backup password",
                "This backup is encrypted. Enter the backup password:",
                QLineEdit.EchoMode.Password,
            )
            if not ok or not (pw or "").strip():
                return
            passphrase = (pw or "").strip()

        self._import_progress_label.setText("Extracting attachments...")
        self._import_progress_bar.setValue(0)
        self._stack.setCurrentWidget(self._import_progress_panel)

        worker = ExtractAttachmentsWorker(
            str(bp.resolve()),
            passphrase,
            self._app_data_root,
            case_id,
            backup_id,
        )

        def on_progress(pct: float, label: str) -> None:
            self._import_progress_bar.setValue(int(pct))
            self._import_progress_label.setText(label)

        def on_finished() -> None:
            w = self._extract_attachments_worker
            self._extract_attachments_worker = None
            if w:
                w.deleteLater()
            self._backup_cache_invalidate(case_id, backup_id)
            item = self._tree.currentItem()
            bid = self._item_to_backup_id.get(id(item)) if item else None
            if item and bid == backup_id:
                self._show_backup(item)
            else:
                self._stack.setCurrentWidget(self._message_panel)
                self._refresh_file_menu_backup_actions()
                QMessageBox.information(
                    self,
                    "Attachments",
                    "Attachments extracted. Select this backup in the tree to refresh the view.",
                )

        def on_error(err: str) -> None:
            w = self._extract_attachments_worker
            self._extract_attachments_worker = None
            if w:
                w.deleteLater()
            self._stack.setCurrentWidget(self._message_panel)
            self._refresh_file_menu_backup_actions()
            QMessageBox.critical(self, "Extract attachments failed", err)

        worker.progress.connect(on_progress)
        worker.extract_finished.connect(on_finished)
        worker.error.connect(on_error)
        self._extract_attachments_worker = worker
        worker.start()

    def _open_search_dialog(self, default_folder_id=None) -> None:
        if not self._current_backup_id:
            QMessageBox.warning(self, "Search", "Load a backup first by selecting it in the tree.")
            return
        if not self._current_case_id:
            QMessageBox.warning(self, "Search", "No case context for saved searches. Select a backup in the tree.")
            return
        lib_name = (self._cases.get(self._current_case_id) or "").strip() or None
        dlg = SearchDialog(
            self._app_data_root,
            self._current_case_id,
            self._current_backup_id,
            self,
            default_folder_id=default_folder_id,
            library_display_name=lib_name,
            chats=self._message_views.get_chats_for_search_picker(),
        )
        dlg.run_search_requested.connect(
            lambda criteria: self._run_search(criteria, notify=True)
        )
        dlg.exec()
        # Expand the folder the new search was filed under so it's visible
        # immediately instead of staying hidden behind a collapsed parent.
        self._message_views.refresh_saved_searches_list(expand_folder_id=default_folder_id)

    def _open_edit_search_dialog(self, search_id: str) -> None:
        if not self._current_backup_id:
            QMessageBox.warning(self, "Search", "Load a backup first by selecting it in the tree.")
            return
        if not self._current_case_id:
            QMessageBox.warning(self, "Search", "No case context for saved searches. Select a backup in the tree.")
            return
        from app.saved_searches import load_saved_searches

        searches = load_saved_searches(
            self._app_data_root, self._current_case_id, self._current_backup_id
        )
        saved = next((s for s in searches if s.get("id") == search_id), None)
        if saved is None:
            QMessageBox.warning(self, "Edit Search", "This saved search could not be found.")
            self._message_views.refresh_saved_searches_list()
            return
        lib_name = (self._cases.get(self._current_case_id) or "").strip() or None
        dlg = SearchDialog(
            self._app_data_root,
            self._current_case_id,
            self._current_backup_id,
            self,
            library_display_name=lib_name,
            edit_search=saved,
            chats=self._message_views.get_chats_for_search_picker(),
        )
        dlg.run_search_requested.connect(
            lambda criteria: self._run_search(criteria, notify=True)
        )
        dlg.exec()
        self._message_views.refresh_saved_searches_list()

    def _needs_backup_fallback(self, messages: List[dict], attach_base: Optional[Path]) -> bool:
        """True if any image attachment in `messages` is missing from the local cache.

        Exports rely on `attach_base / local_path` files for embedding. When an
        attachment has an empty `local_path`, or that file doesn't exist, the
        export needs to fall back to reading the bytes from the raw backup.
        """
        from app.rsmf_export import _is_image_attachment

        for m in messages or []:
            for a in m.get("attachments") or []:
                if not _is_image_attachment(a):
                    continue
                lp = (a.get("local_path") or "").strip()
                if not lp:
                    return True
                if attach_base is None:
                    return True
                if not (attach_base / lp).exists():
                    return True
        return False

    def _resolve_backup_fallback(
        self,
        messages: List[dict],
        attach_base: Optional[Path],
    ) -> tuple[Optional[str], Optional[str], bool]:
        """Decide whether the export should embed attachments from the raw backup.

        Returns `(backup_path, passphrase, cancelled)`:
          - `cancelled` is True if the user dismissed the passphrase prompt;
            callers should abort the export.
          - `backup_path` is set only when fallback is needed AND the saved
            backup folder is still on disk.
          - `passphrase` is set only for encrypted backups that the user
            entered a password for.
        """
        if not self._needs_backup_fallback(messages, attach_base):
            return None, None, False

        meta = cache.load_backup_meta(
            self._app_data_root, self._current_case_id, self._current_backup_id
        )
        bp_str = (meta or {}).get("backup_path") if meta else None
        if not bp_str:
            return None, None, False
        bp = Path(bp_str)
        if not bp.exists():
            QMessageBox.information(
                self,
                "Export RSMF",
                "Some attachments are not in the local cache and the original "
                "backup folder is no longer available; those attachments will "
                "be skipped.",
            )
            return None, None, False

        passphrase: Optional[str] = None
        if backup_appears_encrypted(bp):
            pw, ok = QInputDialog.getText(
                self,
                "Backup password",
                "This backup is encrypted and some attachments need to be read "
                "from it during export. Enter the backup password:",
                QLineEdit.EchoMode.Password,
            )
            if not ok:
                return None, None, True
            pw = (pw or "").strip()
            if not pw:
                return None, None, True
            passphrase = pw

        return str(bp.resolve()), passphrase, False

    def _open_export_rsmf_dialog(self) -> None:
        if not self._current_backup_id:
            QMessageBox.warning(self, "Export RSMF", "Load a backup first.")
            return
        results = self._message_views.get_search_results()
        if not results:
            QMessageBox.warning(self, "Export RSMF", "Run a search first to get results to export.")
            return
        all_messages = self._message_views.get_all_messages()
        chunk_24h = self._message_views.get_current_search_chunk_24h()
        to_export = expand_results_for_rsmf_export(
            results,
            all_messages or [],
            timezone_name=self._current_timezone or "",
            chunk_24h=chunk_24h,
        )
        attach_base = cache.get_backup_cache_root(self._app_data_root, self._current_case_id, self._current_backup_id)
        # Match thread export: decide backup fallback from full chats in the search,
        # not only the expanded hit subset, so uncached attachments still resolve.
        chat_ids = {m.get("chat_id") for m in results if m.get("chat_id") is not None}
        fallback_scope = to_export
        if chat_ids:
            fallback_scope = [
                m for m in (all_messages or []) if m.get("chat_id") in chat_ids
            ]
        backup_path, passphrase, cancelled = self._resolve_backup_fallback(fallback_scope, attach_base)
        if cancelled:
            return
        from datetime import datetime
        chat_id_to_label = self._message_views.get_chat_id_to_label()
        search_name = (self._message_views.get_current_search_name() or "search").replace(" ", "_")
        zip_name = f"search_{search_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        dlg = ExportRsmfDialog(
            to_export,
            attach_base,
            custodian=self._current_custodian,
            parent=self,
            backup_path=backup_path,
            passphrase=passphrase,
            chat_id_to_label=chat_id_to_label,
            zip_name=zip_name,
        )
        dlg.exec()

    def _open_export_threads_dialog(self, chat_rowids: list) -> None:
        if not self._current_backup_id:
            QMessageBox.warning(self, "Export threads", "Load a backup first.")
            return
        if not chat_rowids:
            return

        chat_by_id = {ch.get("rowid"): ch for ch in self._message_views.get_chats_for_search_picker()}
        threads = []
        fallback_scope: List[dict] = []
        for rowid in chat_rowids:
            cid = int(rowid)
            ch = chat_by_id.get(cid)
            label = (ch.get("label") if ch else None) or f"Chat {cid}"
            msgs = self._message_views.get_messages_for_chat(cid)
            if not msgs:
                continue
            threads.append({"label": label, "messages": msgs})
            fallback_scope.extend(msgs)

        if not threads:
            QMessageBox.warning(self, "Export threads", "Selected threads have no messages.")
            return

        preview = ThreadExportPreviewDialog(threads, parent=self)
        if preview.exec() != QDialog.DialogCode.Accepted:
            return

        attach_base = cache.get_backup_cache_root(
            self._app_data_root, self._current_case_id, self._current_backup_id
        )
        backup_path, passphrase, cancelled = self._resolve_backup_fallback(fallback_scope, attach_base)
        if cancelled:
            return
        ThreadExportDialog(
            threads=threads,
            attachment_base=attach_base,
            custodian=self._current_custodian,
            timezone_name=self._current_timezone or "",
            parent=self,
            backup_path=backup_path,
            passphrase=passphrase,
        ).exec()

    def _run_search(self, criteria: Dict[str, Any], *, notify: bool = True) -> None:
        messages = self._message_views.get_all_messages()
        if not messages:
            QMessageBox.warning(self, "Search", "No messages in the current backup.")
            return
        results = run_search(
            messages,
            to_filter=criteria.get("to_filter", ""),
            body_filter=criteria.get("body_filter", ""),
            date_from=criteria.get("date_from", ""),
            date_to=criteria.get("date_to", ""),
            has_attachments=criteria.get("has_attachments", "any"),
            hash_filter=criteria.get("hash_filter", ""),
            chunk_24h=criteria.get("chunk_24h", False),
            timezone_name=self._current_timezone,
            search_name=criteria.get("search_name", "Search results"),
            search_sequence=criteria.get("sequence", 0),
            thread_ids=criteria.get("thread_ids") or [],
        )
        self._message_views.set_search_results(
            results,
            criteria.get("search_id"),
            chunk_24h=bool(criteria.get("chunk_24h", False)),
            search_name=criteria.get("search_name", ""),
        )
        if notify:
            QMessageBox.information(
                self,
                "Search",
                f"Found {len(results)} message(s). Results are shown on the Search Messages tab.",
            )

    def _delete_current_backup(self) -> None:
        if not self._current_case_id or not self._current_backup_id:
            return
        self._confirm_delete_backup(
            self._current_case_id,
            self._current_backup_id,
            None,
        )

    def _confirm_delete_backup(self, case_id: str, backup_id: str, item: Optional[QTreeWidgetItem]) -> None:
        ok = QMessageBox.question(
            self,
            "Delete backup",
            "Remove this backup and all its data from the program? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes
        if not ok:
            return
        cache.delete_backup_cache(self._app_data_root, case_id, backup_id)
        self._backup_cache_invalidate(case_id, backup_id)
        bl = self._case_id_to_backups.get(case_id, [])
        if backup_id in bl:
            bl.remove(backup_id)
        self._rebuild_tree()
        if self._current_backup_id == backup_id:
            self._current_case_id = None
            self._current_backup_id = None
            self._current_custodian = ""
            self._stack.setCurrentWidget(self._placeholder)
            self._refresh_file_menu_backup_actions()

    def _rename_case(self, item: QTreeWidgetItem) -> None:
        case_id = self._item_to_case_id.get(id(item))
        if not case_id:
            return
        current_name = self._cases.get(case_id, "Case")
        new_name, ok = QInputDialog.getText(
            self,
            "Rename case",
            "Edit the case name:",
            text=current_name,
        )
        if ok and new_name.strip():
            self._cases[case_id] = new_name.strip()
            _save_cases(self._cases)
            self._update_case_item_icon(item)

    def _confirm_delete_case(self, item: QTreeWidgetItem) -> None:
        case_id = self._item_to_case_id.get(id(item))
        if not case_id:
            return
        name = self._cases.get(case_id, "Case")
        ok = QMessageBox.question(
            self,
            "Delete case",
            f"Delete case \"{name}\" and all data imported into it? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes
        if not ok:
            return
        cache.delete_case_cache(self._app_data_root, case_id)
        self._backup_cache_invalidate(case_id)
        del self._cases[case_id]
        self._case_id_to_backups.pop(case_id, None)
        _save_cases(self._cases)
        self._rebuild_tree()
        if self._current_case_id == case_id:
            self._current_case_id = None
            self._current_backup_id = None
            self._message_views.set_app_data_root(
                self._app_data_root, case_id=None, backup_id=None
            )
            self._stack.setCurrentWidget(self._placeholder)
            self._refresh_file_menu_backup_actions()

    def _import_backup(self, item: QTreeWidgetItem) -> None:
        case_id = self._item_to_case_id.get(id(item))
        if not case_id:
            return
        dlg = ImportBackupDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        path = dlg.get_path()
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "Invalid path", "Please select a valid backup folder.")
            return
        custodian = dlg.get_custodian()
        timezone_name = dlg.get_timezone()
        passphrase = dlg.get_password()
        control_prefix = dlg.get_control_prefix()
        control_padding = dlg.get_control_padding()
        backup_label = dlg.get_backup_name()
        populate_table_tab = dlg.get_populate_table_tab()
        extract_attachments = dlg.get_extract_attachments()

        self._import_progress_label.setText("Importing backup...")
        self._import_progress_bar.setValue(0)
        self._stack.setCurrentWidget(self._import_progress_panel)

        worker = ImportWorker(
            path, case_id, custodian, timezone_name, passphrase, self._app_data_root,
            control_prefix=control_prefix,
            control_padding=control_padding,
            backup_label=backup_label,
            populate_table_tab=populate_table_tab,
            extract_attachments=extract_attachments,
        )

        def on_progress(pct: float, label: str) -> None:
            self._import_progress_bar.setValue(int(pct))
            self._import_progress_label.setText(label)

        def on_finished(result: dict) -> None:
            w = self._import_worker
            self._import_worker = None
            if w:
                w.deleteLater()
            self._stack.setCurrentWidget(self._placeholder)
            backup_id = result["backup_id"]
            self._backup_cache_invalidate()
            self._case_id_to_backups.setdefault(case_id, []).append(backup_id)
            self._rebuild_tree()
            QMessageBox.information(
                self,
                "Import complete",
                f"Backup imported successfully. {len(result['data'].get('messages', []))} messages cached.",
            )

        def on_error(err: str) -> None:
            w = self._import_worker
            self._import_worker = None
            if w:
                w.deleteLater()
            self._stack.setCurrentWidget(self._placeholder)
            QMessageBox.critical(self, "Import failed", err)

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_finished)
        worker.error.connect(on_error)
        self._import_worker = worker
        worker.start()

    def _on_import_cancel(self) -> None:
        w = self._import_worker
        if w and w.isRunning():
            w.terminate()
            w.wait(60_000)
        self._import_worker = None
        if w and not w.isRunning():
            w.deleteLater()
        ew = self._extract_attachments_worker
        if ew and ew.isRunning():
            ew.terminate()
            ew.wait(60_000)
        self._extract_attachments_worker = None
        if ew and not ew.isRunning():
            ew.deleteLater()
        self._stack.setCurrentWidget(self._placeholder)

    def _on_help(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("GURU Mobile Discovery — Help")
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(32, 28, 32, 20)
        label = QLabel("Coming soon")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.exec()

    def _on_about(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("About GURU Mobile Discovery")
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(14)

        logo_label = QLabel()
        lockup_path = resolve_logo_path("guru_logo_lockup")
        if lockup_path.is_file():
            pix = QPixmap(str(lockup_path))
            if not pix.isNull():
                pix = pix.scaled(
                    360, 120,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                logo_label.setPixmap(pix)
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo_label)

        license_html = self._license_info_html()
        body = QLabel(
            f"""
            <p style="text-align:center;">Version {__version__}</p>
            <p style="text-align:center;">Forensic examination tool for mobile-device backups.<br>
            Organize backups by case, view messages in thread or table form, and search across
            conversations for legal review and discovery.</p>
            {license_html}
            <p style="text-align:center;">&copy; GURU Discovery</p>
            """
        )
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        layout.addWidget(body)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)

        dlg.exec()

    def _license_info_html(self) -> str:
        svc = self._license_service
        if svc is None or not svc.is_cached():
            return ""
        email = svc.cached_email() or "—"
        expires = svc.cached_expires_at() or ""
        expires_short = expires[:10] if expires else "—"
        return (
            f'<p style="text-align:center;">Licensed to <code>{email}</code>'
            f'<br>Expires: {expires_short}</p>'
        )

    def _on_change_license(self) -> None:
        if self._license_service is None:
            return
        from app.license_dialog import LicenseDialog
        dlg = LicenseDialog(
            self._license_service,
            parent=self,
            title="Change License Key",
            prefilled_key=self._license_service.cached_key(),
            allow_close_without_activation=True,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            QMessageBox.information(
                self,
                "License",
                "License updated for this device.",
            )

    def _on_remove_license(self) -> None:
        if self._license_service is None or not self._license_service.is_cached():
            QMessageBox.information(
                self,
                "Remove license",
                "No license is active on this device.",
            )
            return
        ok = QMessageBox.question(
            self,
            "Remove license from this device",
            "Remove the cached license from this device? You will need to paste your "
            "license key again to use the app.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes
        if not ok:
            return
        success, msg = self._license_service.remove_license()
        if not success:
            QMessageBox.warning(self, "Remove license", msg)
            return
        QMessageBox.information(
            self,
            "License removed",
            "The license has been removed from this device. The app will now close.",
        )
        QApplication.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        iw = self._import_worker
        if iw:
            if iw.isRunning():
                iw.terminate()
                iw.wait(60_000)
            self._import_worker = None
            if not iw.isRunning():
                iw.deleteLater()
        ew = self._extract_attachments_worker
        if ew:
            if ew.isRunning():
                ew.terminate()
                ew.wait(60_000)
            self._extract_attachments_worker = None
            if not ew.isRunning():
                ew.deleteLater()
        self._stop_load_backup_worker()
        super().closeEvent(event)
