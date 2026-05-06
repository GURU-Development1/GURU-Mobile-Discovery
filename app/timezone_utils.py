"""
Timezone abbreviation helpers for display (e.g. EST instead of America/New_York).
Uses strftime(%Z); on Windows long names are shortened to common abbreviations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import zoneinfo


def get_tz_abbrev_for_timestamp(unix_ts: float, timezone_name: str) -> str:
    """Return timezone abbreviation (e.g. EST, EDT) for the given Unix timestamp in the given zone."""
    if not timezone_name or not timezone_name.strip():
        try:
            dt = datetime.utcfromtimestamp(unix_ts)
            return dt.strftime("%Z") or "UTC"
        except Exception:
            return "UTC"
    try:
        tz = zoneinfo.ZoneInfo(timezone_name.strip())
        dt = datetime.fromtimestamp(unix_ts, tz=tz)
        abbrev = dt.strftime("%Z") or ""
        # On Windows %Z can return "Eastern Standard Time" instead of "EST"
        if len(abbrev) > 5:
            abbrev = _long_name_to_abbrev(abbrev)
        return abbrev or timezone_name.strip()
    except Exception:
        return timezone_name.strip()


def get_tz_abbrev_now(timezone_name: str) -> str:
    """Return current timezone abbreviation for the given IANA zone (for dropdowns)."""
    if not timezone_name or not timezone_name.strip():
        return "UTC"
    try:
        tz = zoneinfo.ZoneInfo(timezone_name.strip())
        dt = datetime.now(tz)
        abbrev = dt.strftime("%Z") or ""
        if len(abbrev) > 5:
            abbrev = _long_name_to_abbrev(abbrev)
        return abbrev or timezone_name.strip()
    except Exception:
        return timezone_name.strip()


def _long_name_to_abbrev(long_name: str) -> str:
    """Convert Windows-style long name (e.g. 'Eastern Standard Time') to abbreviation (EST)."""
    s = (long_name or "").strip()
    if not s or len(s) <= 5:
        return s
    # Take first letter of each word, upper case
    parts = s.split()
    if len(parts) >= 2:
        return "".join(p[0].upper() for p in parts if p)[:3]
    return s[:3] if len(s) >= 3 else s
