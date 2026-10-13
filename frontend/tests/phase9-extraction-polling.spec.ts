/**
 * Task 9.2 gate — Playwright: extraction progress polling on upload
 * (PHASE9_QA_SWEEP_FIXES.md §3.9.2).
 * Backend (8084) + frontend dev server (9094) must be running; the backend
 * needs PG:5436 + Redis:6380 (bootstrap_stack.py).
 *
 * done_when:
 *  - upload success card renders the live pipeline chips and its status
 *    label transitions to a terminal value via the 2s getDocument poller.
 *    In dev mode the upload flow runs extraction synchronously, so the
 *    GET /documents/{id} response is stubbed to hold QUEUED for the first
 *    polls (the doc allows "stub the API"), then released — the card must
 *    pick the real terminal status up WITHOUT a reload;
 *  - "Review / confirm" (go-to-queue) is disabled while the job is
 *    non-terminal and enabled once the draft is ready (EXTRACTED /
 *    NEEDS_REVIEW);
 *  - the file/[gstin]/[fp] queue re-lists on its 5s interval while a row
 *    is non-terminal (a doc uploaded via API stays QUEUED until the dev
 *    extract endpoint is called) and shows the terminal status after.
 *
 * No new backend endpoints; synthetic fixtures only; GSTINs are
 * checksum-valid by construction (same mod-36 rule as the other specs).
 */
import { expect, test, Page } from "@playwright/test";

const BACKEND = "http://127.0.0.1:8084";

/** Every terminal JobStatus the poller stops on (backend enum values). */
const TERMINAL = ["EXTRACTED", "NEEDS_REVIEW", "FAILED", "CONFIRMED"];

function uniqueEmail(): string {
  return `test_${Date.now()}_${Math.random().toString(36).slice(2, 8)}@example.com`;
}

/** Mod-36 checksum-valid synthetic GSTIN (same rule as the other specs). */
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
      "xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n" +
      "0000000058 00000 n\n0000000115 00000 n\n" +
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
    body: JSON.stringify({ gstin, legal_name: `Poll Co ${Date.now()}` }),
  });
  expect([200, 201, 409]).toContain(res.status);
}

async function loginUi(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByTestId("login-identifier").fill(email);
  await page.getByTestId("login-request-otp").click();
  const banner = page.getByTestId("dev-otp");
  await expect(banner).toBeVisible({ timeout: 15_000 });
  const text = await banner.textContent();
  const code = text!.match(/dev code:\s*(\d{6})/)![1];
  await page.getByTestId("login-otp").fill(code);
  await page.getByTestId("login-verify").click();
  await expect(page).toHaveURL(/\/app$/, { timeout: 10_000 });
}

/** Upload one minimal PDF via the API; job stays QUEUED (no extraction). */
async function uploadApi(
  token: string,
  gstin: string,
  fp: string,
): Promise<string> {
  const form = new FormData();
  form.append("capture_source", "PDF_SCAN");
  form.append("doc_type", "UNCLASSIFIED");
  form.append(
    "files",
    new Blob([new Uint8Array(minimalPdf())], { type: "application/pdf" }),
    "invoice.pdf",
  );
  const res = await fetch(
    `${BACKEND}/api/v1/gst-accounts/${gstin}/months/${fp}/documents`,
    { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: form },
  );
  expect(res.status).toBe(200);
  const body = (await res.json()) as { success: boolean; data: { id: string } };
  expect(body.success).toBe(true);
  return body.data.id;
}

/** Dev-mode synchronous extraction trigger (same endpoint the 7.2 spec uses). */
async function extractApi(token: string, docId: string): Promise<void> {
  const res = await fetch(`${BACKEND}/api/v1/documents/${docId}/extract`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });
  expect(res.status).toBe(200);
}

test.describe("9.2 extraction progress polling", () => {
  test("upload card shows pipeline chips; button gated until draft ready; queue re-lists", async ({
    page,
  }) => {
    const email = uniqueEmail();
    const token = await registerApi(email);
    const gstin = makeGstin(); // checksum-valid synthetic fixture
    await createGstAccount(token, gstin);
    const fp = "092026";
    await loginUi(page, email);

    // Stub GET /documents/{docId} to hold QUEUED (the doc allows stubbing
    // the API: in dev mode the upload flow already extracts synchronously,
    // so without the stub the card would start terminal). Polls are
    // counted; stubReleased flips the handler to pass-through.
    let stubReleased = false;
    let stubHits = 0;
    await page.route("**/api/v1/documents/**", (route) => {
      const req = route.request();
      const path = new URL(req.url()).pathname;
      const isDocGet =
        req.method() === "GET" &&
        /^\/api\/v1\/documents\/[0-9a-f-]{36}$/.test(path);
      if (!isDocGet || stubReleased) {
        void route.fallback();
        return;
      }
      stubHits += 1;
      const docId = path.split("/").pop() ?? "";
      void route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: {
            id: docId,
            gstin,
            fp,
            capture_source: "PDF_SCAN",
            doc_type: "UNCLASSIFIED",
            sha256: "stubbed",
            bytes: 1,
            page_count: 1,
            minio_key: "stubbed",
            uploaded_by: null,
            uploaded_at: null,
            job: { id: "stubbed", status: "QUEUED", created_at: null },
          },
        }),
      });
    });

    await page.goto(`/app/upload/${gstin}/${fp}`);
    await page.getByTestId("file-input").setInputFiles({
      name: "invoice.pdf",
      mimeType: "application/pdf",
      buffer: minimalPdf(),
    });
    await page.getByTestId("upload-submit").click();

    const success = page.getByTestId("upload-success");
    await expect(success).toBeVisible({ timeout: 30_000 });
    const initialText = await success.textContent();
    const docIdMatch = initialText!.match(/Document ID:\s*([0-9a-f-]{36})/i);
    expect(docIdMatch).toBeTruthy();
    const docId = docIdMatch![1];

    // Pipeline chips render every backend JobStatus stage (exact enum
    // values — QUEUED, PREPROCESS, OCR_RUNNING, LLM_RUNNING, EXTRACTED).
    for (const stage of [
      "QUEUED",
      "PREPROCESS",
      "OCR_RUNNING",
      "LLM_RUNNING",
      "EXTRACTED",
    ]) {
      await expect(page.getByTestId(`pipeline-stage-${stage}`)).toBeVisible();
    }

    // While the job is QUEUED (non-terminal): button disabled + hint,
    // and the poller is live (≥2 stubbed polls at the 2s cadence).
    const statusLabel = page.getByTestId("upload-status");
    await expect(statusLabel).toHaveText("Status: QUEUED", {
      timeout: 10_000,
    });
    const goBtn = page.getByTestId("go-to-queue");
    await expect(goBtn).toBeDisabled();
    await expect(page.getByTestId("go-to-queue-hint")).toBeVisible();
    await expect(page.getByTestId("extraction-polling-hint")).toBeVisible();
    await page.waitForTimeout(4_500); // two poll ticks at 2s
    expect(stubHits).toBeGreaterThanOrEqual(2);

    // Release the stub: the next poll hits the real backend (extraction
    // already ran during upload) and the card flips to a terminal status
    // WITHOUT a page reload.
    stubReleased = true;
    await expect(statusLabel).toHaveText(
      new RegExp(`Status: (${TERMINAL.join("|")})`),
      { timeout: 15_000 },
    );

    // Draft ready => button enabled, hint and spinner row gone.
    await expect(goBtn).toBeEnabled();
    await expect(page.getByTestId("go-to-queue-hint")).toHaveCount(0);
    await expect(page.getByTestId("extraction-polling-hint")).toHaveCount(0);

    // The gated button navigates directly to the review editor for the doc.
    await goBtn.click();
    await expect(page).toHaveURL(`/app/review/${gstin}/${fp}/${docId}`);
  });

  test("file queue re-lists every 5s while a row is non-terminal", async ({
    page,
  }) => {
    const email = uniqueEmail();
    const token = await registerApi(email);
    const gstin = makeGstin(); // checksum-valid synthetic fixture
    await createGstAccount(token, gstin);
    const fp = "092026";

    // Upload via API — no extraction trigger, so the job stays QUEUED
    // (dev mode has no background worker) until we call the dev endpoint.
    const docId = await uploadApi(token, gstin, fp);
    await loginUi(page, email);

    await page.goto(`/app/file/${gstin}/${fp}`);
    const row = page.getByTestId("document-row").first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText(/(QUEUED|PREPROCESS|OCR_RUNNING|LLM_RUNNING|NEEDS_REVIEW|EXTRACTED)/);

    // Trigger extraction from outside the page: the queue must pick the
    // new terminal status up on its 5s re-list interval (no reload).
    await extractApi(token, docId);
    await expect(row).toContainText(new RegExp(`(${TERMINAL.join("|")})`), {
      timeout: 15_000,
    });
  });
});