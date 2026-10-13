import { describe, it, expect, beforeEach } from "vitest";
import {
  setSessionCookie,
  readSessionCookie,
  clearSession,
} from "@/lib/auth/session";

describe("Client-Side Session Cookie Management", () => {
  beforeEach(() => {
    // Clear cookies in jsdom before each test
    document.cookie.split(";").forEach((cookie) => {
      const eqPos = cookie.indexOf("=");
      const name = eqPos > -1 ? cookie.substr(0, eqPos) : cookie;
      document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/`;
    });
  });

  it("reads false when no session cookie exists", () => {
    expect(readSessionCookie()).toBe(false);
  });

  it("sets and reads session cookie properly", () => {
    setSessionCookie();
    expect(readSessionCookie()).toBe(true);
    expect(document.cookie).toContain("gst_session=1");
  });

  it("clears session cookie on logout", () => {
    setSessionCookie();
    expect(readSessionCookie()).toBe(true);
    clearSession();
    expect(readSessionCookie()).toBe(false);
  });
});
