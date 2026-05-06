"""
Persist and load saved search definitions (name, criteria, chunk_24h).
Stored under app data root as saved_searches.json.
Each saved search has a sequence number (0001, 0002, ...) for Conversation ID prefixing.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def _saved_searches_file(app_data_root: Path) -> Path:
    return app_data_root / "saved_searches.json"


def load_saved_searches(app_data_root: Path) -> List[Dict[str, Any]]:
    """Load list of saved searches. Each item: id, sequence, name, to_filter, body_filter, date_from, date_to, has_attachments, hash_filter, chunk_24h. Legacy from_filter is ignored."""
    path = _saved_searches_file(app_data_root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("searches", [])
        searches = [dict(s) for s in items]
        # One-time migration: assign sequence only to searches that have none (legacy data).
        # Never reassign by position — sequences are stable and never reused after deletion.
        modified = False
        next_seq = _next_sequence(searches)
        for s in searches:
            seq = s.get("sequence")
            if seq is None or (isinstance(seq, str) and str(seq).strip() == ""):
                s["sequence"] = next_seq
                next_seq += 1
                modified = True
        if modified:
            save_saved_searches(app_data_root, searches)
        return searches
    except Exception:
        return []


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


def save_saved_searches(app_data_root: Path, searches: List[Dict[str, Any]]) -> None:
    """Overwrite saved searches file with the given list."""
    path = _saved_searches_file(app_data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"searches": searches}, indent=2),
        encoding="utf-8",
    )


def add_saved_search(
    app_data_root: Path,
    name: str,
    to_filter: str = "",
    body_filter: str = "",
    date_from: str = "",
    date_to: str = "",
    has_attachments: str = "any",
    hash_filter: str = "",
    chunk_24h: bool = False,
) -> Dict[str, Any]:
    """Append a new saved search and return it (with id and sequence set)."""
    searches = load_saved_searches(app_data_root)
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
    }
    searches.append(item)
    save_saved_searches(app_data_root, searches)
    return item


def update_saved_search(
    app_data_root: Path,
    search_id: str,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Update a saved search by id. Returns updated item or None."""
    searches = load_saved_searches(app_data_root)
    for i, s in enumerate(searches):
        if s.get("id") == search_id:
            allowed = {"name", "to_filter", "body_filter", "date_from", "date_to", "has_attachments", "hash_filter", "chunk_24h", "sequence"}
            for k, v in kwargs.items():
                if k in allowed:
                    searches[i][k] = v
            save_saved_searches(app_data_root, searches)
            return searches[i]
    return None


def delete_saved_search(app_data_root: Path, search_id: str) -> bool:
    """Remove a saved search by id. Returns True if removed."""
    searches = load_saved_searches(app_data_root)
    new_list = [s for s in searches if s.get("id") != search_id]
    if len(new_list) == len(searches):
        return False
    save_saved_searches(app_data_root, new_list)
    return True
