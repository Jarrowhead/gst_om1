/**
 * Phase 12 (Task 12.3) Playwright Test: Maker-Checker & Discrepancy Notice Journey.
 *
 * Verifies:
 * 1. User registers via API and logs in via UI.
 * 2. Creates GST account context.
 * 3. Navigates to /app/file/[gstin]/[fp]:
 *    - Period control banner shows initial OPEN status.
 *    - Freeze button freezes the period (status -> FROZEN, lock alert visible).
 *    - Unlock button restores period to OPEN.
 * 4. Navigates to /app/itc/[gstin]/[fp]:
 *    - Reconciles ITC / views chase-supplier list.
 *    - Tests vendor discrepancy notice endpoint citing Section 16(2)(aa).
 */
import { expect, test } from "@playwright/test";

const BACKEND = "http://127.0.0.1:8084";
const FP = "042026";

function uniqueEmail(): string {
  return `test_p12_${Date.now()}_${Math.random().toString(36).slice(2, 8)}@example.com`;
}

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

async function registerViaApi(email: string): Promise<string> {
  const otpRes = await fetch(`${BACKEND}/api/v1/auth/otp/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier: email, purpose: "REGISTER" }),
  });
  const otpData = (await otpRes.json()) as { success: boolean; data?: { dev_otp?: string } };
  expect(otpData.success).toBe(true);
  const devOtp = otpData.data?.dev_otp;

  const verifyRes = await fetch(`${BACKEND}/api/v1/auth/otp/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier: email, otp: devOtp }),
  });
  expect(verifyRes.status).toBe(200);
  const verifyData = (await verifyRes.json()) as { data: { access_token: string } };
  return verifyData.data.access_token;
}

test("Phase 12 Maker-Checker Freeze and Discrepancy Notice user journey", async ({ page }) => {
  const email = uniqueEmail();
  const token = await registerViaApi(email);
  const gstin = makeGstin();

  // Create GST account via API
  const createAcc = await fetch(`${BACKEND}/api/v1/gst-accounts`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ gstin, legal_name: "Phase 12 Enterprise Corp" }),
  });
  expect(createAcc.status).toBe(200);

  // 1. Perform UI login
  await page.goto("/login");
  await page.getByTestId("login-identifier").fill(email);
  await page.getByTestId("login-request-otp").click();
  const devOtpEl = page.getByTestId("dev-otp");
  await expect(devOtpEl).toBeVisible({ timeout: 10_000 });
  const rawOtpText = (await devOtpEl.textContent()) || "";
  const devOtp = rawOtpText.replace("dev code:", "").trim();
  await page.getByTestId("login-otp").fill(devOtp);
  await page.getByTestId("login-verify").click();
  await page.waitForURL("/app", { timeout: 15_000 });

  // 2. Visit file page and test Freeze / Unfreeze
  await page.goto(`/app/file/${gstin}/${FP}`);
  await expect(page.getByTestId("period-control-banner")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("period-status-badge")).toHaveText("OPEN");

  // Freeze period
  const freezeBtn = page.getByTestId("freeze-period-btn");
  await expect(freezeBtn).toBeVisible();
  await freezeBtn.click();

  // Badge updates to FROZEN and alert shows
  await expect(page.getByTestId("period-status-badge")).toHaveText("FROZEN", { timeout: 10_000 });
  await expect(page.getByTestId("period-frozen-alert")).toBeVisible();

  // Unlock period
  const unfreezeBtn = page.getByTestId("unfreeze-period-btn");
  await expect(unfreezeBtn).toBeVisible();
  await unfreezeBtn.click();
  await expect(page.getByTestId("period-status-badge")).toHaveText("OPEN", { timeout: 10_000 });

  // 3. Visit ITC reconciliation page
  await page.goto(`/app/itc/${gstin}/${FP}`);
  await expect(page.getByTestId("itc-chase-section")).toBeVisible({ timeout: 15_000 });

  // 4. Test discrepancy notice API via client endpoint
  const suppGstin = makeGstin();
  const noticeRes = await fetch(
    `${BACKEND}/api/v1/gst-accounts/${gstin}/months/${FP}/itc/discrepancy-notice`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        invoice_no: "INV-CHASE-01",
        supplier_gstin: suppGstin,
        taxable_paise: 5000000,
        tax_paise: 900000,
        invoice_date: "2026-04-10",
      }),
    },
  );
  expect(noticeRes.status).toBe(200);
  const noticeData = await noticeRes.json();
  expect(noticeData.data.statutory_citation).toContain("Section 16(2)(aa)");
  expect(noticeData.data.formal_notice).toContain("Section 16(2)(aa)");
  expect(noticeData.data.whatsapp_url).toContain("https://wa.me/?text=");
});
