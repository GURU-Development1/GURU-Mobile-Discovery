"""
Offline Ed25519 license service.

License keys are Ed25519-signed tokens verified fully offline — no network call,
no server, no machine binding. See LICENSE_SPEC.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.license_verify import (
    VerifyOutcome,
    decode_payload_without_verify,
    normalize_license_token,
    payload_expires_at_iso,
    verify_license_token,
)
from app.logging_config import get_logger


LICENSE_FILENAME = "license.json"


class LicenseStatus(str, Enum):
    VALID = "valid"
    NOT_ACTIVATED = "not_activated"
    EXPIRED = "expired"
    INVALID_KEY = "invalid_key"
    UNKNOWN = "unknown"


@dataclass
class _CachedLicense:
    token: str
    email: str = ""
    expires_at: str = ""
    last_validated_at: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "token": self.token,
            "email": self.email,
            "expires_at": self.expires_at,
            "last_validated_at": self.last_validated_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _outcome_to_status(outcome: VerifyOutcome) -> LicenseStatus:
    if outcome == VerifyOutcome.VALID:
        return LicenseStatus.VALID
    if outcome == VerifyOutcome.EXPIRED:
        return LicenseStatus.EXPIRED
    if outcome == VerifyOutcome.INVALID:
        return LicenseStatus.INVALID_KEY
    return LicenseStatus.UNKNOWN


class LicenseService:
    """Activate / validate / remove the local offline license token."""

    def __init__(self, data_root: Path) -> None:
        self._data_root = Path(data_root)
        self._path = self._data_root / LICENSE_FILENAME
        self._cached: Optional[_CachedLicense] = None
        self._log = get_logger()

    # ---------- persistence ----------

    def load(self) -> None:
        if not self._path.is_file():
            self._cached = None
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            token = (raw.get("token") or raw.get("license_key") or "").strip()
            if not token:
                self._cached = None
                return
            payload = decode_payload_without_verify(token) or {}
            self._cached = _CachedLicense(
                token=token,
                email=raw.get("email") or payload.get("email") or "",
                expires_at=raw.get("expires_at") or payload_expires_at_iso(payload),
                last_validated_at=raw.get("last_validated_at", "") or "",
            )
        except Exception as exc:
            self._log.warning("license: failed to load %s: %s", self._path, exc)
            self._cached = None

    def _save(self) -> None:
        if self._cached is None:
            return
        try:
            self._data_root.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._cached.to_dict(), indent=2), encoding="utf-8")
        except OSError as exc:
            self._log.error("license: failed to save %s: %s", self._path, exc)

    def _clear(self) -> None:
        self._cached = None
        try:
            if self._path.is_file():
                self._path.unlink()
        except OSError as exc:
            self._log.warning("license: failed to remove %s: %s", self._path, exc)

    # ---------- public state ----------

    def is_cached(self) -> bool:
        return self._cached is not None and bool(self._cached.token)

    def cached_key(self) -> str:
        return self._cached.token if self._cached else ""

    def cached_email(self) -> str:
        return self._cached.email if self._cached else ""

    def cached_expires_at(self) -> str:
        return self._cached.expires_at if self._cached else ""

    # ---------- core operations ----------

    def activate(self, license_key: str) -> Tuple[LicenseStatus, str]:
        """Verify and store the given license token."""
        token = normalize_license_token(license_key)
        if not token:
            return LicenseStatus.INVALID_KEY, "Enter a license key."

        outcome, payload, msg = verify_license_token(token)
        status = _outcome_to_status(outcome)
        if status != LicenseStatus.VALID or payload is None:
            return status, msg

        self._cached = _CachedLicense(
            token=token,
            email=str(payload.get("email") or ""),
            expires_at=payload_expires_at_iso(payload),
            last_validated_at=_now_iso(),
        )
        self._save()
        return LicenseStatus.VALID, "License activated."

    def revalidate(self) -> Tuple[LicenseStatus, str]:
        """Re-check the cached license token. Called on launch."""
        if not self.is_cached():
            return LicenseStatus.NOT_ACTIVATED, "No license on this device."

        assert self._cached is not None
        outcome, payload, msg = verify_license_token(self._cached.token)
        status = _outcome_to_status(outcome)

        if status == LicenseStatus.VALID and payload is not None:
            self._cached.email = str(payload.get("email") or self._cached.email)
            self._cached.expires_at = payload_expires_at_iso(payload)
            self._cached.last_validated_at = _now_iso()
            self._save()
            return LicenseStatus.VALID, "License valid."

        return status, msg

    def remove_license(self) -> Tuple[bool, str]:
        """Remove the cached license token from this device."""
        if not self.is_cached():
            self._clear()
            return True, "No license on this device."
        self._clear()
        return True, "License removed from this device."
