"""
Import worker: parse backup, apply custodian/timezone, optionally extract attachments to cache, save MessagePack.
Runs in a background thread and emits progress.
"""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import zoneinfo

# Self-contained parser (app/backup_parser)
from app.backup_parser import BackupParser, get_messages_data, get_contacts_data, resolve_display_name, is_placeholder_chat_identifier
from app.logging_config import get_logger

from .cache import (
    save_backup_cache,
    backup_id_from_path,
    get_backup_cache_root,
    load_backup_meta,
    load_backup_messages,
    ATTACHMENTS_DIR,
)


def _detect_timezone_from_backup(backup_path: Path) -> Optional[str]:
    """Try to get timezone from backup Info.plist."""
    try:
        import plistlib
        plist_path = backup_path / "Info.plist"
        if not plist_path.exists():
            return None
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)
        # Common keys
        tz = plist.get("Timezone") or plist.get("TimeZone") or plist.get("timezone")
        if isinstance(tz, str) and tz:
            return tz
        return None
    except Exception:
        return None


def _apply_custodian(messages: List[dict], custodian: str) -> None:
    """Replace owner (is_from_me) display name with custodian."""
    for m in messages:
        if m.get("is_from_me"):
            m["display_name"] = custodian.strip() or "Me"
        else:
            m["display_name"] = m.get("sender_display_name") or m.get("sender_id") or "?"


def _apply_timezone(messages: List[dict], tz_name: str) -> None:
    """Convert date_formatted/date_timestamp to the given timezone. Uses UTC if tz_name is empty or invalid."""
    if not (tz_name and str(tz_name).strip()):
        tz_name = "UTC"
    try:
        tz = zoneinfo.ZoneInfo(tz_name.strip())
    except Exception:
        tz = zoneinfo.ZoneInfo("UTC")
    APPLE_EPOCH = 978307200

    def _format_ts(ts: float) -> str:
        try:
            ts = float(ts)
            if ts > 1e15:
                ts = (ts / 1_000_000_000.0) + APPLE_EPOCH
            dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
            dt_local = dt_utc.astimezone(tz)
            h12 = dt_local.hour % 12 or 12
            return f"{dt_local.month}/{dt_local.day:02d}/{dt_local.year} {h12}:{dt_local.minute:02d}:{dt_local.second:02d} {dt_local.strftime('%p')}"
        except Exception:
            return ""

    for m in messages:
        ts = m.get("date_timestamp")
        if ts is None:
            continue
        try:
            ts = float(ts)
            # If wrongly stored as Apple timestamp (nanoseconds e.g. 6e17), convert to Unix
            if ts > 1e15:
                ts = (ts / 1_000_000_000.0) + APPLE_EPOCH
                m["date_timestamp"] = ts
            m["date_formatted"] = _format_ts(ts)
            m["date_timestamp"] = ts  # keep for sorting
        except Exception:
            pass


def _extension_from_mime_or_filename(mime: str, filename: str) -> str:
    """Derive file extension from mime_type or filename. Returns e.g. '.pdf'."""
    fn_lower = (filename or "").lower()
    if "." in fn_lower:
        ext = "." + fn_lower.rsplit(".", 1)[-1]
        if len(ext) <= 5 and ext != ".exe":
            return ext
    mime_map = {
        "video/mp4": ".mp4", "video/quicktime": ".mov", "video/x-m4v": ".m4v",
        "application/pdf": ".pdf", "application/msword": ".doc",
        "application/vnd.ms-excel": ".xls", "text/plain": ".txt",
        "audio/mpeg": ".mp3", "audio/m4a": ".m4a", "audio/x-m4a": ".m4a",
    }
    mime_lower = (mime or "").lower()
    for k, v in mime_map.items():
        if k in mime_lower or mime_lower.startswith(k.split("/")[0] + "/"):
            return v
    if "video/" in mime_lower:
        return ".mp4"
    if "audio/" in mime_lower:
        return ".m4a"
    return ".bin"


def _lookup_attachment_bytes(att: dict, parser: BackupParser) -> Optional[bytes]:
    """Run the 4-step Manifest/path/guid search for an attachment's raw backup bytes.

    Returns the raw bytes if found, or None. Does NOT do any conversion or
    write anything to disk. Shared by `_enrich_attachment_for_cache` and the
    on-the-fly RSMF resolver.
    """
    domain = att.get("domain", "HomeDomain")
    rel = (att.get("relative_path") or "").strip()
    data: Optional[bytes] = None
    guid = (att.get("guid") or "").strip()
    fname = (att.get("transfer_name") or att.get("filename") or "").strip() or "image"
    log = get_logger()

    # 1) Try by path if we have relative_path
    if rel:
        domains_to_try = [domain]
        if "DCIM" in rel or "PhotoData" in rel:
            domains_to_try = list(dict.fromkeys([domain, "CameraRollDomain", "MediaDomain", "HomeDomain"]))
        for dom in domains_to_try:
            entry = parser.get_file_by_path(dom, rel)
            if entry:
                data = parser.read_file_bytes(entry)
                if data:
                    break
        if not data:
            log.debug("resolve_attachment_bytes: get_file_by_path rel=%s -> no data", rel[:60])

    # 2) If we have guid that looks like backup file ID (40-char hex), try direct read by ID
    if not data and guid and len(guid) >= 32 and all(c in "0123456789abcdefABCDEF" for c in guid):
        data = parser.read_file_bytes_by_id(guid)
        if data:
            log.debug("resolve_attachment_bytes: got data by file_id guid=%s len=%d", guid[:12], len(data))

    # 3) Search Manifest for any file whose path ends with this filename (e.g. image000001.jpg)
    if not data and fname:
        fname_base = fname.split("/")[-1].split("\\")[-1]
        if fname_base:
            for dom, path in parser.get_paths_ending_with(fname_base):
                e = parser.get_file_by_path(dom, path)
                if e:
                    data = parser.read_file_bytes(e)
                    if data:
                        log.debug("resolve_attachment_bytes: got data by path ending with %s", fname_base)
                        break
                if data:
                    break

    # 4) Candidate (domain, path) pairs using guid as folder name
    if not data and not rel:
        candidates = [
            ("HomeDomain", f"Library/SMS/Attachments/{guid}/{fname}"),
            ("MediaDomain", f"Library/SMS/Attachments/{guid}/{fname}"),
            ("HomeDomain", f"var/mobile/Library/SMS/Attachments/{guid}/{fname}"),
            ("HomeDomain", f"Library/Messages/Attachments/{guid}/{fname}"),
            ("MediaDomain", f"Library/Messages/Attachments/{guid}/{fname}"),
        ]
        for dom, path in candidates:
            path = path.replace("\\", "/")
            e = parser.get_file_by_path(dom, path)
            if e:
                data = parser.read_file_bytes(e)
                if data:
                    break
        if not data:
            flat_candidates = [
                ("HomeDomain", f"Library/SMS/Attachments/{fname}"),
                ("MediaDomain", f"Library/SMS/Attachments/{fname}"),
                ("HomeDomain", f"Library/Messages/Attachments/{fname}"),
                ("MediaDomain", f"Library/Messages/Attachments/{fname}"),
                ("HomeDomain", f"var/mobile/Library/SMS/Attachments/{fname}"),
            ]
            for dom, path in flat_candidates:
                path = path.replace("\\", "/")
                e = parser.get_file_by_path(dom, path)
                if e:
                    data = parser.read_file_bytes(e)
                    if data:
                        break
        if not data:
            fname_base = fname.split("/")[-1].split("\\")[-1]
            if fname_base and ("." in fname_base or "image" in fname_base.lower()):
                search_sub = fname_base.rsplit(".", 1)[0] if "." in fname_base else fname_base
                if search_sub:
                    for dom, path in parser.get_paths_matching(search_sub):
                        if "attachment" in path.lower() and path.lower().endswith(
                            (".jpg", ".jpeg", ".png", ".gif", ".heic", ".bmp", ".webp")
                        ):
                            e = parser.get_file_by_path(dom, path)
                            if e:
                                data = parser.read_file_bytes(e)
                                if data:
                                    break
                        if data:
                            break

    if not data:
        log.info(
            "resolve_attachment_bytes: failed guid=%s fname=%s reason=no_data_from_candidates",
            guid,
            fname,
        )
        return None
    return data


def resolve_attachment_bytes(
    att: dict,
    parser: BackupParser,
) -> Optional[tuple[bytes, str, bool]]:
    """Pull an attachment from the raw backup and convert it for RSMF embedding.

    Returns `(image_bytes, suggested_filename, is_image)` or None.
    - HEIC -> JPEG, GIF -> first-frame PNG, other images stay as-is (.png/.jpg).
    - Non-image attachments are returned as raw bytes with a best-effort extension.

    Does not write anything to disk. Used by the RSMF export to embed
    attachments straight from the backup when the cache is empty.
    """
    log = get_logger()
    data = _lookup_attachment_bytes(att, parser)
    if not data:
        return None

    mime = (att.get("mime_type") or "").lower()
    fn_lower = (att.get("filename") or att.get("transfer_name") or "").lower()
    is_gif = "gif" in mime or fn_lower.endswith(".gif")
    is_image = "image" in mime or is_gif or fn_lower.endswith(
        (".png", ".jpg", ".jpeg", ".heic", ".bmp", ".webp")
    )
    key = hashlib.sha256(data).hexdigest()[:12]

    if is_gif:
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            if getattr(img, "n_frames", 1) > 1:
                img.seek(0)
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "PNG")
            return (buf.getvalue(), f"{key}.png", True)
        except Exception as e:
            log.info("resolve_attachment_bytes: GIF convert failed: %s", e)
            return None

    is_heic = "heic" in mime or ".heic" in fn_lower
    if is_heic:
        try:
            from PIL import Image
            from pillow_heif import register_heif_opener
            register_heif_opener()
            img = Image.open(io.BytesIO(data))
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=92)
            return (buf.getvalue(), f"{key}.jpg", True)
        except Exception as e:
            log.info("resolve_attachment_bytes: HEIC convert failed: %s", e)
            return None

    if is_image:
        ext = ".png"
        if "jpeg" in mime or "jpg" in mime:
            ext = ".jpg"
        elif fn_lower.endswith((".jpg", ".jpeg")):
            ext = ".jpg"
        return (data, f"{key}{ext}", True)

    ext = _extension_from_mime_or_filename(
        mime, att.get("filename") or att.get("transfer_name") or ""
    )
    return (data, f"{key}{ext}", False)


def _enrich_attachment_for_cache(
    att: dict,
    parser: BackupParser,
    attachments_dir: Path,
    progress_cb: Optional[Callable[[float, str], None]],
) -> Optional[tuple[str, bool]]:
    """
    Extract attachment to attachments_dir and return (relative_path, is_image) or None.
    Thin wrapper over `resolve_attachment_bytes` that persists the bytes to the
    on-disk cache.
    """
    result = resolve_attachment_bytes(att, parser)
    if not result:
        return None
    data, name, is_image = result
    out_path = attachments_dir / name
    try:
        out_path.write_bytes(data)
    except OSError as e:
        get_logger().info("_enrich_attachment_for_cache: write failed for %s: %s", name, e)
        return None
    return (f"{ATTACHMENTS_DIR}/{name}", is_image)


def _attachment_meta_only(att: dict) -> dict:
    """Strip cached file paths so thread view shows filename only until user extracts."""
    na = dict(att)
    na["local_path"] = ""
    na["has_local_file"] = False
    na["is_image"] = False
    return na


def extract_attachments_to_cache(
    backup_path: str,
    passphrase: Optional[str],
    app_data_root: Path,
    case_id: str,
    backup_id: str,
    progress_callback: Callable[[float, str], None],
) -> None:
    """
    Re-open the original backup, extract attachments into the existing cache folder,
    update messages.msgpack and meta (attachments_extracted).
    """
    log = get_logger()
    meta = load_backup_meta(app_data_root, case_id, backup_id)
    data = load_backup_messages(app_data_root, case_id, backup_id)
    if not meta or not data:
        raise ValueError("Backup cache not found.")
    bp = str(Path(backup_path).resolve())
    parser_temp = app_data_root / "tmp" / "parser_temp"
    parser_temp.mkdir(parents=True, exist_ok=True)
    parser = BackupParser(bp, passphrase=passphrase, temp_dir=parser_temp)
    cache_root = get_backup_cache_root(app_data_root, case_id, backup_id)
    attachments_dir = cache_root / ATTACHMENTS_DIR
    attachments_dir.mkdir(parents=True, exist_ok=True)
    messages = data.get("messages") or []
    total = sum(len(m.get("attachments") or []) for m in messages)
    done = 0
    try:
        if total == 0:
            progress_callback(100, "No attachments to extract.")
        for m in messages:
            atts = m.get("attachments") or []
            new_atts = []
            for a in atts:
                result = _enrich_attachment_for_cache(dict(a), parser, attachments_dir, None)
                na = dict(a)
                if result:
                    rel, is_img = result
                    na["local_path"] = rel
                    na["has_local_file"] = True
                    na["is_image"] = is_img
                new_atts.append(na)
                done += 1
                if total and done % 50 == 0:
                    progress_callback(
                        min(99.0, 100.0 * done / total),
                        f"Extracting attachments... ({done}/{total})",
                    )
            m["attachments"] = new_atts
        new_meta = dict(meta)
        new_meta["attachments_extracted"] = True
        save_backup_cache(app_data_root, case_id, backup_id, new_meta, data)
        progress_callback(100, "Attachments saved.")
        log.info("extract_attachments_to_cache: done %d attachments", total)
    finally:
        parser.close()


def run_import(
    backup_path: str,
    case_id: str,
    custodian: str,
    timezone_name: str,
    passphrase: Optional[str],
    app_data_root: Path,
    progress_callback: Callable[[float, str], None],
    control_prefix: str = "",
    control_padding: int = 6,
    backup_label: str = "",
    populate_table_tab: bool = True,
    extract_attachments: bool = False,
) -> Dict[str, Any]:
    """
    Run full import: parse backup, get messages, apply custodian/timezone,
    optionally extract attachments to cache, save MessagePack.
    Returns dict with backup_id, meta, and data for the UI; raises on error.
    """
    backup_path = str(Path(backup_path).resolve())
    backup_id = backup_id_from_path(backup_path)
    encrypted = bool(passphrase)
    log = get_logger()
    log.info("run_import: backup_path=%s case_id=%s encrypted=%s", backup_path, case_id, encrypted)

    cache_root = get_backup_cache_root(app_data_root, case_id, backup_id)
    cache_root.mkdir(parents=True, exist_ok=True)
    attachments_dir = cache_root / ATTACHMENTS_DIR
    attachments_dir.mkdir(exist_ok=True)

    progress_callback(0, "Opening backup...")
    # Use app data root for parser temp so all import writes go to the same drive as cache (e.g. D:)
    parser_temp = app_data_root / "tmp" / "parser_temp"
    parser_temp.mkdir(parents=True, exist_ok=True)
    parser = BackupParser(backup_path, passphrase=passphrase, temp_dir=parser_temp)
    try:
        progress_callback(2, "Reading backup info...")
        info = parser.get_backup_info()
        detected_tz = _detect_timezone_from_backup(Path(backup_path))

        def msg_progress(pct: float, label: str) -> None:
            progress_callback(2 + 0.25 * pct, label)

        progress_callback(5, "Parsing messages...")
        raw = get_messages_data(parser, progress_callback=msg_progress)
        if not raw:
            log.warning("run_import: get_messages_data returned empty")
            raise ValueError("No messages found in this backup.")

        messages = raw["messages"]
        chats = raw["chats"]
        handles = raw["handles"]
        if not messages:
            log.warning("run_import: get_messages_data returned 0 messages")

        progress_callback(30, "Applying custodian and timezone...")
        _apply_custodian(messages, custodian)
        _apply_timezone(messages, timezone_name)

        progress_callback(31, "Assigning control numbers...")
        prefix = (control_prefix or "").strip() or "MSG"
        pad = max(1, min(12, int(control_padding)))
        sorted_msgs = sorted(messages, key=lambda x: (float(x.get("date_timestamp") or x.get("date") or 0), x.get("rowid") or 0))
        for i, m in enumerate(sorted_msgs, start=1):
            m["control_number"] = f"{prefix}{i:0{pad}d}"

        progress_callback(32, "Resolving contact names...")
        contact_map = get_contacts_data(parser)
        if not contact_map:
            log.warning("run_import: no contact names resolved (AddressBook not found or empty)")
        for c in chats:
            ident = c.get("chat_identifier") or ""
            if is_placeholder_chat_identifier(ident) and c.get("participant_handle_ids"):
                if contact_map:
                    names = sorted(
                        set(
                            resolve_display_name(pid, contact_map) or pid
                            for pid in c["participant_handle_ids"]
                            if pid
                        )
                    )
                    c["display_name"] = ", ".join(names) if names else ident
                else:
                    c["display_name"] = ", ".join(pid for pid in c["participant_handle_ids"] if pid) or ident
            else:
                resolved = resolve_display_name(ident, contact_map) if contact_map else ident
                c["display_name"] = resolved or ident
        for m in messages:
            if not m.get("is_from_me"):
                resolved = resolve_display_name(m.get("sender_id") or "", contact_map)
                m["display_name"] = resolved or m.get("sender_id") or ""

        total_att = sum(len(m.get("attachments") or []) for m in messages)
        done_att = 0
        if extract_attachments:
            progress_callback(35, "Extracting attachments...")
            for m in messages:
                atts = m.get("attachments") or []
                new_atts = []
                for a in atts:
                    result = _enrich_attachment_for_cache(a, parser, attachments_dir, None)
                    na = dict(a)
                    if result:
                        rel, is_img = result
                        na["local_path"] = rel
                        na["has_local_file"] = True
                        na["is_image"] = is_img
                    new_atts.append(na)
                    done_att += 1
                    if total_att and done_att % 50 == 0:
                        progress_callback(
                            35 + 45 * done_att / total_att,
                            f"Extracting attachments... ({done_att}/{total_att})",
                        )
                m["attachments"] = new_atts
        else:
            progress_callback(50, "Skipping attachment extraction...")
            for m in messages:
                atts = m.get("attachments") or []
                m["attachments"] = [_attachment_meta_only(a) for a in atts]

        progress_callback(85, "Building message hash...")
        for m in messages:
            # Simple hash for display (guid or rowid + date + text)
            h = hashlib.sha256(
                f"{m.get('rowid')}{m.get('date')}{m.get('text') or ''}".encode()
            ).hexdigest()[:16]
            m["hash"] = h

        progress_callback(90, "Writing cache...")
        meta = {
            "backup_id": backup_id,
            "backup_path": backup_path,
            "custodian": custodian,
            "timezone": timezone_name,
            "device_name": getattr(info, "device_name", None),
            "backup_date": str(getattr(info, "backup_date", "") or ""),
            "detected_timezone": detected_tz,
            "backup_label": (backup_label or "").strip(),
            "populate_table_tab": bool(populate_table_tab),
            "attachments_extracted": bool(extract_attachments),
        }
        data = {"chats": chats, "handles": handles, "messages": messages, "contact_map": contact_map}
        save_backup_cache(app_data_root, case_id, backup_id, meta, data)
        progress_callback(100, "Import complete.")
        log.info("run_import: import complete: %d messages, %d chats", len(messages), len(chats))
        return {"backup_id": backup_id, "meta": meta, "data": data}
    except Exception as e:
        log.info("run_import: exception %s: %s", type(e).__name__, e)
        raise
    finally:
        parser.close()
