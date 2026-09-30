/**
 * GSTIN/PAN client-side validation — FRONTEND_SPECIFICATION.md §4 mirror of
 * the backend validator (backend always re-validates; this is instant UX).
 * Mod-36 checksum per GST law (same algorithm as
 * scripts/measure_extraction.py:gstin_checksum_valid / backend gstin.py).
 */

const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$/;
const PAN_RE = /^[A-Z]{5}[0-9]{4}[A-Z]$/;

const CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";

/**
 * True when the string matches the 15-char GSTIN shape (not the checksum).
 *
 * Flow:
 *   1. Uppercase and trim.
 *   2. Test GSTIN_RE (state, PAN, entity Z, checksum slot).
 *
 * Debug:
 *   Format true but checksum false → gstinChecksumValid. Backend still re-checks.
 */
export function gstinFormatValid(gstin: string): boolean {
  return GSTIN_RE.test(gstin.toUpperCase().trim());
}

/**
 * True when the string matches PAN shape AAAAA9999A.
 *
 * Flow:
 *   Uppercase, trim, PAN_RE.
 *
 * Debug:
 *   UI can pass format and still 422 if the server PAN cross-check fails.
 */
export function panFormatValid(pan: string): boolean {
  return PAN_RE.test(pan.toUpperCase().trim());
}

/**
 * Mod-36 checksum: char value × weight 1/2, folded, complement compared to last char.
 *
 * Flow:
 *   1. Reject bad format.
 *   2. Sum fold(value * weight) over the first 14 chars.
 *   3. Check digit is CHARSET[(36 - sum%36) % 36].
 *
 * Debug:
 *   Must match backend/app/core/businesses/gstin.py, not the older harness helper.
 */
export function gstinChecksumValid(gstin: string): boolean {
  const g = gstin.toUpperCase().trim();
  if (!GSTIN_RE.test(g)) return false;
  let sum = 0;
  for (let i = 0; i < 14; i += 1) {
    const value = CHARSET.indexOf(g[i]);
    const place = i % 2 === 0 ? 1 : 2;
    const product = value * place;
    // fold: quotient + remainder (value*2 may exceed 35)
    sum += Math.floor(product / 36) + (product % 36);
  }
  const check = (36 - (sum % 36)) % 36;
  return CHARSET[check] === g[14];
}

/**
 * PAN embedded in a GSTIN must equal GSTIN slice [2, 12).
 *
 * Flow:
 *   Uppercase both sides and compare. Does not check the checksum.
 *
 * Debug:
 *   Backend error 'positions 3-12' is the same slice (1-based wording).
 */
export function gstinPanMatches(gstin: string, pan: string): boolean {
  return gstin.toUpperCase().trim().slice(2, 12) === pan.toUpperCase().trim();
}