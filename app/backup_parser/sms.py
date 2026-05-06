"""
Extract messages, chats, and handles from sms.db inside an iTunes backup.
Supports legacy (message/msg_group/group_member/madrid_*) and modern (chat/handle/chat_message_join) schemas.
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

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

    # Handles: from group_member or distinct addresses
    seen: set = set()
    for m in messages:
        sid = m.get("sender_id") or m.get("chat_identifier")
        if sid and sid not in seen:
            seen.add(sid)
            handles.append({"rowid": len(handles) + 1, "id": sid})
    return messages, chats, handles


def _get_messages_modern(conn: sqlite3.Connection) -> tuple[List[dict], List[dict], List[dict]]:
    """Modern schema: chat, handle, chat_message_join, message_attachment_join, attachment."""
    cur = conn.cursor()
    messages = []
    chats = []
    handles = []

    cur.execute("SELECT ROWID, chat_identifier FROM chat")
    chat_list = cur.fetchall()
    for rowid, ident in chat_list:
        chats.append({
            "rowid": rowid,
            "display_name": ident or "",
            "chat_identifier": ident or "",
        })
    chat_ids = {c["rowid"] for c in chats}

    cur.execute("SELECT ROWID, id FROM handle")
    handle_list = cur.fetchall()
    for rowid, id_ in handle_list:
        handles.append({"rowid": rowid, "id": id_ or ""})
    handle_by_id = {h["rowid"]: h["id"] for h in handles}

    # message table columns: detect guid and is_deleted if present
    msg_cols: Dict[str, str] = {}
    cur.execute("PRAGMA table_info(message)")
    for row in cur.fetchall():
        if row[1]:
            msg_cols[row[1].lower()] = row[1]
    has_guid = "guid" in msg_cols
    has_is_deleted = "is_deleted" in msg_cols

    select_cols = ["m.ROWID", "m.text", "m.date", "m.is_from_me", "m.handle_id", "cj.chat_id"]
    if has_guid:
        select_cols.insert(5, "m.guid")
    if has_is_deleted:
        select_cols.append("m.is_deleted")
    select_str = ", ".join(select_cols)
    cur.execute(f"""
        SELECT {select_str}
        FROM message m
        JOIN chat_message_join cj ON cj.message_id = m.ROWID
    """)
    for row in cur.fetchall():
        idx = 0
        rowid = row[idx]; idx += 1
        text = row[idx]; idx += 1
        date = row[idx]; idx += 1
        is_from_me = row[idx]; idx += 1
        handle_id = row[idx]; idx += 1
        guid_val = row[idx] if has_guid else None
        if has_guid:
            idx += 1
        chat_id = row[idx]; idx += 1
        is_deleted_val = row[idx] if has_is_deleted else None

        ts = _apple_date_to_unix(date)
        chat_identifier = ""
        for c in chats:
            if c["rowid"] == chat_id:
                chat_identifier = c.get("chat_identifier", "")
                break
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
        }
        msg_dict["message_id"] = str(guid_val).strip() if (has_guid and guid_val) else str(rowid)
        if has_is_deleted and is_deleted_val:
            msg_dict["is_deleted"] = bool(is_deleted_val)
        messages.append(msg_dict)

    # message_attachment_join + attachment
    if _table_exists(cur, "message_attachment_join") and _table_exists(cur, "attachment"):
        # Attachment table may have guid (iOS) for resolving file path in backup
        cur.execute("PRAGMA table_info(attachment)")
        att_cols = {row[1].lower(): row[1] for row in cur.fetchall() if row[1]}
        has_guid = "guid" in att_cols
        if has_guid:
            cur.execute("""
                SELECT maj.message_id, a.filename, a.transfer_name, a.mime_type, a.ROWID, a.guid
                FROM message_attachment_join maj
                JOIN attachment a ON a.ROWID = maj.attachment_id
            """)
        else:
            cur.execute("""
                SELECT maj.message_id, a.filename, a.transfer_name, a.mime_type, a.ROWID
                FROM message_attachment_join maj
                JOIN attachment a ON a.ROWID = maj.attachment_id
            """)
        att_by_msg: Dict[int, List[dict]] = {}
        for row in cur.fetchall():
            msg_id = row[0]
            filename = row[1] or ""
            transfer_name = row[2] or ""
            mime = row[3] or ""
            att_id = row[4]
            guid = row[5] if has_guid and len(row) > 5 else ""
            # Use filename as relative_path if it looks like a path (e.g. Library/SMS/Attachments/...)
            rel_path = ""
            fname = (filename or transfer_name or "").strip()
            if fname and ("/" in fname or "\\" in fname):
                rel_path = fname.replace("\\", "/").lstrip("/")
            att_by_msg.setdefault(msg_id, []).append({
                "filename": filename or transfer_name or "",
                "transfer_name": transfer_name or filename or "",
                "mime_type": mime or "",
                "domain": "HomeDomain",
                "relative_path": rel_path,
                "guid": guid,
            })
        for m in messages:
            m["attachments"] = att_by_msg.get(m["rowid"], [])

    # Participants per chat: from chat_handle_join if present, else from distinct handles in messages
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
