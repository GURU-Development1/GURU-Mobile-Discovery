"""
Persist and load saved search definitions (name, criteria, chunk_24h) and folders.
Stored per backup as ``<app_data>/cases/<case_id>/<backup_id>/saved_searches.json``.
On first access for a backup, seeds from ``cases/<case_id>/saved_searches.json`` if present,
otherwise from legacy global ``<app_data>/saved_searches.json``.

JSON schema:
{
  "searches": [ { id, sequence, name, to_filter, body_filter, date_from, date_to,
                  has_attachments, hash_filter, chunk_24h, thread_ids, folder_id }, ... ],
  "folders":  [ { id, name, parent_id (null only for the library root folder) }, ... ]
}

Each saved search has a sequence number (0001, 0002, ...) for Conversation ID prefixing.
Every backup has exactly one persisted top-level library root folder; all other
folders nest under it, and every saved search lives inside some folder.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.cache import get_backup_cache_root, get_case_cache_root

# Stable id for the always-present library root folder. The all-zero/one pattern
# guarantees no collision with `uuid.uuid4()` (which sets version and variant bits).
LIBRARY_ROOT_FOLDER_ID = "00000000-0001-0001-0001-000000000001"
LIBRARY_ROOT_FOLDER_NAME = "Saved searches"


def library_root_folder_id() -> str:
    """Return the stable id used for the default top-level folder."""
    return LIBRARY_ROOT_FOLDER_ID


def is_library_root_folder_id(folder_id: Optional[str]) -> bool:
    return folder_id == LIBRARY_ROOT_FOLDER_ID


def _saved_searches_file(storage_root: Path) -> Path:
    return storage_root / "saved_searches.json"


def _migrate_to_backup_storage(app_data_root: Path, case_id: str, backup_id: str) -> Path:
    """Ensure per-backup saved_searches.json exists, seeding from case-level or legacy global."""
    backup_root = get_backup_cache_root(app_data_root, case_id, backup_id)
    dest = _saved_searches_file(backup_root)
    if dest.exists():
        return backup_root
    backup_root.mkdir(parents=True, exist_ok=True)
    case_seed = _saved_searches_file(get_case_cache_root(app_data_root, case_id))
    if case_seed.is_file():
        try:
            shutil.copy2(case_seed, dest)
            return backup_root
        except OSError:
            pass
    legacy = app_data_root / "saved_searches.json"
    if legacy.is_file():
        try:
            shutil.copy2(legacy, dest)
        except OSError:
            pass
    return backup_root


def _backup_saved_search_root(app_data_root: Path, case_id: str, backup_id: str) -> Path:
    return _migrate_to_backup_storage(app_data_root, case_id, backup_id)


def _read_raw(storage_root: Path) -> Dict[str, Any]:
    """Read the whole JSON file (searches + folders). Returns empty dict on missing/corrupt."""
    path = _saved_searches_file(storage_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def _write_raw(storage_root: Path, searches: List[Dict[str, Any]], folders: List[Dict[str, Any]]) -> None:
    path = _saved_searches_file(storage_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"searches": searches, "folders": folders}, indent=2),
        encoding="utf-8",
    )


def ensure_library_root(storage_root: Path, library_display_name: Optional[str] = None) -> None:
    """Guarantee the library root folder exists and absorbs any orphan items.

    Idempotent. Reads raw data once, applies migrations, and writes back only
    when something changed. Reads/writes are done at the raw layer to avoid
    recursion with the public load/save helpers.

    When ``library_display_name`` is provided, the root folder uses it for new
    libraries and upgrades the legacy default label ``Saved searches``.
    """
    display = (library_display_name or "").strip()
    data = _read_raw(storage_root)
    raw_folders = data.get("folders") if isinstance(data, dict) else None
    raw_searches = data.get("searches") if isinstance(data, dict) else None
    folders: List[Dict[str, Any]] = [dict(f) for f in raw_folders] if isinstance(raw_folders, list) else []
    searches: List[Dict[str, Any]] = [dict(s) for s in raw_searches] if isinstance(raw_searches, list) else []

    changed = False

    has_root = any(
        isinstance(f, dict) and f.get("id") == LIBRARY_ROOT_FOLDER_ID for f in folders
    )
    if not has_root:
        folders.insert(
            0,
            {
                "id": LIBRARY_ROOT_FOLDER_ID,
                "name": display if display else LIBRARY_ROOT_FOLDER_NAME,
                "parent_id": None,
            },
        )
        changed = True

    for f in folders:
        if not isinstance(f, dict):
            continue
        fid = f.get("id")
        if fid == LIBRARY_ROOT_FOLDER_ID:
            if f.get("parent_id") is not None:
                f["parent_id"] = None
                changed = True
            if display and (f.get("name") or "").strip() == LIBRARY_ROOT_FOLDER_NAME:
                f["name"] = display
                changed = True
            continue
        parent = f.get("parent_id")
        if not parent:
            f["parent_id"] = LIBRARY_ROOT_FOLDER_ID
            changed = True

    for s in searches:
        if not isinstance(s, dict):
            continue
        folder_id = s.get("folder_id")
        if not folder_id:
            s["folder_id"] = LIBRARY_ROOT_FOLDER_ID
            changed = True

    if changed:
        _write_raw(storage_root, searches, folders)


def load_saved_searches(
    app_data_root: Path,
    case_id: str,
    backup_id: str,
    library_display_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Load list of saved searches for a backup."""
    storage_root = _backup_saved_search_root(app_data_root, case_id, backup_id)
    ensure_library_root(storage_root, library_display_name=library_display_name)
    data = _read_raw(storage_root)
    items = data.get("searches", []) if isinstance(data, dict) else []
    folders = data.get("folders", []) if isinstance(data, dict) else []
    searches = [dict(s) for s in items]
    modified = False
    next_seq = _next_sequence(searches)
    for s in searches:
        seq = s.get("sequence")
        if seq is None or (isinstance(seq, str) and str(seq).strip() == ""):
            s["sequence"] = next_seq
            next_seq += 1
            modified = True
    if modified:
        _write_raw(storage_root, searches, [dict(f) for f in folders])
    return searches


def _next_sequence(searches: List[Dict[str, Any]]) -> int:
    """Return the next sequence number (1-based) from existing searches."""
    if not searches:
        return 1
    max_seq = 0
    for s in searches:
        seq = s.get("sequence")
        if isinstance(seq, int) and seq > max_seq:
            max_seq = seq
        elif isinstance(seq, (float, str)):
            try:
                n = int(seq)
                if n > max_seq:
                    max_seq = n
            except (ValueError, TypeError):
                pass
    return max_seq + 1


def save_saved_searches(
    app_data_root: Path,
    case_id: str,
    backup_id: str,
    searches: List[Dict[str, Any]],
) -> None:
    """Overwrite saved searches list, preserving any existing folders on disk."""
    folders = load_folders(app_data_root, case_id, backup_id)
    storage_root = _backup_saved_search_root(app_data_root, case_id, backup_id)
    _write_raw(storage_root, searches, folders)


def add_saved_search(
    app_data_root: Path,
    case_id: str,
    backup_id: str,
    name: str,
    to_filter: str = "",
    body_filter: str = "",
    date_from: str = "",
    date_to: str = "",
    has_attachments: str = "any",
    hash_filter: str = "",
    chunk_24h: bool = False,
    thread_ids: Optional[List[int]] = None,
    folder_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a new saved search and return it (with id and sequence set)."""
    searches = load_saved_searches(app_data_root, case_id, backup_id)
    search_id = str(uuid.uuid4())
    sequence = _next_sequence(searches)
    item = {
        "id": search_id,
        "sequence": sequence,
        "name": (name or "Unnamed search").strip(),
        "to_filter": (to_filter or "").strip(),
        "body_filter": (body_filter or "").strip(),
        "date_from": (date_from or "").strip(),
        "date_to": (date_to or "").strip(),
        "has_attachments": (has_attachments or "any").strip().lower() or "any",
        "hash_filter": (hash_filter or "").strip(),
        "chunk_24h": bool(chunk_24h),
        "thread_ids": [int(t) for t in (thread_ids or []) if t is not None],
        "folder_id": folder_id or LIBRARY_ROOT_FOLDER_ID,
    }
    searches.append(item)
    save_saved_searches(app_data_root, case_id, backup_id, searches)
    return item


def update_saved_search(
    app_data_root: Path,
    case_id: str,
    backup_id: str,
    search_id: str,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Update a saved search by id. Returns updated item or None."""
    searches = load_saved_searches(app_data_root, case_id, backup_id)
    for i, s in enumerate(searches):
        if s.get("id") == search_id:
            allowed = {
                "name",
                "to_filter",
                "body_filter",
                "date_from",
                "date_to",
                "has_attachments",
                "hash_filter",
                "chunk_24h",
                "thread_ids",
                "sequence",
                "folder_id",
            }
            for k, v in kwargs.items():
                if k in allowed:
                    searches[i][k] = v
            save_saved_searches(app_data_root, case_id, backup_id, searches)
            return searches[i]
    return None


def delete_saved_search(
    app_data_root: Path,
    case_id: str,
    backup_id: str,
    search_id: str,
) -> bool:
    """Remove a saved search by id. Returns True if removed."""
    searches = load_saved_searches(app_data_root, case_id, backup_id)
    new_list = [s for s in searches if s.get("id") != search_id]
    if len(new_list) == len(searches):
        return False
    save_saved_searches(app_data_root, case_id, backup_id, new_list)
    return True


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


def load_folders(
    app_data_root: Path,
    case_id: str,
    backup_id: str,
    library_display_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Load list of folders for a backup. Each item: id, name, parent_id (Optional[str])."""
    storage_root = _backup_saved_search_root(app_data_root, case_id, backup_id)
    ensure_library_root(storage_root, library_display_name=library_display_name)
    data = _read_raw(storage_root)
    items = data.get("folders", []) if isinstance(data, dict) else []
    folders: List[Dict[str, Any]] = []
    for f in items:
        if not isinstance(f, dict):
            continue
        fid = f.get("id")
        if not fid:
            continue
        folders.append(
            {
                "id": str(fid),
                "name": str(f.get("name") or "Unnamed folder"),
                "parent_id": (str(f["parent_id"]) if f.get("parent_id") else None),
            }
        )
    return folders


def save_folders(
    app_data_root: Path,
    case_id: str,
    backup_id: str,
    folders: List[Dict[str, Any]],
) -> None:
    """Overwrite folders list, preserving any existing searches on disk."""
    searches = load_saved_searches(app_data_root, case_id, backup_id)
    storage_root = _backup_saved_search_root(app_data_root, case_id, backup_id)
    _write_raw(storage_root, searches, [dict(f) for f in folders])


def add_folder(
    app_data_root: Path,
    case_id: str,
    backup_id: str,
    name: str,
    parent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a new folder under the given parent (defaults to the library root)."""
    folders = load_folders(app_data_root, case_id, backup_id)
    folder = {
        "id": str(uuid.uuid4()),
        "name": (name or "New folder").strip() or "New folder",
        "parent_id": parent_id or LIBRARY_ROOT_FOLDER_ID,
    }
    folders.append(folder)
    save_folders(app_data_root, case_id, backup_id, folders)
    return folder


def rename_folder(
    app_data_root: Path,
    case_id: str,
    backup_id: str,
    folder_id: str,
    name: str,
) -> Optional[Dict[str, Any]]:
    """Rename a folder by id. Returns the updated folder or None."""
    folders = load_folders(app_data_root, case_id, backup_id)
    for f in folders:
        if f.get("id") == folder_id:
            f["name"] = (name or "Unnamed folder").strip() or "Unnamed folder"
            save_folders(app_data_root, case_id, backup_id, folders)
            return f
    return None


def move_folder(
    app_data_root: Path,
    case_id: str,
    backup_id: str,
    folder_id: str,
    parent_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Move a folder to a new parent. Rejects self/descendant cycles and any
    attempt to reparent the library root. Returns updated folder or None."""
    if folder_id == LIBRARY_ROOT_FOLDER_ID:
        return None
    target_parent = parent_id or LIBRARY_ROOT_FOLDER_ID
    if folder_id == target_parent:
        return None
    folders = load_folders(app_data_root, case_id, backup_id)
    if _is_descendant(folders, folder_id, target_parent):
        return None
    for f in folders:
        if f.get("id") == folder_id:
            f["parent_id"] = target_parent
            save_folders(app_data_root, case_id, backup_id, folders)
            return f
    return None


def _children_of(folders: List[Dict[str, Any]], parent_id: Optional[str]) -> List[Dict[str, Any]]:
    return [f for f in folders if (f.get("parent_id") or None) == (parent_id or None)]


def _is_descendant(folders: List[Dict[str, Any]], ancestor_id: str, candidate_id: str) -> bool:
    """Return True if candidate_id is the same as, or a descendant of, ancestor_id."""
    if ancestor_id == candidate_id:
        return True
    by_id = {f.get("id"): f for f in folders}
    current = by_id.get(candidate_id)
    seen: Set[str] = set()
    while current is not None:
        cid = current.get("id")
        if not cid or cid in seen:
            return False
        seen.add(cid)
        parent = current.get("parent_id")
        if parent == ancestor_id:
            return True
        current = by_id.get(parent) if parent else None
    return False


def descendant_folder_ids(folders: List[Dict[str, Any]], folder_id: str) -> List[str]:
    """Return all folder ids in the subtree rooted at folder_id (including folder_id itself)."""
    by_parent: Dict[Optional[str], List[str]] = {}
    for f in folders:
        by_parent.setdefault(f.get("parent_id") or None, []).append(f.get("id"))
    out: List[str] = []
    stack = [folder_id]
    while stack:
        fid = stack.pop()
        if not fid:
            continue
        out.append(fid)
        for child in by_parent.get(fid, []):
            stack.append(child)
    return out


def descendant_search_count(
    folders: List[Dict[str, Any]],
    searches: List[Dict[str, Any]],
    folder_id: str,
) -> int:
    """Count saved searches inside the subtree rooted at folder_id."""
    ids = set(descendant_folder_ids(folders, folder_id))
    return sum(1 for s in searches if (s.get("folder_id") or None) in ids)


def delete_folder_cascade(
    app_data_root: Path,
    case_id: str,
    backup_id: str,
    folder_id: str,
) -> Tuple[int, int]:
    """
    Delete a folder, all descendant folders, and every saved search inside the subtree.
    Returns (folders_deleted, searches_deleted). The library root folder cannot
    be deleted.
    """
    if folder_id == LIBRARY_ROOT_FOLDER_ID:
        return (0, 0)
    folders = load_folders(app_data_root, case_id, backup_id)
    searches = load_saved_searches(app_data_root, case_id, backup_id)
    ids = set(descendant_folder_ids(folders, folder_id))
    if not ids:
        return (0, 0)
    new_folders = [f for f in folders if f.get("id") not in ids]
    new_searches = [s for s in searches if (s.get("folder_id") or None) not in ids]
    folders_deleted = len(folders) - len(new_folders)
    searches_deleted = len(searches) - len(new_searches)
    storage_root = _backup_saved_search_root(app_data_root, case_id, backup_id)
    _write_raw(storage_root, new_searches, new_folders)
    return (folders_deleted, searches_deleted)


def walk_folders_depth_first(folders: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], int]]:
    """
    Return [(folder, depth), ...] in a stable depth-first order rooted at top-level folders.
    The library root folder is emitted first at depth 0; remaining siblings sort by name.
    Folders with missing parents are treated as top-level.
    """
    by_parent: Dict[Optional[str], List[Dict[str, Any]]] = {}
    folder_ids = {f.get("id") for f in folders}
    for f in folders:
        parent = f.get("parent_id") or None
        if parent and parent not in folder_ids:
            parent = None
        by_parent.setdefault(parent, []).append(f)

    def _sort_key(f: Dict[str, Any]) -> Tuple[int, str]:
        is_root = 0 if f.get("id") == LIBRARY_ROOT_FOLDER_ID else 1
        return (is_root, (f.get("name") or "").lower())

    for k in by_parent:
        by_parent[k].sort(key=_sort_key)

    out: List[Tuple[Dict[str, Any], int]] = []

    def visit(parent_id: Optional[str], depth: int) -> None:
        for child in by_parent.get(parent_id, []):
            out.append((child, depth))
            visit(child.get("id"), depth + 1)

    visit(None, 0)
    return out
