# Stripe + Keygen + Resend billing setup (full walkthrough)

Use this guide when you are ready to wire payments to license keys. The Cloudflare Worker lives under [`server/billing/`](../server/billing/). For quick commands and env vars, see [`server/billing/README.md`](../server/billing/README.md).

---

## Stage 0 — Accounts and tools

Set these up before anything else:

| Item | Notes |
|------|--------|
| **Stripe** | [dashboard.stripe.com](https://dashboard.stripe.com/register). Use **test mode** until you go live. |
| **Keygen** | Account + product + policy (`maxMachines: 1`, license auth). IDs may already be in [`app/license_config.py`](../app/license_config.py). |
| **Resend** | [resend.com](https://resend.com). Verify a sending domain (production) or use sandbox `onboarding@resend.dev` for early tests. |
| **Cloudflare** | Free Workers account at [dash.cloudflare.com](https://dash.cloudflare.com/sign-up). |

**Install locally:**

- **Node.js 20+** (LTS): [nodejs.org](https://nodejs.org)
- **Stripe CLI** (Windows): `winget install Stripe.StripeCLI`

---

## Stage 1 — Stripe dashboard (test mode)

1. **Toggle test mode** (top-right of Stripe Dashboard).

2. **Create the product**
   - **Product catalog → Add product**
   - Name: `GURU Mobile Discovery`
   - Pricing: **Recurring**, **Yearly**, set amount → Save

3. **Create a Payment Link**
   - **Product catalog → Payment links → New**
   - Select that yearly price
   - Options: after payment, show a confirmation message (e.g. “Your license key has been emailed.”)
   - Save and copy the URL (`https://buy.stripe.com/test_...`)

4. **Customer portal**
   - **Settings → Billing → Customer portal**
   - Activate test link; allow cancel subscription and payment method updates.

5. **Webhook:** Do **not** add it yet — you need the Worker URL from Stage 4 first.

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
npx wrangler secret put STRIPE_SECRET_KEY
npx wrangler secret put KEYGEN_ACCOUNT_ID
npx wrangler secret put KEYGEN_PRODUCT_ID
npx wrangler secret put KEYGEN_POLICY_ID
npx wrangler secret put KEYGEN_ADMIN_TOKEN
npx wrangler secret put RESEND_API_KEY
npx wrangler secret put RESEND_FROM_EMAIL
```

Skip `STRIPE_WEBHOOK_SECRET` until Stage 5.

Deploy:

```bash
npm run deploy
```

Copy the published Worker URL (e.g. `https://gmd-billing.<subdomain>.workers.dev`).

Sanity: open `https://<worker-host>/healthz` → should return `ok`.

---

## Stage 5 — Stripe webhook

1. Stripe → **Developers → Webhooks → Add endpoint**

2. **URL:** `https://<worker-host>/stripe/webhook`

3. **Events** (subscribe to these):

   - `checkout.session.completed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`
   - `charge.refunded`
   - `charge.dispute.created`

4. Save → **Reveal signing secret** (`whsec_...`)

5. Back in `server/billing`:

```bash
npx wrangler secret put STRIPE_WEBHOOK_SECRET
```

Paste `whsec_...`, then redeploy:

```bash
npm run deploy
```

---

## Stage 6 — Desktop app “Buy” link

In [`app/license_config.py`](../app/license_config.py), set `_DEFAULT_BUY_URL` to your **test** Payment Link URL (from Stage 1), or set env var `GURU_BUY_URL` at runtime. When empty, the license dialog hides the buy link.

---

## Stage 7 — End-to-end test

1. Run the app → activation dialog → **Buy one** (if URL set).

2. Complete checkout with test card `4242 4242 4242 4242`, future expiry, any CVC. Use an email that can receive mail.

3. Tail Worker logs:

```bash
cd server/billing
npm run tail
```

   Expect `POST .../stripe/webhook` → 200.

4. Check email for the license key → paste into **Activate** in the app.

---

## Stage 8 — Verify cancellation

1. Stripe (test) → **Customers** → test customer → cancel subscription (immediately).

2. Relaunch the app — license should fail validation / prompt again if suspended.

---

## Stage 9 — Going live

1. Stripe: switch to **Live mode**; recreate product, price, Payment Link, Customer Portal, and a **new** webhook endpoint pointing at your **production** Worker URL.

2. Resend: use a verified domain and production API key.

3. Worker secrets for production (example):

```bash
npx wrangler secret put STRIPE_SECRET_KEY --env production
# ... repeat each secret with --env production
npm run deploy:prod
```

4. Update `_DEFAULT_BUY_URL` (or build-time env) to the **live** Payment Link before shipping the installer.

---

## Local development (optional)

From `server/billing`:

```bash
npm run dev
```

Stripe CLI forwards webhooks to localhost:

```bash
stripe listen --forward-to localhost:8787/stripe/webhook
```

Put secrets in `server/billing/.dev.vars` (gitignored); see [`server/billing/README.md`](../server/billing/README.md).

---

## Common issues

| Symptom | Likely cause |
|---------|----------------|
| Webhook `400` / signature failed | Wrong `whsec_...` for that endpoint or test vs live mismatch |
| No email | Resend sandbox / unverified domain / recipient not allowed |
| Keygen `401` | Bad admin token or missing scopes |
| App activates but later breaks | Policy mismatch (product scope, machine limit, license auth) |
| “Buy one” missing | `BUY_URL` / `_DEFAULT_BUY_URL` empty |

---

## Related files

| File | Purpose |
|------|---------|
| [`server/billing/`](../server/billing/) | Worker source (`stripe.ts`, `keygen.ts`, `email.ts`) |
| [`server/billing/README.md`](../server/billing/README.md) | Commands, secrets list, local `.dev.vars` |
| [`app/license_config.py`](../app/license_config.py) | Keygen public IDs + `BUY_URL` / `_DEFAULT_BUY_URL` |
| [`app/license_dialog.py`](../app/license_dialog.py) | Buy link opens `BUY_URL` in browser |
