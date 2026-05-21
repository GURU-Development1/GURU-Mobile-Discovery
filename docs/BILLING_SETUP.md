# Lemon Squeezy + Keygen + Resend billing setup (full walkthrough)

Use this guide when you are ready to wire payments to license keys. The Cloudflare Worker lives under [`server/billing/`](../server/billing/). For quick commands and env vars, see [`server/billing/README.md`](../server/billing/README.md).

---

## Stage 0 — Accounts and tools

Set these up before anything else:

| Item | Notes |
|------|--------|
| **Lemon Squeezy** | [lemonsqueezy.com](https://www.lemonsqueezy.com). Create a store and subscription product. |
| **Keygen** | Account + product + policy (`maxMachines: 1`, license auth). IDs may already be in [`app/license_config.py`](../app/license_config.py). |
| **Resend** | [resend.com](https://resend.com). Verify a sending domain (production) or use sandbox `onboarding@resend.dev` for early tests. |
| **Cloudflare** | Free Workers account at [dash.cloudflare.com](https://dash.cloudflare.com/sign-up). |

**Install locally:**

- **Node.js 20+** (LTS): [nodejs.org](https://nodejs.org)

---

## Stage 1 — Lemon Squeezy dashboard

1. **Create a store** (if you have not already).

2. **Create the product**
   - Name: `GURU Mobile Discovery`
   - Pricing: **Subscription**, **Yearly** (or your preferred billing interval)
   - Save and note the **Store ID** and **Variant ID**

3. **Copy the checkout URL**
   - From the product share/checkout link, e.g. `https://yourstore.lemonsqueezy.com/checkout/buy/...`
   - You will paste this into [`app/license_config.py`](../app/license_config.py) as `_DEFAULT_LEMON_SQUEEZY_CHECKOUT_URL` (or set env var `GURU_LEMON_SQUEEZY_CHECKOUT_URL` at build/runtime)

4. **Webhook:** Do **not** add it yet — you need the Worker URL from Stage 4 first.

---

## Stage 2 — Keygen

1. **Account ID** and **Product ID** — match [`app/license_config.py`](../app/license_config.py) (`KEYGEN_ACCOUNT_ID`, `KEYGEN_PRODUCT_ID`).

2. **Policy ID** — Keygen → **Policies** → open your single-seat policy → copy UUID (`KEYGEN_POLICY_ID` for the Worker).

3. **Admin token** — **Account → Tokens → New** (Bearer, admin). Copy immediately. Worker needs scopes such as: `license.create`, `license.read`, `license.update`, `license.update.suspend`, `license.update.reinstate`, `license.renew`.

---

## Stage 3 — Resend

1. **API key** — Dashboard → **API Keys → Create** → copy `re_...`

2. **From address**
   - Production: verify your domain in Resend, then use e.g. `licenses@yourdomain.com`
   - Early test: `onboarding@resend.dev` (sandbox limits apply)

---

## Stage 4 — Deploy the Cloudflare Worker

From the repo root:

```bash
cd server/billing
npm install
npx wrangler login
```

Set secrets (Wrangler prompts for each value):

```bash
npx wrangler secret put LEMON_SQUEEZY_WEBHOOK_SECRET
npx wrangler secret put KEYGEN_ACCOUNT_ID
npx wrangler secret put KEYGEN_PRODUCT_ID
npx wrangler secret put KEYGEN_POLICY_ID
npx wrangler secret put KEYGEN_ADMIN_TOKEN
npx wrangler secret put RESEND_API_KEY
npx wrangler secret put RESEND_FROM_EMAIL
```

Optional (recommended in production):

```bash
npx wrangler secret put LEMON_SQUEEZY_STORE_ID
```

Skip `LEMON_SQUEEZY_WEBHOOK_SECRET` until Stage 5 if you prefer to deploy the Worker first.

Deploy:

```bash
npm run deploy
```

Copy the published Worker URL (e.g. `https://gmd-billing.<subdomain>.workers.dev`).

Sanity: open `https://<worker-host>/healthz` → should return `ok`.

---

## Stage 5 — Lemon Squeezy webhook

1. Lemon Squeezy → **Settings → Webhooks → Create webhook**

2. **URL:** `https://<worker-host>/lemonsqueezy/webhook`

3. **Signing secret** — choose a secret (6–40 characters); save it for the Worker.

4. **Events** (subscribe to these):

   - `subscription_created`
   - `subscription_updated`
   - `subscription_cancelled`
   - `subscription_expired`
   - `subscription_resumed`
   - `subscription_unpaused`
   - `subscription_payment_success`
   - `subscription_payment_failed`
   - `subscription_payment_refunded`

5. Back in `server/billing`:

```bash
npx wrangler secret put LEMON_SQUEEZY_WEBHOOK_SECRET
```

Paste the signing secret, then redeploy:

```bash
npm run deploy
```

---

## Stage 6 — Desktop app “Buy” link

In [`app/license_config.py`](../app/license_config.py), set `_DEFAULT_LEMON_SQUEEZY_CHECKOUT_URL` to your Lemon Squeezy checkout URL, or set env var `GURU_LEMON_SQUEEZY_CHECKOUT_URL` at runtime.

When the checkout URL is **empty**, the license dialog still shows **Buy one**, but clicking it opens a **Coming soon** message. Set the URL when you are ready to accept purchases.

---

## Stage 7 — End-to-end test

1. Run the app → activation dialog → **Buy one** (opens checkout when URL is set).

2. Complete checkout with Lemon Squeezy test mode. Use an email that can receive mail.

3. Tail Worker logs:

```bash
cd server/billing
npm run tail
```

   Expect `POST .../lemonsqueezy/webhook` → 200.

4. Check email for the license key → paste into **Activate** in the app.

---

## Stage 8 — Verify cancellation

1. Lemon Squeezy → cancel the test subscription.

2. Relaunch the app — license should fail validation / prompt again if suspended.

---

## Stage 9 — Going live

1. Lemon Squeezy: switch to **Live mode**; recreate or enable live checkout and register a webhook pointing at your **production** Worker URL.

2. Resend: use a verified domain and production API key.

3. Worker secrets for production (example):

```bash
npx wrangler secret put LEMON_SQUEEZY_WEBHOOK_SECRET --env production
# ... repeat each secret with --env production
npm run deploy:prod
```

4. Update `_DEFAULT_LEMON_SQUEEZY_CHECKOUT_URL` (or build-time env) to the **live** checkout URL before shipping the installer.

---

## Local development (optional)

From `server/billing`:

```bash
npm run dev
```

Put secrets in `server/billing/.dev.vars` (gitignored). Example `.dev.vars`:

```
LEMON_SQUEEZY_WEBHOOK_SECRET=your-signing-secret
LEMON_SQUEEZY_STORE_ID=12345
KEYGEN_ACCOUNT_ID=...
KEYGEN_PRODUCT_ID=...
KEYGEN_POLICY_ID=...
KEYGEN_ADMIN_TOKEN=...
RESEND_API_KEY=re_...
RESEND_FROM_EMAIL=licenses@example.com
```

Use a tunnel (e.g. ngrok, Cloudflare Tunnel) to forward Lemon Squeezy webhooks to `http://localhost:8787/lemonsqueezy/webhook` for local testing.

---

## Common issues

| Symptom | Likely cause |
|---------|----------------|
| Webhook `400` / signature failed | Wrong signing secret for that webhook endpoint |
| No email | Resend sandbox / unverified domain / recipient not allowed |
| Keygen `401` | Bad admin token or missing scopes |
| App activates but later breaks | Policy mismatch (product scope, machine limit, license auth) |
| “Buy one” shows Coming soon | `LEMON_SQUEEZY_CHECKOUT_URL` / `_DEFAULT_LEMON_SQUEEZY_CHECKOUT_URL` empty |

---

## Related files

| File | Purpose |
|------|---------|
| [`server/billing/`](../server/billing/) | Worker source (`lemon_squeezy.ts`, `keygen.ts`, `email.ts`) |
| [`server/billing/README.md`](../server/billing/README.md) | Commands, secrets list, local `.dev.vars` |
| [`app/license_config.py`](../app/license_config.py) | Keygen public IDs + `LEMON_SQUEEZY_CHECKOUT_URL` |
| [`app/license_dialog.py`](../app/license_dialog.py) | Buy link → checkout URL or Coming soon dialog |
