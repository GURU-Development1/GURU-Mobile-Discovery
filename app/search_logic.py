"""
Run search over messages: filter by criteria and optionally assign conversation_id by 24h chunks (midnight–midnight in backup timezone).
"""

from __future__ import annotations

import hashlib
import string
import zoneinfo
from datetime import datetime
from typing import Any, Dict, List, Optional

# Apple epoch for fallback
_APPLE_EPOCH_OFFSET = 978307200


def _random20_deterministic(seed: str) -> str:
    """Generate 20 lowercase alphanumeric chars from seed (reproducible)."""
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    return "".join(c if c in string.ascii_lowercase + string.digits else chr(ord("a") + (ord(c) % 26)) for c in h)


def _apple_date_to_unix(apple_date: Any) -> Optional[float]:
    if apple_date is None:
        return None
    try:
        val = float(apple_date)
        if val > 1e15:
            val = val / 1_000_000_000.0
        return val + _APPLE_EPOCH_OFFSET
    except (TypeError, ValueError):
        return None


def _message_unix_timestamp(m: dict) -> Optional[float]:
    ts = m.get("date_timestamp")
    if ts is not None:
        try:
            f = float(ts)
            if f <= 1e15:
                return f
        except (TypeError, ValueError):
            pass
    return _apple_date_to_unix(m.get("date"))


def _message_date_ymd(m: dict, timezone_name: str) -> Optional[str]:
    """Return message date as YYYY-MM-DD in the given timezone."""
    unix = _message_unix_timestamp(m)
    if unix is None:
        return None
    try:
        tz = zoneinfo.ZoneInfo(timezone_name.strip()) if (timezone_name and timezone_name.strip()) else zoneinfo.ZoneInfo("UTC")
        dt = datetime.fromtimestamp(unix, tz=tz)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def _match_text(value: str, filter_str: str) -> bool:
    if not filter_str:
        return True
    return (filter_str.lower() in (value or "").lower())


def _match_date_range(ymd: Optional[str], date_from: str, date_to: str) -> bool:
    if not date_from and not date_to:
        return True
    if not ymd:
        return False
    if date_from and ymd < date_from:
        return False
    if date_to and ymd > date_to:
        return False
    return True


def _parse_recipient_tokens(recipient_filter: str) -> List[str]:
    """Split comma-separated recipient field; strip; drop empties."""
    if not recipient_filter or not recipient_filter.strip():
        return []
    return [t.strip() for t in recipient_filter.split(",") if t.strip()]


def _recipient_haystack(m: dict) -> str:
    """
    Combined lowercase text for matching recipient tokens: conversation participants and identifiers.
    Includes sender display/handle so messages sent by a searched participant still match.
    """
    parts: List[str] = []
    for key in ("to_display", "chat_display_name", "chat_identifier"):
        v = m.get(key)
        if v:
            parts.append(str(v))
    for key in ("display_name", "sender_display_name", "sender_id"):
        v = m.get(key)
        if v:
            parts.append(str(v))
    pids = m.get("participant_handle_ids")
    if isinstance(pids, (list, tuple)):
        for p in pids:
            if p:
                parts.append(str(p))
    return " ".join(parts).lower()


def _token_matches_haystack(token: str, haystack: str) -> bool:
    """Substring match (case-insensitive) of token in haystack."""
    t = (token or "").strip().lower()
    if not t:
        return True
    return t in haystack


def _match_recipients_and(m: dict, tokens: List[str]) -> bool:
    """
    If tokens is empty, match all. Otherwise every token must appear somewhere in the conversation
    participant haystack (AND across tokens).
    """
    if not tokens:
        return True
    hay = _recipient_haystack(m)
    return all(_token_matches_haystack(tok, hay) for tok in tokens)


def run_search(
    messages: List[dict],
    to_filter: str = "",
    body_filter: str = "",
    date_from: str = "",
    date_to: str = "",
    has_attachments: str = "any",
    hash_filter: str = "",
    chunk_24h: bool = False,
    timezone_name: str = "",
    search_name: str = "Search results",
    search_sequence: int = 0,
    thread_ids: Optional[List[int]] = None,
) -> List[dict]:
    """
    Filter messages by criteria and assign conversation_id.
    - If chunk_24h: conversation_id = {sequence:04d}{random6} - YYYY-MM-DD (deterministic per search+date).
    - Else: one conversation_id for all: {sequence:04d}{random6} (no date suffix).
    search_sequence: 0 for ad-hoc, 1+ for saved searches.
    Returns a new list of shallow-copied message dicts with conversation_id set.
    """
    timezone_name = timezone_name or "UTC"
    to_filter = (to_filter or "").strip()
    body_filter = (body_filter or "").strip()
    date_from = (date_from or "").strip()
    date_to = (date_to or "").strip()
    has_attachments = (has_attachments or "any").strip().lower() or "any"
    hash_filter = (hash_filter or "").strip()

    recipient_tokens = _parse_recipient_tokens(to_filter)
    thread_id_set: Optional[set] = None
    if thread_ids:
        thread_id_set = {int(t) for t in thread_ids if t is not None}

    filtered: List[dict] = []
    for m in messages:
        if thread_id_set is not None:
            cid = m.get("chat_id")
            if cid is None or int(cid) not in thread_id_set:
                continue
        if not _match_recipients_and(m, recipient_tokens):
            continue

        text = m.get("text") or ""
        if not _match_text(text, body_filter):
            continue
        ymd = _message_date_ymd(m, timezone_name)
        if not _match_date_range(ymd, date_from, date_to):
            continue
        atts = m.get("attachments") or []
        has_att = len(atts) > 0
        if has_attachments == "yes" and not has_att:
            continue
        if has_attachments == "no" and has_att:
            continue
        h = m.get("hash") or ""
        if not _match_text(h, hash_filter):
            continue
        filtered.append(m)

    # Sort by date
    filtered = sorted(filtered, key=lambda x: (_message_unix_timestamp(x) or 0, x.get("rowid") or 0))

    if chunk_24h:
        # Group by calendar day (midnight–midnight in backup TZ); same day => same conversation_id
        # Format: NNNN{random20} - YYYY-MM-DD
        seq_prefix = f"{search_sequence:04d}"
        date_to_cid: Dict[str, str] = {}
        result = []
        for m in filtered:
            ymd = _message_date_ymd(m, timezone_name) or "unknown"
            if ymd not in date_to_cid:
                seed = f"{search_sequence}:{ymd}"
                r20 = _random20_deterministic(seed)
                date_to_cid[ymd] = f"{seq_prefix}{r20} - {ymd}"
            copy = dict(m)
            copy["conversation_id"] = date_to_cid[ymd]
            result.append(copy)
        return result
    else:
        seed = f"{search_sequence}:single"
        r20 = _random20_deterministic(seed)
        cid = f"{search_sequence:04d}{r20}"
        return [dict(m, conversation_id=cid) for m in filtered]


def expand_results_for_rsmf_export(
    search_results: List[dict],
    all_messages: List[dict],
    timezone_name: str = "",
    chunk_24h: bool = False,
) -> List[dict]:
    """
    Expand saved-search hits for RSMF export.

    - chunk_24h: include every message from matching chats on each hit date (24h chunks).
    - otherwise: include every message from each chat that appears in the results, using
      the search's shared conversation_id (matches thread export coverage).
    """
    if not search_results:
        return []
    if chunk_24h:
        return expand_results_to_full_threads(search_results, all_messages, timezone_name)

    conv_id = search_results[0].get("conversation_id")
    chat_ids = {m.get("chat_id") for m in search_results if m.get("chat_id") is not None}
    if not chat_ids or not conv_id:
        return list(search_results)

    seen_rowids: set = set()
    expanded: List[dict] = []
    for m in all_messages:
        if m.get("chat_id") not in chat_ids:
            continue
        rowid = m.get("rowid")
        if rowid is not None and rowid in seen_rowids:
            continue
        if rowid is not None:
            seen_rowids.add(rowid)
        copy = dict(m)
        copy["conversation_id"] = conv_id
        expanded.append(copy)
    expanded.sort(key=lambda x: (_message_unix_timestamp(x) or 0, x.get("rowid") or 0))
    return expanded


def expand_results_to_full_threads(
    search_results: List[dict],
    all_messages: List[dict],
    timezone_name: str = "",
) -> List[dict]:
    """
    Expand search results to include all messages from matching threads on matching dates.
    For each (chat_id, ymd) in search results, include every message from that chat on that date
    with the same conversation_id. Used for RSMF export so each 24-hour chunk contains the full thread.
    """
    timezone_name = timezone_name or "UTC"
    # Build (chat_id, ymd) -> conversation_id from search results
    pair_to_cid: Dict[tuple, str] = {}
    for m in search_results:
        cid_key = m.get("chat_id")
        ymd = _message_date_ymd(m, timezone_name)
        conv_id = m.get("conversation_id")
        if cid_key is not None and ymd and conv_id:
            pair_to_cid[(cid_key, ymd)] = conv_id
    if not pair_to_cid:
        return list(search_results)
    # Include all messages from all_messages whose (chat_id, ymd) matches
    seen_rowids: set = set()
    expanded: List[dict] = []
    for m in all_messages:
        cid_key = m.get("chat_id")
        ymd = _message_date_ymd(m, timezone_name)
        if cid_key is None or not ymd:
            continue
        key = (cid_key, ymd)
        if key not in pair_to_cid:
            continue
        rowid = m.get("rowid")
        if rowid is not None and rowid in seen_rowids:
            continue
        if rowid is not None:
            seen_rowids.add(rowid)
        copy = dict(m)
        copy["conversation_id"] = pair_to_cid[key]
        expanded.append(copy)
    expanded.sort(key=lambda x: (_message_unix_timestamp(x) or 0, x.get("rowid") or 0))
    return expanded
