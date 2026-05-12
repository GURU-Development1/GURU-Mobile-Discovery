"""
Keygen-backed license service.

Single-seat enforcement: the Keygen policy is configured with maxMachines=1, so a second
machine's activation request returns MACHINE_LIMIT_EXCEEDED and we surface it as
"already in use elsewhere".

This module is intentionally vendor-specific. Other vendors can be added later by
swapping this class behind a thin interface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

from app.license_config import (
    KEYGEN_PRODUCT_ID,
    account_url,
    is_configured,
)
from app.license_fingerprint import (
    machine_fingerprint,
    machine_label,
    machine_platform,
)
from app.logging_config import get_logger


LICENSE_FILENAME = "license.json"
_HTTP_TIMEOUT = 15  # seconds


class LicenseStatus(str, Enum):
    VALID = "valid"
    NOT_ACTIVATED = "not_activated"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    IN_USE_ELSEWHERE = "in_use_elsewhere"
    INVALID_KEY = "invalid_key"
    NETWORK_ERROR = "network_error"
    NOT_CONFIGURED = "not_configured"
    UNKNOWN = "unknown"


@dataclass
class _CachedLicense:
    license_key: str
    license_id: str = ""
    machine_id: str = ""
    fingerprint: str = ""
    last_validated_at: str = ""
    expires_at: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "license_key": self.license_key,
            "license_id": self.license_id,
            "machine_id": self.machine_id,
            "fingerprint": self.fingerprint,
            "last_validated_at": self.last_validated_at,
            "expires_at": self.expires_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class LicenseService:
    """Activate / validate / deactivate the local license against Keygen."""

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
            key = (raw.get("license_key") or "").strip()
            if not key:
                self._cached = None
                return
            self._cached = _CachedLicense(
                license_key=key,
                license_id=raw.get("license_id", "") or "",
                machine_id=raw.get("machine_id", "") or "",
                fingerprint=raw.get("fingerprint", "") or "",
                last_validated_at=raw.get("last_validated_at", "") or "",
                expires_at=raw.get("expires_at", "") or "",
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
        return self._cached is not None and bool(self._cached.license_key)

    def cached_key(self) -> str:
        return self._cached.license_key if self._cached else ""

    def cached_expires_at(self) -> str:
        return self._cached.expires_at if self._cached else ""

    # ---------- core operations ----------

    def activate(self, license_key: str) -> Tuple[LicenseStatus, str]:
        """Validate then (if needed) activate this machine for the given key."""
        key = (license_key or "").strip()
        if not key:
            return LicenseStatus.INVALID_KEY, "Enter a license key."
        if not is_configured():
            return (
                LicenseStatus.NOT_CONFIGURED,
                "Licensing is not configured for this build. Contact support.",
            )

        fp = machine_fingerprint()
        status, val_payload, msg = self._validate_key(key, fp)

        if status == LicenseStatus.VALID:
            self._cached = _CachedLicense(license_key=key)
            self._cache_from_validate(val_payload, fp)
            self._save()
            return LicenseStatus.VALID, "License activated."

        if status == LicenseStatus.NOT_ACTIVATED:
            license_id = _license_id_from_validate(val_payload)
            if not license_id:
                return LicenseStatus.INVALID_KEY, "License key not recognized."
            act_status, act_msg = self._activate_machine(key, license_id, fp)
            if act_status != LicenseStatus.VALID:
                return act_status, act_msg
            self._cached = _CachedLicense(license_key=key, license_id=license_id, fingerprint=fp)
            # Final re-validate to capture machine_id + canonical expiry.
            status2, val2, _ = self._validate_key(key, fp)
            if status2 == LicenseStatus.VALID:
                self._cache_from_validate(val2, fp)
            self._save()
            return LicenseStatus.VALID, "License activated."

        return status, msg

    def revalidate(self) -> Tuple[LicenseStatus, str]:
        """Re-check the cached license. Called on launch."""
        if not self.is_cached():
            return LicenseStatus.NOT_ACTIVATED, "No license on this device."
        if not is_configured():
            return (
                LicenseStatus.NOT_CONFIGURED,
                "Licensing is not configured for this build. Contact support.",
            )

        assert self._cached is not None
        fp = machine_fingerprint()
        status, payload, msg = self._validate_key(self._cached.license_key, fp)

        if status == LicenseStatus.VALID:
            self._cache_from_validate(payload, fp)
            self._save()
            return LicenseStatus.VALID, "License valid."

        if status == LicenseStatus.NOT_ACTIVATED:
            # Cached locally, but server says no machine for this fingerprint anymore
            # (e.g. seat was deactivated remotely). Treat as IN_USE_ELSEWHERE so the user
            # is prompted to reactivate, while we keep the key cached for convenience.
            return (
                LicenseStatus.IN_USE_ELSEWHERE,
                "This device's activation was removed. Reactivate or use a new key.",
            )

        return status, msg

    def deactivate(self) -> Tuple[bool, str]:
        """Release this machine's seat on Keygen and clear local cache."""
        if not self.is_cached():
            self._clear()
            return True, "No active license on this device."
        assert self._cached is not None
        key = self._cached.license_key
        machine_id = self._cached.machine_id

        if not machine_id:
            # Nothing to delete server-side; just clear local cache.
            self._clear()
            return True, "License removed from this device."

        try:
            resp = requests.delete(
                account_url(f"/machines/{machine_id}"),
                headers={
                    "Authorization": f"License {key}",
                    "Accept": "application/vnd.api+json",
                },
                timeout=_HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            self._log.warning("license: deactivate network error: %s", exc)
            return False, "Network error contacting license server."

        if resp.status_code in (200, 202, 204, 404):
            # 404 means it was already gone server-side; still clear locally.
            self._clear()
            return True, "Device deactivated."

        self._log.warning("license: deactivate failed %s %s", resp.status_code, _short(resp.text))
        return False, "Deactivation failed. Please contact support."

    # ---------- Keygen wire calls ----------

    def _validate_key(
        self, key: str, fingerprint: str
    ) -> Tuple[LicenseStatus, Dict[str, Any], str]:
        scope: Dict[str, Any] = {"fingerprint": fingerprint}
        if KEYGEN_PRODUCT_ID:
            scope["product"] = KEYGEN_PRODUCT_ID
        body = {"meta": {"key": key, "scope": scope}}
        try:
            resp = requests.post(
                account_url("/licenses/actions/validate-key"),
                headers={
                    "Accept": "application/vnd.api+json",
                    "Content-Type": "application/vnd.api+json",
                },
                json=body,
                timeout=_HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            self._log.warning("license: validate network error: %s", exc)
            return LicenseStatus.NETWORK_ERROR, {}, "Network error contacting license server."

        if resp.status_code == 404:
            return LicenseStatus.INVALID_KEY, {}, "License key not found."
        if resp.status_code >= 500:
            return (
                LicenseStatus.NETWORK_ERROR,
                {},
                "License server is temporarily unavailable. Try again shortly.",
            )

        try:
            payload = resp.json()
        except ValueError:
            return LicenseStatus.UNKNOWN, {}, "Unexpected response from license server."

        meta = payload.get("meta") or {}
        code = (meta.get("code") or "").upper()
        valid = bool(meta.get("valid"))

        if valid and code == "VALID":
            return LicenseStatus.VALID, payload, "License valid."
        if code in {
            "NO_MACHINE",
            "NO_MACHINES",
            "FINGERPRINT_SCOPE_MISMATCH",
            "FINGERPRINT_SCOPE_REQUIRED",
            "FINGERPRINT_SCOPE_EMPTY",
        }:
            return LicenseStatus.NOT_ACTIVATED, payload, "License needs to be activated for this device."
        if code in {"TOO_MANY_MACHINES", "MACHINE_LIMIT_EXCEEDED"}:
            return (
                LicenseStatus.IN_USE_ELSEWHERE,
                payload,
                "This license is already active on another device.",
            )
        if code in {"EXPIRED"}:
            return LicenseStatus.EXPIRED, payload, "This license has expired."
        if code in {"SUSPENDED", "BANNED"}:
            return LicenseStatus.SUSPENDED, payload, "This license has been suspended. Contact support."
        if code in {"NOT_FOUND"}:
            return LicenseStatus.INVALID_KEY, payload, "License key not recognized."
        if code in {"PRODUCT_SCOPE_MISMATCH", "PRODUCT_SCOPE_REQUIRED"}:
            return (
                LicenseStatus.INVALID_KEY,
                payload,
                "This license key is for a different product.",
            )

        detail = meta.get("detail") or f"Validation failed ({code or 'UNKNOWN'})."
        return LicenseStatus.UNKNOWN, payload, str(detail)

    def _activate_machine(
        self, key: str, license_id: str, fingerprint: str
    ) -> Tuple[LicenseStatus, str]:
        body = {
            "data": {
                "type": "machines",
                "attributes": {
                    "fingerprint": fingerprint,
                    "platform": machine_platform(),
                    "name": machine_label(),
                },
                "relationships": {
                    "license": {"data": {"type": "licenses", "id": license_id}},
                },
            }
        }
        try:
            resp = requests.post(
                account_url("/machines"),
                headers={
                    "Authorization": f"License {key}",
                    "Accept": "application/vnd.api+json",
                    "Content-Type": "application/vnd.api+json",
                },
                json=body,
                timeout=_HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            self._log.warning("license: activate network error: %s", exc)
            return LicenseStatus.NETWORK_ERROR, "Network error contacting license server."

        if resp.status_code in (200, 201):
            return LicenseStatus.VALID, "Activated."

        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        codes = {
            (err.get("code") or "").upper()
            for err in (payload.get("errors") or [])
            if isinstance(err, dict)
        }

        if codes & {
            "MACHINE_LIMIT_EXCEEDED",
            "FINGERPRINT_TAKEN",
            "FINGERPRINT_TAKEN_BY_MACHINE",
        }:
            return (
                LicenseStatus.IN_USE_ELSEWHERE,
                "This license is already active on another device. "
                "Deactivate it on the other device first, or contact support.",
            )
        if resp.status_code in (401, 403):
            return LicenseStatus.INVALID_KEY, "License key was rejected by the server."

        self._log.warning(
            "license: activate failed %s codes=%s body=%s",
            resp.status_code, sorted(codes), _short(resp.text),
        )
        return LicenseStatus.UNKNOWN, "Activation failed. Please try again or contact support."

    # ---------- helpers ----------

    def _cache_from_validate(self, payload: Dict[str, Any], fingerprint: str) -> None:
        if self._cached is None:
            return
        data = payload.get("data") or {}
        attrs = data.get("attributes") or {}
        self._cached.license_id = data.get("id", "") or self._cached.license_id
        self._cached.fingerprint = fingerprint
        self._cached.expires_at = attrs.get("expiry") or ""
        self._cached.last_validated_at = _now_iso()
        machine_id = _machine_id_from_validate(payload, fingerprint)
        if machine_id:
            self._cached.machine_id = machine_id


def _license_id_from_validate(payload: Dict[str, Any]) -> str:
    data = payload.get("data") or {}
    return data.get("id", "") or ""


def _machine_id_from_validate(payload: Dict[str, Any], fingerprint: str) -> str:
    """Walk the JSON:API 'included' array for a machine with our fingerprint."""
    for inc in payload.get("included") or []:
        if not isinstance(inc, dict):
            continue
        if inc.get("type") != "machines":
            continue
        attrs = inc.get("attributes") or {}
        if attrs.get("fingerprint") == fingerprint:
            return inc.get("id", "") or ""
    return ""


def _short(text: str, limit: int = 400) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "..."
