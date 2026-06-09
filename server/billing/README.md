# GURU Mobile Discovery — billing worker

Cloudflare Worker: Stripe subscription webhooks → Ed25519 license token → Resend email.

**Full setup:** [docs/BILLING_SETUP.md](../../docs/BILLING_SETUP.md)

## Quick start

```bash
cd server/billing
npm install
npx wrangler login
```

Secrets (`.dev.vars` locally, `wrangler secret put` for deploy):

```
LICENSE_SIGNING_KEY=...       # Ed25519 PKCS8 DER, base64 (pairs with app public key)
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_SECRET_KEY=sk_test_...
RESEND_API_KEY=re_...
RESEND_FROM_EMAIL=licenses@example.com
```

```bash
npm run dev          # http://localhost:8787
npm run deploy       # publish worker
```

Register webhook in Stripe Dashboard:

- URL: `https://<worker-host>/stripe/webhook`
- Events: `checkout.session.completed`, `invoice.paid`

## Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/healthz` | Liveness |
| POST | `/stripe/webhook` | Issue + email license keys |

## Keypair

If you don't have the private key matching the app's embedded public key, generate a new pair:

```bash
python scripts/generate_license_keypair.py
```

Update `LICENSE_PUBLIC_KEY_SPKI_B64` in `app/license_config.py` and set `LICENSE_SIGNING_KEY` on the worker.
