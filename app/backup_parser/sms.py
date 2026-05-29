"""
Extract messages, chats, and handles from sms.db inside an iTunes backup.
Supports legacy (message/msg_group/group_member/madrid_*) and modern (chat/handle/chat_message_join) schemas.
"""

from __future__ import annotations

import base64
import re
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Apple epoch: seconds between 2001-01-01 and 1970-01-01
APPLE_EPOCH_OFFSET = 978307200

# iMessage embeds U+FFFC (Object Replacement) in text for attachments; strip Unicode Specials block
_OBJ_PATTERN = re.compile(r"[\ufff0-\uffff]")


def _strip_obj_from_text(text: Optional[str]) -> str:
    """Remove U+FFFC and other Unicode Specials from message text."""
    if not text:
        return ""
    return _OBJ_PATTERN.sub("", text).strip()


def _log():
    from app.logging_config import get_logger
    return get_logger()


def _apple_date_to_unix(apple_date: Optional[int]) -> Optional[float]:
    """Convert Apple (seconds since 2001) or nanosecond timestamp to Unix timestamp."""
    if apple_date is None:
        return None
    try:
        val = float(apple_date)
        # Apple nanosecond timestamps are typically 1e17–1e18; seconds are 1e8–1e10
        if val > 1e15:
            val = val / 1_000_000_000.0
        return val + APPLE_EPOCH_OFFSET
    except (TypeError, ValueError):
        return None


# Well-known fileID for sms.db in iTunes backups (SHA1 of HomeDomain-Library/SMS/sms.db)
SMS_DB_FILE_ID = "3d0d7e5fb2ce288813306e4d4636395e047a3d28"

# Optional sms.db columns extracted when present (modern + legacy message tables).
MESSAGE_OPTIONAL_COLUMNS = [
    "service", "account", "account_guid", "item_type",
    "group_title", "group_action_type",
    "is_system_message", "is_service_message", "is_auto_reply",
]

ATTACHMENT_OPTIONAL_COLUMNS = [
    "total_bytes", "create_date", "is_outgoing", "transfer_state", "uti",
    "is_sticker", "hide_attachment",
]

MESSAGE_APPLE_DATE_COLUMNS = frozenset()

ATTACHMENT_APPLE_DATE_COLUMNS = frozenset({
    "create_date",
})

SUPPLEMENTAL_MESSAGE_TABLES = (
    "deleted_messages",
    "sync_deleted_messages",
)


def _table_column_map(cur: sqlite3.Cursor, table: str) -> Dict[str, str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {(row[1] or "").lower(): row[1] for row in cur.fetchall() if row[1]}


def _pick_existing_columns(col_map: Dict[str, str], names: List[str]) -> List[str]:
    return [col_map[name.lower()] for name in names if name.lower() in col_map]


def _pick_cloudkit_columns(col_map: Dict[str, str]) -> List[str]:
    return [col_map[key] for key in sorted(col_map) if key.startswith("ck_cloudkit")]


def _serialize_blob(value: Any, max_len: int = 500) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:max_len]
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
            if text and sum(1 for c in text[:80] if c.isprintable() or c in "\r\n\t") >= len(text[:80]) * 0.8:
                return text[:max_len]
        except Exception:
            pass
        preview = base64.b64encode(value[:128]).decode("ascii")
        suffix = f" ({len(value)} bytes)" if len(value) > 128 else ""
        return preview + suffix
    return str(value)[:max_len]


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


def _assign_parsed_field(target: dict, key: str, value: Any) -> None:
    key_lower = key.lower()
    if key_lower in MESSAGE_APPLE_DATE_COLUMNS:
        target[f"{key_lower}_timestamp"] = _apple_date_to_unix(value)
        return
    if key_lower in ATTACHMENT_APPLE_DATE_COLUMNS:
        target[f"{key_lower}_timestamp"] = _apple_date_to_unix(value)
        return
    if key_lower.startswith("is_"):
        target[key_lower] = _coerce_bool(value)
        return
    if value is None:
        return
    if isinstance(value, bytes):
        target[key_lower] = _serialize_blob(value)
        return
    target[key_lower] = value


def _row_dict(columns: List[str], row: Tuple[Any, ...]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for idx, col in enumerate(columns):
        if idx >= len(row):
            break
        out[col.lower()] = row[idx]
    return out


def _apply_optional_message_fields(msg: dict, fields: Dict[str, Any]) -> None:
    for key, value in fields.items():
        if key in {"rowid", "text", "date", "is_from_me", "handle_id", "chat_id", "guid", "is_deleted"}:
            continue
        _assign_parsed_field(msg, key, value)


def _get_sms_db_bytes(parser: Any) -> Optional[bytes]:
    """Retrieve sms.db from backup. Tries: direct read by ID, entry by ID, path candidates, path search, scan Manifest .db, brute-force scan (unencrypted only)."""
    from .parser import BackupParser
    log = _log()
    # 1) Direct read by file ID
    log.info("sms discovery step 1: direct read by ID")
    data = parser.read_file_bytes_by_id(SMS_DB_FILE_ID)
    if data and len(data) > 100:
        log.info("sms db found: %d bytes from step 1 (direct by ID)", len(data))
        return data
    log.info("step 1: direct read failed or < 100 bytes")
    # 2) Well-known file ID via entry
    log.info("sms discovery step 2: get_file_by_id + read_file_bytes")
    entry = parser.get_file_by_id(SMS_DB_FILE_ID)
    log.info("step 2: get_file_by_id(sms_id)=%s", "found" if entry else "not found")
    if entry:
        data = parser.read_file_bytes(entry)
        n = len(data) if data else 0
        log.info("step 2: read_file_bytes: %d bytes", n)
        if data and n > 100:
            log.info("sms db found: %d bytes from step 2", n)
            return data
    # 3) Path-based candidates
    log.info("sms discovery step 3: path candidates")
    candidates = [
        ("HomeDomain", "Library/SMS/sms.db"),
        ("MediaDomain", "Library/SMS/sms.db"),
        ("HomeDomain", "var/mobile/Library/SMS/sms.db"),
        ("HomeDomain", "Library\\SMS\\sms.db"),
        ("", "Library/SMS/sms.db"),
        ("HomeDomain", "SMS/sms.db"),
    ]
    for domain, rel_path in candidates:
        entry = parser.get_file_by_path(domain, rel_path)
        data = parser.read_file_bytes(entry) if entry else None
        n = len(data) if data else 0
        log.info("step 3 candidate (%s, %s): entry=%s read=%d bytes", domain, rel_path, "found" if entry else "not found", n)
        if data and n > 100:
            log.info("sms db found: %d bytes from step 3", n)
            return data
    # 4) Paths matching "sms"
    log.info("sms discovery step 4: paths matching 'sms'")
    matches = parser.get_paths_matching("sms")
    log.info("step 4: get_paths_matching('sms') returned %d paths", len(matches))
    for domain, rel_path in matches:
        if not rel_path.lower().endswith(".db"):
            continue
        entry = parser.get_file_by_path(domain, rel_path)
        if not entry:
            continue
        data = parser.read_file_bytes(entry)
        if not data or len(data) < 100:
            continue
        is_msg = _is_messages_db(data)
        has_msg = _db_has_messages(data) if is_msg else False
        log.info("step 4 path (%s, %s): is_messages_db=%s has_messages=%s", domain, rel_path, is_msg, has_msg)
        if is_msg and has_msg:
            log.info("sms db found: %d bytes from step 4", len(data))
            return data
    # 5) Scan every .db in Manifest
    log.info("sms discovery step 5: scan all .db from Manifest")
    db_files = parser.list_db_files()
    seen_ids: set = set()
    unique_count = len(set(f[0] for f in db_files))
    log.info("step 5: list_db_files returned %d entries (%d unique file_ids)", len(db_files), unique_count)
    for file_id, domain, rel_path in db_files:
        if file_id in seen_ids:
            continue
        seen_ids.add(file_id)
        entry = parser.get_file_by_id(file_id)
        if not entry:
            continue
        data = parser.read_file_bytes(entry)
        if not data or len(data) < 100:
            continue
        if not _is_messages_db(data):
            continue
        has_msg = _db_has_messages(data)
        log.debug("step 5 file_id=%s path=(%s,%s): is_messages_db=True has_messages=%s", file_id, domain, rel_path, has_msg)
        if has_msg:
            log.info("sms db found: %d bytes from step 5 (file_id=%s)", len(data), file_id)
            return data
    # 6) Brute-force: scan backup dir for 40-char hex files that are SQLite message DBs (unencrypted only)
    if not getattr(parser, "_is_encrypted", True):
        log.info("sms discovery step 6: brute-force scan backup dir")
        data = _brute_force_find_sms_db(parser, log)
        if data:
            log.info("sms db found: %d bytes from step 6 (brute-force)", len(data))
            return data
    log.warning("sms db not found after all steps")
    return None


def _brute_force_find_sms_db(parser: Any, log: Any) -> Optional[bytes]:
    """For unencrypted backups only: scan backup_path for files with 40-char hex name that are SQLite message DBs."""
    backup_path = getattr(parser, "backup_path", None)
    if not backup_path or not hasattr(parser, "_is_encrypted") or parser._is_encrypted:
        return None
    hex_re = re.compile(r"^[0-9a-fA-F]{40}$")
    candidates: List[Path] = []
    try:
        for child in backup_path.iterdir():
            if child.is_file() and hex_re.match(child.name):
                candidates.append(child)
            elif child.is_dir() and len(child.name) == 2 and child.name.isalnum():
                for sub in child.iterdir():
                    if sub.is_file() and hex_re.match(sub.name):
                        candidates.append(sub)
    except Exception as e:
        log.info("brute-force: listdir failed: %s", e)
        return None
    log.info("brute-force: %d candidate files", len(candidates))
    max_try = 500
    tried = 0
    sqlite_count = 0
    msg_db_count = 0
    for path in candidates[:max_try]:
        tried += 1
        try:
            header = path.read_bytes()[:16]
            if not header.startswith(b"SQLite format 3\x00"):
                continue
            sqlite_count += 1
            data = path.read_bytes()
            if len(data) < 100:
                continue
            if not _is_messages_db(data):
                continue
            msg_db_count += 1
            if _db_has_messages(data):
                log.info("brute-force: using %s size=%d (tried=%d sqlite=%d message_dbs=%d)", path, len(data), tried, sqlite_count, msg_db_count)
                return data
        except Exception:
            continue
    log.info("brute-force: tried %d files, %d SQLite, %d message DBs, none with rows", tried, sqlite_count, msg_db_count)
    return None


def _db_has_messages(data: bytes) -> bool:
    """Return True if the SQLite DB has at least one row in message or chat table."""
    try:
        tmp = Path(tempfile.mktemp(suffix=".db"))
        tmp.write_bytes(data)
        conn = sqlite3.connect(str(tmp))
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0].lower() for r in cur.fetchall()}
        if "message" in tables:
            cur.execute("SELECT 1 FROM message LIMIT 1")
            if cur.fetchone():
                conn.close()
                tmp.unlink(missing_ok=True)
                _log().debug("_db_has_messages: tables=%s has message row=True", sorted(tables))
                return True
        if "chat" in tables:
            cur.execute("SELECT 1 FROM chat LIMIT 1")
            if cur.fetchone():
                conn.close()
                tmp.unlink(missing_ok=True)
                _log().debug("_db_has_messages: tables=%s has chat row=True", sorted(tables))
                return True
        conn.close()
        tmp.unlink(missing_ok=True)
        _log().debug("_db_has_messages: tables=%s has_rows=False", sorted(tables))
    except Exception as e:
        _log().debug("_db_has_messages: exception %s", e)
    return False


def _is_messages_db(data: bytes) -> bool:
    """Quick check that bytes look like an SQLite DB with a message-related table."""
    if not data.startswith(b"SQLite format 3"):
        return False
    try:
        tmp = Path(tempfile.mktemp(suffix=".db"))
        tmp.write_bytes(data)
        conn = sqlite3.connect(str(tmp))
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0].lower() for r in cur.fetchall()}
        conn.close()
        tmp.unlink(missing_ok=True)
        ok = "message" in tables or "chat" in tables
        _log().debug("_is_messages_db: tables=%s message_or_chat=%s", sorted(tables), ok)
        return ok
    except Exception as e:
        _log().debug("_is_messages_db: exception %s", e)
        return False


def _table_exists(cursor: sqlite3.Cursor, name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cursor.fetchone() is not None


def _get_messages_legacy(conn: sqlite3.Connection) -> tuple[List[dict], List[dict], List[dict]]:
    """Legacy schema: message, msg_group, group_member, madrid_chat, madrid_attachment."""
    cur = conn.cursor()
    messages: List[dict] = []
    chats: List[dict] = []
    handles: List[dict] = []

    # msg_group.ROWID = chat id; group_member links group to address (contact)
    group_to_address: Dict[int, str] = {}
    if _table_exists(cur, "group_member"):
        cur.execute("SELECT group_id, address FROM group_member")
        for gid, addr in cur.fetchall():
            if gid and addr:
                group_to_address[gid] = addr

    # Build chats from msg_group (or madrid_chat if present)
    if _table_exists(cur, "msg_group"):
        cur.execute("SELECT ROWID FROM msg_group")
        for (rowid,) in cur.fetchall():
            chats.append({
                "rowid": rowid,
                "display_name": group_to_address.get(rowid, ""),
                "chat_identifier": group_to_address.get(rowid, ""),
            })
    if _table_exists(cur, "madrid_chat") and not chats:
        cur.execute("SELECT ROWID, chat_identifier FROM madrid_chat")
        for rowid, ident in cur.fetchall():
            chats.append({"rowid": rowid, "display_name": ident or "", "chat_identifier": ident or ""})

    # message table columns
    message_columns = []
    cur.execute("PRAGMA table_info(message)")
    for col in cur.fetchall():
        message_columns.append(col[1].lower())
    has_is_from_me = "is_from_me" in message_columns
    has_group_id = "group_id" in message_columns

    if has_group_id:
        cur.execute("SELECT ROWID, text, date, group_id, address FROM message")
    else:
        cur.execute("SELECT ROWID, text, date, address FROM message")
    rows = cur.fetchall()

    for row in rows:
        if has_group_id and len(row) >= 5:
            rowid, text, date, group_id, address = row[0], row[1], row[2], row[3], row[4]
            chat_id = group_id if group_id else 0
        else:
            rowid, text, date, address = row[0], row[1], row[2], row[3]
            chat_id = 0
        ts = _apple_date_to_unix(date)
        messages.append({
            "rowid": rowid,
            "message_id": str(rowid),  # legacy has no guid; use rowid
            "text": _strip_obj_from_text(text),
            "date": date,
            "date_timestamp": ts,
            "date_formatted": "",
            "is_from_me": False,
            "handle_id": None,
            "chat_id": chat_id,
            "sender_id": address or "",
            "chat_identifier": group_to_address.get(chat_id, "") if chat_id else (address or ""),
            "attachments": [],
        })

    # Ensure chat 0 exists for messages with no group
    if has_group_id and not any(c.get("rowid") == 0 for c in chats):
        chats.insert(0, {"rowid": 0, "display_name": "Unknown", "chat_identifier": "Unknown"})

    # is_from_me: try to set from flags or other column
    if has_is_from_me:
        cur.execute("SELECT ROWID, is_from_me FROM message")
        from_map = {r[0]: bool(r[1]) for r in cur.fetchall()}
        for m in messages:
            m["is_from_me"] = from_map.get(m["rowid"], False)

    msg_by_rowid = {m["rowid"]: m for m in messages}
    msg_cols = _table_column_map(cur, "message")
    optional_msg_cols = _pick_existing_columns(msg_cols, MESSAGE_OPTIONAL_COLUMNS)
    if "guid" in msg_cols and "guid" not in optional_msg_cols:
        optional_msg_cols = [msg_cols["guid"]] + optional_msg_cols
    if optional_msg_cols:
        cols_sql = ", ".join(["ROWID"] + [f'"{c}"' if not c.isidentifier() else c for c in optional_msg_cols])
        cur.execute(f"SELECT {cols_sql} FROM message")
        for row in cur.fetchall():
            msg = msg_by_rowid.get(row[0])
            if not msg:
                continue
            for i, col in enumerate(optional_msg_cols, start=1):
                col_lower = col.lower()
                if col_lower == "guid":
                    if row[i]:
                        msg["message_id"] = str(row[i]).strip()
                    continue
                _assign_parsed_field(msg, col_lower, row[i])
    for m in messages:
        m.setdefault("record_source", "message")

    # Attachments: madrid_attachment has message_id, filename, uti_type
    if _table_exists(cur, "madrid_attachment"):
        cur.execute("SELECT message_id, filename, uti_type, ROWID FROM madrid_attachment")
        att_by_msg: Dict[int, List[dict]] = {}
        for msg_id, filename, uti, att_id in cur.fetchall():
            att_by_msg.setdefault(msg_id, []).append({
                "filename": filename or "",
                "mime_type": uti or "",
                "domain": "HomeDomain",
                "relative_path": "",  # legacy attachments often in Attachments folder by guid
            })
        for m in messages:
            m["attachments"] = att_by_msg.get(m["rowid"], [])

    _append_supplemental_messages(cur, messages, chat_by_id={}, handle_by_id={})
    _attach_recoverable_parts(cur, messages)

    # Handles: from group_member or distinct addresses
    seen: set = set()
    for m in messages:
        sid = m.get("sender_id") or m.get("chat_identifier")
        if sid and sid not in seen:
            seen.add(sid)
            handles.append({"rowid": len(handles) + 1, "id": sid})
    return messages, chats, handles


def _load_modern_chats(cur: sqlite3.Cursor) -> Tuple[List[dict], Dict[int, dict]]:
    cur.execute("SELECT ROWID, chat_identifier FROM chat")
    chats: List[dict] = []
    chat_by_id: Dict[int, dict] = {}
    for rowid, ident in cur.fetchall():
        chat = {
            "rowid": rowid,
            "display_name": ident or "",
            "chat_identifier": ident or "",
        }
        chats.append(chat)
        chat_by_id[rowid] = chat
    return chats, chat_by_id


def _build_attachment_record(
    att_cols: Dict[str, str],
    base_values: Dict[str, Any],
    extra_values: Dict[str, Any],
) -> dict:
    att = {
        "filename": base_values.get("filename") or base_values.get("transfer_name") or "",
        "transfer_name": base_values.get("transfer_name") or base_values.get("filename") or "",
        "mime_type": base_values.get("mime_type") or "",
        "domain": "HomeDomain",
        "relative_path": base_values.get("relative_path") or "",
        "guid": base_values.get("guid") or "",
    }
    for key, value in extra_values.items():
        _assign_parsed_field(att, key, value)
    return att


def _append_supplemental_messages(
    cur: sqlite3.Cursor,
    messages: List[dict],
    chat_by_id: Dict[int, dict],
    handle_by_id: Dict[int, str],
) -> None:
    """Add rows from deleted_messages / sync_deleted_messages when present."""
    next_negative_rowid = -1
    for table in SUPPLEMENTAL_MESSAGE_TABLES:
        if not _table_exists(cur, table):
            continue
        col_map = _table_column_map(cur, table)
        select_cols = ["ROWID"] + _pick_existing_columns(
            col_map,
            [
                "text", "date", "guid", "handle_id", "chat_id", "is_from_me", "service",
                "account", "account_guid", "item_type", "delete_date",
            ],
        )
        if len(select_cols) <= 1:
            continue
        cols_sql = ", ".join(select_cols)
        cur.execute(f"SELECT {cols_sql} FROM {table}")
        for row in cur.fetchall():
            fields = _row_dict(select_cols, row)
            rowid = fields.pop("rowid")
            text = fields.pop("text", None)
            date = fields.pop("date", None) or fields.pop("delete_date", None)
            guid = fields.pop("guid", None)
            handle_id = fields.pop("handle_id", None)
            chat_id = fields.pop("chat_id", None)
            is_from_me = fields.pop("is_from_me", None)
            ts = _apple_date_to_unix(date)
            chat_identifier = ""
            if chat_id is not None:
                chat = chat_by_id.get(chat_id)
                if chat:
                    chat_identifier = chat.get("chat_identifier") or ""
            sender_id = handle_by_id.get(handle_id, "") if handle_id else ""
            msg_dict: Dict[str, Any] = {
                "rowid": next_negative_rowid,
                "message_id": str(guid).strip() if guid else f"{table}:{rowid}",
                "text": _strip_obj_from_text(text),
                "date": date,
                "date_timestamp": ts,
                "date_formatted": "",
                "is_from_me": _coerce_bool(is_from_me),
                "handle_id": handle_id,
                "chat_id": chat_id,
                "sender_id": sender_id,
                "chat_identifier": chat_identifier,
                "attachments": [],
                "is_deleted": True,
                "record_source": table,
            }
            _apply_optional_message_fields(msg_dict, fields)
            messages.append(msg_dict)
            next_negative_rowid -= 1


def _attach_recoverable_parts(cur: sqlite3.Cursor, messages: List[dict]) -> None:
    """Attach recoverable_message_part rows to matching messages when tables exist."""
    if not _table_exists(cur, "recoverable_message_part"):
        return
    part_cols = _table_column_map(cur, "recoverable_message_part")
    select_cols = ["ROWID"] + _pick_existing_columns(
        part_cols,
        ["message_id", "part_index", "data", "content_type", "owner_id", "attribution_info"],
    )
    if "message_id" not in {c.lower() for c in select_cols}:
        return
    cols_sql = ", ".join(select_cols)
    cur.execute(f"SELECT {cols_sql} FROM recoverable_message_part")
    parts_by_message: Dict[int, List[dict]] = defaultdict(list)
    for row in cur.fetchall():
        fields = _row_dict(select_cols, row)
        msg_id = fields.get("message_id")
        if msg_id is None:
            continue
        part: Dict[str, Any] = {"rowid": fields.get("rowid")}
        for key, value in fields.items():
            if key in {"rowid", "message_id"}:
                continue
            if key == "data":
                part[key] = _serialize_blob(value)
            else:
                part[key] = value
        parts_by_message[int(msg_id)].append(part)
    msg_by_rowid = {m["rowid"]: m for m in messages if (m.get("rowid") or 0) > 0}
    for msg_id, parts in parts_by_message.items():
        msg = msg_by_rowid.get(msg_id)
        if msg is not None:
            msg["recoverable_parts"] = parts


def _get_messages_modern(conn: sqlite3.Connection) -> tuple[List[dict], List[dict], List[dict]]:
    """Modern schema: chat, handle, chat_message_join, message_attachment_join, attachment."""
    cur = conn.cursor()
    messages: List[dict] = []
    handles: List[dict] = []

    chats, chat_by_id = _load_modern_chats(cur)

    cur.execute("SELECT ROWID, id FROM handle")
    handle_list = cur.fetchall()
    for rowid, id_ in handle_list:
        handles.append({"rowid": rowid, "id": id_ or ""})
    handle_by_id = {h["rowid"]: h["id"] for h in handles}

    msg_cols = _table_column_map(cur, "message")
    extra_msg_cols = _pick_existing_columns(msg_cols, MESSAGE_OPTIONAL_COLUMNS)

    select_cols = ["m.ROWID", "m.text", "m.date", "m.is_from_me", "m.handle_id"]
    if "guid" in msg_cols:
        select_cols.append("m.guid")
    select_cols.append("cj.chat_id")
    if "is_deleted" in msg_cols:
        select_cols.append("m.is_deleted")
    for col in extra_msg_cols:
        select_cols.append(f"m.{col}")
    select_str = ", ".join(select_cols)
    cur.execute(f"""
        SELECT {select_str}
        FROM message m
        JOIN chat_message_join cj ON cj.message_id = m.ROWID
    """)
    result_cols = [part.split(".")[-1] for part in select_cols]
    for row in cur.fetchall():
        fields = _row_dict(result_cols, row)
        rowid = fields.pop("rowid")
        text = fields.pop("text", None)
        date = fields.pop("date", None)
        is_from_me = fields.pop("is_from_me", None)
        handle_id = fields.pop("handle_id", None)
        guid_val = fields.pop("guid", None)
        chat_id = fields.pop("chat_id", None)
        is_deleted_val = fields.pop("is_deleted", None)

        ts = _apple_date_to_unix(date)
        chat = chat_by_id.get(chat_id) if chat_id is not None else None
        chat_identifier = (chat or {}).get("chat_identifier") or ""
        sender_id = handle_by_id.get(handle_id, "") if handle_id else ""
        msg_dict: Dict[str, Any] = {
            "rowid": rowid,
            "text": _strip_obj_from_text(text),
            "date": date,
            "date_timestamp": ts,
            "date_formatted": "",
            "is_from_me": bool(is_from_me),
            "handle_id": handle_id,
            "chat_id": chat_id,
            "sender_id": sender_id,
            "chat_identifier": chat_identifier,
            "attachments": [],
            "record_source": "message",
        }
        msg_dict["message_id"] = str(guid_val).strip() if guid_val else str(rowid)
        if is_deleted_val:
            msg_dict["is_deleted"] = bool(is_deleted_val)
        _apply_optional_message_fields(msg_dict, fields)
        messages.append(msg_dict)

    if _table_exists(cur, "message_attachment_join") and _table_exists(cur, "attachment"):
        att_cols = _table_column_map(cur, "attachment")
        base_att_cols = ["filename", "transfer_name", "mime_type", "ROWID"]
        if "guid" in att_cols:
            base_att_cols.append("guid")
        optional_att_cols = _pick_existing_columns(att_cols, ATTACHMENT_OPTIONAL_COLUMNS)
        optional_att_cols.extend(_pick_cloudkit_columns(att_cols))
        select_att = ["maj.message_id"] + [f"a.{c}" for c in base_att_cols if c != "ROWID"] + ["a.ROWID"]
        for col in optional_att_cols:
            if col.lower() not in {c.lower() for c in base_att_cols}:
                select_att.append(f"a.{col}")
        att_result_cols = [part.split(".")[-1] for part in select_att]
        cur.execute(f"""
            SELECT {", ".join(select_att)}
            FROM message_attachment_join maj
            JOIN attachment a ON a.ROWID = maj.attachment_id
        """)
        att_by_msg: Dict[int, List[dict]] = {}
        for row in cur.fetchall():
            fields = _row_dict(att_result_cols, row)
            msg_id = fields.pop("message_id")
            row_id = fields.pop("rowid", None)
            filename = fields.pop("filename", "") or ""
            transfer_name = fields.pop("transfer_name", "") or ""
            mime = fields.pop("mime_type", "") or ""
            guid = fields.pop("guid", "") or ""
            rel_path = ""
            fname = (filename or transfer_name or "").strip()
            if fname and ("/" in fname or "\\" in fname):
                rel_path = fname.replace("\\", "/").lstrip("/")
            att_by_msg.setdefault(msg_id, []).append(
                _build_attachment_record(
                    att_cols,
                    {
                        "filename": filename,
                        "transfer_name": transfer_name,
                        "mime_type": mime,
                        "guid": guid,
                        "relative_path": rel_path,
                    },
                    fields,
                )
            )
            if row_id is not None:
                att_by_msg[msg_id][-1]["rowid"] = row_id
        for m in messages:
            m["attachments"] = att_by_msg.get(m["rowid"], [])

    participants_by_chat: Dict[int, List[str]] = {}
    if _table_exists(cur, "chat_handle_join"):
        cur.execute("SELECT chat_id, handle_id FROM chat_handle_join")
        join_map: Dict[int, List[int]] = defaultdict(list)
        for cid, hid in cur.fetchall():
            if cid and hid:
                join_map[cid].append(hid)
        for c in chats:
            rowid = c["rowid"]
            ids = sorted(set(handle_by_id.get(hid) for hid in join_map.get(rowid, []) if handle_by_id.get(hid)))
            if ids:
                participants_by_chat[rowid] = ids
    else:
        msg_handles: Dict[int, set] = defaultdict(set)
        for m in messages:
            cid = m.get("chat_id")
            hid = m.get("handle_id")
            if cid is not None and hid is not None:
                msg_handles[cid].add(hid)
        for c in chats:
            rowid = c["rowid"]
            ids = sorted(set(handle_by_id.get(hid) for hid in msg_handles.get(rowid, []) if handle_by_id.get(hid)))
            if ids:
                participants_by_chat[rowid] = ids
    for c in chats:
        c["participant_handle_ids"] = participants_by_chat.get(c["rowid"], [])

    _append_supplemental_messages(cur, messages, chat_by_id, handle_by_id)
    _attach_recoverable_parts(cur, messages)

    return messages, chats, handles


def get_messages_data(
    parser: Any,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Dict[str, List[dict]]:
    """
    Extract messages, chats, and handles from the backup's sms.db.
    Returns {"messages": [...], "chats": [...], "handles": [...]}.
    """
    def progress(pct: float, label: str) -> None:
        if progress_callback:
            progress_callback(pct, label)

    progress(0, "Locating sms.db...")
    data = _get_sms_db_bytes(parser)
    if not data:
        _log().warning("get_messages_data: no sms db found, returning empty")
        return {"messages": [], "chats": [], "handles": []}

    progress(10, "Reading messages database...")
    tmp_path = Path(tempfile.mktemp(suffix=".db"))
    try:
        tmp_path.write_bytes(data)
        conn = sqlite3.connect(str(tmp_path))
    except Exception as e:
        _log().info("get_messages_data: failed to open temp db: %s", e)
        tmp_path.unlink(missing_ok=True)
        return {"messages": [], "chats": [], "handles": []}

    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        table_names = [r[0] for r in cur.fetchall()]
        _log().info("get_messages_data: DB tables: %s", table_names)
        has_chat = _table_exists(cur, "chat")
        has_handle = _table_exists(cur, "handle")
        has_chat_message_join = _table_exists(cur, "chat_message_join")

        if has_chat and has_handle and has_chat_message_join:
            progress(50, "Parsing messages (modern schema)...")
            _log().info("get_messages_data: using modern schema")
            messages, chats, handles = _get_messages_modern(conn)
        else:
            progress(50, "Parsing messages (legacy schema)...")
            _log().info("get_messages_data: using legacy schema")
            messages, chats, handles = _get_messages_legacy(conn)

        n_msg, n_chat, n_handle = len(messages), len(chats), len(handles)
        _log().info("get_messages_data: messages=%d chats=%d handles=%d", n_msg, n_chat, n_handle)
        if n_msg == 0:
            _log().warning("get_messages_data: 0 messages in DB")
        progress(100, "Done.")
        return {"messages": messages, "chats": chats, "handles": handles}
    finally:
        conn.close()
        tmp_path.unlink(missing_ok=True)
