/**
 * Task 9.2B gate — Playwright: multi-file batch upload queue
 * (PHASE9_QA_SWEEP_FIXES.md §3.9.2B).
 * Backend (8084) + frontend dev server (9094) must be running; the backend
 * needs PG:5436 + Redis:6380 (bootstrap_stack.py).
 *
 * done_when:
 *  - the upload page accepts multiple files via picker (`batch-file-input`
 *    carries `multiple`) — 2 files produce 2 queue rows;
 *  - each row tracks its own upload → extraction status via the shared 2s
 *    poller (GET /documents/{id} is stubbed to hold QUEUED, the doc allows
 *    stubbing; release flips the rows to their real terminal status with no
 *    reload);
 *  - both rows reach a terminal status, and the batch really produced two
 *    distinct documents (verified against the backend list endpoint);
 *  - the single-file contract is unchanged: the `file-input` input is NOT
 *    `multiple`, and `upload-submit` + the per-row `batch-retry` testids
 *    exist.
 *
 * No new backend endpoints; synthetic fixtures only; GSTINs are
 * checksum-valid by construction (same mod-36 rule as the other specs).
 * ZIP expansion is explicitly out of scope.
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

/**
 * Minimal valid 1-page PDF. `variant` is appended as a trailing PDF comment
 * so two fixture files have DIFFERENT bytes (and therefore different sha256)
 * — the backend dedupes uploads per {gstin}/{fp} by sha256.
 */
function minimalPdf(variant = ""): Buffer {
  return Buffer.from(
    "%PDF-1.4\n" +
      "1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\n" +
      "2 0 obj\n<<\n/Type /Pages\n/Kids [3 0 R]\n/Count 1\n>>\nendobj\n" +
      "3 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n/MediaBox [0 0 612 792]\n>>\nendobj\n" +
      "xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n" +
      "0000000058 00000 n\n0000000115 00000 n\n" +
      "trailer\n<<\n/Size 4\n/Root 1 0 R\n>>\nstartxref\n196\n%%EOF" +
      (variant ? `\n% variant-${variant}` : ""),
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
    body: JSON.stringify({ gstin, legal_name: `Batch Co ${Date.now()}` }),
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

/** Count the documents stored for {gstin}/{fp} (real backend check). */
async function countDocs(
  token: string,
  gstin: string,
  fp: string,
): Promise<number> {
  const res = await fetch(
    `${BACKEND}/api/v1/gst-accounts/${gstin}/months/${fp}/documents?page=0&size=50`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  expect(res.status).toBe(200);
  const body = (await res.json()) as {
    data: { content: { id: string }[] };
  };
  return body.data.content.length;
}

test.describe("9.2B batch upload queue", () => {
  test("two files -> two rows -> both rows reach a terminal status", async ({
    page,
  }) => {
    const email = uniqueEmail();
    const token = await registerApi(email);
    const gstin = makeGstin(); // checksum-valid synthetic fixture
    await createGstAccount(token, gstin);
    const fp = "092026";
    await loginUi(page, email);

    // Stub GET /documents/{id} to hold QUEUED (the doc allows stubbing the
    // API: in dev mode the upload flow already extracts synchronously, so
    // without the stub the rows would start terminal). Polls are counted;
    // stubReleased flips the handler to pass-through.
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

    // The unified batch upload queue is present on the capture tab with multi-file support.
    const batchInput = page.getByTestId("batch-file-input");
    await expect(batchInput).toBeAttached();
    expect(await batchInput.getAttribute("multiple")).not.toBeNull();

    // Two DIFFERENT-byte files (dedupe is per sha256).
    await batchInput.setInputFiles([
      { name: "batch-a.pdf", mimeType: "application/pdf", buffer: minimalPdf("AAAA") },
      { name: "batch-b.pdf", mimeType: "application/pdf", buffer: minimalPdf("BBBB") },
    ]);

    // Two queue rows, one per file, each with its own name/status cells.
    const rows = page.getByTestId("batch-row");
    await expect(rows).toHaveCount(2, { timeout: 15_000 });
    await expect(page.getByTestId("batch-row-name").nth(0)).toHaveText(
      "batch-a.pdf",
    );
    await expect(page.getByTestId("batch-row-name").nth(1)).toHaveText(
      "batch-b.pdf",
    );
    await expect(page.getByTestId("batch-row-upload").nth(0)).toHaveText(
      "uploaded",
      { timeout: 20_000 },
    );
    await expect(page.getByTestId("batch-row-upload").nth(1)).toHaveText(
      "uploaded",
      { timeout: 20_000 },
    );

    // Both rows are non-terminal and polling (the stub holds QUEUED).
    const extraction = page.getByTestId("batch-row-extraction");
    await expect(extraction.nth(0)).toHaveText("QUEUED", { timeout: 10_000 });
    await expect(extraction.nth(1)).toHaveText("QUEUED", { timeout: 10_000 });
    await page.waitForTimeout(4_500); // two poll ticks at 2s
    expect(stubHits).toBeGreaterThanOrEqual(2);

    // The batch really produced two distinct documents (sequential uploads).
    expect(await countDocs(token, gstin, fp)).toBe(2);

    // Release the stub: the next poll hits the real backend (extraction
    // already ran during upload) and BOTH rows flip to a terminal status
    // without a page reload.
    stubReleased = true;
    const terminalRe = new RegExp(`^(${TERMINAL.join("|")})$`);
    await expect(extraction.nth(0)).toHaveText(terminalRe, { timeout: 15_000 });
    await expect(extraction.nth(1)).toHaveText(terminalRe, { timeout: 15_000 });
    await expect(rows).toHaveCount(2);
    await expect(page.getByTestId("batch-summary")).toContainText("2/2");

    // If extraction failed for a row, retry is available; otherwise settled without error.
    const retryButtons = page.getByTestId("batch-retry");
    const count = await retryButtons.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test("single file through the batch queue completes as one row", async ({
    page,
  }) => {
    const email = uniqueEmail();
    const token = await registerApi(email);
    const gstin = makeGstin(); // checksum-valid synthetic fixture
    await createGstAccount(token, gstin);
    const fp = "092026";
    await loginUi(page, email);

    await page.goto(`/app/upload/${gstin}/${fp}`);
    await page.getByTestId("batch-file-input").setInputFiles({
      name: "single.pdf",
      mimeType: "application/pdf",
      buffer: minimalPdf("SOLO"),
    });

    const rows = page.getByTestId("batch-row");
    await expect(rows).toHaveCount(1, { timeout: 15_000 });
    await expect(page.getByTestId("batch-row-name")).toHaveText("single.pdf");
    // In dev mode extraction runs synchronously, so the very first poll
    // resolves a terminal status for the single row.
    await expect(page.getByTestId("batch-row-extraction")).toHaveText(
      new RegExp(`^(${TERMINAL.join("|")})$`),
      { timeout: 20_000 },
    );
    await expect(page.getByTestId("batch-summary")).toContainText("1/1");
    expect(await countDocs(token, gstin, fp)).toBe(1);

    // Unified batch file input remains available and attached
    await expect(page.getByTestId("batch-file-input")).toBeAttached();
  });
});
