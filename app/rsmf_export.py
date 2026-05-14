"""
RSMF export: build RFC 5322 .rsmf files per Conversation ID for Relativity Short Message Format.
One RSMF per unique conversation_id; each contains rsmf_manifest.json + attachments in rsmf.zip.
"""

from __future__ import annotations

import base64
import hashlib
import quopri
import io
import json
import re
import secrets
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional



# Unicode Specials block (U+FFF0-U+FFFF): includes U+FFFC (Object Replacement), U+FFFD (Replacement), etc.
# iMessage embeds these as placeholders for non-image attachments (MOV, MP4, etc.)
_OBJ_PATTERN = re.compile(r"[\ufff0-\uffff]")

# Extensions that Relativity can render inline; others (MOV, MP4, etc.) show "OBJ" placeholder
_IMAGE_EXTENSIONS = frozenset((".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".heif"))


def _is_image_attachment(att: dict) -> bool:
    """True if attachment appears to be an image (Relativity can render without OBJ placeholder)."""
    name = (att.get("transfer_name") or att.get("filename") or att.get("local_path") or "").lower()
    mime = (att.get("mime_type") or "").lower()
    if any(name.endswith(ext) for ext in _IMAGE_EXTENSIONS):
        return True
    if "image/" in mime:
        return True
    return False


def _strip_obj_char(text: str) -> str:
    """Remove Unicode Specials (U+FFF0-U+FFFF) from text. iMessage embeds U+FFFC for attachments."""
    if not text:
        return text
    return _OBJ_PATTERN.sub("", text).strip()


def _sanitize_filename(s: str) -> str:
    """Replace unsafe chars for use as filename."""
    return re.sub(r'[<>:"/\\|?*\[\]]', "_", s).strip() or "conversation"


def _timestamp_to_iso8601(ts: Any) -> str:
    """Convert Unix timestamp or Apple timestamp to ISO8601 string (manifest format, no fractional seconds)."""
    if ts is None:
        return ""
    try:
        t = float(ts)
        if t > 1e15:
            t = (t / 1_000_000_000.0) + 978307200
        dt = datetime.utcfromtimestamp(t)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return ""


def _timestamp_for_headers(ts: Any) -> str:
    """ISO8601 with fractional seconds for RFC 5322 headers and extracted text body (reference format)."""
    if ts is None:
        return ""
    try:
        t = float(ts)
        if t > 1e15:
            t = (t / 1_000_000_000.0) + 978307200
        dt = datetime.utcfromtimestamp(t)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
    except (TypeError, ValueError):
        return ""


def _get_field(msg: dict, field: str) -> Any:
    """Get value from message by field name (supports nested like display_name)."""
    return msg.get(field)


def _default_field_mapping() -> Dict[str, str]:
    """Default RSMF field -> source field mapping."""
    return {
        "X-RSMF-Custodian": "custodian",
        "X-RSMF-Application": "iMessage/SMS",
        "X-RSMF-Participants": "to_display",
        "X-RSMF-BeginDate": "auto",
        "X-RSMF-EndDate": "auto",
        "X-RSMF-EventCount": "auto",
        "X-RSMF-EventCollectionID": "conversation_id",
        "participant": "display_name",
        "body": "text",
        "timestamp": "date_timestamp",
        "direction": "is_from_me",
    }


def _build_to_header(messages: List[dict]) -> str:
    """Build To: header as 'Name <identifier>' per participant, RFC 5322 folded."""
    seen: set = set()
    entries: List[str] = []
    for m in messages:
        name = m.get("display_name") or m.get("sender_id") or "?"
        ident = m.get("sender_id") or m.get("chat_identifier") or ""
        key = (name, ident)
        if key not in seen:
            seen.add(key)
            if ident:
                entries.append(f'"{name} <{ident}>"')
            else:
                entries.append(f'"{name}"')
    # Add chat participant (other side in 1:1) if not already in senders
    if messages:
        m0 = messages[0]
        chat_name = m0.get("chat_display_name") or m0.get("to_display") or ""
        chat_id = m0.get("chat_identifier") or ""
        if (chat_name or chat_id) and (chat_name or chat_id, chat_id) not in seen:
            seen.add((chat_name or chat_id, chat_id))
            name = chat_name or chat_id or "?"
            if chat_id:
                entries.append(f'"{name} <{chat_id}>"')
            else:
                entries.append(f'"{name}"')
    return ",\r\n ".join(entries) if entries else "Conversation"


def _build_participants(messages: List[dict], custodian: str = "") -> tuple[List[dict], Dict[str, str]]:
    """Build unique participants with hash-based IDs (Relativity-compatible). Returns (participants, display_to_id)."""
    seen: set = set()
    participants: List[dict] = []
    display_to_id: Dict[str, str] = {}

    def _add_participant(name: str, ident: str) -> None:
        display = f"{name} <{ident}>" if ident else name
        if not display or display in seen:
            return
        seen.add(display)
        pid_hash = hashlib.md5(display.encode("utf-8")).hexdigest()
        p: Dict[str, Any] = {"id": pid_hash, "display": display}
        if ident and "@" in ident:
            p["email"] = ident
        participants.append(p)
        display_to_id[display] = pid_hash

    for m in messages:
        name = m.get("display_name") or m.get("sender_id") or "?"
        ident = m.get("sender_id") or m.get("chat_identifier") or ""
        _add_participant(name, ident)

    # Add chat counterpart (other party in 1:1) if not already in senders
    if messages:
        m0 = messages[0]
        chat_name = m0.get("chat_display_name") or m0.get("to_display") or ""
        chat_id = m0.get("chat_identifier") or ""
        if chat_name or chat_id:
            _add_participant(chat_name or chat_id or "?", chat_id)

    # Add custodian if not already a participant (device owner / other party)
    if custodian and custodian.strip():
        cust_name = custodian.strip()
        # Don't add if custodian name already appears in an existing participant display
        already_covered = any(
            cust_name.lower() in p["display"].lower()
            for p in participants
        )
        if not already_covered:
            _add_participant(cust_name, "")

    return participants, display_to_id


def _build_events(
    messages: List[dict],
    attachment_base: Optional[Path],
    zip_attachments: Dict[str, str],
    conversation_uuid: str,
    display_to_id: Dict[str, str],
    rsmf_version: str = "1.0.0",
    include_control_number: bool = True,
    include_is_deleted: bool = True,
    include_attachments: bool = True,
    attachment_resolver: Optional[Callable[[dict], Optional[tuple]]] = None,
    zip_attachments_inline: Optional[Dict[str, bytes]] = None,
) -> List[dict]:
    """Build events array with mapped fields; collect attachments for zip.

    Attachments are sourced in priority order:
      1. From the extracted cache at `attachment_base / local_path` when the file exists.
      2. From `attachment_resolver(att)` which returns (bytes, suggested_name) or None.
         Used as a fallback so RSMF exports still embed attachments when the
         user hasn't run "Extract Attachments...".
    """
    events: List[dict] = []
    seen_names: set = set()
    include_direction = rsmf_version == "2.0.0"

    def _unique_zip_name(base: str) -> str:
        if base not in seen_names:
            seen_names.add(base)
            return base
        i = 0
        while f"{base}_{i}" in seen_names:
            i += 1
        name = f"{base}_{i}"
        seen_names.add(name)
        return name

    for idx, m in enumerate(messages):
        name = m.get("display_name") or m.get("sender_id") or "?"
        ident = m.get("sender_id") or m.get("chat_identifier") or ""
        display = f"{name} <{ident}>" if ident else name
        participant = display_to_id.get(display, display)
        body = _strip_obj_char(m.get("text") or "")
        ts = m.get("date_timestamp") or m.get("date")
        event_id = m.get("control_number") or m.get("message_id") or str(idx)
        event: Dict[str, Any] = {
            "id": event_id,
            "type": "message",
            "participant": participant,
            "body": body,
            "timestamp": _timestamp_to_iso8601(ts),
            "importance": "normal",
            "conversation": conversation_uuid,
            "deleted": bool(m.get("is_deleted")),
        }
        if include_direction:
            event["direction"] = "outgoing" if m.get("is_from_me") else "incoming"
        if rsmf_version == "2.0.0":
            custom: List[Dict[str, str]] = []
            if include_control_number and m.get("control_number"):
                custom.append({"name": "control_number", "value": m["control_number"]})
            if m.get("message_id"):
                custom.append({"name": "message_id", "value": m["message_id"]})
            if include_is_deleted and m.get("is_deleted"):
                custom.append({"name": "is_deleted", "value": "Yes"})
            if custom:
                event["custom"] = custom
        att_objs: List[Dict[str, Any]] = []
        if include_attachments:
            for a in m.get("attachments") or []:
                if not _is_image_attachment(a):
                    # Skip non-image (MOV, MP4, etc.) - Relativity shows "OBJ" placeholder for these
                    continue
                lp = a.get("local_path") or ""
                cached_full: Optional[Path] = None
                if lp and attachment_base:
                    candidate = attachment_base / lp
                    if candidate.exists():
                        cached_full = candidate

                if cached_full is not None:
                    raw_name = (
                        a.get("transfer_name")
                        or a.get("filename")
                        or lp.split("/")[-1]
                        or "attachment"
                    ).strip()
                    zip_name = _unique_zip_name(raw_name)
                    zip_attachments[zip_name] = str(cached_full)
                    try:
                        size = cached_full.stat().st_size
                    except OSError:
                        size = 0
                    att_objs.append({"id": zip_name, "display": zip_name, "size": size})
                    continue

                # Fallback: pull bytes directly from the raw backup when the cache
                # is empty or stale. attachment_resolver returns (bytes, suggested_name)
                # or None when the lookup fails for any reason.
                if attachment_resolver is None or zip_attachments_inline is None:
                    continue
                try:
                    resolved = attachment_resolver(a)
                except Exception:
                    resolved = None
                if not resolved:
                    continue
                data, suggested_name = resolved[0], resolved[1]
                raw_name = (
                    a.get("transfer_name")
                    or a.get("filename")
                    or suggested_name
                    or "attachment"
                ).strip()
                zip_name = _unique_zip_name(raw_name)
                zip_attachments_inline[zip_name] = data
                att_objs.append({"id": zip_name, "display": zip_name, "size": len(data)})
        if att_objs:
            event["attachments"] = att_objs
        events.append(event)
    return events


def _build_rsmf_manifest(
    conversation_id: str,
    messages: List[dict],
    attachment_base: Optional[Path],
    zip_attachments: Dict[str, str],
    rsmf_version: str = "1.0.0",
    include_control_number: bool = True,
    include_is_deleted: bool = True,
    include_attachments: bool = True,
    custodian: str = "",
    attachment_resolver: Optional[Callable[[dict], Optional[tuple]]] = None,
    zip_attachments_inline: Optional[Dict[str, bytes]] = None,
) -> dict:
    """Build rsmf_manifest.json structure."""
    participants, display_to_id = _build_participants(messages, custodian)
    participant_ids = [p["id"] for p in participants]
    conversation_uuid = str(uuid.uuid4())
    events = _build_events(
        messages, attachment_base, zip_attachments, conversation_uuid, display_to_id,
        rsmf_version=rsmf_version,
        include_control_number=include_control_number,
        include_is_deleted=include_is_deleted,
        include_attachments=include_attachments,
        attachment_resolver=attachment_resolver,
        zip_attachments_inline=zip_attachments_inline,
    )
    to_display = conversation_id
    is_group = len(participants) > 2
    manifest: Dict[str, Any] = {
        "version": rsmf_version,
        "participants": participants,
        "conversations": [
            {
                "id": conversation_uuid,
                "display": to_display,
                "platform": "SMS",
                "type": "channel" if is_group else "direct",
                "participants": participant_ids,
            }
        ],
        "events": events,
    }
    if rsmf_version == "2.0.0":
        manifest["eventcollectionid"] = conversation_id
    return manifest


def _build_extracted_text(messages: List[dict]) -> str:
    """Build text body: 3 lines per message (Name <identifier>, timestamp, body) with single newlines within each message, blank line between messages."""
    blocks: List[str] = []
    for m in messages:
        name = m.get("display_name") or m.get("sender_id") or "?"
        ident = m.get("sender_id") or m.get("chat_identifier") or ""
        ts = _timestamp_for_headers(m.get("date_timestamp") or m.get("date"))
        body = _strip_obj_char(m.get("text") or "")
        if ident:
            header = f"{name} <{ident}>"
        else:
            header = name
        blocks.append(f"{header}\n{ts}\n{body}")
    return "\n\n".join(blocks)


def _build_rsmf_bytes(
    conversation_id: str,
    messages: List[dict],
    attachment_base: Optional[Path],
    custodian: str = "",
    rsmf_version: str = "1.0.0",
    include_control_number: bool = True,
    include_is_deleted: bool = True,
    include_attachments: bool = True,
    attachment_resolver: Optional[Callable[[dict], Optional[tuple]]] = None,
) -> Optional[bytes]:
    """
    Build the RFC 5322 body of one conversation's .rsmf file as raw bytes.
    Shared by both the per-file and the zip-bundle export paths.
    """
    if not messages:
        return None
    zip_attachments: Dict[str, str] = {}
    zip_attachments_inline: Dict[str, bytes] = {}
    manifest = _build_rsmf_manifest(
        conversation_id, messages, attachment_base, zip_attachments, rsmf_version,
        include_control_number=include_control_number,
        include_is_deleted=include_is_deleted,
        include_attachments=include_attachments,
        custodian=custodian,
        attachment_resolver=attachment_resolver,
        zip_attachments_inline=zip_attachments_inline,
    )
    extracted_text = _build_extracted_text(messages)

    # Build the inner rsmf.zip (manifest + attachments) in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("rsmf_manifest.json", json.dumps(manifest, indent=2))
        for zip_name, filepath in zip_attachments.items():
            try:
                with open(filepath, "rb") as f:
                    data = f.read()
                zf.writestr(zip_name, data)
            except Exception:
                pass
        for zip_name, data in zip_attachments_inline.items():
            try:
                zf.writestr(zip_name, data)
            except Exception:
                pass
    zip_bytes = zip_buffer.getvalue()
    zip_b64 = base64.b64encode(zip_bytes).decode("ascii")

    timestamps = [m.get("date_timestamp") or m.get("date") for m in messages if m.get("date_timestamp") or m.get("date")]
    begin_ts = min(timestamps) if timestamps else None
    end_ts = max(timestamps) if timestamps else None
    to_display = messages[0].get("to_display") or messages[0].get("chat_display_name") or "Conversation"
    to_header = _build_to_header(messages)
    boundary = "=-" + secrets.token_urlsafe(16) + "=="

    lines: List[str] = [
        "X-RSMF-Generator: GURU Mobile Discovery",
        f"X-RSMF-Version: {rsmf_version}",
        f"X-RSMF-EventCount: {len(messages)}",
        f"X-RSMF-BeginDate: {_timestamp_for_headers(begin_ts)}",
        f"X-RSMF-EndDate: {_timestamp_for_headers(end_ts)}",
    ]
    if rsmf_version == "2.0.0":
        lines.extend([
            f"X-RSMF-Custodian: {custodian or 'Unknown'}",
            "X-RSMF-Application: iMessage/SMS",
            f"X-RSMF-Participants: {to_display}",
            f"X-RSMF-EventCollectionID: {conversation_id}",
        ])
    lines.extend([
        f"To: {to_header}",
        "MIME-Version: 1.0",
        f'Content-Type: multipart/mixed; boundary="{boundary}"',
        "",
        f"--{boundary}",
        "Content-Type: text/plain; charset=utf-8",
        "Content-Transfer-Encoding: quoted-printable",
        "",
        quopri.encodestring(extracted_text.encode("utf-8")).decode("ascii").rstrip("\n").replace("\n", "\r\n"),
        "",
        "",
        f"--{boundary}",
        "Content-Type: application/octet-stream; name=rsmf.zip",
        "Content-Disposition: attachment; filename=rsmf.zip",
        "Content-Transfer-Encoding: base64",
        "",
    ])
    body = "\r\n".join(lines)
    for i in range(0, len(zip_b64), 76):
        body += "\r\n" + zip_b64[i : i + 76]
    body += "\r\n\r\n" + f"--{boundary}--\r\n"
    return body.encode("utf-8")


def export_conversation_to_rsmf(
    conversation_id: str,
    messages: List[dict],
    attachment_base: Optional[Path],
    output_path: Path,
    custodian: str = "",
    rsmf_version: str = "1.0.0",
    include_control_number: bool = True,
    include_is_deleted: bool = True,
    include_attachments: bool = True,
    progress_cb: Optional[Callable[[str], None]] = None,
    attachment_resolver: Optional[Callable[[dict], Optional[tuple]]] = None,
) -> Optional[Path]:
    """
    Export one conversation (messages with same conversation_id) to a single .rsmf file.
    Returns the output path on success, None on failure.
    """
    body = _build_rsmf_bytes(
        conversation_id,
        messages,
        attachment_base,
        custodian=custodian,
        rsmf_version=rsmf_version,
        include_control_number=include_control_number,
        include_is_deleted=include_is_deleted,
        include_attachments=include_attachments,
        attachment_resolver=attachment_resolver,
    )
    if body is None:
        return None
    out_path = output_path / f"{_sanitize_filename(conversation_id)}.rsmf"
    try:
        out_path.write_bytes(body)
        if progress_cb:
            progress_cb(str(out_path))
        return out_path
    except Exception:
        return None


def _group_by_conversation_id(messages: List[dict]) -> Dict[str, List[dict]]:
    """Group messages by conversation_id, falling back to a deterministic id when missing."""
    cid_to_msgs: Dict[str, List[dict]] = {}
    for m in messages:
        cid = m.get("conversation_id") or ""
        if not cid:
            # Fallback when conversation_id missing (e.g. export without a saved search):
            # build a deterministic id from chat + date so filenames stay unique.
            chat_id = m.get("chat_id", "")
            ts = m.get("date_timestamp") or m.get("date") or 0
            try:
                t = float(ts)
                if t > 1e15:
                    t = (t / 1_000_000_000.0) + 978307200
                dt = datetime.utcfromtimestamp(t)
                ymd = dt.strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                ymd = "nodate"
            cid = f"0000_{hashlib.md5(f'{chat_id}_{ymd}'.encode()).hexdigest()[:16]}"
        cid_to_msgs.setdefault(cid, []).append(m)
    return cid_to_msgs


def _sanitize_zip_member_name(name: str, used: set) -> str:
    """Make a .rsmf member name unique inside the output ZIP."""
    base = _sanitize_filename(name)
    candidate = f"{base}.rsmf"
    if candidate not in used:
        used.add(candidate)
        return candidate
    i = 1
    while f"{base}_{i}.rsmf" in used:
        i += 1
    candidate = f"{base}_{i}.rsmf"
    used.add(candidate)
    return candidate


def export_search_results_to_rsmf(
    messages: List[dict],
    attachment_base: Optional[Path],
    output_dir: Path,
    custodian: str = "",
    rsmf_version: str = "1.0.0",
    include_control_number: bool = True,
    include_is_deleted: bool = True,
    include_attachments: bool = True,
    progress_cb: Optional[Callable[[float, str], None]] = None,
    zip_name: Optional[str] = None,
    attachment_resolver: Optional[Callable[[dict], Optional[tuple]]] = None,
) -> List[Path]:
    """
    Group messages by conversation_id and bundle all .rsmf files into a single
    ZIP archive inside output_dir. Returns [zip_path] on success, [] on failure.

    The ZIP filename is `zip_name` (sanitized, .zip extension auto-appended) if
    provided, otherwise `rsmf_export_<YYYYMMDD_HHMMSS>.zip`.
    """
    if progress_cb:
        progress_cb(0, f"Grouping {len(messages)} messages by conversation...")
    cid_to_msgs = _group_by_conversation_id(messages)
    total = len(cid_to_msgs)
    if not total:
        return []

    if zip_name:
        zip_stem = _sanitize_filename(zip_name)
        if zip_stem.lower().endswith(".zip"):
            zip_stem = zip_stem[:-4]
    else:
        zip_stem = f"rsmf_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    zip_path = output_dir / f"{zip_stem}.zip"

    if progress_cb:
        progress_cb(0, f"Bundling {total} conversation(s) into {zip_path.name}...")

    used_names: set = set()
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            done = 0
            for cid, msgs in cid_to_msgs.items():
                msgs_sorted = sorted(
                    msgs,
                    key=lambda x: (
                        float(x.get("date_timestamp") or x.get("date") or 0),
                        x.get("rowid") or 0,
                    ),
                )
                att_count = sum(len(m.get("attachments") or []) for m in msgs_sorted)
                short_id = (cid[:40] + "...") if len(cid) > 40 else cid
                current = done + 1
                if progress_cb:
                    progress_cb(
                        100 * done / total,
                        f"Building RSMF {current}/{total}: {short_id} "
                        f"({len(msgs_sorted)} messages, {att_count} attachments)",
                    )
                body = _build_rsmf_bytes(
                    cid,
                    msgs_sorted,
                    attachment_base,
                    custodian=custodian,
                    rsmf_version=rsmf_version,
                    include_control_number=include_control_number,
                    include_is_deleted=include_is_deleted,
                    include_attachments=include_attachments,
                    attachment_resolver=attachment_resolver,
                )
                if body is None:
                    done += 1
                    continue
                member_name = _sanitize_zip_member_name(cid, used_names)
                zf.writestr(member_name, body)
                done += 1
                if progress_cb:
                    progress_cb(
                        100 * done / total,
                        f"Added {member_name} ({done}/{total})",
                    )
    except OSError:
        return []

    if progress_cb:
        progress_cb(100, f"Wrote {zip_path.name} ({total} conversation(s))")
    return [zip_path]
