# GURU Mobile Discovery — billing worker

Cloudflare Worker that turns Lemon Squeezy subscription events into Keygen license
operations and emails the license key to the buyer.

**Full step-by-step setup (Lemon Squeezy, Keygen, Resend, Worker deploy, webhooks, testing):**  
see **[docs/BILLING_SETUP.md](../../docs/BILLING_SETUP.md)** in this repo.

Single source of truth for the subscription-to-license mapping is **Keygen license
metadata** (`lemonsqueezySubscriptionId`). No database needed for v1.

## Architecture at a glance

```
Buyer -> Lemon Squeezy Checkout -> payment complete
   `--> webhook POST /lemonsqueezy/webhook -> Cloudflare Worker
        |- create Keygen license (admin API)
        |- store subscription id in license metadata
        |- send key via Resend
        `-> 200 OK
```

## One-time Lemon Squeezy setup (test mode first)

1. **Product + subscription**: in the Lemon Squeezy dashboard create a product
   "GURU Mobile Discovery" with a recurring **yearly** price.
2. **Checkout URL**: copy the product checkout link — this goes in the desktop
   app's `GURU_LEMON_SQUEEZY_CHECKOUT_URL` / [`app/license_config.py`](../../app/license_config.py)
   `LEMON_SQUEEZY_CHECKOUT_URL`. When empty, Buy shows a Coming soon dialog.
3. **Webhook endpoint**: after the Worker is deployed (next section), go to
   **Settings → Webhooks → Create webhook**. URL is the deployed Worker URL plus
   `/lemonsqueezy/webhook`. Subscribe to subscription lifecycle events (see
   [docs/BILLING_SETUP.md](../../docs/BILLING_SETUP.md)).
4. Copy the **signing secret** — used as `LEMON_SQUEEZY_WEBHOOK_SECRET`.
5. Optionally note your **Store ID** for `LEMON_SQUEEZY_STORE_ID` (ignores webhooks from other stores).

## One-time Keygen setup

These are already done in the Keygen dashboard if you followed the earlier
licensing PR. The Worker needs an **admin token** with permission to create
and modify licenses. Generate one under Account > Tokens with at least:

- `license.create`
- `license.read`
- `license.update`
- `license.update.suspend`
- `license.update.reinstate`
- `license.renew`

## Deploy

```bash
cd server/billing
npm install
npx wrangler login         # one-time
```

Set the secrets — repeat for the production environment with `--env production`:

```bash
wrangler secret put LEMON_SQUEEZY_WEBHOOK_SECRET
wrangler secret put KEYGEN_ACCOUNT_ID
wrangler secret put KEYGEN_PRODUCT_ID
wrangler secret put KEYGEN_POLICY_ID
wrangler secret put KEYGEN_ADMIN_TOKEN
wrangler secret put RESEND_API_KEY
wrangler secret put RESEND_FROM_EMAIL
# optional:
wrangler secret put LEMON_SQUEEZY_STORE_ID
```

Deploy:

```bash
npm run deploy                    # dev / default
npm run deploy:prod               # production env
```

The first deploy prints the public URL (something like
`https://gmd-billing.<your-subdomain>.workers.dev`). Use that in the Lemon Squeezy
webhook configuration above.

## Local dev

```bash
npm run dev
```

This runs `wrangler dev` on `http://localhost:8787`. Put secrets in `.dev.vars`
(Wrangler auto-loads this file for local runs). Example `.dev.vars`:

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

`.dev.vars` is gitignored. Use a tunnel to forward Lemon Squeezy webhooks to
`localhost:8787/lemonsqueezy/webhook` for local testing.

## End-to-end test

1. Open the checkout URL in your browser (test mode).
2. Watch the Worker logs: `npm run tail` (or `tail:prod`).
3. The buyer's inbox receives the license key from Resend.
4. Pasting that key into the desktop app's Activate dialog should succeed.

## Refund / cancellation behavior

| Lemon Squeezy event | Keygen action |
| --- | --- |
| `subscription_created` | create license + email key |
| `subscription_payment_success` (renewal) | renew license (extend expiry by policy duration) |
| `subscription_updated` → active/on_trial | reinstate license |
| `subscription_updated` → cancelled/expired/past_due/unpaid/paused | suspend license |
| `subscription_resumed` / `subscription_unpaused` | reinstate license |
| `subscription_cancelled` / `subscription_expired` | suspend license |
| `subscription_payment_failed` / `subscription_payment_refunded` | suspend license |

The license is **suspended**, not deleted, so a customer paying again or
resuming a subscription can be reinstated by switching the flag back.

## Types

`Env` in `src/types.ts` lists every secret the Worker reads. Adding a new
secret is: declare on `Env`, `wrangler secret put`, use via `env.NEW_SECRET`.

Reserved for future use: `LEMON_SQUEEZY_API_KEY` (Lemon Squeezy REST API calls).
