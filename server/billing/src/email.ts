/**
 * Send license key emails via Resend.
 */

import type { Env } from "./types";
import type { LicensePayload } from "./types";
import { formatExpiry } from "./license";

export async function sendLicenseEmail(
  env: Env,
  toEmail: string,
  token: string,
  payload: LicensePayload,
): Promise<void> {
  const to = toEmail.trim().toLowerCase();
  if (!to) {
    throw new Error("Missing recipient email");
  }

  const expiry = formatExpiry(payload);
  const subject = "Your GURU Mobile Discovery license key";
  const text = [
    "Thank you for subscribing to GURU Mobile Discovery.",
    "",
    "Your license key (paste this into the app when prompted):",
    "",
    token,
    "",
    `Licensed to: ${payload.email}`,
    `Valid through: ${expiry} (includes a 7-day grace period)`,
    "",
    "To activate:",
    "1. Open GURU Mobile Discovery",
    "2. Paste the license key above into the activation dialog",
    "3. Click Activate",
    "",
    "Keep this email — you'll need the key again if you reinstall.",
    "Renewals will email a fresh key before your current one expires.",
    "",
    "Questions? Reply to this email or contact support.",
  ].join("\n");

  const resp = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: env.RESEND_FROM_EMAIL,
      to: [to],
      subject,
      text,
    }),
  });

  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`Resend failed (${resp.status}): ${body.slice(0, 400)}`);
  }
}
