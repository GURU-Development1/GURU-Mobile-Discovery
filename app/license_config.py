"""
Ed25519 offline licensing configuration.

The public key is baked into the shipped exe. Override the Stripe checkout URL at
runtime with:

  GURU_STRIPE_CHECKOUT_URL   (Stripe checkout URL; empty = Buy shows Coming soon)
"""

from __future__ import annotations

import os

# Ed25519 public key, SPKI DER, base64 — see LICENSE_SPEC.md
LICENSE_PUBLIC_KEY_SPKI_B64 = "MCowBQYDK2VwAyEAycKQYoxlPBkxzkG/y65qMaklbUB6Wj7uXG1iAJk9UHM="

# Sample signed token for local testing — see LICENSE_SPEC.md (far-future exp).
SAMPLE_LICENSE_TOKEN = (
    "eyJ2IjoxLCJwcm9kdWN0IjoiZ21kIiwicGxhbiI6ImFubnVhbCIsImVtYWlsIjoiamFuZUBsYXdmaXJtLmNvbSIs"
    "InN1YiI6InN1Yl8xMjMiLCJpc3MiOjE3ODAzOTI3ODgsImV4cCI6MTgxMjYxOTk4OH0."
    "3cyxF44lbQRD3wPxIwdpoJP1_e6CkoEO6cuHKIXJ0DegVLgMQZymFXI-scJSd5G-1uaBj-nm0YC9j5aiP9DPBg"
)

# Paste the Stripe checkout URL here when ready. When empty, Buy shows Coming soon.
_DEFAULT_STRIPE_CHECKOUT_URL = "https://buy.stripe.com/9B6bJ17CI5LSet315bdwc01"


def _env_or(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val.strip() if val and val.strip() else default


STRIPE_CHECKOUT_URL: str = _env_or(
    "GURU_STRIPE_CHECKOUT_URL",
    _DEFAULT_STRIPE_CHECKOUT_URL,
)


def has_checkout_url() -> bool:
    """True if a Stripe checkout URL is configured."""
    return bool(STRIPE_CHECKOUT_URL)
