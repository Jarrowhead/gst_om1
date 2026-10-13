/**
 * Task 10.2 gate — Playwright: shared client shell layout
 * (PHASE10_BUG_SWEEP_FIXES.md §10.2, FRONTEND_SPECIFICATION.md §3.11).
 * Backend (8084) + frontend dev server (9094) must be running; the backend
 * needs PG:5436 + Redis:6380 (bootstrap_stack.py).
 *
 * done_when:
 *  - the ShellNav header renders on the workspace routes that previously had
 *    no shared chrome — /app/upload/[gstin]/[fp] and /app/returns/[gstin]/[fp]
 *    (plus /app/file and /app/einvoice) — with the role chip, user chip, GSTIN
 *    switcher and Sign out;
 *  - it renders EXACTLY ONCE (no double header) on the routes that used to
 *    embed it per page (dashboard, businesses);
 *  - the GSTIN switcher and Sign out remain wired on a workspace route.
 *
 * Synthetic identity and GSTIN only; the GSTIN is checksum-valid by
 * construction (mod-36 makeGstin, same rule as lib/validation/gstin.ts).
 */
import { expect, test, Page } from "@playwright/test";

const BACKEND = "http://127.0.0.1:8084";
const FP = "092026";

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
    data?: { dev_otp?: string };
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

/** Attach a GSTIN so the nav's GSTIN-scoped links and switcher have a target. */
async function addGstinViaApi(accessToken: string, gstin: string): Promise<void> {
  const res = await fetch(`${BACKEND}/api/v1/gst-accounts`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ gstin, legal_name: `Shell Nav Co ${Date.now()}` }),
  });
  expect([200, 201, 409]).toContain(res.status);
}

async function loginViaUi(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByTestId("login-identifier").fill(email);
  await page.getByTestId("login-request-otp").click();
  const banner = page.getByTestId("dev-otp");
  await expect(banner).toBeVisible({ timeout: 15_000 });
  const match = (await banner.textContent())?.match(/dev code:\s*(\d{6})/);
  expect(match).toBeTruthy();
  await page.getByTestId("login-otp").fill(match![1]);
  await page.getByTestId("login-verify").click();
  await expect(page).toHaveURL(/\/app$/, { timeout: 10_000 });
}

/** Assert the shared header renders exactly once with its full chrome. */
async function expectHeaderOnce(page: Page, gstin: string): Promise<void> {
  await expect(page.getByTestId("shell-nav-links")).toHaveCount(1, {
    timeout: 15_000,
  });
  await expect(page.getByTestId("shell-role")).toHaveCount(1);
  await expect(page.getByTestId("shell-role")).toHaveText("GST Filing");
  await expect(page.getByTestId("shell-user")).toHaveCount(1);
  await expect(page.getByTestId("signout-btn")).toHaveCount(1);
  const switcher = page.getByTestId("gstin-switcher");
  await expect(switcher).toHaveCount(1);
  await expect(switcher).toHaveText(gstin);
}

test.describe("10.2 shared client shell layout", () => {
  test("header renders once on /app/upload and /app/returns (workspace routes)", async ({
    page,
  }) => {
    const email = uniqueEmail();
    const token = await registerViaApi(email);
    const gstin = makeGstin();
    await addGstinViaApi(token, gstin);
    await loginViaUi(page, email);

    // /app/upload — previously a bare-layout screen with no header.
    await page.goto(`/app/upload/${gstin}/${FP}`);
    await expect(page.getByTestId("upload-submit")).toBeVisible({
      timeout: 15_000,
    });
    await expectHeaderOnce(page, gstin);

    // /app/returns — previously a bare-layout screen with no header.
    await page.goto(`/app/returns/${gstin}/${FP}`);
    await expect(page.getByTestId("generate-section")).toBeVisible({
      timeout: 15_000,
    });
    await expectHeaderOnce(page, gstin);

    // /app/file — the document queue also gains the shared header.
    await page.goto(`/app/file/${gstin}/${FP}`);
    await expect(page.getByTestId("gsp-filing-hub")).toBeVisible({
      timeout: 15_000,
    });
    await expectHeaderOnce(page, gstin);

    // /app/einvoice — the IRN board also gains the shared header.
    await page.goto(`/app/einvoice/${gstin}/${FP}`);
    await expect(page.getByTestId("einvoice-invoices-table")).toBeVisible({
      timeout: 15_000,
    });
    await expectHeaderOnce(page, gstin);
  });

  test("header renders exactly once on /app and /app/businesses (no double header)", async ({
    page,
  }) => {
    const email = uniqueEmail();
    const token = await registerViaApi(email);
    const gstin = makeGstin();
    await addGstinViaApi(token, gstin);
    await loginViaUi(page, email);

    // Dashboard — the page no longer embeds ShellNav; the layout owns it.
    await page.goto("/app");
    await expect(
      page.getByTestId("gst-account-card").filter({ hasText: gstin }),
    ).toBeVisible({ timeout: 15_000 });
    await expectHeaderOnce(page, gstin);
    // No double header: the role chip appears exactly once.
    await expect(page.getByTestId("shell-role")).toHaveCount(1);

    // Businesses — same contract.
    await page.goto("/app/businesses");
    await expect(page.getByTestId("business-list")).toBeVisible({
      timeout: 15_000,
    });
    await expectHeaderOnce(page, gstin);
  });

  test("GSTIN switcher and Sign out stay wired on a workspace route", async ({
    page,
  }) => {
    const email = uniqueEmail();
    const token = await registerViaApi(email);
    const gstin = makeGstin();
    await addGstinViaApi(token, gstin);
    await loginViaUi(page, email);

    await page.goto(`/app/returns/${gstin}/${FP}`);
    await expect(page.getByTestId("generate-section")).toBeVisible({
      timeout: 15_000,
    });

    // The switcher keeps the current section when jumping to another GSTIN
    // (single account: selecting it again is a no-op navigation).
    const switcher = page.getByTestId("gstin-switcher");
    await expect(switcher).toBeVisible();
    await expect(switcher).toHaveText(gstin);

    // Opening the switcher exposes a search bar; typing filters the list down
    // to the matching GSTIN before it is picked.
    await switcher.click();
    const search = page.getByTestId("gstin-switcher-search");
    await expect(search).toBeVisible();
    await search.fill(gstin);
    await page.getByTestId(`gstin-option-${gstin}`).click();
    await expect(page).toHaveURL(
      new RegExp(`/app/returns/${gstin}/${FP}$`),
      { timeout: 15_000 },
    );

    // Sign out clears the session and returns to /login.
    await page.getByTestId("signout-btn").click();
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
  });
});
