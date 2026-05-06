"""
Extract contact names from AddressBook.sqlitedb in an iTunes backup.
Maps phone numbers and emails to display names (First Last / Organization).
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional

def _log():
    from app.logging_config import get_logger
    return get_logger()


def _normalize_phone(value: str) -> List[str]:
    """Return list of keys to use for lookup: digits-only, +1..., 1..., 10-digit US."""
    if not value or not isinstance(value, str):
        return []
    digits = re.sub(r"\D", "", value)
    if not digits:
        return []
    keys = [digits]
    if len(digits) == 11 and digits.startswith("1"):
        keys.append(digits[1:])  # 10-digit
    if len(digits) == 10 and digits.isdigit():
        keys.append("1" + digits)
        keys.append("+1" + digits)
    keys.append("+" + digits)
    return keys


def _normalize_email(value: str) -> List[str]:
    if not value or not isinstance(value, str):
        return []
    v = value.strip().lower()
    return [v] if v else []


# Well-known fileID for AddressBook.sqlitedb (HomeDomain-Library/AddressBook/AddressBook.sqlitedb)
ADDRESSBOOK_FILE_ID = "31bb7ba8914766d4ba40d6dfb6113c8b614be442"


def _get_addressbook_bytes(parser: Any) -> Optional[bytes]:
    """Retrieve AddressBook.sqlitedb from backup."""
    log = _log()
    # 1) Direct read by well-known file ID
    data = parser.read_file_bytes_by_id(ADDRESSBOOK_FILE_ID)
    if data and len(data) > 500:
        log.info("contacts: AddressBook found by file ID (%d bytes)", len(data))
        return data
    # 2) Entry by file ID + read_file_bytes
    entry = parser.get_file_by_id(ADDRESSBOOK_FILE_ID)
    if entry:
        data = parser.read_file_bytes(entry)
        if data and len(data) > 500:
            log.info("contacts: AddressBook found by file ID + entry (%d bytes)", len(data))
            return data
    # 3) Path candidates
    candidates = [
        ("HomeDomain", "Library/AddressBook/AddressBook.sqlitedb"),
        ("HomeDomain", "var/mobile/Library/AddressBook/AddressBook.sqlitedb"),
        ("HomeDomain", "Library\\AddressBook\\AddressBook.sqlitedb"),
    ]
    for domain, rel in candidates:
        entry = parser.get_file_by_path(domain, rel)
        if entry:
            data = parser.read_file_bytes(entry)
            if data and len(data) > 500:
                log.info("contacts: AddressBook found at %s/%s (%d bytes)", domain, rel, len(data))
                return data
    # 4) Path search AddressBook
    for domain, rel in parser.get_paths_matching("AddressBook"):
        if not rel.endswith(".sqlitedb") and not rel.endswith(".db"):
            continue
        entry = parser.get_file_by_path(domain, rel)
        if entry:
            data = parser.read_file_bytes(entry)
            if data and len(data) > 500:
                log.info("contacts: AddressBook found at %s/%s (%d bytes)", domain, rel, len(data))
                return data
    # 5) Path search Contacts (some iOS versions)
    for domain, rel in parser.get_paths_matching("Contacts"):
        if not rel.endswith(".sqlitedb") and not rel.endswith(".db"):
            continue
        entry = parser.get_file_by_path(domain, rel)
        if entry:
            data = parser.read_file_bytes(entry)
            if data and len(data) > 500:
                log.info("contacts: Contacts db found at %s/%s (%d bytes)", domain, rel, len(data))
                return data
    log.info("contacts: AddressBook.sqlitedb not found")
    return None


def _table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def _get_column_names(cur: sqlite3.Cursor, table: str) -> Dict[str, str]:
    """Return lowercase column name -> actual column name for the table."""
    cur.execute("PRAGMA table_info(%s)" % table)
    rows = cur.fetchall()
    # rows: (cid, name, type, notnull, dflt_value, pk)
    return { (row[1] or "").lower(): row[1] for row in rows if row[1] }


def _value_to_str(value: Any) -> Optional[str]:
    """Convert ABMultiValue.value to string; may be str or BLOB."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8").strip() or None
        except Exception:
            return None
    return None


def _build_contact_map_from_db(data: bytes) -> Dict[str, str]:
    """Build identifier -> display_name from AddressBook.sqlitedb bytes."""
    import os
    out: Dict[str, str] = {}
    # On Windows, NamedTemporaryFile(delete=True) keeps the file locked so SQLite can't open it.
    # Use delete=False and remove the file manually after use.
    fd = None
    path = None
    try:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.write(fd, data)
        os.close(fd)
        fd = None
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        if not _table_exists(cur, "ABPerson") or not _table_exists(cur, "ABMultiValue"):
            conn.close()
            return out
        # ABPerson: discover column names (First/first, Last/last, Organization/organization)
        pcols = _get_column_names(cur, "ABPerson")
        first_col = pcols.get("first") or pcols.get("firstname")
        last_col = pcols.get("last") or pcols.get("lastname")
        org_col = pcols.get("organization") or pcols.get("organizationname")
        if not first_col and not last_col and not org_col:
            # Fallback to hardcoded
            try:
                cur.execute("SELECT ROWID, First, Last, Organization FROM ABPerson")
                first_col, last_col, org_col = "First", "Last", "Organization"
            except sqlite3.OperationalError:
                try:
                    cur.execute("SELECT ROWID, first, last, organization FROM ABPerson")
                    first_col, last_col, org_col = "first", "last", "organization"
                except sqlite3.OperationalError:
                    conn.close()
                    return out
        if first_col and last_col and org_col:
            cur.execute("SELECT ROWID, %s, %s, %s FROM ABPerson" % (
                first_col, last_col, org_col
            ))
        elif first_col and last_col:
            cur.execute("SELECT ROWID, %s, %s, NULL FROM ABPerson" % (first_col, last_col))
        else:
            conn.close()
            return out
        person_names: Dict[int, str] = {}
        for row in cur.fetchall():
            rowid, first, last, org = (row[0], row[1], row[2], row[3] if len(row) > 3 else None)
            first = (first or "").strip() if isinstance(first, str) else ""
            last = (last or "").strip() if isinstance(last, str) else ""
            org = (org or "").strip() if isinstance(org, str) else ""
            if org:
                name = org
            elif first and last:
                name = f"{first} {last}".strip()
            elif first or last:
                name = (first or last).strip()
            else:
                name = ""
            if name:
                person_names[int(rowid)] = name
        # ABMultiValue: discover column names (record_id, property, value)
        mcols = _get_column_names(cur, "ABMultiValue")
        rec_col = mcols.get("record_id") or "record_id"
        prop_col = mcols.get("property") or "property"
        val_col = mcols.get("value") or "value"
        try:
            cur.execute(
                "SELECT %s, %s, %s FROM ABMultiValue WHERE %s IN (3, 4)" % (
                    rec_col, prop_col, val_col, prop_col
                )
            )
        except sqlite3.OperationalError:
            conn.close()
            return out
        for row in cur.fetchall():
            record_id, prop, value = row[0], row[1], row[2]
            value_str = _value_to_str(value)
            if not value_str:
                continue
            name = person_names.get(int(record_id), "")
            if not name:
                continue
            if prop == 3:  # phone
                for k in _normalize_phone(value_str):
                    if k and (k not in out or len(name) > len(out.get(k, ""))):
                        out[k] = name
            elif prop == 4:  # email
                for k in _normalize_email(value_str):
                    if k:
                        out[k] = name
        conn.close()
    except Exception as e:
        _log().info("contacts: parse error %s: %s", type(e).__name__, e)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if path is not None:
            try:
                os.unlink(path)
            except Exception:
                pass
    return out


def get_contacts_data(parser: Any) -> Dict[str, str]:
    """
    Extract contact names from the backup's AddressBook.
    Returns a map from normalized phone/email to display name (First Last or Organization).
    Keys include: digits-only phone, +1..., 1..., 10-digit US; lowercase email.
    """
    data = _get_addressbook_bytes(parser)
    if not data:
        return {}
    contact_map = _build_contact_map_from_db(data)
    _log().info("contacts: resolved %d contact names", len(contact_map))
    return contact_map


def resolve_display_name(identifier: str, contact_map: Dict[str, str]) -> str:
    """
    Resolve a handle identifier (phone or email) to a display name using the contact map.
    Returns contact name if found, otherwise the original identifier (never empty).
    """
    if not identifier:
        return ""
    id_stripped = identifier.strip()
    if not id_stripped:
        return identifier
    # Exact match
    if id_stripped in contact_map:
        name = contact_map[id_stripped]
        return name if name else id_stripped
    # Phone variants
    for k in _normalize_phone(id_stripped):
        if k in contact_map:
            name = contact_map[k]
            return name if name else id_stripped
    # Email
    for k in _normalize_email(id_stripped):
        if k in contact_map:
            name = contact_map[k]
            return name if name else id_stripped
    # Digits-only fallback for +12015551234 style
    digits = re.sub(r"\D", "", id_stripped)
    if digits in contact_map:
        name = contact_map[digits]
        return name if name else id_stripped
    if len(digits) == 11 and digits.startswith("1") and digits[1:] in contact_map:
        name = contact_map[digits[1:]]
        return name if name else id_stripped
    return id_stripped


# Pattern for placeholder group chat IDs (e.g. chat00000000...); show participants instead
_CHAT_PLACEHOLDER_RE = re.compile(r"^chat[0-9a-fA-F]+$")


def is_placeholder_chat_identifier(identifier: str) -> bool:
    """True if identifier looks like a placeholder group chat ID (chat + hex string)."""
    return bool(identifier and _CHAT_PLACEHOLDER_RE.match(identifier.strip()))
