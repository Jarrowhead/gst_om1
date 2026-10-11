"use client";

/**
 * ITC reconciliation dashboard — PHASE9_QA_SWEEP_FIXES.md §3.9.4
 * (FRONTEND_SPECIFICATION.md §3.5, API_SPECIFICATION.md §9).
 *
 * Route: /app/itc/[gstin]/[fp]
 *
 *   1. 2B import status — the current statement for the period (source,
 *      downloaded-at, parsed entry count), or a designed empty state.
 *   2. Reconcile — POST /itc/reconcile (the existing engine), then re-read
 *      the report.
 *   3. Status filter — the 5 MatchStatus verdicts as chips with counts.
 *   4. Books-vs-2B side-by-side table with a status badge per row.
 *   5. GSTR-3B ITC prefill card — books vs 2B ITC per tax head, in paise.
 *   6. "Chase supplier" list — the MISSING_IN_2B documents (in books, absent
 *      from the supplier's 2B) with the supplier GSTIN to follow up.
 *
 * Uses ONLY existing backend endpoints (API_SPEC §9): gstr2b/import,
 * gstr2b/fetch (surfaced from the returns workspace), itc/reconcile and
 * itc/report. Money is integer paise from the API; the rupee conversion
 * happens only at render (FRONTEND_SPECIFICATION.md §4).
 * Phase 10.2: the header lives in the shared (client)/app layout; this page
 * pushes the user name + GST accounts into the ShellContext.
 */
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { useShell } from "@/components/shared/ShellContext";
import {
  ApiError,
  DiscrepancyNoticeOut,
  fetchMe,
  generateDiscrepancyNotice,
  getItcReport,
  ITC_MATCH_STATUSES,
  reconcileItc,
  silentRefresh,
} from "@/lib/api/client";
import type {
  ItcMatchStatus,
  ItcReport,
  ItcReportRow,
  ItcTaxBucket,
} from "@/lib/api/client";
import { clearSession } from "@/lib/auth/session";

/** Paise → "₹1,23,456.78" (Indian grouping) — render-only conversion. */
function rupees(paise: number): string {
  const value = paise / 100;
  const formatted = new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
  return `₹${formatted}`;
}

/** Human label for a MatchStatus (the enum value stays the contract). */
const STATUS_LABELS: Record<string, string> = {
  MATCHED: "Matched",
  PROBABLE: "Probable",
  UNMATCHED: "Unmatched",
  MISSING_IN_2B: "Missing in 2B",
  MISSING_IN_BOOKS: "Missing in books",
};

/**
 * Badge colours per verdict. Deliberately three tiers: settled (green),
 * needs a human look (amber), and money at risk (red) — FRONTEND_SPEC §4.
 */
const STATUS_CLASSES: Record<string, string> = {
  MATCHED: "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
  PROBABLE: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  UNMATCHED: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  MISSING_IN_2B: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  MISSING_IN_BOOKS:
    "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
};

const TAX_HEADS = [
  { key: "cgst_paise", label: "CGST" },
  { key: "sgst_paise", label: "SGST" },
  { key: "igst_paise", label: "IGST" },
  { key: "cess_paise", label: "Cess" },
] as const;

type TaxKey = (typeof TAX_HEADS)[number]["key"];

function bucketValue(bucket: ItcTaxBucket, key: TaxKey): number {
  return bucket[key];
}

/** The date part of an ISO timestamp, or the input unchanged. */
function isoDate(value: string): string {
  return value.length >= 10 ? value.slice(0, 10) : value;
}

export default function ItcPage() {
  const params = useParams<{ gstin: string; fp: string }>();
  const { gstin, fp } = params;
  const router = useRouter();
  const shell = useShell();
  // Stable state setters (identity never changes) so the mount fetch below
  // cannot re-fire when the shell value object changes — that would loop.
  const setShellUserName = shell?.setUserName;
  const setShellGstAccounts = shell?.setGstAccounts;

  const [report, setReport] = useState<ItcReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reconciling, setReconciling] = useState(false);
  const [filter, setFilter] = useState<ItcMatchStatus | "ALL">("ALL");
  const [selectedNotice, setSelectedNotice] = useState<DiscrepancyNoticeOut | null>(null);
  const [noticeLoading, setNoticeLoading] = useState(false);
  const [copiedType, setCopiedType] = useState<"formal" | "whatsapp" | null>(null);

  async function handleOpenNotice(row: ItcReportRow) {
    setNoticeLoading(true);
    try {
      const res = await generateDiscrepancyNotice(gstin, fp, {
        recon_id: row.id,
        invoice_no: row.books?.invoice_no ?? row.gstr2b?.invoice_no,
        supplier_gstin: row.books?.supplier_gstin ?? row.gstr2b?.supplier_gstin,
        taxable_paise: row.books?.taxable_value_paise ?? row.gstr2b?.taxable_value_paise,
        tax_paise: row.books?.total_paise ?? row.gstr2b?.total_paise,
        invoice_date: row.books?.invoice_date ?? row.gstr2b?.invoice_date,
      });
      setSelectedNotice(res);
    } catch (err) {
      alert(err instanceof ApiError ? err.message : "Failed to generate notice");
    } finally {
      setNoticeLoading(false);
    }
  }

  function copyText(text: string, type: "formal" | "whatsapp") {
    void navigator.clipboard.writeText(text);
    setCopiedType(type);
    setTimeout(() => setCopiedType(null), 2000);
  }

  const loadReport = useCallback(async () => {
    // The report is fetched in full; the status filter is applied below so the
    // chip counts stay live while a filter is active (no re-fetch per click).
    const data = await getItcReport(gstin, fp);
    setReport(data);
  }, [gstin, fp]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (!(await silentRefresh())) {
          clearSession();
          router.replace("/login");
          return;
        }
        const me = await fetchMe();
        if (cancelled) return;
        // 10.2: feed the shared shell header (user chip + GSTIN switcher).
        setShellUserName?.(me.user.full_name);
        setShellGstAccounts?.(me.gst_accounts);
        const data = await getItcReport(gstin, fp);
        if (cancelled) return;
        setReport(data);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          clearSession();
          router.replace("/login");
          return;
        }
        setLoadError(
          err instanceof ApiError ? err.message : "failed to load ITC report",
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [gstin, fp, router, setShellUserName, setShellGstAccounts]);

  async function handleReconcile() {
    setReconciling(true);
    setActionError(null);
    try {
      await reconcileItc(gstin, fp);
      await loadReport();
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "reconciliation failed",
      );
    } finally {
      setReconciling(false);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-[calc(100vh-3rem)] items-center justify-center text-sm text-slate-500">
        Checking session…
      </div>
    );
  }

  if (loadError !== null) {
    return (
      <div className="flex min-h-[calc(100vh-3rem)] items-center justify-center">
        <p
          className="text-sm text-red-600 dark:text-red-400"
          data-testid="itc-load-error"
        >
          {loadError}
        </p>
      </div>
    );
  }

  const statement = report?.statement ?? null;
  const rows: ItcReportRow[] = report?.rows ?? [];
  const counts = report?.summary.counts ?? { total: 0 };
  const visible = filter === "ALL" ? rows : rows.filter((r) => r.match_status === filter);
  const chaseRows = rows.filter((r) => r.match_status === "MISSING_IN_2B");

  return (
    <main
      className="mx-auto w-full max-w-5xl flex-1 px-6 py-8"
      data-testid="itc-page"
    >
        <h1 className="text-2xl font-semibold">ITC reconciliation</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          GSTIN: <span className="font-mono">{gstin}</span> · Period:{" "}
          <span className="font-mono">{fp}</span> · books vs GSTR-2B
        </p>

        {actionError !== null && (
          <p
            className="mt-6 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
            data-testid="itc-action-error"
          >
            {actionError}
          </p>
        )}

        {/* 1. 2B import status */}
        <section
          className="mt-8 rounded-xl border border-slate-200 p-6 dark:border-slate-800"
          data-testid="itc-statement-section"
        >
          <h2 className="font-semibold">1. GSTR-2B import</h2>
          {statement === null ? (
            <div
              className="mt-4 rounded-lg border border-dashed border-slate-300 p-6 text-center dark:border-slate-700"
              data-testid="itc-statement-empty"
            >
              <p className="text-sm text-slate-500 dark:text-slate-400">
                No GSTR-2B statement imported for this period yet — reconciliation
                has nothing to compare against.
              </p>
              <Link
                href={`/app/returns/${gstin}/${fp}`}
                className="mt-4 inline-block text-sm font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                data-testid="itc-import-link"
              >
                Import GSTR-2B from the returns workspace →
              </Link>
            </div>
          ) : (
            <div
              className="mt-4 grid gap-4 sm:grid-cols-3"
              data-testid="itc-statement-status"
            >
              <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
                <p className="text-xs text-slate-500">Source</p>
                <p className="text-sm font-semibold" data-testid="itc-statement-source">
                  {statement.source === "PORTAL_UPLOAD" ? "Portal upload" : statement.source}
                </p>
              </div>
              <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
                <p className="text-xs text-slate-500">Imported</p>
                <p className="text-sm font-semibold" data-testid="itc-statement-imported">
                  {isoDate(statement.downloaded_at)}
                </p>
              </div>
              <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
                <p className="text-xs text-slate-500">2B entries</p>
                <p className="text-sm font-semibold" data-testid="itc-statement-entries">
                  {statement.entry_count}
                </p>
              </div>
            </div>
          )}

          <button
            type="button"
            onClick={() => void handleReconcile()}
            disabled={reconciling || statement === null}
            className="mt-5 rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
            data-testid="itc-reconcile"
          >
            {reconciling ? "Reconciling…" : "Run reconciliation"}
          </button>
        </section>

        {/* 2. Status filter chips */}
        <section
          className="mt-8 rounded-xl border border-slate-200 p-6 dark:border-slate-800"
          data-testid="itc-filter-section"
        >
          <h2 className="font-semibold">2. Match status</h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Filter the reconciliation table by verdict. Counts cover every row.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setFilter("ALL")}
              aria-pressed={filter === "ALL"}
              className={
                filter === "ALL"
                  ? "rounded-full bg-slate-900 px-3 py-1 text-xs font-semibold text-white dark:bg-slate-100 dark:text-slate-900"
                  : "rounded-full border border-slate-300 px-3 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
              }
              data-testid="itc-filter-ALL"
            >
              All · {counts.total ?? 0}
            </button>
            {ITC_MATCH_STATUSES.map((status) => (
              <button
                key={status}
                type="button"
                onClick={() => setFilter(status)}
                aria-pressed={filter === status}
                className={
                  filter === status
                    ? "rounded-full bg-slate-900 px-3 py-1 text-xs font-semibold text-white dark:bg-slate-100 dark:text-slate-900"
                    : "rounded-full border border-slate-300 px-3 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                }
                data-testid={`itc-filter-${status}`}
              >
                {STATUS_LABELS[status]} · {counts[status] ?? 0}
              </button>
            ))}
          </div>
        </section>

        {/* 3. Books vs 2B table */}
        <section
          className="mt-8 rounded-xl border border-slate-200 p-6 dark:border-slate-800"
          data-testid="itc-table-section"
        >
          <h2 className="font-semibold">3. Books vs GSTR-2B</h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Each row pairs the confirmed purchase invoice with its 2B entry.
            Amounts are the portal-stated figures; both sides are integer paise.
          </p>
          {visible.length === 0 ? (
            <p
              className="mt-4 text-sm text-slate-500 dark:text-slate-400"
              data-testid="itc-table-empty"
            >
              No rows for this filter.
            </p>
          ) : (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left text-[13px]" data-testid="itc-table">
                <thead>
                  <tr className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-700">
                    <th className="py-2 pr-3 font-medium">Status</th>
                    <th className="py-2 pr-3 font-medium">Invoice no</th>
                    <th className="py-2 pr-3 font-medium">Supplier GSTIN</th>
                    <th className="py-2 pr-3 font-medium">Books taxable</th>
                    <th className="py-2 pr-3 font-medium">2B taxable</th>
                    <th className="py-2 pr-3 font-medium">Books tax</th>
                    <th className="py-2 pr-3 font-medium">2B tax</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((row) => (
                    <tr
                      key={row.id}
                      className="border-b border-slate-100 dark:border-slate-800"
                      data-testid="itc-row"
                    >
                      <td className="py-2 pr-3">
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                            STATUS_CLASSES[row.match_status] ??
                            "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                          }`}
                          data-testid="itc-badge"
                        >
                          {row.match_status}
                        </span>
                      </td>
                      <td className="py-2 pr-3 font-mono">
                        {row.books?.invoice_no ?? row.gstr2b?.invoice_no ?? "—"}
                      </td>
                      <td className="py-2 pr-3 font-mono">
                        {row.books?.supplier_gstin ?? row.gstr2b?.supplier_gstin ?? "—"}
                      </td>
                      <td className="py-2 pr-3">
                        {row.books === null ? "—" : rupees(row.books.taxable_value_paise)}
                      </td>
                      <td className="py-2 pr-3">
                        {row.gstr2b === null
                          ? "—"
                          : rupees(row.gstr2b.taxable_value_paise)}
                      </td>
                      <td className="py-2 pr-3">
                        {row.books === null ? "—" : rupees(row.books.total_paise)}
                      </td>
                      <td className="py-2 pr-3">
                        {row.gstr2b === null ? "—" : rupees(row.gstr2b.total_paise)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* 4. ITC summary prefill card for GSTR-3B table 4(A) */}
        {report !== null && (
          <section
            className="mt-8 rounded-xl border border-slate-200 p-6 dark:border-slate-800"
            data-testid="itc-summary-section"
          >
            <h2 className="font-semibold">4. GSTR-3B ITC prefill</h2>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              Input tax credit per the books and per GSTR-2B. The delta is books
              minus 2B — a non-zero delta is ITC you cannot claim without
              fixing the underlying document.
            </p>
            <div className="mt-4 overflow-x-auto">
              <table
                className="w-full text-left text-[13px]"
                data-testid="itc-summary-table"
              >
                <thead>
                  <tr className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-700">
                    <th className="py-2 pr-3 font-medium">Tax head</th>
                    <th className="py-2 pr-3 font-medium">Books</th>
                    <th className="py-2 pr-3 font-medium">GSTR-2B</th>
                    <th className="py-2 pr-3 font-medium">Delta (books − 2B)</th>
                  </tr>
                </thead>
                <tbody>
                  {TAX_HEADS.map((head) => {
                    const books = bucketValue(report.summary.books_itc_paise, head.key);
                    const twoB = bucketValue(report.summary.gstr2b_itc_paise, head.key);
                    const delta = bucketValue(report.summary.delta_itc_paise, head.key);
                    return (
                      <tr
                        key={head.key}
                        className="border-b border-slate-100 dark:border-slate-800"
                        data-testid={`itc-summary-${head.key}`}
                      >
                        <td className="py-2 pr-3">{head.label}</td>
                        <td className="py-2 pr-3">{rupees(books)}</td>
                        <td className="py-2 pr-3">{rupees(twoB)}</td>
                        <td
                          className={`py-2 pr-3 ${
                            delta === 0
                              ? "text-slate-600 dark:text-slate-300"
                              : "text-amber-700 dark:text-amber-300"
                          }`}
                        >
                          {rupees(delta)}
                        </td>
                      </tr>
                    );
                  })}
                  <tr
                    className="font-semibold text-slate-700 dark:text-slate-200"
                    data-testid="itc-summary-total"
                  >
                    <td className="py-2 pr-3">Total</td>
                    <td className="py-2 pr-3">
                      {rupees(report.summary.books_itc_paise.total_paise)}
                    </td>
                    <td className="py-2 pr-3">
                      {rupees(report.summary.gstr2b_itc_paise.total_paise)}
                    </td>
                    <td className="py-2 pr-3">
                      {rupees(report.summary.delta_itc_paise.total_paise)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* 5. Chase-supplier list */}
        <section
          className="mt-8 rounded-xl border border-slate-200 p-6 dark:border-slate-800"
          data-testid="itc-chase-section"
        >
          <h2 className="font-semibold">5. Chase supplier</h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Purchases in your books that the supplier has not reported in GSTR-2B.
            Until they file, this ITC is at risk — ask them to report the invoice.
          </p>
          {chaseRows.length === 0 ? (
            <p
              className="mt-4 text-sm text-slate-500 dark:text-slate-400"
              data-testid="itc-chase-empty"
            >
              Nothing to chase — every books document appears in GSTR-2B.
            </p>
          ) : (
            <ul className="mt-4 space-y-2" data-testid="itc-chase-list">
              {chaseRows.map((row) => (
                <li
                  key={`chase-${row.id}`}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200"
                  data-testid="itc-chase-row"
                >
                  <div>
                    <span className="font-mono text-xs">
                      {row.books?.supplier_gstin ?? "—"}
                    </span>
                    {" · "}
                    {row.books?.invoice_no ?? "—"}
                    {" · "}
                    {row.books === null ? "—" : rupees(row.books.taxable_value_paise)}
                    {row.books !== null && (
                      <>
                        {" · tax "}
                        {rupees(row.books.total_paise)}
                      </>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleOpenNotice(row)}
                    disabled={noticeLoading}
                    className="rounded bg-amber-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-amber-700 disabled:opacity-50"
                    data-testid="vendor-notice-btn"
                  >
                    Notice →
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

      {/* 6. Discrepancy Notice Modal (Task 12.2) */}
      {selectedNotice !== null && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          data-testid="notice-modal"
        >
          <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl bg-white p-6 shadow-2xl dark:bg-slate-900">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="text-lg font-semibold text-slate-900 dark:text-white">
                  Vendor Discrepancy Notice
                </h3>
                <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
                  {selectedNotice.statutory_citation}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedNotice(null)}
                className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
                data-testid="close-notice-modal"
              >
                ✕
              </button>
            </div>

            <div className="mt-4 rounded-lg bg-slate-50 p-3 text-xs dark:bg-slate-800">
              <span className="font-semibold">Invoice:</span> {selectedNotice.invoice_no} ·{" "}
              <span className="font-semibold">Supplier:</span> {selectedNotice.supplier_gstin} ·{" "}
              <span className="font-semibold">Tax:</span> {rupees(selectedNotice.tax_paise)}
            </div>

            {/* Formal Notice */}
            <div className="mt-4">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Formal Notice Copy
                </h4>
                <button
                  type="button"
                  onClick={() => copyText(selectedNotice.formal_notice, "formal")}
                  className="text-xs font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                  data-testid="copy-formal-notice"
                >
                  {copiedType === "formal" ? "Copied!" : "Copy Notice"}
                </button>
              </div>
              <pre className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-3 font-mono text-xs text-slate-700 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300">
                {selectedNotice.formal_notice}
              </pre>
            </div>

            {/* WhatsApp Draft */}
            <div className="mt-4">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  WhatsApp Draft
                </h4>
                <button
                  type="button"
                  onClick={() => copyText(selectedNotice.whatsapp_draft, "whatsapp")}
                  className="text-xs font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                  data-testid="copy-whatsapp-draft"
                >
                  {copiedType === "whatsapp" ? "Copied!" : "Copy WhatsApp"}
                </button>
              </div>
              <pre className="mt-2 whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-3 font-mono text-xs text-slate-700 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300">
                {selectedNotice.whatsapp_draft}
              </pre>
            </div>

            {/* Action buttons */}
            <div className="mt-6 flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={() => setSelectedNotice(null)}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
              >
                Close
              </button>
              <a
                href={selectedNotice.whatsapp_url}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-md bg-green-600 px-4 py-2 text-sm font-semibold text-white hover:bg-green-700"
                data-testid="open-whatsapp-link"
              >
                Send via WhatsApp ↗
              </a>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
