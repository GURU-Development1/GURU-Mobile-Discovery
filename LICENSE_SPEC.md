# GURU Mobile Discovery — License Key Spec

How the desktop app validates a license key. Keys are **Ed25519-signed** and
verify **fully offline** — the app holds the public key below and checks the
signature itself. No network call, no server, no database.

## Public key (embed this in the app)

Ed25519 public key, **SPKI, base64 (DER)**:

```
MCowBQYDK2VwAyEAycKQYoxlPBkxzkG/y65qMaklbUB6Wj7uXG1iAJk9UHM=
```

The matching private key lives only as the Cloudflare secret
`LICENSE_SIGNING_KEY` and is used solely by the webhook to sign keys.

## Token format

```
<base64url(payloadJSON)> "." <base64url(signature)>
```

- **payloadJSON** — UTF-8 JSON, signed as-is (raw bytes).
- **signature** — Ed25519 signature over those exact payload bytes.

Payload fields:

| field | meaning |
|-------|---------|
| `v` | schema version (`1`) |
| `product` | `"gmd"` — reject anything else |
| `plan` | `"annual"` |
| `email` | licensee email (lowercased) |
| `sub` | Stripe subscription id (for support) |
| `iss` | issued-at, unix seconds |
| `exp` | expires-at, unix seconds (paid period end + 7-day grace) |

## Validation algorithm

1. Split the token on `"."` → `payloadB64`, `sigB64`. Reject if not exactly two parts.
2. base64url-decode each into bytes.
3. **Verify** the signature over the **decoded payload bytes** with the public key.
   Reject if it fails. *(Verify over the raw decoded bytes — do NOT re-serialize the JSON.)*
4. Parse the payload JSON.
5. Reject unless `product === "gmd"`.
6. Reject if `now (unix seconds) > exp`. *(Optionally allow a small clock skew.)*
7. Otherwise valid — unlock, and you may show `email` / `exp`.

A renewed subscription emails a fresh token with a later `exp` each period, so a
paying customer always has a current key. A cancelled subscription stops getting
new keys and lapses when the current `exp` passes.

## Reference verifier — JavaScript (Web Crypto; browser / Node 20+ / Electron)

```js
const PUBLIC_KEY_SPKI_B64 = "MCowBQYDK2VwAyEAycKQYoxlPBkxzkG/y65qMaklbUB6Wj7uXG1iAJk9UHM=";

const b64 = (s) => Uint8Array.from(atob(s), c => c.charCodeAt(0));
const b64url = (s) => b64(s.replace(/-/g, "+").replace(/_/g, "/"));

export async function verifyLicense(token) {
  const parts = String(token).trim().split(".");
  if (parts.length !== 2) return { valid: false, reason: "malformed" };
  const payloadBytes = b64url(parts[0]);
  const sigBytes = b64url(parts[1]);

  const key = await crypto.subtle.importKey(
    "spki", b64(PUBLIC_KEY_SPKI_B64), { name: "Ed25519" }, false, ["verify"]
  );
  const ok = await crypto.subtle.verify({ name: "Ed25519" }, key, sigBytes, payloadBytes);
  if (!ok) return { valid: false, reason: "bad-signature" };

  const p = JSON.parse(new TextDecoder().decode(payloadBytes));
  if (p.product !== "gmd") return { valid: false, reason: "wrong-product" };
  if (Math.floor(Date.now() / 1000) > p.exp) return { valid: false, reason: "expired" };
  return { valid: true, payload: p };
}
```

## Reference verifier — Node (crypto module)

```js
const crypto = require("crypto");
const PUB = "MCowBQYDK2VwAyEAycKQYoxlPBkxzkG/y65qMaklbUB6Wj7uXG1iAJk9UHM=";
const unb64url = (s) => Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64");

function verifyLicense(token) {
  const [p, s] = String(token).trim().split(".");
  if (!p || !s) return { valid: false, reason: "malformed" };
  const payloadBytes = unb64url(p);
  const pub = crypto.createPublicKey({ key: Buffer.from(PUB, "base64"), format: "der", type: "spki" });
  if (!crypto.verify(null, payloadBytes, pub, unb64url(s))) return { valid: false, reason: "bad-signature" };
  const payload = JSON.parse(payloadBytes.toString());
  if (payload.product !== "gmd") return { valid: false, reason: "wrong-product" };
  if (Math.floor(Date.now() / 1000) > payload.exp) return { valid: false, reason: "expired" };
  return { valid: true, payload };
}
```

## Other languages

The algorithm is plain Ed25519 + base64url. For a native Windows/.NET app:
import the SPKI public key and verify with **BouncyCastle** (`Ed25519Signer` /
`Ed25519PublicKeyParameters`), or .NET's `System.Security.Cryptography` Ed25519
support where available. Any Ed25519 library works — the key is standard SPKI DER.

## Sample token (for testing your verifier)

This is a real, signed token (with a far-future `exp`) you can paste into your
verifier to confirm it accepts a valid key:

```
eyJ2IjoxLCJwcm9kdWN0IjoiZ21kIiwicGxhbiI6ImFubnVhbCIsImVtYWlsIjoiamFuZUBsYXdmaXJtLmNvbSIsInN1YiI6InN1Yl8xMjMiLCJpc3MiOjE3ODAzOTI3ODgsImV4cCI6MTgxMjYxOTk4OH0.3cyxF44lbQRD3wPxIwdpoJP1_e6CkoEO6cuHKIXJ0DegVLgMQZymFXI-scJSd5G-1uaBj-nm0YC9j5aiP9DPBg
```

Expected decoded payload:

```json
{"v":1,"product":"gmd","plan":"annual","email":"jane@lawfirm.com","sub":"sub_123","iss":1780392788,"exp":1812619988}
```

Tamper with any character and verification must fail.
