"""
MessagePack cache for case/backup message data.
Stores messages and extracted attachment bytes so viewing requires no access to original backup.
"""

from __future__ import annotations

import hashlib
import msgpack
from pathlib import Path
from typing import Any, Dict, List, Optional

# Attachment bytes are stored under cache_root/attachments/<key>
ATTACHMENTS_DIR = "attachments"


def _backup_id_from_path(backup_path: str) -> str:
    """Stable id for a backup (use path hash if no folder name)."""
    p = Path(backup_path)
    if p.name and len(p.name) > 8 and p.name not in ("Backup", "MobileSync"):
        return p.name
    return hashlib.sha256(backup_path.encode("utf-8")).hexdigest()[:16]


def get_case_cache_root(app_data_root: Path, case_id: str) -> Path:
    return app_data_root / "cases" / case_id


def get_backup_cache_root(app_data_root: Path, case_id: str, backup_id: str) -> Path:
    return get_case_cache_root(app_data_root, case_id) / backup_id


def list_backup_ids(app_data_root: Path, case_id: str) -> List[str]:
    """List backup IDs (folder names) for a case."""
    root = get_case_cache_root(app_data_root, case_id)
    if not root.exists():
        return []
    return [d.name for d in root.iterdir() if d.is_dir() and (d / "messages.msgpack").exists()]


def load_backup_meta(app_data_root: Path, case_id: str, backup_id: str) -> Optional[Dict[str, Any]]:
    path = get_backup_cache_root(app_data_root, case_id, backup_id) / "meta.msgpack"
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return msgpack.unpackb(f.read(), raw=False)


def load_backup_messages(app_data_root: Path, case_id: str, backup_id: str) -> Optional[Dict[str, Any]]:
    path = get_backup_cache_root(app_data_root, case_id, backup_id) / "messages.msgpack"
    if not path.exists():
        return None
    with open(path, "rb") as f:
        data = msgpack.unpackb(f.read(), raw=False)
    data = _normalize_msgpack_keys(data)
    messages = data.get("messages") if isinstance(data, dict) else []
    if messages and isinstance(messages, list) and len(messages) > 0:
        first = messages[0] if isinstance(messages[0], dict) else {}
        from app.logging_config import get_logger
        get_logger().info(
            "cache_load: first message keys=%s date=%s date_timestamp=%s date_formatted=%s",
            list(first.keys()) if first else [],
            first.get("date"),
            first.get("date_timestamp"),
            first.get("date_formatted"),
        )
    return data


def _normalize_msgpack_keys(obj: Any) -> Any:
    """Ensure all dict keys from msgpack are str (some versions return bytes)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = k.decode("utf-8") if isinstance(k, bytes) else k
            out[key] = _normalize_msgpack_keys(v)
        return out
    if isinstance(obj, list):
        return [_normalize_msgpack_keys(x) for x in obj]
    return obj


def save_backup_cache(
    app_data_root: Path,
    case_id: str,
    backup_id: str,
    meta: Dict[str, Any],
    data: Dict[str, Any],
) -> None:
    root = get_backup_cache_root(app_data_root, case_id, backup_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "meta.msgpack").write_bytes(msgpack.packb(meta, use_bin_type=True))
    (root / "messages.msgpack").write_bytes(msgpack.packb(data, use_bin_type=True))


def update_backup_meta_fields(
    app_data_root: Path,
    case_id: str,
    backup_id: str,
    updates: Dict[str, Any],
) -> None:
    """Merge updates into existing meta.msgpack (creates minimal meta if missing)."""
    meta = load_backup_meta(app_data_root, case_id, backup_id) or {}
    meta.update(updates)
    root = get_backup_cache_root(app_data_root, case_id, backup_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "meta.msgpack").write_bytes(msgpack.packb(meta, use_bin_type=True))


def get_attachment_path(app_data_root: Path, case_id: str, backup_id: str, relative_path: str) -> Path:
    """Resolve relative_path (e.g. attachments/xyz) to full path."""
    return get_backup_cache_root(app_data_root, case_id, backup_id) / relative_path


def delete_backup_cache(app_data_root: Path, case_id: str, backup_id: str) -> None:
    import shutil
    root = get_backup_cache_root(app_data_root, case_id, backup_id)
    if root.exists():
        shutil.rmtree(root)


def delete_case_cache(app_data_root: Path, case_id: str) -> None:
    import shutil
    root = get_case_cache_root(app_data_root, case_id)
    if root.exists():
        shutil.rmtree(root)


def backup_id_from_path(backup_path: str) -> str:
    return _backup_id_from_path(backup_path)
