"use client";

/**
 * Task 11.2 — 1-click Batch Confirm for clean high-confidence invoices
 * (PHASE11_13_RECOMMENDATION_BUILD.md rec 2).
 *
 * Button on the review queue (the documents list carries the period's
 * NEEDS_REVIEW rows). Gating is honest — driven by the backend's dry_run
 * evaluation, never a client-side heuristic:
 *
 *   on mount (and after each confirm) POST batch-confirm?dry_run=true
 *     -> the button is DISABLED while the clean count is 0 (and enabled with
 *        the real count when it isn't);
 *   click -> the real POST -> every clean (no BLOCK flag) >= 0.95 draft is
 *     confirmed; below-threshold / flagged / raced drafts stay pending and
 *     are listed with the backend's reason (no client invention).
 *
 * Additive: the document rows and their Review links are untouched.
 */
import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  BatchConfirmResult,
  batchConfirm,
} from "@/lib/api/client";

const QUARANTINE_REASON_LABELS: Record<string, string> = {
  BLOCKING_FLAGS: "validation flags",
  BELOW_THRESHOLD: "confidence below 95%",
};

/** Paise -> ₹ display (the one sanctioned boundary crossing, at render). */
function rupees(paise: number): string {
  return `₹${(paise / 100).toFixed(2)}`;
}

export function BatchConfirmPanel({ gstin, fp }: { gstin: string; fp: string }) {
  /** Evaluated clean count from the backend's dry run (null = not yet known). */
  const [cleanCount, setCleanCount] = useState<number | null>(null);
  const [evaluating, setEvaluating] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<BatchConfirmResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const evaluate = useCallback(async () => {
    setEvaluating(true);
    setError(null);
    try {
      const preview = await batchConfirm(gstin, fp, true);
      setCleanCount(preview.confirmed);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "batch evaluation failed");
      setCleanCount(null);
    } finally {
      setEvaluating(false);
    }
  }, [gstin, fp]);

  useEffect(() => {
    void evaluate();
  }, [evaluate]);

  async function run() {
    setConfirming(true);
    setError(null);
    try {
      const outcome = await batchConfirm(gstin, fp, false);
      setResult(outcome);
      // Re-evaluate so the button gates on the post-call reality (typically
      // 0 -> disabled again, "No clean drafts to confirm").
      await evaluate();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "batch confirm failed");
    } finally {
      setConfirming(false);
    }
  }

  const disabled = evaluating || confirming || cleanCount === null || cleanCount === 0;
  const label =
    confirming
      ? "Confirming…"
      : evaluating
        ? "Checking clean drafts…"
        : disabled
          ? "No clean drafts to confirm"
          : `Batch Confirm ${cleanCount} clean invoice${cleanCount === 1 ? "" : "s"}`;

  return (
    <section
      className="rounded-xl border border-slate-200 p-4 dark:border-slate-800"
      data-testid="batch-confirm-panel"
    >
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">Batch confirm</h2>
          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
            Confirms every draft with no blocking flag and confidence ≥ 95% in
            one click. The rest stay in review.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void run()}
          disabled={disabled}
          className="shrink-0 rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
          data-testid="batch-confirm-btn"
        >
          {label}
        </button>
      </div>

      {error !== null && (
        <p
          className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
          role="alert"
          data-testid="batch-confirm-error"
        >
          {error}
        </p>
      )}

      {result !== null && (
        <div
          className="mt-3 rounded-md bg-green-50 px-3 py-2 text-sm text-green-800 dark:bg-green-950 dark:text-green-200"
          data-testid="batch-confirm-result"
        >
          <span data-testid="batch-confirm-count">{result.confirmed}</span>{" "}
          confirmed
          {result.total_paise > 0 && (
            <>
              {" · "}
              <span data-testid="batch-confirm-total">{rupees(result.total_paise)}</span>
            </>
          )}
          {result.quarantined > 0 && (
            <>
              {" · "}
              <span data-testid="batch-confirm-quarantined">
                {result.quarantined} left in review
              </span>
            </>
          )}
        </div>
      )}

      {result !== null && result.quarantine.length > 0 && (
        <table className="mt-2 w-full text-left text-xs" data-testid="batch-confirm-quarantine">
          <thead className="text-slate-500 dark:text-slate-400">
            <tr>
              <th className="py-1 pr-2 font-medium">Invoice</th>
              <th className="py-1 pr-2 font-medium">Confidence</th>
              <th className="py-1 pr-2 font-medium">Why it stayed in review</th>
            </tr>
          </thead>
          <tbody>
            {result.quarantine.map((row) => (
              <tr
                key={row.draft_id}
                className="border-t border-slate-100 dark:border-slate-800"
                data-testid="batch-confirm-quarantine-row"
              >
                <td className="py-1 pr-2 font-mono">{row.invoice_no ?? "—"}</td>
                <td className="py-1 pr-2">
                  {row.confidence_avg === null
                    ? "—"
                    : `${Math.round(row.confidence_avg * 100)}%`}
                </td>
                <td className="py-1 pr-2">
                  {QUARANTINE_REASON_LABELS[row.reason] ?? row.reason}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}