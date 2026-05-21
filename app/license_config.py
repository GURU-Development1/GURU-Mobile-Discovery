"""
Keygen licensing configuration.

These constants are not secret; they are baked into the shipped exe. Override at runtime
with environment variables (handy for dev/CI against a separate Keygen account):

  GURU_KEYGEN_ACCOUNT_ID
  GURU_KEYGEN_PRODUCT_ID
  GURU_KEYGEN_API_BASE
  GURU_KEYGEN_PUBLIC_KEY      (Ed25519 verify key, hex; reserved for future offline files)
  GURU_LEMON_SQUEEZY_CHECKOUT_URL   (Lemon Squeezy checkout URL; empty = Buy shows Coming soon)

Fill in the account/product IDs after creating them in the Keygen dashboard.
"""

from __future__ import annotations

import os

# Fill these from your Keygen dashboard, then commit. Both are public identifiers.
_DEFAULT_ACCOUNT_ID = "21a2c672-1c8f-4a16-8f73-72371962dc00"
_DEFAULT_PRODUCT_ID = "8b16a1c3-fa56-4e7d-889c-d8239ac47c03"
_DEFAULT_API_BASE = "https://api.keygen.sh/v1"
_DEFAULT_PUBLIC_KEY = ""
# Paste the Lemon Squeezy checkout URL here when ready. When empty, Buy shows Coming soon.
_DEFAULT_LEMON_SQUEEZY_CHECKOUT_URL = ""


def _env_or(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val.strip() if val and val.strip() else default


KEYGEN_ACCOUNT_ID: str = _env_or("GURU_KEYGEN_ACCOUNT_ID", _DEFAULT_ACCOUNT_ID)
KEYGEN_PRODUCT_ID: str = _env_or("GURU_KEYGEN_PRODUCT_ID", _DEFAULT_PRODUCT_ID)
KEYGEN_API_BASE: str = _env_or("GURU_KEYGEN_API_BASE", _DEFAULT_API_BASE)
KEYGEN_PUBLIC_KEY: str = _env_or("GURU_KEYGEN_PUBLIC_KEY", _DEFAULT_PUBLIC_KEY)
LEMON_SQUEEZY_CHECKOUT_URL: str = _env_or(
    "GURU_LEMON_SQUEEZY_CHECKOUT_URL",
    _DEFAULT_LEMON_SQUEEZY_CHECKOUT_URL,
)


def is_configured() -> bool:
    """True if account + product IDs are set (locally or via env)."""
    return bool(KEYGEN_ACCOUNT_ID and KEYGEN_PRODUCT_ID)


def has_checkout_url() -> bool:
    """True if a Lemon Squeezy checkout URL is configured."""
    return bool(LEMON_SQUEEZY_CHECKOUT_URL)


def account_url(path: str = "") -> str:
    """Build a Keygen account-scoped URL, e.g. account_url('/licenses/actions/validate-key')."""
    base = f"{KEYGEN_API_BASE.rstrip('/')}/accounts/{KEYGEN_ACCOUNT_ID}"
    return f"{base}{path}" if path.startswith("/") else f"{base}/{path}"
