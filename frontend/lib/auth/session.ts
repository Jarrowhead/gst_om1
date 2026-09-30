/**
 * Session store (module scope, client-side): access JWT in memory only
 * (SECURITY §6: no tokens in localStorage), role in a non-sensitive,
 * non-httpOnly routing cookie. The backend remains the authorization
 * authority — FRONTEND_SPECIFICATION.md §4.
 */
import type { MeDto } from "@/lib/api/client";

export type Role = "CLIENT" | "CA";

const ROLE_COOKIE = "gst_role";

/**
 * Write the gst_role routing cookie. Not an auth token.
 *
 * Flow:
 *   document.cookie gst_role=CLIENT|CA, path=/, 7 days, samesite=lax.
 *
 * Debug:
 *   Middleware redirects use this cookie. Shells must still call /auth/me.
 */
export function setRoleCookie(role: Role): void {
  // routing hint only — never used for authorization
  document.cookie = `${ROLE_COOKIE}=${role}; path=/; samesite=lax; max-age=${7 * 24 * 3600}`;
}

/**
 * Read gst_role from document.cookie.
 *
 * Flow:
 *   Find gst_role=; return CLIENT or CA, else null (missing or garbage).
 *
 * Debug:
 *   null sends middleware to /login. A forged value is not trusted by the API.
 */
export function readRoleCookie(): Role | null {
  const match = document.cookie
    .split("; ")
    .find((c) => c.startsWith(`${ROLE_COOKIE}=`));
  if (!match) return null;
  const value = match.split("=")[1];
  return value === "CLIENT" || value === "CA" ? value : null;
}

/**
 * Drop the role cookie. Does not clear the in-memory access token.
 *
 * Flow:
 *   Set gst_role with max-age=0.
 *
 * Debug:
 *   Sign-out must also setAccessToken(null) or the next apiFetch still sends Bearer.
 */
export function clearSession(): void {
  document.cookie = `${ROLE_COOKIE}=; path=/; max-age=0`;
}

/**
 * Resolve the effective role from /auth/me (server truth), not the cookie.
 *
 * Flow:
 *   firm !== null → CA, else CLIENT.
 *
 * Debug:
 *   CA registration before firm create stays CLIENT and lands on /app.
 */
export function resolveRole(me: MeDto): Role {
  return me.firm !== null ? "CA" : "CLIENT";
}