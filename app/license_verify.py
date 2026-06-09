"""
Ed25519 offline license token verification.

Token format: <base64url(payloadJSON)>.<base64url(signature)>

Verification is performed over the raw decoded payload bytes — the JSON is never
re-serialized. See LICENSE_SPEC.md for the full spec.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization

from app.license_config import LICENSE_PUBLIC_KEY_SPKI_B64


class VerifyOutcome(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    EXPIRED = "expired"


def normalize_license_token(token: str) -> str:
    """Collapse whitespace/newlines from pasted or wrapped UI input."""
    return "".join((token or "").split())


def _b64url_decode(segment: str) -> bytes:
    """Decode a base64url segment (no padding required)."""
    padded = segment.replace("-", "+").replace("_", "/")
    pad_len = (-len(padded)) % 4
    if pad_len:
        padded += "=" * pad_len
    return base64.b64decode(padded)


def _load_public_key():
    key_bytes = base64.b64decode(LICENSE_PUBLIC_KEY_SPKI_B64)
    return serialization.load_der_public_key(key_bytes)


_PUBLIC_KEY = _load_public_key()


def _split_token(token: str) -> Tuple[Optional[bytes], Optional[bytes]]:
    """Split token into payload bytes and signature bytes."""
    parts = normalize_license_token(token).split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None, None
    try:
        payload_bytes = _b64url_decode(parts[0])
        sig_bytes = _b64url_decode(parts[1])
    except (ValueError, base64.binascii.Error):
        return None, None
    return payload_bytes, sig_bytes


def decode_payload_without_verify(token: str) -> Optional[Dict[str, Any]]:
    """Parse payload JSON from a token without verifying the signature.

    Used only for cheap display of cached email/exp on load. Always call
    verify_license_token before trusting a token for access control.
    """
    payload_bytes, _ = _split_token(token)
    if payload_bytes is None:
        return None
    try:
        return json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _exp_to_iso(exp: int) -> str:
    return (
        datetime.fromtimestamp(exp, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def verify_license_token(token: str) -> Tuple[VerifyOutcome, Optional[Dict[str, Any]], str]:
    """Verify an Ed25519 license token fully offline.

    Returns (outcome, payload_or_none, user_message).
    """
    key = normalize_license_token(token)
    if not key:
        return VerifyOutcome.INVALID, None, "Enter a license key."

    payload_bytes, sig_bytes = _split_token(key)
    if payload_bytes is None or sig_bytes is None:
        return VerifyOutcome.INVALID, None, "License key is not valid."

    try:
        _PUBLIC_KEY.verify(sig_bytes, payload_bytes)
    except InvalidSignature:
        return VerifyOutcome.INVALID, None, "License key signature is invalid."

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return VerifyOutcome.INVALID, None, "License key payload is not valid."

    if not isinstance(payload, dict):
        return VerifyOutcome.INVALID, None, "License key payload is not valid."

    if payload.get("product") != "gmd":
        return VerifyOutcome.INVALID, None, "This license key is for a different product."

    exp = payload.get("exp")
    if not isinstance(exp, int):
        return VerifyOutcome.INVALID, None, "License key payload is not valid."

    now = int(time.time())
    if now > exp:
        return VerifyOutcome.EXPIRED, payload, "This license has expired."

    return VerifyOutcome.VALID, payload, "License valid."


def payload_expires_at_iso(payload: Dict[str, Any]) -> str:
    """Return ISO8601 expiry string from a verified payload."""
    exp = payload.get("exp")
    if isinstance(exp, int):
        return _exp_to_iso(exp)
    return ""
