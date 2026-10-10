"use client";

/**
 * Bulk Excel/CSV import — Task 11.1 (PHASE11_13_RECOMMENDATION_BUILD.md rec 1).
 *
 * Three steps, mirroring the backend contract exactly:
 *   1. pick a .xlsx / .csv export and POST it with confirm=false
 *      -> the backend's detected-column mapping + required-column check
 *         + the first few rows of the file (nothing is written);
 *   2. eyeball/repair the mapping (unmapped headers are listed; a required
 *      group that could not be mapped blocks the import and says which);
 *   3. POST the same file with confirm=true
 *      -> one `extraction.invoice_drafts` row per valid line, with every
 *         rejected row reported individually.
 *
 * Additive: a separate component. The single-file card and the batch queue on
 * the upload page keep their own testids and behaviour untouched. Every label
 * rendered here comes from the backend report — no client-side status
 * invention.
 */
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError, BulkImportReport, bulkImport } from "@/lib/api/client";

/** Same accept list the backend parses (magic bytes decide, not the name). */
export const BULK_ACCEPTED_TYPES = ".xlsx,.csv,text/csv";

type Step = "IDLE" | "PREVIEWING" | "PREVIEWED" | "IMPORTING" | "IMPORTED";

function stepLabel(step: Step): string {
  switch (step) {
    case "IDLE":
      return "choose a file";
    case "PREVIEWING":
      return "reading the file…";
    case "PREVIEWED":
      return "mapping detected";
    case "IMPORTING":
      return "importing…";
    case "IMPORTED":
      return "import finished";
  }
}

export function BulkImportTab({ gstin, fp }: { gstin: string; fp: string }) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [step, setStep] = useState<Step>("IDLE");
  const [report, setReport] = useState<BulkImportReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  function pick(selected: File | null) {
    setFile(selected);
    setReport(null);
    setError(null);
    setStep("IDLE");
  }

  async function run(confirm: boolean) {
    if (file === null) return;
    setStep(confirm ? "IMPORTING" : "PREVIEWING");
    setError(null);
    try {
      const result = await bulkImport(gstin, fp, file, confirm);
      setReport(result);
      setStep(confirm ? "IMPORTED" : "PREVIEWED");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "import failed");
      setStep("IDLE");
    }
  }

  const blocked = report !== null && report.required_missing.length > 0;

  return (
    <section className="mt-8 space-y-6" data-testid="bulk-import-panel">
      <div>
        <h2 className="text-lg font-semibold">Bulk Excel / CSV import</h2>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Upload a Tally / Busy / Zoho export (.xlsx or .csv). Columns are
          auto-detected; each row is validated on its own and imported into the
          draft queue — a bad row is reported, never silently dropped and never
          a reason to reject the whole file.
        </p>
      </div>

      <div
        className="cursor-pointer rounded-xl border-2 border-dashed border-slate-300 p-8 text-center dark:border-slate-700"
        data-testid="bulk-dropzone"
      >
        <input
          type="file"
          accept={BULK_ACCEPTED_TYPES}
          className="hidden"
          id="bulk-file-input"
          onChange={(e) => pick(e.target.files?.[0] ?? null)}
          data-testid="bulk-file-input"
        />
        <label htmlFor="bulk-file-input" className="cursor-pointer">
          {file === null ? (
            <>
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Click to choose a .xlsx or .csv file
              </p>
              <p className="mt-2 text-xs text-slate-400">
                Invoice No · Invoice Date · Supplier/Buyer GSTIN · Taxable Value
                · GST Rate · HSN
              </p>
            </>
          ) : (
            <p
              className="text-sm font-medium text-indigo-700 dark:text-indigo-300"
              data-testid="bulk-file-name"
            >
              {file.name}
            </p>
          )}
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void run(false)}
          disabled={file === null || step === "PREVIEWING" || step === "IMPORTING"}
          className="rounded-md bg-slate-700 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
          data-testid="bulk-preview"
        >
          Detect columns
        </button>
        <button
          type="button"
          onClick={() => void run(true)}
          disabled={
            file === null || blocked || step === "PREVIEWING" || step === "IMPORTING"
          }
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
          data-testid="bulk-confirm"
        >
          Import rows
        </button>
        <span className="text-xs text-slate-500 dark:text-slate-400" data-testid="bulk-step">
          {stepLabel(step)}
        </span>
      </div>

      {error !== null && (
        <p
          className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
          role="alert"
          data-testid="bulk-error"
        >
          {error}
        </p>
      )}

      {report !== null && (
        <div className="space-y-6" data-testid="bulk-report">
          <p className="text-sm" data-testid="bulk-file-summary">
            <span className="font-medium">{report.filename}</span> ·{" "}
            {report.format} · {report.data_rows} row(s) found
          </p>

          {blocked && (
            <div
              className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200"
              data-testid="bulk-required-missing"
            >
              No column could be mapped for:{" "}
              <span className="font-mono">{report.required_missing.join(", ")}</span>
              . Add or rename the column in the file (or send an explicit
              mapping) — nothing is imported until every required column is
              mapped.
            </div>
          )}

          <div>
            <h3 className="text-sm font-semibold">Detected columns</h3>
            <table className="mt-2 w-full text-left text-xs">
              <thead className="text-slate-500 dark:text-slate-400">
                <tr>
                  <th className="py-1 pr-2 font-medium">Field</th>
                  <th className="py-1 pr-2 font-medium">Column in file</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(report.mapping).map(([field, header]) => (
                  <tr
                    key={field}
                    className="border-t border-slate-100 dark:border-slate-800"
                    data-testid="bulk-mapping-row"
                  >
                    <td className="py-1 pr-2 font-mono">{field}</td>
                    <td className="py-1 pr-2" data-testid={`bulk-mapping-${field}`}>
                      {header}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {report.unmapped_headers.length > 0 && (
              <p className="mt-2 text-xs text-slate-500 dark:text-slate-400" data-testid="bulk-unmapped">
                Not used: <span className="font-mono">{report.unmapped_headers.join(", ")}</span>
              </p>
            )}
          </div>

          {report.sample.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold">First rows as read</h3>
              <table className="mt-2 w-full text-left text-xs">
                <thead className="text-slate-500 dark:text-slate-400">
                  <tr>
                    <th className="py-1 pr-2 font-medium">Row</th>
                    {Object.keys(report.sample[0].values).map((field) => (
                      <th key={field} className="py-1 pr-2 font-medium font-mono">
                        {field}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {report.sample.map((sample) => (
                    <tr
                      key={sample.row}
                      className="border-t border-slate-100 dark:border-slate-800"
                      data-testid="bulk-sample-row"
                    >
                      <td className="py-1 pr-2 text-slate-500 dark:text-slate-400">
                        {sample.row}
                      </td>
                      {Object.keys(report.sample[0].values).map((field) => (
                        <td key={field} className="py-1 pr-2">
                          {sample.values[field] ?? "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {report.confirmed && (
            <div data-testid="bulk-result">
              <h3 className="text-sm font-semibold">
                Import result:{" "}
                <span className="text-green-700 dark:text-green-300" data-testid="bulk-imported">
                  {report.imported} imported
                </span>
                {report.invalid > 0 && (
                  <>
                    {" · "}
                    <span className="text-red-700 dark:text-red-300" data-testid="bulk-invalid">
                      {report.invalid} rejected
                    </span>
                  </>
                )}
              </h3>

              <table className="mt-2 w-full text-left text-xs">
                <thead className="text-slate-500 dark:text-slate-400">
                  <tr>
                    <th className="py-1 pr-2 font-medium">Row</th>
                    <th className="py-1 pr-2 font-medium">Invoice</th>
                    <th className="py-1 pr-2 font-medium">Status</th>
                    <th className="py-1 pr-2 font-medium">Reason / flags</th>
                  </tr>
                </thead>
                <tbody>
                  {report.rows.map((row) => (
                    <tr
                      key={row.row}
                      className="border-t border-slate-100 dark:border-slate-800"
                      data-testid="bulk-result-row"
                    >
                      <td className="py-1 pr-2 text-slate-500 dark:text-slate-400">
                        {row.row}
                      </td>
                      <td className="py-1 pr-2 font-mono">{row.invoice_no ?? "—"}</td>
                      <td className="py-1 pr-2" data-testid="bulk-result-status">
                        <span
                          className={
                            row.status === "IMPORTED"
                              ? "text-green-700 dark:text-green-300"
                              : "text-red-700 dark:text-red-300"
                          }
                        >
                          {row.status}
                        </span>
                      </td>
                      <td className="py-1 pr-2" data-testid="bulk-result-detail">
                        {row.errors.length > 0 ? (
                          <ul className="list-disc pl-4">
                            {row.errors.map((message) => (
                              <li key={message}>{message}</li>
                            ))}
                          </ul>
                        ) : row.flags.length > 0 ? (
                          <ul className="list-disc pl-4">
                            {row.flags.map((flag) => (
                              <li key={`${flag.rule}-${flag.message}`}>
                                {flag.rule}: {flag.message}
                              </li>
                            ))}
                          </ul>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {report.imported > 0 && (
                <button
                  type="button"
                  onClick={() => router.push(`/app/file/${gstin}/${fp}`)}
                  className="mt-3 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700"
                  data-testid="bulk-go-to-queue"
                >
                  Review imported drafts →
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
