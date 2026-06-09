/**
 * Cloudflare Worker environment (secrets + bindings).
 *
 * Set via `wrangler secret put <NAME>` or `.dev.vars` for local dev.
 */
export interface Env {
  /** Ed25519 PKCS8 DER private key, base64 (NOT the SPKI public key). */
  LICENSE_SIGNING_KEY: string;
  /** Stripe webhook signing secret (whsec_...). */
  STRIPE_WEBHOOK_SECRET: string;
  /** Stripe secret API key (sk_test_... / sk_live_...). */
  STRIPE_SECRET_KEY: string;
  /** Resend API key (re_...). */
  RESEND_API_KEY: string;
  /** Verified sender, e.g. licenses@yourdomain.com */
  RESEND_FROM_EMAIL: string;
}

export interface LicensePayload {
  v: 1;
  product: "gmd";
  plan: "annual";
  email: string;
  sub: string;
  iss: number;
  exp: number;
}
