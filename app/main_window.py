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
    QLineEdit,
    QTextBrowser,
    QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QAction, QCloseEvent, QMouseEvent, QIcon, QPixmap

from .import_dialog import ImportBackupDialog
from .search_dialog import SearchDialog
from .export_dialog import ExportRsmfDialog
from .message_views import MessageViews
from .style import icon as load_icon, icon_path as resolve_icon_path
from . import cache
from .import_worker import run_import, extract_attachments_to_cache
from app.paths import get_app_data_root
from app.backup_parser import resolve_display_name, is_placeholder_chat_identifier
from app.backup_parser.parser import backup_appears_encrypted
from app.search_logic import run_search, expand_results_to_full_threads
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


class CaseTreeWidget(QTreeWidget):
    """Tree widget that clears selection when clicking empty space."""
    empty_area_clicked = pyqtSignal()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if item is None:
                self.clearSelection()
                self.setCurrentItem(None)
                self.empty_area_clicked.emit()
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
    def __init__(self):
        super().__init__()
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
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Case tree
        tree_container = QWidget()
        tree_layout = QVBoxLayout(tree_container)
        tree_layout.setContentsMargins(8, 8, 4, 8)
        tree_layout.setSpacing(8)
        cases_heading = QLabel("CASES")
        cases_heading.setProperty("class", "section-heading")
        tree_layout.addWidget(cases_heading)
        add_case_btn = QPushButton("  New Case")
        add_case_btn.setIcon(load_icon("plus"))
        add_case_btn.setIconSize(QSize(14, 14))
        add_case_btn.clicked.connect(self._add_case)
        tree_layout.addWidget(add_case_btn)
        self._tree = CaseTreeWidget()
        self._tree.setHeaderLabels(["Cases"])
        self._tree.setHeaderHidden(True)
        self._tree.setItemsExpandable(True)
        self._tree.setRootIsDecorated(False)
        # Default indentation nests backup rows under the case; 0 made children align with parents.
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree.itemClicked.connect(self._on_tree_item_clicked)
        self._tree.itemExpanded.connect(self._on_tree_item_expanded_collapsed)
        self._tree.itemCollapsed.connect(self._on_tree_item_expanded_collapsed)
        self._tree.currentItemChanged.connect(self._on_tree_selection_changed)
        self._tree.empty_area_clicked.connect(self._on_tree_empty_click)
        tree_layout.addWidget(self._tree)
        splitter.addWidget(tree_container)
        self._tree.setMinimumWidth(220)
        self._item_to_case_id: Dict[int, str] = {}  # id(item) -> case_id
        self._item_to_backup_id: Dict[int, Optional[str]] = {}  # id(item) -> backup_id or None

        # Right: content area
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self._stack = QStackedWidget()
        self._placeholder = self._build_empty_placeholder()
        self._stack.addWidget(self._placeholder)
        self._message_panel = QWidget()
        msg_panel_layout = QVBoxLayout(self._message_panel)
        self._message_views = MessageViews()
        self._message_views.add_search_requested.connect(self._open_search_dialog)
        self._message_views.run_saved_search_requested.connect(self._run_search)
        self._message_views.export_rsmf_requested.connect(self._open_export_rsmf_dialog)
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
        splitter.addWidget(right)
        splitter.setSizes([250, 900])
        layout.addWidget(splitter)

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
        """Centered icon + heading + subtitle shown when no backup is selected."""
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.setSpacing(10)

        # Icon
        icon_label = QLabel()
        msg_icon_path = resolve_icon_path("messages")
        if msg_icon_path.is_file():
            pix = QPixmap(str(msg_icon_path))
            if not pix.isNull():
                pix = pix.scaled(
                    72, 72,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                icon_label.setPixmap(pix)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(icon_label)

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
                self._item_to_case_id[id(back_item)] = case_id
                self._item_to_backup_id[id(back_item)] = backup_id
            case_item.setExpanded(False)
            self._update_case_item_icon(case_item)

    def _on_tree_empty_click(self) -> None:
        """Called when user clicks empty space in tree; show placeholder."""
        self._load_backup_timer.stop()
        self._pending_backup_item = None
        self._stack.setCurrentWidget(self._placeholder)
        self._refresh_file_menu_backup_actions()

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
        menu.exec(self._tree.mapToGlobal(position))

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Toggle expand/collapse when clicking a case item (no backup_id)."""
        backup_id = self._item_to_backup_id.get(id(item))
        if backup_id is None and item.childCount() > 0:
            item.setExpanded(not item.isExpanded())

    def _on_tree_item_expanded_collapsed(self, item: QTreeWidgetItem) -> None:
        """Update icon when case item is expanded or collapsed."""
        self._update_case_item_icon(item)

    def _update_case_item_icon(self, item: QTreeWidgetItem) -> None:
        """Set case item icon (arrow-right collapsed, arrow-down expanded) and text."""
        if self._item_to_backup_id.get(id(item)) is not None:
            return
        case_id = self._item_to_case_id.get(id(item))
        if not case_id:
            return
        name = self._cases.get(case_id, "Case")
        item.setText(0, name)
        chev = load_icon("chevron-down" if item.isExpanded() else "chevron-right")
        if chev.isNull():
            style = QApplication.style()
            fallback = style.standardIcon(
                QStyle.StandardPixmap.SP_ArrowDown
                if item.isExpanded()
                else QStyle.StandardPixmap.SP_ArrowRight
            )
            chev = QIcon(fallback.pixmap(QSize(12, 12)))
        item.setIcon(0, chev)

    def _on_tree_selection_changed(self) -> None:
        item = self._tree.currentItem()
        if not item:
            return
        backup_id = self._item_to_backup_id.get(id(item))
        if backup_id:
            self._pending_backup_item = item
            self._show_loading_overlay()
            self._load_backup_timer.start(150)
        else:
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
        self._message_views.set_app_data_root(self._app_data_root)
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

    def _open_search_dialog(self) -> None:
        if not self._current_backup_id:
            QMessageBox.warning(self, "Search", "Load a backup first by selecting it in the tree.")
            return
        dlg = SearchDialog(self._app_data_root, self)
        dlg.run_search_requested.connect(self._run_search)
        dlg.exec()
        self._message_views.refresh_saved_searches_list()

    def _open_export_rsmf_dialog(self) -> None:
        if not self._current_backup_id:
            QMessageBox.warning(self, "Export RSMF", "Load a backup first.")
            return
        results = self._message_views.get_search_results()
        if not results:
            QMessageBox.warning(self, "Export RSMF", "Run a search first to get results to export.")
            return
        all_messages = self._message_views.get_all_messages()
        to_export = expand_results_to_full_threads(
            results,
            all_messages or [],
            timezone_name=self._current_timezone or "",
        )
        attach_base = cache.get_backup_cache_root(self._app_data_root, self._current_case_id, self._current_backup_id)
        dlg = ExportRsmfDialog(
            to_export,
            attach_base,
            custodian=self._current_custodian,
            parent=self,
        )
        dlg.exec()

    def _run_search(self, criteria: Dict[str, Any]) -> None:
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
        )
        self._message_views.set_search_results(results, criteria.get("search_id"))
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
        dlg.resize(720, 600)
        layout = QVBoxLayout(dlg)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(self._help_html())
        layout.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.exec()

    def _help_html(self) -> str:
        return """
        <h2>GURU Mobile Discovery — User Guide</h2>
        <p>GURU Mobile Discovery organizes mobile-device backups by case for forensic and legal review.
        Each case can hold one or more imported backups; messages are pre-cached so that switching
        between conversations and table views is instant. iPhone (iTunes) backups are supported today;
        Android backups are planned.</p>

        <h3>Cases</h3>
        <ul>
          <li><b>Add a case</b> — click <i>New Case</i> at the top of the case list (or use File &rarr; New Case) and enter a name.</li>
          <li><b>Rename</b> — double-click a case name in the tree, edit, and press Enter.</li>
          <li><b>Delete a case</b> — right-click the case &rarr; <i>Delete case</i>. This removes the
          case and <b>all cached data for every backup inside it</b>.</li>
        </ul>

        <h3>Importing an iTunes backup</h3>
        <ol>
          <li>Right-click the case you want to import into &rarr; <i>Import iTunes backup…</i></li>
          <li>In the dialog:
            <ul>
              <li><b>Backup folder</b> — browse to the iTunes backup root (the folder that contains
              <code>Manifest.db</code> / <code>Manifest.plist</code>).</li>
              <li><b>Custodian</b> — name shown in messages in place of the device owner
              (e.g. "John Smith" rather than "Me").</li>
              <li><b>Time zone</b> — defaults to the backup's recorded time zone when available;
              change to control how timestamps are displayed.</li>
              <li><b>Password</b> — required only for encrypted backups. The password is stored
              with the backup so you don't need to re-enter it.</li>
            </ul>
          </li>
          <li>Click <i>Import</i>. A progress bar shows backup parsing, message extraction, and
          caching. The backup appears under the case when complete.</li>
        </ol>

        <h3>Viewing messages</h3>
        <p>Click a backup under a case to load it. The right pane shows two tabs:</p>
        <ul>
          <li><b>Thread view</b> — full conversations with inline images, emojis, and stills for GIFs.
          Use the conversation list on the left of the tab to jump between chats.</li>
          <li><b>Table view</b> — every message in a sortable grid (Date, Direction, From/To, Body,
          Hash, Sender ID, Chat). Useful for filtering, sorting, and exporting evidence.</li>
        </ul>
        <p>Switching between tabs is instant once the backup is loaded; the loading bar at the top of the
        right pane shows progress whenever a backup is being read from the cache.</p>

        <h3>Search</h3>
        <p>Use the search box / search action on the message panel to find messages across all chats in
        the current backup. Results expand to full threads so context is preserved.</p>

        <h3>File menu</h3>
        <ul>
          <li><b>Extract Attachments…</b> — copies attachment files (images, videos, etc.) from the
          original backup into the case cache, so they can render inline in the thread view.</li>
          <li><b>Populate Table View…</b> — builds the full message grid for the Table View tab.
          Useful if Table View was skipped during import to speed up that import.</li>
        </ul>

        <h3>Deleting a backup</h3>
        <p>Right-click a backup &rarr; <i>Delete backup</i>. This removes the cached messages and
        attachments for that backup but leaves the case in place.</p>

        <h3>Where data is stored</h3>
        <p>By default, all cases, cached messages, and attachments live under
        <code>%LOCALAPPDATA%\\GURU Mobile Discovery\\</code> on Windows (existing installs may still
        use <code>iTunes Parser v2</code> until you migrate the folder). To override the location, set
        the <code>GURU_MOBILE_DISCOVERY_DATA</code> environment variable to an absolute path before launching
        the program (the legacy name <code>ITUNES_PARSER_V2_DATA</code> is still accepted).</p>
        """

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "About GURU Mobile Discovery",
            f"""
            <h3>GURU Mobile Discovery</h3>
            <p>Version {__version__}</p>
            <p>Forensic examination tool for mobile-device backups.<br>
            Organize backups by case, view messages in thread or table form, and search across
            conversations for legal review and discovery.</p>
            <p>&copy; GURU Discovery</p>
            """,
        )

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
