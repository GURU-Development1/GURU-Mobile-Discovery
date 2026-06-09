/**
 * Cloudflare Worker entry point for GURU Mobile Discovery billing.
 *
 * Routes:
 *   POST /stripe/webhook  — Stripe subscription events → sign license → Resend email
 *   GET  /healthz         — liveness probe
 */

import { handleStripeWebhook, WebhookError } from "./stripe";
import type { Env } from "./types";

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/healthz") {
      return new Response("ok", { status: 200 });
    }

    if (request.method === "POST" && url.pathname === "/stripe/webhook") {
      try {
        return await handleStripeWebhook(request, env);
      } catch (err) {
        if (err instanceof WebhookError) {
          return new Response(err.message, { status: err.status });
        }
        console.error("webhook error:", err);
        return new Response("Internal error", { status: 500 });
      }
    }

    return new Response("Not found", { status: 404 });
  },
};
