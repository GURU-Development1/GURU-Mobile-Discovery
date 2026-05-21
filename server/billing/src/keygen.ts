/**
 * Thin Keygen Admin API client. Uses the admin token from env (Worker secret).
 *
 * Authentication: `Authorization: Bearer <KEYGEN_ADMIN_TOKEN>`
 * Content negotiation: JSON:API via `application/vnd.api+json`.
 */

import type { Env } from "./types";

export interface KeygenLicense {
  id: string;
  key: string;
  expiry: string | null;
}

/** Metadata key stored on each license for Lemon Squeezy subscription lookup. */
export const LS_SUBSCRIPTION_METADATA_KEY = "lemonsqueezySubscriptionId";

function keygenUrl(env: Env, path: string): string {
  return `https://api.keygen.sh/v1/accounts/${env.KEYGEN_ACCOUNT_ID}${path}`;
}

function authHeaders(env: Env): Record<string, string> {
  return {
    Authorization: `Bearer ${env.KEYGEN_ADMIN_TOKEN}`,
    Accept: "application/vnd.api+json",
    "Content-Type": "application/vnd.api+json",
  };
}

async function readJson<T = unknown>(response: Response): Promise<T> {
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`Keygen ${response.status}: ${text.slice(0, 500)}`);
  }
  return (text ? JSON.parse(text) : {}) as T;
}

interface KeygenLicenseResource {
  id?: string;
  attributes?: {
    key?: string;
    expiry?: string | null;
    metadata?: Record<string, string>;
  };
}

interface KeygenLicenseListResponse {
  data?: KeygenLicenseResource[];
}

export async function createLicense(
  env: Env,
  opts: { email: string; lemonsqueezySubscriptionId: string },
): Promise<KeygenLicense> {
  const body = {
    data: {
      type: "licenses",
      attributes: {
        name: `LemonSqueezy sub ${opts.lemonsqueezySubscriptionId}`,
        metadata: {
          [LS_SUBSCRIPTION_METADATA_KEY]: opts.lemonsqueezySubscriptionId,
          email: opts.email,
        },
      },
      relationships: {
        policy: { data: { type: "policies", id: env.KEYGEN_POLICY_ID } },
      },
    },
  };

  const response = await fetch(keygenUrl(env, "/licenses"), {
    method: "POST",
    headers: authHeaders(env),
    body: JSON.stringify(body),
  });
  const payload = await readJson<{ data?: KeygenLicenseResource }>(response);
  const data = payload.data || {};
  if (!data.id || !data.attributes?.key) {
    throw new Error("Keygen createLicense: missing id/key in response");
  }
  return {
    id: data.id,
    key: data.attributes.key,
    expiry: data.attributes.expiry ?? null,
  };
}

export async function findLicenseBySubscriptionId(
  env: Env,
  subscriptionId: string,
): Promise<KeygenLicense | null> {
  const params = new URLSearchParams({
    [`metadata[${LS_SUBSCRIPTION_METADATA_KEY}]`]: subscriptionId,
    "page[size]": "1",
  });
  const response = await fetch(
    keygenUrl(env, `/licenses?${params.toString()}`),
    { headers: authHeaders(env) },
  );
  const payload = await readJson<KeygenLicenseListResponse>(response);
  const row = payload.data?.[0];
  if (!row?.id || !row.attributes?.key) {
    return null;
  }
  return {
    id: row.id,
    key: row.attributes.key,
    expiry: row.attributes.expiry ?? null,
  };
}

async function setSuspended(env: Env, licenseId: string, suspended: boolean): Promise<void> {
  const body = {
    data: {
      type: "licenses",
      id: licenseId,
      attributes: { suspended },
    },
  };
  const response = await fetch(keygenUrl(env, `/licenses/${licenseId}`), {
    method: "PATCH",
    headers: authHeaders(env),
    body: JSON.stringify(body),
  });
  await readJson(response);
}

export async function suspendLicense(env: Env, licenseId: string): Promise<void> {
  await setSuspended(env, licenseId, true);
}

export async function reinstateLicense(env: Env, licenseId: string): Promise<void> {
  await setSuspended(env, licenseId, false);
}

/**
 * Renew a license by its policy's duration. Used on subscription renewals (not
 * the initial purchase, which already gets a fresh expiry from license creation).
 */
export async function renewLicense(env: Env, licenseId: string): Promise<void> {
  const response = await fetch(
    keygenUrl(env, `/licenses/${licenseId}/actions/renew`),
    {
      method: "POST",
      headers: authHeaders(env),
    },
  );
  await readJson(response);
}
