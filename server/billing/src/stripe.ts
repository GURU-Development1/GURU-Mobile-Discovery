/**
 * Stripe webhook handler. Verifies the signature using SubtleCrypto (Workers
 * runtime), then dispatches subscription lifecycle events to Keygen.
 *
 * The license <-> customer mapping is stored on the Stripe customer object as
 * `metadata.keygen_license_id`, so we never need a separate database.
 */

import Stripe from "stripe";

import { sendLicenseDelivery } from "./email";
import {
  createLicense,
  reinstateLicense,
  renewLicense,
  suspendLicense,
} from "./keygen";
import type { Env } from "./types";

const STRIPE_API_VERSION: Stripe.LatestApiVersion = "2024-06-20";

function stripeClient(env: Env): Stripe {
  return new Stripe(env.STRIPE_SECRET_KEY, {
    apiVersion: STRIPE_API_VERSION,
    httpClient: Stripe.createFetchHttpClient(),
  });
}

export async function handleStripeWebhook(
  request: Request,
  env: Env,
): Promise<Response> {
  const stripe = stripeClient(env);

  const rawBody = await request.text();
  const signature = request.headers.get("stripe-signature");
  if (!signature) {
    return new Response("Missing Stripe-Signature header", { status: 400 });
  }

  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(
      rawBody,
      signature,
      env.STRIPE_WEBHOOK_SECRET,
      undefined,
      Stripe.createSubtleCryptoProvider(),
    );
  } catch (err) {
    return new Response(
      `Webhook signature verification failed: ${(err as Error).message}`,
      { status: 400 },
    );
  }

  try {
    switch (event.type) {
      case "checkout.session.completed":
        await onCheckoutCompleted(event, stripe, env);
        break;
      case "customer.subscription.updated":
        await onSubscriptionUpdated(event, stripe, env);
        break;
      case "customer.subscription.deleted":
        await onSubscriptionDeleted(event, stripe, env);
        break;
      case "invoice.payment_succeeded":
        await onInvoicePaid(event, stripe, env);
        break;
      case "invoice.payment_failed":
        await onInvoiceFailed(event, stripe, env);
        break;
      case "charge.refunded":
      case "charge.dispute.created":
        await onChargeReversal(event, stripe, env);
        break;
      default:
        // Other events are acknowledged but not handled.
        break;
    }
  } catch (err) {
    console.error(`webhook handler failed for ${event.type}:`, err);
    return new Response("Handler error", { status: 500 });
  }

  return new Response("ok", { status: 200 });
}

/* ----------------------------- event handlers ----------------------------- */

async function onCheckoutCompleted(
  event: Stripe.Event,
  stripe: Stripe,
  env: Env,
): Promise<void> {
  const session = event.data.object as Stripe.Checkout.Session;
  if (session.mode !== "subscription") return;
  if (!session.customer || typeof session.customer !== "string") return;

  const customer = await stripe.customers.retrieve(session.customer);
  if (!customer || (customer as Stripe.DeletedCustomer).deleted) return;
  const fullCustomer = customer as Stripe.Customer;

  if (fullCustomer.metadata?.keygen_license_id) {
    // Idempotent: already created a license for this customer.
    return;
  }

  const email = fullCustomer.email || session.customer_email || "";
  if (!email) {
    console.warn(
      `checkout.session.completed: no email on customer ${fullCustomer.id}`,
    );
    return;
  }

  const license = await createLicense(env, {
    email,
    stripeCustomerId: fullCustomer.id,
  });

  await stripe.customers.update(fullCustomer.id, {
    metadata: {
      ...(fullCustomer.metadata || {}),
      keygen_license_id: license.id,
    },
  });

  await sendLicenseDelivery(env, email, license.key);
}

async function onSubscriptionUpdated(
  event: Stripe.Event,
  stripe: Stripe,
  env: Env,
): Promise<void> {
  const sub = event.data.object as Stripe.Subscription;
  const licenseId = await licenseIdForCustomer(stripe, sub.customer);
  if (!licenseId) return;

  if (sub.status === "active" || sub.status === "trialing") {
    await reinstateLicense(env, licenseId);
    return;
  }

  if (
    sub.status === "canceled" ||
    sub.status === "unpaid" ||
    sub.status === "past_due" ||
    sub.status === "incomplete_expired"
  ) {
    await suspendLicense(env, licenseId);
  }
}

async function onSubscriptionDeleted(
  event: Stripe.Event,
  stripe: Stripe,
  env: Env,
): Promise<void> {
  const sub = event.data.object as Stripe.Subscription;
  const licenseId = await licenseIdForCustomer(stripe, sub.customer);
  if (!licenseId) return;
  await suspendLicense(env, licenseId);
}

async function onInvoicePaid(
  event: Stripe.Event,
  stripe: Stripe,
  env: Env,
): Promise<void> {
  const invoice = event.data.object as Stripe.Invoice;
  // Only act on automatic renewals; the first invoice is paired with
  // checkout.session.completed which already issues a fresh license.
  if (invoice.billing_reason !== "subscription_cycle") return;

  const licenseId = await licenseIdForCustomer(stripe, invoice.customer);
  if (!licenseId) return;
  await renewLicense(env, licenseId);
}

async function onInvoiceFailed(
  event: Stripe.Event,
  stripe: Stripe,
  env: Env,
): Promise<void> {
  const invoice = event.data.object as Stripe.Invoice;
  // Only suspend once Stripe Smart Retries have given up: there is no scheduled
  // next attempt. Earlier failures are tolerated so transient card errors don't
  // briefly lock the customer out.
  if (invoice.next_payment_attempt !== null) return;

  const licenseId = await licenseIdForCustomer(stripe, invoice.customer);
  if (!licenseId) return;
  await suspendLicense(env, licenseId);
}

async function onChargeReversal(
  event: Stripe.Event,
  stripe: Stripe,
  env: Env,
): Promise<void> {
  const charge = event.data.object as Stripe.Charge;
  const licenseId = await licenseIdForCustomer(stripe, charge.customer);
  if (!licenseId) return;
  await suspendLicense(env, licenseId);
}

/* --------------------------------- helpers -------------------------------- */

async function licenseIdForCustomer(
  stripe: Stripe,
  customer: string | Stripe.Customer | Stripe.DeletedCustomer | null,
): Promise<string | null> {
  if (!customer) return null;
  const customerId = typeof customer === "string" ? customer : customer.id;
  const fetched = await stripe.customers.retrieve(customerId);
  if (!fetched || (fetched as Stripe.DeletedCustomer).deleted) return null;
  const meta = (fetched as Stripe.Customer).metadata || {};
  return meta.keygen_license_id || null;
}
