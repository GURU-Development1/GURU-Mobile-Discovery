# Stripe + Ed25519 + Resend billing setup

Wire Stripe subscription payments to Ed25519-signed license keys emailed via Resend. The desktop app verifies tokens fully offline — see [LICENSE_SPEC.md](../LICENSE_SPEC.md).

Worker source: [`server/billing/`](../server/billing/)

---

## Architecture

```
Buyer → Stripe Checkout (Payment Link)
     → Stripe webhook POST /stripe/webhook
          → verify Stripe-Signature
          → GET subscription (period end + email)
          → Ed25519 sign license token
          → Resend email with token
     → Buyer pastes token into app Activate dialog
```

---

## Stage 0 — Accounts

| Service | Purpose |
|---------|---------|
| [Stripe](https://dashboard.stripe.com) | Payments + subscriptions |
| [Cloudflare](https://dash.cloudflare.com) | Host the Worker (free tier OK) |
| [Resend](https://resend.com) | Email license keys |

---

## Stage 1 — Ed25519 signing key

The app embeds this **public** key in [`app/license_config.py`](../app/license_config.py):

```
MCowBQYDK2VwAyEAycKQYoxlPBkxzkG/y65qMaklbUB6Wj7uXG1iAJk9UHM=
```

The worker needs the matching **private** key as `LICENSE_SIGNING_KEY`.

### If you already have the private key

Set it as the Worker secret (PKCS8 DER, base64). **Do not change the app public key.**

### If you need a new keypair

From the repo root:

```bash
python scripts/generate_license_keypair.py
```

This prints:
- `LICENSE_PUBLIC_KEY_SPKI_B64` → update [`app/license_config.py`](../app/license_config.py)
- `LICENSE_SIGNING_KEY` → Worker secret
- A sample token to test in the app

---

## Stage 2 — Stripe

1. **Test mode** in Stripe Dashboard (toggle top-right).
2. Create product **GURU Mobile Discovery** with a **yearly** recurring price.
3. Create a **Payment Link** → copy the URL.
4. Set in [`app/license_config.py`](../app/license_config.py):

   ```python
   _DEFAULT_STRIPE_CHECKOUT_URL = "https://buy.stripe.com/test_..."
   ```

   Or env var `GURU_STRIPE_CHECKOUT_URL`.

5. Test card: `4242 4242 4242 4242`, any future expiry, any CVC.

---

## Stage 3 — Resend

1. Create API key (`re_...`).
2. Verify your sending domain (production) or use `onboarding@resend.dev` for early tests (sandbox limits apply).
3. Choose `RESEND_FROM_EMAIL`, e.g. `licenses@yourdomain.com`.

---

## Stage 4 — Deploy the Worker

```bash
cd server/billing
npm install
npx wrangler login
```

Set secrets:

```bash
npx wrangler secret put LICENSE_SIGNING_KEY
npx wrangler secret put STRIPE_WEBHOOK_SECRET
npx wrangler secret put STRIPE_SECRET_KEY
npx wrangler secret put RESEND_API_KEY
npx wrangler secret put RESEND_FROM_EMAIL
```

Deploy:

```bash
npm run deploy
```

Copy the Worker URL (e.g. `https://gmd-billing.<subdomain>.workers.dev`).

Sanity check: open `https://<worker-host>/healthz` → `ok`.

---

## Stage 5 — Stripe webhook

1. Stripe Dashboard → **Developers → Webhooks → Add endpoint**
2. URL: `https://<worker-host>/stripe/webhook`
3. Events:
   - `checkout.session.completed`
   - `invoice.paid`
4. Copy the **signing secret** (`whsec_...`) → `wrangler secret put STRIPE_WEBHOOK_SECRET` → redeploy.

---

## Stage 6 — End-to-end test

1. Run the app → **Buy one** → complete test checkout.
2. Tail Worker logs: `cd server/billing && npm run tail`
3. Check email for the license key.
4. Paste into **Activate** in the app.

### Local webhook testing (optional)

```bash
cd server/billing
# Put secrets in .dev.vars (gitignored)
npm run dev
```

In another terminal:

```bash
stripe listen --forward-to localhost:8787/stripe/webhook
stripe trigger checkout.session.completed
```

---

## Secrets reference

| Secret | Example | Notes |
|--------|---------|-------|
| `LICENSE_SIGNING_KEY` | base64 PKCS8 DER | Ed25519 **private** key; pairs with app public key |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` | From webhook endpoint settings |
| `STRIPE_SECRET_KEY` | `sk_test_...` | For fetching subscription/customer details |
| `RESEND_API_KEY` | `re_...` | Resend dashboard |
| `RESEND_FROM_EMAIL` | `licenses@domain.com` | Must be verified in Resend |

Local dev: create `server/billing/.dev.vars` with the same keys (Wrangler loads it automatically).

---

## Renewal and cancellation

| Event | Behavior |
|-------|----------|
| `invoice.paid` (renewal) | New token emailed with later `exp` |
| Subscription cancelled | No new tokens; current token lapses at `exp` |

---

## Common issues

| Symptom | Likely cause |
|---------|----------------|
| Webhook 400 / signature failed | Wrong `STRIPE_WEBHOOK_SECRET` for that endpoint |
| App rejects key | Public/private key mismatch; or token tampered |
| No email | Resend domain not verified / sandbox recipient limits |
| Stripe API 401 | Wrong `STRIPE_SECRET_KEY` or test/live mismatch |
| Buy shows Coming soon | Empty `STRIPE_CHECKOUT_URL` in `license_config.py` |

---

## Related files

| File | Purpose |
|------|---------|
| [`server/billing/`](../server/billing/) | Worker source |
| [`LICENSE_SPEC.md`](../LICENSE_SPEC.md) | Token format + verification |
| [`app/license_config.py`](../app/license_config.py) | Public key + checkout URL |
| [`app/license_verify.py`](../app/license_verify.py) | Desktop offline verifier |
| [`scripts/generate_license_keypair.py`](../scripts/generate_license_keypair.py) | Generate new keypair |
