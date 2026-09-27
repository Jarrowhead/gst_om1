/**
 * Routing-only guard — FRONTEND_SPECIFICATION.md §4: middleware handles
 * routing, never authorization (the server is the authority). The gst_role
 * cookie is a non-sensitive hint written after /auth/me role resolution.
 */
import { NextRequest, NextResponse } from "next/server";

const PUBLIC_PATHS = ["/login", "/register", "/totp"];
const ONBOARDING_PATHS = ["/totp"]; // TOTP setup must stay reachable during CA onboarding

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

function isOnboarding(pathname: string): boolean {
  return ONBOARDING_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

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