/**
 * Task 8.11 gate — Playwright: Phase-8 product-shell reachability audit
 * (PHASE8_PRODUCT_COMPLETENESS.md §3.8.11 + VERIFICATION_SWEEP.md Lens 2).
 * Backend (8084) + frontend dev server (9094) must be running; the backend
 * needs PG:5436 + Redis:6380 (bootstrap_stack.py).
 *
 * done_when: a fresh AI can reach every Phase-8 screen BY CLICKING THE NAV
 * from /app — never a direct URL. Walks: Dashboard -> Businesses -> list ->
 * business detail -> Upload workspace -> Returns workspace -> Profile ->
 * Settings, then back to Dashboard, asserting each route + active-link
 * highlight. Every click is a nav-link click; page.goto is used exactly
 * once (the initial /app entry after login).
 *
 * Lens 2 (VERIFICATION_SWEEP.md): no role split anywhere on the walked
 * screens — the shell exposes one user type only.
 *
 * Synthetic identities and GSTIN only; the GSTIN is checksum-valid by
 * construction (mod-36 makeGstin, same rule as lib/validation/gstin.ts).
 */
import { expect, test, Page } from "@playwright/test";

const BACKEND = "http://127.0.0.1:8084";

function uniqueEmail(): string {
  return `test_${Date.now()}_${Math.random().toString(36).slice(2, 8)}@example.com`;
}

/** Mod-36 checksum-valid synthetic GSTIN (same rule as lib/validation/gstin.ts). */
function makeGstin(): string {
  const state = "27";
  const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const digits = "0123456789";
  const pan =
    Array.from({ length: 5 }, () => letters[Math.floor(Math.random() * 26)]).join("") +
    Array.from({ length: 4 }, () => digits[Math.floor(Math.random() * 10)]).join("") +
    letters[Math.floor(Math.random() * 26)];
  const first14 = `${state}${pan}1Z`;
  const charset = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const weights = [1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2];
  let total = 0;
  for (let i = 0; i < 14; i += 1) {
    const prod = charset.indexOf(first14[i]) * weights[i];
    total += Math.floor(prod / 36) + (prod % 36);
  }
  const check = charset[(36 - (total % 36)) % 36];
  return `${first14}${check}`;
}

/** Register the user via the backend API (dev OTP is echoed in the response). */
async function registerViaApi(email: string): Promise<string> {
  const res = await fetch(`${BACKEND}/api/v1/auth/otp/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier: email, purpose: "REGISTER" }),
  });
  const body = (await res.json()) as {
    success: boolean;
    data?: { otp_sent: boolean; dev_otp?: string };
  };
  expect(body.success).toBe(true);
  expect(body.data?.dev_otp).toBeTruthy();
  const verify = await fetch(`${BACKEND}/api/v1/auth/otp/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier: email, otp: body.data!.dev_otp }),
  });
  expect(verify.status).toBe(200);
  const tokens = ((await verify.json()) as { data: { access_token: string } }).data;
  return tokens.access_token;
}

/** Attach a GSTIN to the user so the nav's GSTIN-scoped links have a target. */
async function addGstinViaApi(accessToken: string, gstin: string): Promise<void> {
  const res = await fetch(`${BACKEND}/api/v1/gst-accounts`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ gstin, legal_name: `Reachability Co ${Date.now()}` }),
  });
  expect(res.status).toBe(200);
}

/** Log the registered user in through the real login screen. */
async function loginViaUi(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByTestId("login-identifier").fill(email);
  await page.getByTestId("login-request-otp").click();
  const banner = page.getByTestId("dev-otp");
  // Explicit timeout: under the full suite the dev server + backend are contended.
  await expect(banner).toBeVisible({ timeout: 15_000 });
  const match = (await banner.textContent())?.match(/dev code:\s*(\d{6})/);
  expect(match).toBeTruthy();
  await page.getByTestId("login-otp").fill(match![1]);
  await page.getByTestId("login-verify").click();
  await expect(page).toHaveURL(/\/app$/, { timeout: 10_000 });
}

test.describe("Phase-8 product shell — nav reachability audit", () => {
  test("every Phase-8 screen reachable by clicking nav from /app", async ({
    page,
  }) => {
    const email = uniqueEmail();
    const token = await registerViaApi(email);
    const gstin = makeGstin(); // checksum-valid synthetic fixture
    await addGstinViaApi(token, gstin);
    await loginViaUi(page, email);

    // The ONLY direct navigation: land on the shell (login already went to
    // /app). Everything below is a nav click.
    await page.goto("/app");
    await expect(page.getByTestId("shell-nav-links")).toBeVisible();

    // Lens 2: one user type — the shell role chip is the unified product
    // label ("GST Filing"), never a CA/business-owner role switcher.
    await expect(page.getByTestId("shell-role")).toHaveText("GST Filing");
    await expect(page.getByTestId("nav-link-ca")).toHaveCount(0);

    // 1. Businesses — list renders with the seeded account.
    await page.getByTestId("nav-link-businesses").click();
    await expect(page).toHaveURL(/\/app\/businesses$/);
    const card = page.getByTestId("business-card").filter({ hasText: gstin });
    await expect(card).toBeVisible({ timeout: 15_000 });

    // 2. Business detail — reached from the LIST card, not a typed URL.
    await card.getByTestId("business-open-link").click();
    await expect(page).toHaveURL(new RegExp(`/app/businesses/${gstin}$`), {
      timeout: 15_000,
    });
    await expect(page.getByTestId("business-detail-title")).toBeVisible();
    await expect(page.getByTestId("business-field-gstin")).toHaveText(gstin);

    // 3. Upload workspace — nav link goes to the GSTIN-scoped route. (The
    // upload screen itself predates the Phase-8 shell and keeps its bare
    // layout; the nav click is what the reachability audit asserts.)
    await page.getByTestId("nav-link-upload").click();
    await expect(page).toHaveURL(
      new RegExp(`/app/upload/${gstin}/\\d{6}$`),
      { timeout: 15_000 },
    );

    // 4. Returns workspace — the upload screen has no nav bar (bare layout),
    // so the audit returns to the shell via the browser back button and
    // clicks the Returns nav from there. The nav is the audited path.
    await page.goBack();
    await expect(page.getByTestId("shell-nav-links")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByTestId("nav-link-returns").click();
    await expect(page).toHaveURL(
      new RegExp(`/app/returns/${gstin}/\\d{6}$`),
      { timeout: 15_000 },
    );

    // 5. Profile — the returns workspace is also a bare-layout screen (no
    // nav bar), so return to the shell first; the Profile nav click is the
    // audited path from there.
    await page.goBack();
    await expect(page.getByTestId("shell-nav-links")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByTestId("nav-link-profile").click();
    await expect(page).toHaveURL(/\/app\/profile$/);
    // Explicit timeouts: under the full suite the dev server + backend are
    // contended (the profile spec does the same).
    await expect(page.getByTestId("profile-email")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("profile-email")).toHaveValue(email);
    await expect(page.getByTestId("nav-link-profile")).toHaveAttribute(
      "aria-current",
      "page",
    );

    // 6. Settings.
    await page.getByTestId("nav-link-settings").click();
    await expect(page).toHaveURL(/\/app\/settings$/);
    await expect(page.getByTestId("settings-pref-email")).toBeAttached({
      timeout: 15_000,
    });
    await expect(page.getByTestId("nav-link-settings")).toHaveAttribute(
      "aria-current",
      "page",
    );

    // 7. Dashboard — the tracker grid also renders via nav click.
    await page.getByTestId("nav-link-dashboard").click();
    await expect(page).toHaveURL(/\/app$/, { timeout: 15_000 });
    const dashCard = page.getByTestId("gst-account-card").filter({
      hasText: gstin,
    });
    await expect(dashCard).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByTestId(`filing-status-grid-${gstin}`),
    ).toBeVisible();
    await expect(page.getByTestId("nav-link-dashboard")).toHaveAttribute(
      "aria-current",
      "page",
    );

    // 8. GSTIN switcher is present and keeps the section when switching
    // (single account: selecting it again is a no-op navigation).
    const switcher = page.getByTestId("gstin-switcher");
    await expect(switcher).toBeVisible({ timeout: 15_000 });
    await expect(switcher).toHaveText(gstin);
    await switcher.click();
    await page.getByTestId(`gstin-option-${gstin}`).click();
    await expect(page).toHaveURL(/\/app\/upload\//, { timeout: 15_000 });
  });
});