"""
Stable per-machine fingerprint used for license activation.

Strategy:
- Windows: read MachineGuid from HKLM\\SOFTWARE\\Microsoft\\Cryptography. Survives user
  changes and most reinstalls; reset by clean OS reinstall (which is what we want).
- Other platforms: combine hostname + the BIOS/MAC-derived uuid.getnode().
- Hash the resulting raw identity with SHA-256 so we never send raw machine IDs over the wire.
"""

from __future__ import annotations

import hashlib
import platform
import sys
import uuid


def _windows_machine_guid() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    for hive_key, subkey in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Cryptography"),
    ):
        try:
            with winreg.OpenKey(
                hive_key,
                subkey,
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                if value:
                    return str(value)
        except OSError:
            continue
    return None


def _raw_identity() -> str:
    parts: list[str] = []
    guid = _windows_machine_guid()
    if guid:
        parts.append(f"win-guid:{guid}")
    parts.append(f"node:{platform.node() or ''}")
    parts.append(f"mac:{uuid.getnode():012x}")
    parts.append(f"system:{platform.system()}")
    return "|".join(parts)


def machine_fingerprint() -> str:
    """Return a stable SHA-256 fingerprint for the current machine."""
    raw = _raw_identity().encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()


def machine_label() -> str:
    """Human-readable label for the activation record on Keygen (machine 'name')."""
    return platform.node() or "Unknown device"


def machine_platform() -> str:
    """Keygen 'platform' attribute."""
    sysname = platform.system().lower()
    if sysname.startswith("win"):
        return "windows"
    if sysname == "darwin":
        return "macos"
    return sysname or "unknown"
