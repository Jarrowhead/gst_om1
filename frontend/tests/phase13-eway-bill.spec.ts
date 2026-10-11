/**
 * Phase 13 (Task 13.2) Playwright Test: E-Way Bill Sandbox Journey.
 *
 * Verifies:
 * 1. User registers/logs in.
 * 2. Navigates to E-Invoice / E-Way Bill board.
 * 3. Confirms page loads with table and GSTIN context.
 */
import { expect, test } from "@playwright/test";

const BACKEND = "http://127.0.0.1:8084";
const FP = "042026";

function uniqueEmail(): string {
  return `test_ewb_${Date.now()}_${Math.random().toString(36).slice(2, 8)}@example.com`;
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

test("Phase 13 E-Way Bill sandbox generation user journey", async ({ page }) => {
  const email = uniqueEmail();
  const token = await registerViaApi(email);
  const gstin = makeGstin();

  // 1. Create GST Account via API
  const gstRes = await fetch(`${BACKEND}/api/v1/gst-accounts`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ gstin, legal_name: "Phase 13 Logistics Pvt Ltd" }),
  });
  expect(gstRes.status).toBe(200);

  // 2. Perform UI login
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

  // 3. Navigate to E-Invoice & E-Way Bill board
  await page.goto(`/app/einvoice/${gstin}/${FP}`);
  await expect(page.getByTestId("einvoice-invoices-table")).toBeVisible({ timeout: 15_000 });

  // Page loads properly and header displays GSTIN
  await expect(page.locator("h1")).toContainText("E-Invoice");
  await expect(page.locator("main")).toContainText(gstin);
});
