/**
 * Cloudflare Worker entry point for the GURU Mobile Discovery billing pipeline.
 *
 * Routes:
 *   POST /stripe/webhook  - Stripe -> Keygen license operations + email delivery
 *   GET  /healthz         - liveness probe
 */

import { handleStripeWebhook } from "./stripe";
import type { Env } from "./types";

export default {
  async fetch(request: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/healthz") {
      return new Response("ok", { status: 200 });
    }

    if (request.method === "POST" && url.pathname === "/stripe/webhook") {
      return handleStripeWebhook(request, env);
    }

    return new Response("Not found", { status: 404 });
  },
};
