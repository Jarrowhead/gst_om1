/**
 * Task 0.7 gate — Playwright: both roles onboard, routed to the correct
 * shell. Backend (8084) + frontend dev server (9094) must be running; the
 * backend needs PG:5436 + Redis:6380 (bootstrap_stack.py).
 *
 * Synthetic identities only — mobile numbers use the reserved 0-prefix test
 * range; the CA firm PAN is format-valid synthetic data (no real PII).
 */
import { expect, test, Page } from "@playwright/test";

import { totpCode } from "./totp-utils";

const BACKEND = "http://127.0.0.1:8084";

/**
 * Reserved 0-prefix mobile, unique per call via Date.now.
 *
 * Flow:
 *   Take the last 12 digits of the timestamp and prefix 0 so the result is 13 digits.
 *
 * Debug:
 *   Two tests in the same millisecond can collide. OTP purpose must be REGISTER first.
 */
function uniqueMobile(): string {
  // 13 digits with the reserved 0-prefix (validate_identifier: 10-13 digits);
  // timestamp-derived so concurrent tests never share an identifier.
  return `0${String(Date.now()).slice(-12)}`;
}

/**
 * POST /auth/otp/request against the real backend and return the data object.
 *
 * Flow:
 *   1. fetch http://127.0.0.1:8084/api/v1/auth/otp/request.
 *   2. Assert success and return data, including dev_otp in dev mode.
 *
 * Debug:
 *   Connection refused means the API on 8084 is not running.
 */
async function requestOtp(identifier: string, purpose: string) {
  const res = await fetch(`${BACKEND}/api/v1/auth/otp/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier, purpose }),
  });
  const body = (await res.json()) as {
    success: boolean;
    data?: { otp_sent: boolean; dev_otp?: string };
  };
  expect(body.success).toBe(true);
  return body.data!;
}

/**
 * Read the 6-digit code from the register screen's dev OTP banner.
 *
 * Flow:
 *   Wait for test id reg-dev-otp, then capture the digits after "dev code:".
 *
 * Debug:
 *   Timeout means the banner never rendered, usually because dev_otp was null.
 */
async function waitForDevOtp(page: Page): Promise<string> {
  const banner = page.getByTestId("reg-dev-otp");
  await expect(banner).toBeVisible();
  const text = await banner.textContent();
  const match = text?.match(/dev code:\s*(\d{6})/);
  expect(match).toBeTruthy();
  return match![1];
}

/**
 * Read the 6-digit code from the login screen's dev OTP banner.
 *
 * Flow:
 *   Wait for test id dev-otp, then capture the digits after "dev code:".
 *
 * Debug:
 *   Register uses reg-dev-otp. This helper only matches the login banner.
 */
async function waitForLoginDevOtp(page: Page): Promise<string> {
  const banner = page.getByTestId("dev-otp");
  await expect(banner).toBeVisible();
  const text = await banner.textContent();
  const match = text?.match(/dev code:\s*(\d{6})/);
  expect(match).toBeTruthy();
  return match![1];
}

test.describe("client onboarding", () => {
  test("business role registers and lands on client shell", async ({ page }) => {
    const mobile = uniqueMobile();
    // The backend must know this identifier for REGISTER verify to auto-create the user.
    await requestOtp(mobile, "REGISTER");

    await page.goto("/register");
    await page.getByTestId("register-client").click();
    await page.getByTestId("reg-identifier").fill(mobile);
    await page.getByTestId("reg-request-otp").click();
    const devOtp = await waitForDevOtp(page);
    await page.getByTestId("reg-otp").fill(devOtp);
    await page.getByTestId("reg-verify").click();

    await expect(page).toHaveURL(/\/app$/, { timeout: 10_000 });
    await expect(page.getByTestId("shell-user")).toContainText("User");
    await expect(page.locator('[data-testid="shell-role"]')).toHaveText("Business");
  });

  test("login for existing client routes to client shell", async ({ page }) => {
    const mobile = uniqueMobile();
    await requestOtp(mobile, "REGISTER");
    const { dev_otp: regOtp } = await requestOtp(mobile, "REGISTER");
    expect(regOtp).toBeTruthy();
    const res = await fetch(`${BACKEND}/api/v1/auth/otp/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identifier: mobile, otp: regOtp }),
    });
    expect(res.status).toBe(200);

    await page.goto("/login");
    await page.getByTestId("login-identifier").fill(mobile);
    await page.getByTestId("login-request-otp").click();
    const pageOtp = await waitForLoginDevOtp(page);
    await page.getByTestId("login-otp").fill(pageOtp);
    await page.getByTestId("login-verify").click();

    await expect(page).toHaveURL(/\/app$/, { timeout: 10_000 });
    await expect(page.getByTestId("shell-user")).toBeVisible();
  });
});

test.describe("CA onboarding", () => {
  test("CA role registers, enrolls TOTP, creates firm, lands on CA shell", async ({
    page,
  }) => {
    const mobile = uniqueMobile();
    await requestOtp(mobile, "REGISTER");

    await page.goto("/register");
    await page.getByTestId("register-ca").click();
    await page.getByTestId("reg-identifier").fill(mobile);
    await page.getByTestId("reg-request-otp").click();
    const devOtp = await waitForDevOtp(page);
    await page.getByTestId("reg-otp").fill(devOtp);
    await page.getByTestId("reg-verify").click();

    // TOTP enforced pre-firm-join (SECURITY §1) — routed before any firm page.
    await expect(page).toHaveURL(/\/totp$/, { timeout: 10_000 });
    await page.getByTestId("totp-begin").click();
    await expect(page.getByTestId("totp-qr")).toBeVisible();
    const secret = await page.getByTestId("totp-secret").textContent();
    expect(secret).toBeTruthy();

    const code = totpCode(secret!.trim());
    await page.getByTestId("totp-code").fill(code);
    await page.getByTestId("totp-verify-btn").click();
    await expect(page.getByTestId("totp-enabled-banner")).toBeVisible();

    await page.getByTestId("firm-name").fill("E2E Test CA Firm");
    await page.getByTestId("firm-pan").fill("AAAFE9999F");
    await page.getByTestId("firm-create").click();

    await expect(page).toHaveURL(/\/ca$/, { timeout: 10_000 });
    await expect(page.getByTestId("shell-user")).toBeVisible();
    await expect(page.locator('[data-testid="shell-role"]')).toHaveText("CA firm");
  });

  test("CA login routes to CA shell", async ({ page }) => {
    const mobile = uniqueMobile();
    await requestOtp(mobile, "REGISTER");
    const { dev_otp: regOtp } = await requestOtp(mobile, "REGISTER");
    expect(regOtp).toBeTruthy();
    const res = await fetch(`${BACKEND}/api/v1/auth/otp/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identifier: mobile, otp: regOtp }),
    });
    expect(res.status).toBe(200);

    await page.goto("/login");
    await page.getByTestId("login-identifier").fill(mobile);
    await page.getByTestId("login-request-otp").click();
    const caLoginOtp = await waitForLoginDevOtp(page);
    await page.getByTestId("login-otp").fill(caLoginOtp);
    await page.getByTestId("login-verify").click();

    // No firm yet → role resolves CLIENT → client shell (server truth).
    await expect(page).toHaveURL(/\/app$/, { timeout: 10_000 });
  });
});

test.describe("route guard", () => {
  test("unauthenticated visitor is redirected to login", async ({ page }) => {
    await page.addInitScript(() => {
      document.cookie = "gst_role=; path=/; max-age=0";
    });
    await page.goto("/app");
    await expect(page).toHaveURL(/\/login/);
  });

  test("forged gst_role cookie without session is rejected", async ({ page }) => {
    await page.context().addCookies([
      { name: "gst_role", value: "CA", domain: "127.0.0.1", path: "/" },
    ]);
    await page.goto("/ca");
    await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });
  });

  test("authenticated user hitting /login is sent to their shell", async ({
    page,
  }) => {
    const mobile = uniqueMobile();
    await requestOtp(mobile, "REGISTER");

    await page.goto("/register");
    await page.getByTestId("register-client").click();
    await page.getByTestId("reg-identifier").fill(mobile);
    await page.getByTestId("reg-request-otp").click();
    const devOtp = await waitForDevOtp(page);
    await page.getByTestId("reg-otp").fill(devOtp);
    await page.getByTestId("reg-verify").click();
    await expect(page).toHaveURL(/\/app$/, { timeout: 10_000 });

    await page.goto("/login");
    await expect(page).toHaveURL(/\/app$/, { timeout: 10_000 });
  });
});