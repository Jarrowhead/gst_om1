/**
 * RFC 6238 TOTP code generation for E2E (mirrors backend pyotp semantics:
 * base32 secret, SHA-1, 30s step, 6 digits, ±1 window).
 */
import crypto from "node:crypto";

/**
 * Decode a base32 TOTP secret to bytes (RFC 4648, ignore padding and bad chars).
 *
 * Flow:
 *   1. Strip trailing '=' and uppercase.
 *   2. Accumulate 5-bit indexes; emit a byte whenever 8 bits are ready.
 *
 * Debug:
 *   Empty output → secret had no valid alphabet characters.
 */
function base32Decode(input: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = 0;
  let value = 0;
  const out: number[] = [];
  for (const ch of input.replace(/=+$/, "").toUpperCase()) {
    const idx = alphabet.indexOf(ch);
    if (idx === -1) continue;
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(out);
}

/**
 * 6-digit TOTP for E2E. SHA-1, 30s step, optional window offset.
 *
 * Flow:
 *   1. counter = floor(time/30) + window.
 *   2. HMAC-SHA1 of the 8-byte counter with the decoded secret.
 *   3. Dynamic truncation, mod 1_000_000, zero-pad.
 *
 * Debug:
 *   Backend valid_window is ±1. If verify fails, retry with window -1 or +1 near the boundary.
 */
export function totpCode(secret: string, forTime = Date.now(), window = 0): string {
  const counter = Math.floor(forTime / 1000 / 30) + window;
  const buf = Buffer.alloc(8);
  buf.writeUInt32BE(Math.floor(counter / 2 ** 32), 0);
  buf.writeUInt32BE(counter >>> 0, 4);
  const hmac = crypto.createHmac("sha1", base32Decode(secret)).update(buf).digest();
  const offset = hmac[hmac.length - 1] & 0x0f;
  const bin =
    ((hmac[offset] & 0x7f) << 24) |
    (hmac[offset + 1] << 16) |
    (hmac[offset + 2] << 8) |
    hmac[offset + 3];
  return String(bin % 1_000_000).padStart(6, "0");
}