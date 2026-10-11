/**
 * Typed API client — API_SPECIFICATION.md envelope, bearer access token in
 * memory (SECURITY §6: no tokens in localStorage), silent refresh via the
 * backend's httpOnly cookie. Types mirror backend/app/api/schemas.py (the
 * generated-from-Pydantic generation lands with the full make api-types task).
 */

const API_BASE = "/api/v1";

export interface ApiErrorBody {
  success: false;
  error: { code: string; message: string };
}

export interface Envelope<T> {
  success: true;
  data: T;
}

export type EnvelopeResult<T> = Envelope<T> | ApiErrorBody;

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

export interface UserDto {
  id: string;
  email: string;
  full_name: string;
  totp_enabled: boolean;
}

export interface GstinRefDto {
  gstin: string;
  role: string;
  legal_name: string;
}

export interface MeDto {
  user: UserDto;
  gst_accounts: GstinRefDto[];
}

export interface OtpRequestResult {
  otp_sent: boolean;
  dev_otp: string | null;
}

export interface OtpVerifyResult {
  user: UserDto;
  access_token: string;
  refresh_token: string;
}

export interface TotpSetupResult {
  secret: string;
  qr_uri: string;
}

export interface TotpVerifyResult {
  enabled: boolean;
}

export interface GstAccountDto {
  gstin: string;
  legal_name: string;
  trade_name: string | null;
  pan: string;
  state_code: string;
  filing_scheme: string;
  irn_applicable: boolean;
  aato_latest_minor: number;
  registered_address: string | null;
  role: string;
  created_at: string | null;
}

export interface GspFileResult {
  ref_id: string;
  ack_no: string;
  status: string;
  timestamp: string;
}

export interface EInvoiceDto {
  irn: string;
  ack_no: string | null;
  ack_date: string | null;
  cancelled_at: string | null;
}

export interface DocumentJobDto {
  id: string;
  status: string;
  created_at: string | null;
}

export interface UploadResult {
  id: string;
  gstin: string;
  fp: string;
  capture_source: string;
  doc_type: string;
  sha256: string;
  bytes: number;
  page_count: number | null;
  minio_key: string;
  uploaded_by: string | null;
  uploaded_at: string | null;
  job: DocumentJobDto | null;
}

export interface DocumentRow {
  id: string;
  gstin: string;
  fp: string;
  capture_source: string;
  doc_type: string;
  sha256: string;
  bytes: number;
  page_count: number | null;
  minio_key: string;
  uploaded_by: string | null;
  uploaded_at: string | null;
  job: DocumentJobDto | null;
}

export interface DocumentListEnvelope {
  content: DocumentRow[];
  page: number;
  size: number;
  totalElements: number;
  last: boolean;
}

/* ------------------------------------------------------------------ *
 * Task 11.1 — bulk Excel/CSV import (PHASE11_13 rec 1).
 * Mirrors app/services/bulk_import.py's report shape: preview (no writes)
 * and confirm (bulk-ingest into extraction.invoice_drafts).
 * ------------------------------------------------------------------ */

export interface BulkImportFlag {
  rule: string;
  severity: string;
  message: string;
}

export interface BulkImportSampleRow {
  row: number;
  values: Record<string, string | null>;
}

export interface BulkImportRowResult {
  row: number;
  status: "IMPORTED" | "INVALID";
  invoice_no: string | null;
  draft_id: string | null;
  errors: string[];
  flags: BulkImportFlag[];
}

export interface BulkImportReport {
  filename: string;
  format: string;
  headers: string[];
  mapping: Record<string, string>;
  unmapped_headers: string[];
  required_missing: string[];
  data_rows: number;
  capture_source: string;
  sample: BulkImportSampleRow[];
  confirmed: boolean;
  imported: number;
  invalid: number;
  rows: BulkImportRowResult[];
  error_report: { row: number; errors: string[] }[];
}

export interface DraftDto {
  document_id: string;
  job_id: string;
  job_status: string;
  fp: string;
  capture_source: string;
  fields: Record<string, unknown>;
  lines: Record<string, unknown>[];
  confidence: Record<string, number>;
  flags: { rule: string; severity: string; message: string }[];
  auto_confirm: string;
  confidence_avg: number | null;
  derived: Record<string, string>;
}

export async function uploadDocument(
  gstin: string,
  fp: string,
  captureSource: string,
  file: File,
  docType = "UNCLASSIFIED",
): Promise<UploadResult> {
  const form = new FormData();
  form.append("capture_source", captureSource);
  form.append("doc_type", docType);
  form.append("files", file);
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/documents`, {
    method: "POST",
    body: form,
  });
}

export async function listDocuments(
  gstin: string,
  fp: string,
  page = 0,
  size = 20,
): Promise<DocumentListEnvelope> {
  return apiFetch(
    `/gst-accounts/${gstin}/months/${fp}/documents?page=${page}&size=${size}`,
  );
}

export async function getDocument(docId: string): Promise<DocumentRow> {
  return apiFetch(`/documents/${docId}`);
}

export async function getDraft(docId: string): Promise<DraftDto> {
  return apiFetch(`/documents/${docId}/draft`);
}

export async function updateDraft(
  docId: string,
  fields: Partial<DraftDto["fields"]>,
): Promise<DraftDto> {
  return apiFetch(`/documents/${docId}/draft`, {
    method: "PUT",
    body: JSON.stringify(fields),
  });
}

export async function confirmDocument(docId: string): Promise<{ invoice_id: string }> {
  return apiFetch(`/documents/${docId}/confirm`, { method: "POST" });
}

export async function rejectDocument(docId: string, reason?: string): Promise<unknown> {
  return apiFetch(`/documents/${docId}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

/* ------------------------------------------------------------------ *
 * Task 11.2 — 1-click Batch Confirm (PHASE11_13 rec 2).
 * Backend: POST /gst-accounts/{gstin}/months/{fp}/review-queue/batch-confirm
 * (`dry_run=true` evaluates + writes nothing). Shapes mirror
 * app/services/batch_confirm.py exactly — no client-side invention.
 * ------------------------------------------------------------------ */

export interface BatchConfirmQuarantineRow {
  kind: "EXTRACTION" | "BULK_IMPORT";
  document_id: string | null;
  draft_id: string;
  invoice_no: string | null;
  confidence_avg: number | null;
  /** BLOCKING_FLAGS | BELOW_THRESHOLD | WRITE_<ERROR_CODE> */
  reason: string;
  rules: string[];
}

export interface BatchConfirmResult {
  gstin: string;
  fp: string;
  dry_run: boolean;
  /** Integer paise total of the invoices the (real) call wrote. */
  total_paise: number;
  confirmed: number;
  confirmed_invoice_ids: string[];
  quarantined: number;
  quarantine: BatchConfirmQuarantineRow[];
}

export async function batchConfirm(
  gstin: string,
  fp: string,
  dryRun: boolean,
): Promise<BatchConfirmResult> {
  return apiFetch(
    `/gst-accounts/${gstin}/months/${fp}/review-queue/batch-confirm` +
      (dryRun ? "?dry_run=true" : ""),
    { method: "POST" },
  );
}

export async function triggerExtraction(docId: string): Promise<{ status: string }> {
  return apiFetch(`/documents/${docId}/extract`, { method: "POST" });
}

/**
 * Task 11.1 — bulk Excel/CSV import.
 * `confirm=false` returns the detected-column preview (nothing is written);
 * `confirm=true` bulk-ingests the rows and returns a per-row result report.
 */
export async function bulkImport(
  gstin: string,
  fp: string,
  file: File,
  confirm: boolean,
  mapping?: Record<string, string>,
): Promise<BulkImportReport> {
  const form = new FormData();
  form.append("file", file);
  form.append("confirm", confirm ? "true" : "false");
  if (mapping !== undefined) {
    form.append("mapping", JSON.stringify(mapping));
  }
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/bulk-import`, {
    method: "POST",
    body: form,
  });
}

export async function getGstr1Export(gstin: string, fp: string): Promise<Record<string, unknown>> {
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/gstr1/export.json`);
}

export async function getGstr3bExport(gstin: string, fp: string): Promise<Record<string, unknown>> {
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/gstr3b/export.json`);
}

/* ------------------------------------------------------------------ *
 * PHASE8 8.4 — returns generate pipeline + format export + HSN +
 * pre-file validation (PHASE8_PRODUCT_COMPLETENESS.md §3.8.4).
 * Money is integer paise everywhere; rupee conversion happens only at
 * render in lib/format-style helpers inside the page.
 */

/** One return's status in the POST /returns/generate envelope. */
export interface GeneratedReturnStatus {
  status: "READY" | "FAILED";
  invoice_count: number;
  totals: PaiseTotals | Record<string, never>;
  error?: string;
}

/** Paise-integer totals shared by generate / HSN rows / HSN totals. */
export interface PaiseTotals {
  txval_paise: number;
  iamt_paise: number;
  camt_paise: number;
  samt_paise: number;
  csamt_paise: number;
}

export interface ReturnsGenerateResult {
  gstin: string;
  fp: string;
  returns: { gstr1: GeneratedReturnStatus; gstr3b: GeneratedReturnStatus };
  gstr1_export_id: string | null;
}

export async function generateReturns(
  gstin: string,
  fp: string,
): Promise<ReturnsGenerateResult> {
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/returns/generate`, {
    method: "POST",
  });
}

/** HSN summary row (GET /gstr1/hsn-summary). */
export interface HsnSummaryRow extends PaiseTotals {
  hsn_sac: string;
  rt: number;
  num_of_lines: number;
}

export interface HsnSummaryResult {
  rows: HsnSummaryRow[];
  totals: PaiseTotals;
}

export async function getHsnSummary(
  gstin: string,
  fp: string,
): Promise<HsnSummaryResult> {
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/gstr1/hsn-summary`);
}

/** Pre-filing validation finding (GET /returns/validation). */
export interface ValidationFinding {
  rule: string;
  message: string;
  [key: string]: unknown;
}

export interface ReturnsValidationResult {
  blocking: ValidationFinding[];
  warnings: ValidationFinding[];
}

export async function getReturnsValidation(
  gstin: string,
  fp: string,
): Promise<ReturnsValidationResult> {
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/returns/validation`);
}

/* ---- Rule 88A set-off matrix + PMT-06 challan (task 11.3) ---- */

/** One statutory step of the Rule 88A credit-utilisation order. */
export interface SetOffStep {
  order: number;
  from_head: "IGST" | "CGST" | "SGST" | "CESS";
  to_head: "IGST" | "CGST" | "SGST" | "CESS";
  amount_paise: number;
  credit_after_paise: number;
  liability_after_paise: number;
}

/** Per-head money groups use the flat `*_paise` int shape (task 11.3). */
export interface SetOffHeads {
  igst_paise: number;
  cgst_paise: number;
  sgst_paise: number;
  cess_paise: number;
}

export interface ChallanSummary {
  pmt06_note: string;
  deposit: SetOffHeads;
  total_net_paise: number;
  zero_payable: boolean;
  portal_url: string | null;
  /** Always null from the API — a CPIN is generated only on the GST portal. */
  cpin: null;
  cpin_note: string;
}

export interface SetOffResult {
  gstin: string;
  fp: string;
  itc_credits: SetOffHeads;
  liability: SetOffHeads;
  credit_leftover: SetOffHeads;
  matrix: { steps: SetOffStep[] };
  net_cash: SetOffHeads;
  challan: ChallanSummary;
}

export async function getGstr3bSetoff(
  gstin: string,
  fp: string,
): Promise<SetOffResult> {
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/gstr3b/setoff`);
}

/**
 * Download the server-rendered .xlsx for a return form. The file is built
 * in-process by the backend (openpyxl, OSS) from the SAME payload as the
 * JSON export — single source of truth, so numbers cannot disagree.
 */
export async function downloadReturnXlsx(
  gstin: string,
  fp: string,
  form: "gstr1" | "gstr3b",
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/gst-accounts/${gstin}/months/${fp}/${form}/export.xlsx`,
    {
      headers:
        accessToken !== null ? { Authorization: `Bearer ${accessToken}` } : {},
      credentials: "include",
    },
  );
  if (!res.ok) {
    throw new ApiError("DOWNLOAD_FAILED", `export failed (${res.status})`, res.status);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${form.toUpperCase()}-${gstin}-${fp}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export interface MonthSummaryDto {
  fp: string;
  status: string;
  gstr1_due_date: string | null;
  gstr3b_due_date: string | null;
  days_to_deadline: number | null;
  doc_count: number;
  confirmed_count: number;
  review_count: number;
  pending_count: number;
  failed_count: number;
  total_taxable_minor: number;
  total_cgst_minor: number;
  total_sgst_minor: number;
  total_igst_minor: number;
  total_cess_minor: number;
  nil_eligible: boolean;
}

export async function getMonthSummary(gstin: string, fp: string): Promise<MonthSummaryDto> {
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/summary`);
}

export async function fileGstr1ViaGsp(
  gstin: string,
  fp: string,
): Promise<GspFileResult> {
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/gsp/gstr1/file`, {
    method: "POST",
  });
}

export async function fileGstr3bViaGsp(
  gstin: string,
  fp: string,
): Promise<GspFileResult> {
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/gsp/gstr3b/file`, {
    method: "POST",
  });
}

export async function generateIrn(invoiceId: string): Promise<EInvoiceDto> {
  return apiFetch(`/invoices/${invoiceId}/irn`, { method: "POST" });
}

export async function cancelIrn(invoiceId: string): Promise<EInvoiceDto> {
  return apiFetch(`/einvoices/${invoiceId}/cancel`, { method: "POST" });
}

export async function listEinvoices(
  gstin: string,
  fp: string,
): Promise<EInvoiceDto[]> {
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/einvoices`);
}

/** Confirmed outward invoice for the IRN board (backend einvoice router). */
export interface EwbPartADto {
  from_gstin: string;
  to_gstin: string;
  doc_no: string;
  doc_date: string;
  total_value_minor: number;
  hsn_code: string | null;
  reason_for_transport: string;
}

export interface EwbPartBDto {
  transporter_id: string | null;
  vehicle_no: string | null;
  distance_km: number;
}

export interface EwbDto {
  ewb_no: string;
  ewb_date: string;
  valid_until: string;
  status: string;
  part_a: EwbPartADto;
  part_b: EwbPartBDto;
  signed_qr_base64: string;
}

export interface GenerateEwbPayload {
  transporter_id?: string;
  vehicle_no?: string;
  distance_km?: number;
}

export async function generateEwb(
  gstin: string,
  fp: string,
  invoiceId: string,
  payload?: GenerateEwbPayload,
): Promise<EwbDto> {
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/einvoices/${invoiceId}/ewb`, {
    method: "POST",
    body: JSON.stringify(payload ?? {}),
  });
}

/** Confirmed outward invoice for the IRN board (backend einvoice router). */
export interface InvoiceDto {
  id: string;
  invoice_no: string;
  invoice_date: string;
  buyer_gstin: string | null;
  total_value_minor: number;
  supply_type: string;
  irn: string | null;
  irn_status: "PENDING" | "GENERATED" | "CANCELLED";
  ewb_no?: string | null;
  ewb_status?: "PENDING" | "GENERATED" | "NOT_REQUIRED";
}

export async function listInvoices(
  gstin: string,
  fp: string,
): Promise<InvoiceDto[]> {
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/invoices`);
}

/* ------------------------------------------------------------------ *
 * Task 9.4 — ITC reconciliation dashboard (PHASE9_QA_SWEEP_FIXES §3.9.4).
 * Backend: `app/services/returns/gstr2b.py:build_itc_report`. Money is
 * integer paise on BOTH sides; rupee conversion happens only at render.
 */

/** The 5 reconciliation verdicts (backend MatchStatus enum, never invented). */
export const ITC_MATCH_STATUSES = [
  "MATCHED",
  "PROBABLE",
  "UNMATCHED",
  "MISSING_IN_2B",
  "MISSING_IN_BOOKS",
] as const;

export type ItcMatchStatus = (typeof ITC_MATCH_STATUSES)[number];

/** Paise tax bucket shared by both sides of the reconciliation. */
export interface ItcTaxBucket {
  cgst_paise: number;
  sgst_paise: number;
  igst_paise: number;
  cess_paise: number;
  total_paise: number;
}

/** Books side of a reconciliation row (from the confirmed purchase invoice). */
export interface ItcBooksSide extends ItcTaxBucket {
  invoice_id: string;
  invoice_no: string;
  invoice_date: string;
  supplier_gstin: string | null;
  buyer_gstin: string | null;
  place_of_supply: string;
  status: string;
  taxable_value_paise: number;
  total_value_paise: number;
}

/** 2B side of a reconciliation row (from the imported statement entry). */
export interface ItcGstr2bSide extends ItcTaxBucket {
  entry_id: string;
  invoice_no: string;
  invoice_date: string;
  supplier_gstin: string;
  doc_type: string;
  itc_eligible: boolean;
  taxable_value_paise: number;
}

export interface ItcReportRow {
  id: string;
  match_status: ItcMatchStatus;
  confidence: number | null;
  remarks: string | null;
  books: ItcBooksSide | null;
  gstr2b: ItcGstr2bSide | null;
}

export interface ItcReportStatement {
  id: string;
  source: string;
  downloaded_at: string;
  entry_count: number;
}

export interface ItcReport {
  gstin: string;
  fp: string;
  statement: ItcReportStatement | null;
  rows: ItcReportRow[];
  summary: {
    /** Per-status counts across ALL rows, plus `total`. */
    counts: Record<string, number>;
    books_itc_paise: ItcTaxBucket;
    gstr2b_itc_paise: ItcTaxBucket;
    delta_itc_paise: ItcTaxBucket;
  };
}

export async function getItcReport(
  gstin: string,
  fp: string,
  status?: ItcMatchStatus,
): Promise<ItcReport> {
  const query = status === undefined ? "" : `?status=${status}`;
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/itc/report${query}`);
}

export interface Gstr2bImportResult {
  id: string;
  gstin: string;
  fp: string;
  source: string;
  downloaded_at: string;
}

/** Portal 2B JSON upload → statement + parsed entries. */
export async function importGstr2b(
  gstin: string,
  fp: string,
  payload: Record<string, unknown>,
): Promise<Gstr2bImportResult> {
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/gstr2b/import`, {
    method: "POST",
    body: JSON.stringify({ payload }),
  });
}

/** Runs the matching engine → the 5-status reconciliation rows. */
export async function reconcileItc(
  gstin: string,
  fp: string,
): Promise<{ id: string; match_status: string }[]> {
  return apiFetch(`/gst-accounts/${gstin}/months/${fp}/itc/reconcile`, {
    method: "POST",
  });
}

let accessToken: string | null = null;
let refreshPromise: Promise<boolean> | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function hasAccessToken(): boolean {
  return accessToken !== null;
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  allowRefreshRetry = true,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (accessToken !== null) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }
  if (init.body !== undefined && !headers.has("Content-Type")) {
    // FormData gets its multipart boundary from the browser; don't override it.
    if (!(init.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
  }
  // Include cookies on same-origin API calls so the httpOnly refresh token is
  // available to /auth/refresh and auth-state endpoints.
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  const body = (await res.json().catch(() => null)) as EnvelopeResult<T> | null;
  if (body !== null && body.success === true) {
    return body.data;
  }
  const errBody = body as ApiErrorBody | null;
  const code = errBody?.error?.code ?? "NETWORK_ERROR";
  const message = errBody?.error?.message ?? `request failed (${res.status})`;
  const apiErr = new ApiError(code, message, res.status);
  if (allowRefreshRetry && apiErr.status === 401) {
    // Route through the deduplicated silentRefresh(): concurrent 401s (React
    // Strict Mode double-mount, parallel page fetches) must share ONE refresh
    // call. Calling refreshTokens() directly here replays the already-rotated
    // refresh cookie, which the backend treats as reuse and kills the family.
    const refreshed = await silentRefresh();
    if (refreshed) {
      return await apiFetch<T>(path, init, false);
    }
    throw apiErr;
  }
  throw apiErr;
}

/** Silent refresh via the backend's httpOnly cookie (no token in storage). */
async function refreshTokens(): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({}),
  });
  const body = (await res.json().catch(() => null)) as
    | Envelope<{ access_token: string }>
    | ApiErrorBody
    | null;
  if (body !== null && body.success === true) {
    accessToken = body.data.access_token;
    return;
  }
  accessToken = null;
  throw new ApiError(
    body?.error?.code ?? "REFRESH_FAILED",
    body?.error?.message ?? "session expired",
    res.status,
  );
}

export async function silentRefresh(): Promise<boolean> {
  if (refreshPromise !== null) {
    return refreshPromise;
  }
  refreshPromise = (async () => {
    try {
      await refreshTokens();
      return accessToken !== null;
    } catch {
      return false;
    } finally {
      refreshPromise = null;
    }
  })();
  return refreshPromise;
}

export async function requestOtp(
  identifier: string,
  purpose: "LOGIN" | "REGISTER",
): Promise<OtpRequestResult> {
  return apiFetch("/auth/otp/request", {
    method: "POST",
    body: JSON.stringify({ identifier, purpose }),
  });
}

export async function verifyOtp(
  identifier: string,
  otp: string,
): Promise<OtpVerifyResult> {
  return apiFetch("/auth/otp/verify", {
    method: "POST",
    body: JSON.stringify({ identifier, otp }),
  });
}

export async function fetchMe(): Promise<MeDto> {
  return apiFetch("/auth/me");
}

/** PATCH /me/profile body — full_name ONLY (PHASE8 8.1, email is read-only). */
export interface ProfilePatch {
  full_name: string;
}

/** GET/PATCH /me/settings aggregate (PHASE8 8.2). */
export interface NotificationPrefs {
  email: boolean;
  in_app: boolean;
}

export interface SessionInfo {
  active_sessions: number;
  refresh_ttl_days: number;
}

export interface SettingsDto {
  notification_preferences: Record<string, unknown> | null;
  totp_enabled: boolean;
  session: SessionInfo;
}

/** POST /auth/logout-all result (PHASE8 8.7 sign-out-everywhere). */
export interface LogoutAllResult {
  revoked_sessions: number;
  active_sessions: number;
}

export async function fetchSettings(): Promise<SettingsDto> {
  return apiFetch("/me/settings");
}

export async function updateSettings(
  prefs: NotificationPrefs,
): Promise<SettingsDto> {
  // PATCH /me/settings delegates to the /me/notification-prefs handler
  // (8.2 contract: no duplicated write logic); the response is the full
  // settings aggregate, same as GET.
  const payload: Record<string, boolean> = { ...prefs };
  return apiFetch("/me/settings", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function signOutEverywhere(): Promise<LogoutAllResult> {
  return apiFetch("/auth/logout-all", { method: "POST" });
}

/** DPDP self-service export result (POST /me/data/export, step-up protected). */
export interface DpdpExportResult {
  request_id: string;
  status: string;
  export: Record<string, unknown>;
}

/** DPDP erasure-request result (POST /me/data/erasure-request). */
export interface DpdpErasureResult {
  request_id: string;
  status: string;
  retention_carve_out_applied: boolean;
  sla_days: number;
  sla_due_date: string;
}

/**
 * DPDP export + erasure are step-up protected (SECURITY §1). The caller sends
 * a fresh 6-digit OTP via the X-OTP header; the backend verifies it against
 * Redis (same single-use semantics as login) before acting.
 */
export async function dpdpExport(otp: string): Promise<DpdpExportResult> {
  return apiFetch("/me/data/export", {
    method: "POST",
    headers: { "X-OTP": otp },
  });
}

export async function dpdpRequestErasure(
  otp: string,
): Promise<DpdpErasureResult> {
  return apiFetch("/me/data/erasure-request", {
    method: "POST",
    headers: { "X-OTP": otp },
  });
}

export async function fetchProfile(): Promise<UserDto> {
  return apiFetch("/me/profile");
}

export async function updateProfile(fullName: string): Promise<UserDto> {
  return apiFetch("/me/profile", {
    method: "PATCH",
    body: JSON.stringify({ full_name: fullName } satisfies ProfilePatch),
  });
}

export async function totpSetup(): Promise<TotpSetupResult> {
  return apiFetch("/auth/totp/setup", { method: "POST" });
}

export async function totpVerify(code: string): Promise<TotpVerifyResult> {
  return apiFetch("/auth/totp/verify", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export async function listGstAccounts(): Promise<GstAccountDto[]> {
  return apiFetch("/gst-accounts");
}

/** POST /gst-accounts body — PHASE8 8.8 add-GSTIN form (server re-validates). */
export interface GstAccountCreatePayload {
  gstin: string;
  legal_name: string;
  trade_name?: string;
  registered_address?: string;
  filing_scheme?: string;
}

export async function addGstAccount(
  payload: GstAccountCreatePayload,
): Promise<GstAccountDto> {
  return apiFetch("/gst-accounts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getGstAccount(gstin: string): Promise<GstAccountDto> {
  return apiFetch(`/gst-accounts/${gstin}`);
}

/** PATCH /gst-accounts/{gstin} body — business fields (PHASE8 8.3/8.8). */
export interface GstAccountPatchPayload {
  legal_name?: string;
  trade_name?: string;
  registered_address?: string;
  filing_scheme?: string;
}

export async function updateGstAccount(
  gstin: string,
  payload: GstAccountPatchPayload,
): Promise<GstAccountDto> {
  return apiFetch(`/gst-accounts/${gstin}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

/** GET /gst-accounts/{gstin}/overview — detail + this-FY periods (8.3). */
export interface FyPeriodStatus {
  fp: string;
  status: string;
  gstr1_due_date: string | null;
  gstr3b_due_date: string | null;
  locked_at: string | null;
  filed_at: string | null;
}

export interface GstAccountOverviewDto {
  gstin: string;
  fy: string;
  detail: GstAccountDto;
  periods: FyPeriodStatus[];
}

export async function getGstAccountOverview(
  gstin: string,
): Promise<GstAccountOverviewDto> {
  return apiFetch(`/gst-accounts/${gstin}/overview`);
}