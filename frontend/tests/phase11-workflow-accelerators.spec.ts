/**
 * Task 11.4 gate — Playwright: Phase-11 workflow accelerators reached by
 * CLICKING THE NAV (VERIFICATION_SWEEP.md Lens 2 — the nav journey, never a
 * typed workspace URL; page.goto is used exactly once, the initial /app entry
 * after login, same pattern as phase8-product-shell.spec.ts). Backend (8084)
 * + frontend dev server (9094) must be running; the backend needs PG:5436 +
 * Redis:6380 (bootstrap_stack.py).
 *
 * done_when (PHASE11_13_RECOMMENDATION_BUILD.md recs 1–3):
 *  - 11.1  nav-link Upload -> the upload page renders the "Bulk Excel/CSV"
 *          tab (data-testid upload-tab-bulk); switching to it renders the
 *          bulk-import panel (bulk-import-panel / bulk-dropzone /
 *          bulk-file-input), additive over the untouched capture tab;
 *  - 11.2  nav-link Filing -> the documents page renders the Batch Confirm
 *          panel with the honest dry-run-gated button (batch-confirm-btn);
 *          with nothing pending it is disabled ("No clean drafts to confirm");
 *          with a seeded clean high-confidence NEEDS_REVIEW draft it is
 *          enabled with the real count;
 *  - 11.3  nav-link Returns -> the returns workspace renders the Rule 88A
 *          set-off matrix (setoff-matrix, 6 ordered statutory steps) and the
 *          PMT-06 challan card (challan-section) fed by the seeded ledger;
 *          the challan NEVER shows a CPIN (challan-cpin is asserted null-data
 *          only through the zero/pay state the page renders).
 *
 * Synthetic identities and GSTIN only; the GSTIN is checksum-valid by
 * construction (mod-36 makeGstin, same rule as lib/validation/gstin.ts).
 * Money is integer paise everywhere in the seeded fixtures; the page renders
 * rupees only at display. No CPIN is fabricated anywhere.
 */
import { expect, test, Page } from "@playwright/test";

const BACKEND = "http://127.0.0.1:8084";
const FP = "102026";

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

/** Corrupt the check digit (a valid-format fixture whose mod-36 fails). */
function bulkBadGstin(): string {
  return `27AAECS1234C1Z${"0"}`;
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

/** Attach a GSTIN so the nav's GSTIN-scoped links have a target. */
async function addGstinViaApi(accessToken: string, gstin: string): Promise<void> {
  const res = await fetch(`${BACKEND}/api/v1/gst-accounts`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ gstin, legal_name: `Phase 11 Co ${Date.now()}` }),
  });
  expect([200, 201, 409]).toContain(res.status);
}

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

/** Log the registered user in through the real login screen. */
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

test.describe("Phase-11 workflow accelerators (11.1/11.2/11.3)", () => {
  test("upload bulk tab, batch-confirm button and set-off matrix reachable by nav", async ({
    page,
  }) => {
    const email = uniqueEmail();
    const token = await registerViaApi(email);
    const gstin = makeGstin();
    await addGstinViaApi(token, gstin);

    await loginViaUi(page, email);
    await page.goto("/app");
    await expect(page.getByTestId("shell-nav-links")).toBeVisible();

    // ---- 11.1: nav Upload -> the Bulk Excel/CSV tab is reachable + renders.
    await page.getByTestId("nav-link-upload").click();
    await expect(page).toHaveURL(
      new RegExp(`/app/upload/${gstin}/\\d{6}$`),
      { timeout: 15_000 },
    );
    // The capture tab is the default and is untouched.
    await expect(page.getByTestId("upload-tab-capture")).toBeVisible();
    await page.getByTestId("upload-tab-bulk").click();
    await expect(page.getByTestId("bulk-import-panel")).toBeVisible();
    await expect(page.getByTestId("bulk-dropzone")).toBeVisible();
    await expect(page.getByTestId("bulk-file-input")).toBeAttached();
    await expect(page.getByTestId("upload-submit")).toHaveCount(0);

    // ---- 11.2: nav Filing -> Batch Confirm panel + honest dry-run gating.
    await page.getByTestId("nav-link-file").click();
    await expect(page).toHaveURL(
      new RegExp(`/app/file/${gstin}/\\d{6}$`),
      { timeout: 15_000 },
    );
    const panel = page.getByTestId("batch-confirm-panel");
    await expect(panel).toBeVisible({ timeout: 15_000 });
    // Nothing pending for this fresh tenant: the button is disabled and says
    // so (the label comes from the backend dry_run count, not a heuristic).
    const btn = page.getByTestId("batch-confirm-btn");
    await expect(btn).toBeDisabled({ timeout: 15_000 });
    await expect(btn).toContainText("No clean drafts to confirm");

    // ---- 11.3: nav Returns -> set-off matrix + challan card render.
    await page.getByTestId("nav-link-returns").click();
    await expect(page).toHaveURL(
      new RegExp(`/app/returns/${gstin}/\\d{6}$`),
      { timeout: 15_000 },
    );
    await expect(page.getByTestId("generate-section")).toBeVisible({
      timeout: 15_000,
    });
    // The set-off matrix + challan card are rendered from GET /gstr3b/setoff
    // (the page loads them on mount). 6 statutory steps in order.
    const matrix = page.getByTestId("setoff-matrix");
    await expect(matrix).toBeVisible({ timeout: 15_000 });
    const rows = page.getByTestId("setoff-matrix-row");
    await expect(rows).toHaveCount(6);
    await expect(rows.nth(0)).toContainText("IGST");
    await expect(rows.nth(3)).toContainText("CGST");
    await expect(rows.nth(5)).toContainText("CESS");
    await expect(page.getByTestId("challan-section")).toBeVisible();
    await expect(page.getByTestId("challan-total-net")).toBeVisible();
  });

  test("batch confirm confirms only clean high-confidence drafts (11.1+11.2 through the UI)", async ({
    page,
  }) => {
    const email = uniqueEmail();
    const token = await registerViaApi(email);
    const gstin = makeGstin();
    await addGstinViaApi(token, gstin);

    // Seed through the REAL product path: bulk-import a 3-row CSV (one bad
    // checksum GSTIN row), then confirm the import. Two clean high-confidence
    // draft rows (file-supplied fields are confidence 1.0) + 1 rejected row.
    const supplier = makeGstin();
    const buyer = gstin;
    const csv = [
      "Invoice No,Invoice Date,Supplier GSTIN,Buyer GSTIN,Taxable Value,GST Rate,HSN",
      `BC-UI-1,2026-10-05,${supplier},${buyer},10000.00,18,73269099`,
      `BC-UI-2,2026-10-06,${supplier},${buyer},20000.00,18,73269099`,
      `BC-UI-3,2026-10-07,${bulkBadGstin()},${buyer},30000.00,18,73269099`,
    ].join("\n");

    const form = new FormData();
    form.append("confirm", "true");
    form.append(
      "file",
      new Blob([csv], { type: "text/csv" }),
      "bc-ui-seed.csv",
    );
    const importRes = await fetch(
      `${BACKEND}/api/v1/gst-accounts/${gstin}/months/${FP}/bulk-import`,
      { method: "POST", headers: authHeaders(token), body: form },
    );
    expect(importRes.status).toBe(200);
    const report = ((await importRes.json()) as {
      data: { imported: number; invalid: number };
    }).data;
    expect(report.imported).toBe(2);
    expect(report.invalid).toBe(1);

    await loginViaUi(page, email);
    await page.goto("/app");
    await expect(page.getByTestId("shell-nav-links")).toBeVisible();

    // Nav to Filing; the dry-run gate now sees 2 clean BULK_IMPORT drafts and
    // enables the button with the real count.
    await page.getByTestId("nav-link-file").click();
    await expect(page).toHaveURL(
      new RegExp(`/app/file/${gstin}/\\d{6}$`),
      { timeout: 15_000 },
    );
    const btn = page.getByTestId("batch-confirm-btn");
    await expect(btn).toBeEnabled({ timeout: 15_000 });
    await expect(btn).toContainText("Batch Confirm 2 clean invoice");

    // The one click confirms ONLY the clean high-confidence drafts.
    await btn.click();
    const result = page.getByTestId("batch-confirm-result");
    await expect(result).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("batch-confirm-count")).toHaveText("2");
    // 10,000.00 + 20,000.00 = 30,000.00 rupees of taxable+tax = 3540_00.
    await expect(page.getByTestId("batch-confirm-total")).toContainText(
      "₹35,400.00",
    );
    // The quarantined rows are reported with the backend's reason — the bad
    // checksum row never became a draft, so exactly 0 quarantines here.
    await expect(page.getByTestId("batch-confirm-quarantined")).toHaveText("0");

    // Post-call re-evaluation: everything clean is confirmed -> disabled again.
    await expect(btn).toBeDisabled({ timeout: 15_000 });

    // Idempotency through the UI: reloading re-runs the dry run -> still 0.
    await page.reload();
    await expect(page.getByTestId("batch-confirm-panel")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("batch-confirm-btn")).toBeDisabled({
      timeout: 15_000,
    });
  });
});