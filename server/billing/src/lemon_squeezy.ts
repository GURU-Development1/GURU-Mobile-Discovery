/**
 * Lemon Squeezy webhook handler. Verifies HMAC-SHA256 signatures (Web Crypto),
 * then dispatches subscription lifecycle events to Keygen.
 *
 * The license ↔ subscription mapping is stored in Keygen license metadata
 * (`lemonsqueezySubscriptionId`), so no separate database is needed.
 */

import { sendLicenseDelivery } from "./email";
import {
  createLicense,
  findLicenseBySubscriptionId,
  reinstateLicense,
  renewLicense,
  suspendLicense,
} from "./keygen";
import type { Env } from "./types";

interface LsWebhookPayload {
  meta?: {
    event_name?: string;
    custom_data?: Record<string, unknown>;
  };
  data?: {
    type?: string;
    id?: string;
    attributes?: Record<string, unknown>;
  };
}

export async function handleLemonSqueezyWebhook(
  request: Request,
  env: Env,
): Promise<Response> {
  const rawBody = await request.text();
  const signature = request.headers.get("X-Signature");
  if (!signature) {
    return new Response("Missing X-Signature header", { status: 400 });
  }

  const valid = await verifySignature(rawBody, signature, env.LEMON_SQUEEZY_WEBHOOK_SECRET);
  if (!valid) {
    return new Response("Webhook signature verification failed", { status: 400 });
  }

  let payload: LsWebhookPayload;
  try {
    payload = JSON.parse(rawBody) as LsWebhookPayload;
  } catch {
    return new Response("Invalid JSON body", { status: 400 });
  }

  const eventName =
    request.headers.get("X-Event-Name") ||
    payload.meta?.event_name ||
    "";

  try {
    switch (eventName) {
      case "subscription_created":
        await onSubscriptionCreated(payload, env);
        break;
      case "subscription_payment_success":
        await onSubscriptionPaymentSuccess(payload, env);
        break;
      case "subscription_updated":
        await onSubscriptionUpdated(payload, env);
        break;
      case "subscription_resumed":
      case "subscription_unpaused":
        await onSubscriptionReinstated(payload, env);
        break;
      case "subscription_cancelled":
      case "subscription_expired":
      case "subscription_payment_failed":
      case "subscription_payment_refunded":
        await onSubscriptionSuspended(payload, env);
        break;
      default:
        // Other events are acknowledged but not handled.
        break;
    }
  } catch (err) {
    console.error(`webhook handler failed for ${eventName}:`, err);
    return new Response("Handler error", { status: 500 });
  }

  return new Response("ok", { status: 200 });
}

/* ----------------------------- event handlers ----------------------------- */

async function onSubscriptionCreated(
  payload: LsWebhookPayload,
  env: Env,
): Promise<void> {
  const subscriptionId = payload.data?.id;
  const attrs = payload.data?.attributes || {};
  if (!subscriptionId) return;
  if (!storeMatches(env, attrs)) return;

  const existing = await findLicenseBySubscriptionId(env, subscriptionId);
  if (existing) return;

  const email = String(attrs.user_email || attrs.user_name || "").trim();
  if (!email || !email.includes("@")) {
    console.warn(
      `subscription_created: no email on subscription ${subscriptionId}`,
    );
    return;
  }

  const license = await createLicense(env, {
    email,
    lemonsqueezySubscriptionId: subscriptionId,
  });
  await sendLicenseDelivery(env, email, license.key);
}

async function onSubscriptionPaymentSuccess(
  payload: LsWebhookPayload,
  env: Env,
): Promise<void> {
  const subscriptionId = subscriptionIdFromPayload(payload);
  if (!subscriptionId) return;
  if (!storeMatches(env, payload.data?.attributes || {})) return;

  const license = await findLicenseBySubscriptionId(env, subscriptionId);
  if (!license) return;

  // Only renew on explicit renewal payments; initial purchase is handled by
  // subscription_created (which already sets expiry from the Keygen policy).
  const billingReason = String(payload.data?.attributes?.billing_reason || "").toLowerCase();
  if (billingReason !== "renewal") return;

  await renewLicense(env, license.id);
}

async function onSubscriptionUpdated(
  payload: LsWebhookPayload,
  env: Env,
): Promise<void> {
  const subscriptionId = subscriptionIdFromPayload(payload);
  if (!subscriptionId) return;
  if (!storeMatches(env, payload.data?.attributes || {})) return;

  const license = await findLicenseBySubscriptionId(env, subscriptionId);
  if (!license) return;

  const status = String(payload.data?.attributes?.status || "").toLowerCase();
  if (status === "active" || status === "on_trial") {
    await reinstateLicense(env, license.id);
    return;
  }

  if (
    status === "cancelled" ||
    status === "expired" ||
    status === "past_due" ||
    status === "unpaid" ||
    status === "paused"
  ) {
    await suspendLicense(env, license.id);
  }
}

async function onSubscriptionReinstated(
  payload: LsWebhookPayload,
  env: Env,
): Promise<void> {
  const subscriptionId = subscriptionIdFromPayload(payload);
  if (!subscriptionId) return;
  if (!storeMatches(env, payload.data?.attributes || {})) return;

  const license = await findLicenseBySubscriptionId(env, subscriptionId);
  if (!license) return;
  await reinstateLicense(env, license.id);
}

async function onSubscriptionSuspended(
  payload: LsWebhookPayload,
  env: Env,
): Promise<void> {
  const subscriptionId = subscriptionIdFromPayload(payload);
  if (!subscriptionId) return;
  if (!storeMatches(env, payload.data?.attributes || {})) return;

  const license = await findLicenseBySubscriptionId(env, subscriptionId);
  if (!license) return;
  await suspendLicense(env, license.id);
}

/* --------------------------------- helpers -------------------------------- */

function subscriptionIdFromPayload(payload: LsWebhookPayload): string | null {
  if (payload.data?.type === "subscriptions" && payload.data.id) {
    return payload.data.id;
  }
  const subId = payload.data?.attributes?.subscription_id;
  return subId != null ? String(subId) : null;
}

function storeMatches(env: Env, attrs: Record<string, unknown>): boolean {
  if (!env.LEMON_SQUEEZY_STORE_ID) return true;
  const storeId = attrs.store_id;
  return String(storeId ?? "") === env.LEMON_SQUEEZY_STORE_ID;
}

async function verifySignature(
  rawBody: string,
  signature: string,
  secret: string,
): Promise<boolean> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const digest = await crypto.subtle.sign("HMAC", key, encoder.encode(rawBody));
  const hex = [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return timingSafeEqual(hex, signature.trim());
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}
