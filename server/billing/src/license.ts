/**
 * Ed25519 license token signing per LICENSE_SPEC.md.
 *
 * Signs the raw UTF-8 JSON bytes once — never re-serializes after signing.
 */

import type { Env, LicensePayload } from "./types";

const GRACE_SECONDS = 7 * 24 * 60 * 60;

function b64url(data: Uint8Array): string {
  let binary = "";
  for (const byte of data) {
    binary += String.fromCharCode(byte);
  }
  const b64 = btoa(binary);
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64Decode(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

let _cachedKey: CryptoKey | null = null;
let _cachedKeyMaterial = "";

async function importSigningKey(env: Env): Promise<CryptoKey> {
  const material = env.LICENSE_SIGNING_KEY.trim();
  if (_cachedKey && _cachedKeyMaterial === material) {
    return _cachedKey;
  }
  const der = b64Decode(material);
  _cachedKey = await crypto.subtle.importKey(
    "pkcs8",
    der,
    { name: "Ed25519" },
    false,
    ["sign"],
  );
  _cachedKeyMaterial = material;
  return _cachedKey;
}

export function buildPayload(
  email: string,
  subscriptionId: string,
  periodEndUnix: number,
): LicensePayload {
  const now = Math.floor(Date.now() / 1000);
  return {
    v: 1,
    product: "gmd",
    plan: "annual",
    email: email.trim().toLowerCase(),
    sub: subscriptionId,
    iss: now,
    exp: periodEndUnix + GRACE_SECONDS,
  };
}

/** JSON.stringify once; sign those exact bytes. */
export async function signLicenseToken(
  env: Env,
  payload: LicensePayload,
): Promise<{ token: string; payloadBytes: Uint8Array }> {
  const json = JSON.stringify(payload);
  const payloadBytes = new TextEncoder().encode(json);
  const key = await importSigningKey(env);
  const signature = await crypto.subtle.sign({ name: "Ed25519" }, key, payloadBytes);
  const token = `${b64url(payloadBytes)}.${b64url(new Uint8Array(signature))}`;
  return { token, payloadBytes };
}

export function formatExpiry(payload: LicensePayload): string {
  return new Date(payload.exp * 1000).toISOString().slice(0, 10);
}
