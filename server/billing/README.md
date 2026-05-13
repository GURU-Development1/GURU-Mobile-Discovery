# GURU Mobile Discovery — billing worker

Cloudflare Worker that turns Stripe subscription events into Keygen license
operations and emails the license key to the buyer.

**Full step-by-step setup (Stripe, Keygen, Resend, Worker deploy, webhooks, testing):**  
see **[docs/BILLING_SETUP.md](../../docs/BILLING_SETUP.md)** in this repo.

Single source of truth for the customer-to-license mapping is **Stripe customer
metadata** (`metadata.keygen_license_id`). No database needed for v1.

## Architecture at a glance

```
Buyer -> Stripe Payment Link -> Stripe checkout
   `--> webhook POST /stripe/webhook -> Cloudflare Worker
        |- create Keygen license (admin API)
        |- save license_id to stripe.customers.metadata
        |- send key via Resend
        `-> 200 OK
```

## One-time Stripe setup (test mode first)

1. **Product + Price**: in the Stripe dashboard create a product
   "GURU Mobile Discovery" with a recurring **yearly** price.
2. **Payment Link**: under Payment Links, create one for that price. Enable
   "Allow customers to update subscription quantities" off, "Allow promotion
   codes" on if you want coupons, and save the URL — this goes in the desktop
   app's `GURU_BUY_URL` / `app/license_config.py` `BUY_URL`.
3. **Customer Portal**: under Billing > Customer Portal, enable cancellations
   and payment-method updates. Note the portal URL or build a redirect later.
4. **Webhook endpoint**: after the Worker is deployed (next section), come
   back here. Developers > Webhooks > Add endpoint. URL is the deployed
   Worker URL plus `/stripe/webhook`. Subscribe to these events:
   - `checkout.session.completed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`
   - `charge.refunded`
   - `charge.dispute.created`

   Copy the **Signing secret** (`whsec_...`) — used as `STRIPE_WEBHOOK_SECRET`.

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
wrangler secret put STRIPE_SECRET_KEY
wrangler secret put STRIPE_WEBHOOK_SECRET
wrangler secret put KEYGEN_ACCOUNT_ID
wrangler secret put KEYGEN_PRODUCT_ID
wrangler secret put KEYGEN_POLICY_ID
wrangler secret put KEYGEN_ADMIN_TOKEN
wrangler secret put RESEND_API_KEY
wrangler secret put RESEND_FROM_EMAIL
```

Deploy:

```bash
npm run deploy                    # dev / default
npm run deploy:prod               # production env
```

The first deploy prints the public URL (something like
`https://gmd-billing.<your-subdomain>.workers.dev`). Use that in the Stripe
webhook configuration above.

## Local dev

```bash
npm run dev
```

This runs `wrangler dev` on `http://localhost:8787`. Forward Stripe events
locally with the [Stripe CLI](https://docs.stripe.com/stripe-cli):

```bash
stripe listen --forward-to localhost:8787/stripe/webhook
```

The CLI prints a `whsec_...` signing secret; set it in `.dev.vars` (Wrangler
auto-loads this file for local runs) as `STRIPE_WEBHOOK_SECRET`. Example
`.dev.vars`:

```
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
KEYGEN_ACCOUNT_ID=...
KEYGEN_PRODUCT_ID=...
KEYGEN_POLICY_ID=...
KEYGEN_ADMIN_TOKEN=...
RESEND_API_KEY=re_...
RESEND_FROM_EMAIL=licenses@example.com
```

`.dev.vars` is gitignored.

## End-to-end test

1. Open the Payment Link in your browser; use card `4242 4242 4242 4242`,
   any future expiry, any CVC.
2. Watch the Worker logs: `npm run tail` (or `tail:prod`).
3. The buyer's inbox receives the license key from Resend.
4. Pasting that key into the desktop app's Activate dialog should succeed.

## Refund / cancellation behavior

| Stripe event | Keygen action |
| --- | --- |
| `customer.subscription.deleted` | suspend license |
| `customer.subscription.updated` → active/trialing | reinstate license |
| `customer.subscription.updated` → canceled/unpaid/past_due/incomplete_expired | suspend license |
| `invoice.payment_failed` (no further retry scheduled) | suspend license |
| `invoice.payment_succeeded` (renewal cycle) | renew license (extend expiry by policy duration) |
| `charge.refunded` / `charge.dispute.created` | suspend license |

The license is **suspended**, not deleted, so a customer paying again or
having a refund reversed can be reinstated by switching the flag back.

## Types

`Env` in `src/types.ts` lists every secret the Worker reads. Adding a new
secret is: declare on `Env`, `wrangler secret put`, use via `env.NEW_SECRET`.
