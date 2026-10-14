/**
 * Task 8.9 gate — Playwright: returns workspace (PHASE8_PRODUCT_COMPLETENESS.md
 * §3.8.9). Backend (8084) + frontend dev server (9094) must be running; the
 * backend needs PG:5436 + Redis:6380 (bootstrap_stack.py).
 *
 * done_when: generate calls POST /returns/generate (the 8.4 endpoint) and
 * shows per-return status; the format picker switches to Excel and the
 * .xlsx downloads; the HSN summary table and the pre-filing validation
 * checklist render. The existing download-gstr1 / download-gstr3b testids
 * survive (asserted by task7-2, re-asserted here in JSON mode).
 *
 * Synthetic identities and GSTIN only; the GSTIN is checksum-valid by
 * construction (mod-36 makeGstin, same rule as lib/validation/gstin.ts).
 */
import { expect, test, Page } from "@playwright/test";

const BACKEND = "http://127.0.0.1:8084";

function uniqueEmail(): string {
  return `test_${Date.now()}_${Math.random().toString(36).slice(2, 8)}@example.com`;
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

function minimalPdf(): Buffer {
  return Buffer.from(
    "%PDF-1.4\n" +
      "1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\n" +
      "2 0 obj\n<<\n/Type /Pages\n/Kids [3 0 R]\n/Count 1\n>>\nendobj\n" +
      "3 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n/MediaBox [0 0 612 792]\n>>\nendobj\n" +
      "xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n" +
      "0000000058 00000 n \n0000000115 00000 n \n" +
      "trailer\n<<\n/Size 4\n/Root 1 0 R\n>>\nstartxref\n196\n%%EOF",
  );
}

async function registerApi(email: string): Promise<string> {
  const otpRes = await fetch(`${BACKEND}/api/v1/auth/otp/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier: email, purpose: "REGISTER" }),
  });
  const otpBody = (await otpRes.json()) as {
    success: boolean;
    data?: { dev_otp?: string };
  };
  expect(otpBody.success).toBe(true);
  const verifyRes = await fetch(`${BACKEND}/api/v1/auth/otp/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier: email, otp: otpBody.data!.dev_otp! }),
  });
  expect(verifyRes.status).toBe(200);
  const body = (await verifyRes.json()) as {
    data: { access_token: string };
  };
  return body.data.access_token;
}

async function createGstAccount(token: string, gstin: string): Promise<void> {
  const res = await fetch(`${BACKEND}/api/v1/gst-accounts`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ gstin, legal_name: `Returns WS Co ${Date.now()}` }),
  });
  expect([200, 201, 409]).toContain(res.status);
}

/** Upload + dev-extract + confirm one invoice through the backend API. */
async function seedConfirmedInvoice(
  token: string,
  gstin: string,
): Promise<void> {
  const form = new FormData();
  form.append("capture_source", "PDF_SCAN");
  form.append(
    "files",
    new Blob([new Uint8Array(minimalPdf())], { type: "application/pdf" }),
    "invoice.pdf",
  );
  const upRes = await fetch(
    `${BACKEND}/api/v1/gst-accounts/${gstin}/months/092026/documents`,
    { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: form },
  );
  expect(upRes.status).toBe(200);
  const up = (await upRes.json()) as {
    data: { id: string };
  };
  const docId = up.data.id;

  const extractRes = await fetch(
    `${BACKEND}/api/v1/documents/${docId}/extract`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
    },
  );
  expect(extractRes.status).toBe(200);

  // Confirm as soon as the draft exists (the dev extract is synchronous).
  let confirmed = false;
  for (let i = 0; i < 20 && !confirmed; i += 1) {
    const confirmRes = await fetch(
      `${BACKEND}/api/v1/documents/${docId}/confirm`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
      },
    );
    if (confirmRes.status === 200) {
      confirmed = true;
    } else {
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  expect(confirmed).toBe(true);
}

async function loginUi(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByTestId("login-identifier").fill(email);
  await page.getByTestId("login-request-otp").click();
  const banner = page.getByTestId("dev-otp");
  // Explicit timeout: under the full suite the dev server + backend are contended.
  await expect(banner).toBeVisible({ timeout: 15_000 });
  const text = await banner.textContent();
  const code = text!.match(/dev code:\s*(\d{6})/)![1];
  await page.getByTestId("login-otp").fill(code);
  await page.getByTestId("login-verify").click();
  await expect(page).toHaveURL(/\/app$/, { timeout: 10_000 });
}

test.describe("returns workspace (/app/returns/[gstin]/[fp])", () => {
  test("generate -> per-return status -> HSN table -> validation checklist -> Excel downloads", async ({
    page,
  }) => {
    const email = uniqueEmail();
    const token = await registerApi(email);
    const gstin = makeGstin(); // checksum-valid synthetic fixture
    await createGstAccount(token, gstin);
    await seedConfirmedInvoice(token, gstin);

    await loginUi(page, email);
    await page.goto(`/app/returns/${gstin}/092026`);

    // The workspace sections all render.
    await expect(page.getByTestId("generate-section")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("validation-section")).toBeVisible();
    await expect(page.getByTestId("hsn-section")).toBeVisible();
    await expect(page.getByTestId("download-section")).toBeVisible();

    // HSN table: the seeded line (HSN 73269099, 18%) is aggregated with a
    // totals footer that reconciles to the row.
    const hsnTable = page.getByTestId("hsn-table");
    await expect(hsnTable).toBeVisible();
    await expect(page.getByTestId("hsn-row")).toHaveText(/73269099/);
    await expect(page.getByTestId("hsn-totals")).toContainText("₹1,000.00");

    // Validation checklist: clean seeded period -> no blocking issues.
    await expect(
      page.getByTestId("validation-blocking-state"),
    ).toHaveText("✓");
    await expect(page.getByTestId("validation-blocking-row")).toHaveCount(0);

    // Format picker defaults to JSON; the existing testids survive.
    await expect(page.getByTestId("format-json")).toBeChecked();
    const [jsonDownload] = await Promise.all([
      page.waitForEvent("download"),
      page.getByTestId("download-gstr1").click(),
    ]);
    expect(jsonDownload.suggestedFilename()).toMatch(/GSTR1-.*\.json/);

    // 2. Generate — calls the 8.4 orchestration and shows per-return status.
    await page.getByTestId("generate-returns").click();
    const gstr1Status = page.getByTestId("generate-status-gstr1");
    await expect(gstr1Status).toBeVisible({ timeout: 15_000 });
    await expect(gstr1Status).toContainText("READY");
    await expect(page.getByTestId("generate-status-gstr3b")).toContainText(
      "READY",
    );
    await expect(gstr1Status).toContainText("₹1,000.00");

    // 3. Switch the format picker to Excel and download GSTR-1 as .xlsx.
    await page.getByTestId("format-xlsx").check();
    await expect(page.getByTestId("format-xlsx")).toBeChecked();

    const [xlsxDownload] = await Promise.all([
      page.waitForEvent("download"),
      page.getByTestId("download-gstr1").click(),
    ]);
    expect(xlsxDownload.suggestedFilename()).toMatch(/GSTR1-.*\.xlsx/);
    expect(xlsxDownload.suggestedFilename()).toBe(
      `GSTR1-${gstin}-092026.xlsx`,
    );

    // 4. GSTR-3B Excel downloads too (same picker).
    const [xlsx3bDownload] = await Promise.all([
      page.waitForEvent("download"),
      page.getByTestId("download-gstr3b").click(),
    ]);
    expect(xlsx3bDownload.suggestedFilename()).toBe(
      `GSTR3B-${gstin}-092026.xlsx`,
    );
  });

  test("renders empty returns workspace cleanly without 404 or errors when 0 invoices exist", async ({
    page,
  }) => {
    const email = uniqueEmail();
    const token = await registerApi(email);
    const gstin = makeGstin();
    await createGstAccount(token, gstin);

    await loginUi(page, email);
    await page.goto(`/app/returns/${gstin}/092026`);

    // The workspace sections all render cleanly without error banner
    await expect(page.getByTestId("generate-section")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("returns-error")).toHaveCount(0);
    await expect(page.getByTestId("validation-section")).toBeVisible();
    await expect(page.getByTestId("hsn-section")).toBeVisible();
    await expect(page.getByTestId("download-section")).toBeVisible();

    // HSN empty state is shown
    await expect(page.getByTestId("hsn-empty")).toBeVisible();
    await expect(page.getByTestId("hsn-empty")).toContainText(
      "No outward line items for this period yet",
    );

    // Validation checklist shows no blocking issues
    await expect(page.getByTestId("validation-blocking-state")).toHaveText("✓");
    await expect(page.getByTestId("validation-blocking-row")).toHaveCount(0);
  });
});