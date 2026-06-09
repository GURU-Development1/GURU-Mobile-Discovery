#!/usr/bin/env python3
"""
Generate an Ed25519 keypair for GURU Mobile Discovery license signing.

Prints:
  - LICENSE_PUBLIC_KEY_SPKI_B64  → embed in app/license_config.py
  - LICENSE_SIGNING_KEY          → wrangler secret put LICENSE_SIGNING_KEY (PKCS8 DER, base64)

Optionally signs a sample payload and verifies with app/license_verify.py.

Usage (from repo root):
  python scripts/generate_license_keypair.py
  python scripts/generate_license_keypair.py --verify-app-key
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

# Allow running as `python scripts/generate_license_keypair.py` from repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_keypair() -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    public_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(public_der).decode("ascii"), base64.b64encode(private_der).decode("ascii")


def sign_sample_token(private_b64: str, public_b64: str) -> str:
    """Sign a sample payload and verify with the given public key."""
    from cryptography.hazmat.primitives import serialization as ser

    private_key = ser.load_der_private_key(base64.b64decode(private_b64), password=None)
    now = int(time.time())
    payload = {
        "v": 1,
        "product": "gmd",
        "plan": "annual",
        "email": "jane@lawfirm.com",
        "sub": "sub_123",
        "iss": now,
        "exp": now + 365 * 86400,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = private_key.sign(payload_bytes)
    token = f"{_b64url(payload_bytes)}.{_b64url(sig)}"

    pub = ser.load_der_public_key(base64.b64decode(public_b64))
    pub.verify(sig, payload_bytes)
    return token


def verify_with_app_verifier(token: str, public_b64: str) -> None:
    """Verify token using app/license_verify with the given public key."""
    from cryptography.hazmat.primitives import serialization

    import app.license_verify as verify

    pub = serialization.load_der_public_key(base64.b64decode(public_b64))
    old_key = verify._PUBLIC_KEY
    verify._PUBLIC_KEY = pub
    try:
        outcome, payload, msg = verify.verify_license_token(token)
    finally:
        verify._PUBLIC_KEY = old_key

    if outcome.value != "valid":
        raise RuntimeError(f"app verifier rejected token: {msg}")
    print(f"  app verifier OK — email={payload.get('email')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Ed25519 license signing keypair")
    parser.add_argument(
        "--verify-app-key",
        action="store_true",
        help="Round-trip sign/verify using LICENSE_PUBLIC_KEY_SPKI_B64 already in license_config.py",
    )
    args = parser.parse_args()

    if args.verify_app_key:
        from app.license_config import LICENSE_PUBLIC_KEY_SPKI_B64

        print("Using existing LICENSE_PUBLIC_KEY_SPKI_B64 from app/license_config.py")
        print("You must supply the matching LICENSE_SIGNING_KEY (PKCS8 base64) via stdin or env.")
        print("(This script cannot recover the private key from the public key.)")
        return 0

    public_b64, private_b64 = generate_keypair()
    token = sign_sample_token(private_b64, public_b64)
    verify_with_app_verifier(token, public_b64)

    print()
    print("=== Ed25519 license keypair (NEW — update app + worker secrets) ===")
    print()
    print("PUBLIC (SPKI DER, base64) — paste into app/license_config.py:")
    print(f"  LICENSE_PUBLIC_KEY_SPKI_B64 = \"{public_b64}\"")
    print()
    print("PRIVATE (PKCS8 DER, base64) — wrangler secret put LICENSE_SIGNING_KEY:")
    print(f"  {private_b64}")
    print()
    print("Sample token (verify in app after updating public key):")
    print(f"  {token}")
    print()
    print("Round-trip: cryptography sign + app/license_verify.py OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
