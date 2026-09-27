/**
 * Session store (module scope, client-side): access JWT in memory only
 * (SECURITY §6: no tokens in localStorage), role in a non-sensitive,
 * non-httpOnly routing cookie. The backend remains the authorization
 * authority — FRONTEND_SPECIFICATION.md §4.
 */
import type { MeDto } from "@/lib/api/client";

export type Role = "CLIENT" | "CA";

const ROLE_COOKIE = "gst_role";

export function setRoleCookie(role: Role): void {
  // routing hint only — never used for authorization
  document.cookie = `${ROLE_COOKIE}=${role}; path=/; samesite=lax; max-age=${7 * 24 * 3600}`;
}

export function readRoleCookie(): Role | null {
  const match = document.cookie
    .split("; ")
    .find((c) => c.startsWith(`${ROLE_COOKIE}=`));
  if (!match) return null;
  const value = match.split("=")[1];
  return value === "CLIENT" || value === "CA" ? value : null;
}

export function clearSession(): void {
  document.cookie = `${ROLE_COOKIE}=; path=/; max-age=0`;
}

/** Resolve the effective role from /auth/me (server truth), not the cookie. */
export function resolveRole(me: MeDto): Role {
  return me.firm !== null ? "CA" : "CLIENT";
}