/**
 * Routing-only guard — FRONTEND_SPECIFICATION.md §4: middleware handles
 * routing, never authorization (the server is the authority). The gst_role
 * cookie is a non-sensitive hint written after /auth/me role resolution.
 */
import { NextRequest, NextResponse } from "next/server";

const PUBLIC_PATHS = ["/login", "/register", "/totp"];
const ONBOARDING_PATHS = ["/totp"]; // TOTP setup must stay reachable during CA onboarding

/**
 * True for /login, /register, /totp (and nested paths).
 *
 * Flow:
 *   Match pathname exactly or as a prefix of PUBLIC_PATHS.
 *
 * Debug:
 *   A new public page must be added to PUBLIC_PATHS or guests get sent to login.
 */
function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/**
 * True for paths that stay reachable after a role cookie exists (/totp).
 *
 * Flow:
 *   Match pathname exactly or as a prefix of ONBOARDING_PATHS.
 *
 * Debug:
 *   Without this, a CA mid-setup is bounced from /totp to /.
 */
function isOnboarding(pathname: string): boolean {
  return ONBOARDING_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/**
 * Route by gst_role cookie. Does not check the access token.
 *
 * Flow:
 *   1. Public path + role cookie + not onboarding → redirect /.
 *   2. Public path otherwise → next.
 *   3. No role cookie → /login?next=<path>.
 *   4. Role present → next. Shell pages re-check the session.
 *
 * Debug:
 *   Loop between / and /login → cookie set but shell silentRefresh failed.
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const role = request.cookies.get("gst_role")?.value ?? null;

  if (isPublic(pathname)) {
    // Authenticated users skip the auth screens, except onboarding flows
    // (e.g. CA TOTP setup) that must remain reachable mid-onboarding.
    if ((role === "CLIENT" || role === "CA") && !isOnboarding(pathname)) {
      return NextResponse.redirect(new URL("/", request.url));
    }
    return NextResponse.next();
  }

  if (role === null) {
    // navigation sugar only — the cookie is client-writable, so shells re-verify via silentRefresh() + /auth/me (item 3).
    const login = new URL("/login", request.url);
    login.searchParams.set("next", pathname);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  matcher: [
    // everything except Next internals and the proxied API
    "/((?!api/v1|_next/static|_next/image|favicon.ico).*)",
  ],
};