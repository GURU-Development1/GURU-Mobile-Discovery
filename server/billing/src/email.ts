/**
 * License-delivery email via Resend.
 *
 * The buyer receives the license key after a successful Lemon Squeezy purchase.
 * Plain text and HTML variants are sent so the message renders cleanly in clients
 * that block remote content.
 */

import { Resend } from "resend";
import type { Env } from "./types";

export async function sendLicenseDelivery(
  env: Env,
  to: string,
  licenseKey: string,
): Promise<void> {
  const resend = new Resend(env.RESEND_API_KEY);
  const subject = "Your GURU Mobile Discovery license key";
  const html = renderLicenseEmailHtml(licenseKey);
  const text = renderLicenseEmailText(licenseKey);

  const { error } = await resend.emails.send({
    from: env.RESEND_FROM_EMAIL,
    to,
    subject,
    html,
    text,
  });
  if (error) {
    const message = (error as { message?: string }).message || JSON.stringify(error);
    throw new Error(`Resend send failed: ${message}`);
  }
}

function renderLicenseEmailHtml(key: string): string {
  return [
    "<!doctype html>",
    '<html><body style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1f2937;padding:24px;max-width:560px;margin:0 auto;">',
    '  <h2 style="color:#0c1a3a;">Thanks for your purchase</h2>',
    "  <p>Your GURU Mobile Discovery license key is below. Open the app, choose",
    "  <strong>Activate License</strong>, paste the key, and click Activate.</p>",
    '  <p style="background:#f6f8fb;padding:14px;border-radius:8px;border:1px solid #e1e6ed;font-family:Consolas,Menlo,monospace;font-size:15px;word-break:break-all;">',
    `    ${escapeHtml(key)}`,
    "  </p>",
    "  <p>One device per license. You can deactivate and move to another device at any time from",
    "  <strong>Help &rarr; Deactivate This Device</strong> inside the app.</p>",
    '  <p style="color:#6b7280;">Need help? Reply to this email and we will get back to you.</p>',
    "</body></html>",
  ].join("\n");
}

function renderLicenseEmailText(key: string): string {
  return [
    "Thanks for your purchase!",
    "",
    "Your GURU Mobile Discovery license key:",
    "",
    `  ${key}`,
    "",
    "Open the app, choose Activate License, paste the key, and click Activate.",
    "One device per license; deactivate any time from Help > Deactivate This Device.",
  ].join("\n");
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
