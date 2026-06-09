/**
 * Stripe webhook verification and license issuance.
 */

import type { Env } from "./types";
import { buildPayload, signLicenseToken } from "./license";
import { sendLicenseEmail } from "./email";

const HANDLED_EVENTS = new Set([
  "checkout.session.completed",
  "invoice.paid",
]);

/** Decode whsec_... to raw bytes for HMAC. */
function webhookSecretBytes(secret: string): Uint8Array {
  const trimmed = secret.trim();
  const b64 = trimmed.startsWith("whsec_") ? trimmed.slice(6) : trimmed;
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function hexToBytes(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

function timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a[i] ^ b[i];
  }
  return diff === 0;
}

async function computeStripeSignature(
  payload: string,
  timestamp: string,
  secret: string,
): Promise<Uint8Array> {
  const signedPayload = `${timestamp}.${payload}`;
  const key = await crypto.subtle.importKey(
    "raw",
    webhookSecretBytes(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(signedPayload),
  );
  return new Uint8Array(sig);
}

export async function verifyStripeWebhook(
  rawBody: string,
  signatureHeader: string | null,
  secret: string,
): Promise<unknown> {
  if (!signatureHeader) {
    throw new WebhookError("Missing Stripe-Signature header", 400);
  }

  let timestamp = "";
  const v1Signatures: string[] = [];
  for (const part of signatureHeader.split(",")) {
    const [key, value] = part.split("=", 2);
    if (key === "t") timestamp = value;
    if (key === "v1") v1Signatures.push(value);
  }
  if (!timestamp || v1Signatures.length === 0) {
    throw new WebhookError("Invalid Stripe-Signature header", 400);
  }

  const age = Math.floor(Date.now() / 1000) - parseInt(timestamp, 10);
  if (Number.isNaN(age) || age > 300) {
    throw new WebhookError("Webhook timestamp too old", 400);
  }

  const expected = await computeStripeSignature(rawBody, timestamp, secret);
  const expectedHex = [...expected].map((b) => b.toString(16).padStart(2, "0")).join("");
  const ok = v1Signatures.some((sig) => timingSafeEqual(hexToBytes(sig), hexToBytes(expectedHex)));
  if (!ok) {
    throw new WebhookError("Webhook signature verification failed", 400);
  }

  try {
    return JSON.parse(rawBody);
  } catch {
    throw new WebhookError("Invalid JSON body", 400);
  }
}

async function stripeGet<T>(env: Env, path: string): Promise<T> {
  const resp = await fetch(`https://api.stripe.com/v1${path}`, {
    headers: {
      Authorization: `Bearer ${env.STRIPE_SECRET_KEY}`,
    },
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`Stripe API ${path} failed (${resp.status}): ${body.slice(0, 400)}`);
  }
  return resp.json() as Promise<T>;
}

interface StripeSubscription {
  id: string;
  current_period_end: number;
  customer: string | { id?: string };
}

interface StripeCustomer {
  email?: string | null;
}

interface StripeCheckoutSession {
  id: string;
  subscription?: string | null;
  customer?: string | null;
  customer_details?: { email?: string | null } | null;
  customer_email?: string | null;
}

interface StripeInvoice {
  id: string;
  subscription?: string | null;
  customer?: string | null;
  customer_email?: string | null;
}

async function resolveEmail(env: Env, customerId: string | null | undefined, fallback?: string | null): Promise<string> {
  if (fallback?.trim()) {
    return fallback.trim().toLowerCase();
  }
  if (!customerId) {
    throw new Error("No customer id and no email on event");
  }
  const customer = await stripeGet<StripeCustomer>(env, `/customers/${customerId}`);
  if (!customer.email?.trim()) {
    throw new Error(`Customer ${customerId} has no email`);
  }
  return customer.email.trim().toLowerCase();
}

async function issueForSubscription(
  env: Env,
  subscriptionId: string,
  emailHint?: string | null,
  customerId?: string | null,
): Promise<{ token: string; email: string }> {
  const sub = await stripeGet<StripeSubscription>(env, `/subscriptions/${subscriptionId}`);
  const custId =
    typeof sub.customer === "string" ? sub.customer : sub.customer?.id ?? customerId ?? null;
  const email = await resolveEmail(env, custId, emailHint);
  const payload = buildPayload(email, sub.id, sub.current_period_end);
  const { token } = await signLicenseToken(env, payload);
  await sendLicenseEmail(env, email, token, payload);
  return { token, email };
}

export class WebhookError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "WebhookError";
  }
}

export async function handleStripeWebhook(request: Request, env: Env): Promise<Response> {
  const rawBody = await request.text();
  const event = (await verifyStripeWebhook(
    rawBody,
    request.headers.get("Stripe-Signature"),
    env.STRIPE_WEBHOOK_SECRET,
  )) as { type?: string; data?: { object?: Record<string, unknown> } };

  const type = event.type ?? "";
  if (!HANDLED_EVENTS.has(type)) {
    return new Response(JSON.stringify({ ok: true, skipped: type }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  const obj = event.data?.object ?? {};

  try {
    if (type === "checkout.session.completed") {
      const session = obj as unknown as StripeCheckoutSession;
      const subId = session.subscription;
      if (!subId) {
        return new Response(JSON.stringify({ ok: true, skipped: "no subscription on session" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      const emailHint =
        session.customer_details?.email ?? session.customer_email ?? null;
      const result = await issueForSubscription(env, subId, emailHint, session.customer);
      return new Response(JSON.stringify({ ok: true, event: type, email: result.email }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }

    if (type === "invoice.paid") {
      const invoice = obj as unknown as StripeInvoice;
      const subId = invoice.subscription;
      if (!subId) {
        return new Response(JSON.stringify({ ok: true, skipped: "no subscription on invoice" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      // checkout.session.completed also fires on first purchase; invoice.paid
      // covers renewals. Sending twice on initial checkout is acceptable (same exp).
      const result = await issueForSubscription(
        env,
        subId,
        invoice.customer_email,
        invoice.customer,
      );
      return new Response(JSON.stringify({ ok: true, event: type, email: result.email }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`license issuance failed (${type}):`, msg);
    return new Response(JSON.stringify({ ok: false, error: msg }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }

  return new Response(JSON.stringify({ ok: true }), { status: 200 });
}
