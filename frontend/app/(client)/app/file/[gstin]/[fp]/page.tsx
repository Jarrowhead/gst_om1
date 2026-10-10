"use client";

/**
 * File page for a GSTIN + period — two surfaces, both kept (additive):
 * 1. The document queue (Task 7.2): uploaded documents with extraction
 *    status, linking into the review-confirm screen.
 * 2. The Phase-4 GSP filing hub: sandbox direct filing for GSTR-1 and
 *    GSTR-3B (restored by Task 8.11 — 7.2 had silently retired it, breaking
 *    the phase4-filing spec contract; never retire a shipped screen).
 * GSTIN + period are route params.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import {
  ApiError,
  DocumentRow,
  fileGstr1ViaGsp,
  fileGstr3bViaGsp,
  GspFileResult,
  listDocuments,
} from "@/lib/api/client";
import { BatchConfirmPanel } from "@/components/upload/BatchConfirmPanel";
import { isTerminalStatus } from "@/lib/extraction/status";

/** Re-list interval while any row is non-terminal (Task 9.2: 5s). */
const QUEUE_REFRESH_MS = 5_000;

function statusColor(status: string) {
  switch (status) {
    case "CONFIRMED":
      return "text-green-700 dark:text-green-300";
    case "NEEDS_REVIEW":
      return "text-amber-700 dark:text-amber-300";
    case "FAILED":
      return "text-red-700 dark:text-red-300";
    default:
      return "text-slate-500 dark:text-slate-400";
  }
}

export default function FileQueuePage() {
  const params = useParams<{ gstin: string; fp: string }>();
  const { gstin, fp } = params;

  const [rows, setRows] = useState<DocumentRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [busy, setBusy] = useState<"gstr1" | "gstr3b" | null>(null);
  const [fileResult, setFileResult] = useState<GspFileResult | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    /** Re-list the queue; resolves to whether any row is still non-terminal. */
    async function load(): Promise<boolean> {
      let hadRows = false;
      try {
        const data = await listDocuments(gstin, fp);
        if (!cancelled) {
          setRows(data.content);
          hadRows = data.content.length > 0;
        }
        return data.content.some(
          (doc) => !isTerminalStatus(doc.job?.status ?? "UNKNOWN"),
        );
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "failed to load");
        }
        // A transient failure while rows are on screen keeps the poller
        // alive (same policy as the upload poller); a hard first-load
        // failure stops it.
        return hadRows;
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load().then((anyNonTerminal) => {
      if (!cancelled && anyNonTerminal) {
        // Task 9.2: re-list every 5s while any row is non-terminal; the
        // interval clears itself once every row reaches a terminal status
        // and is cleaned up on unmount.
        timer = setInterval(() => {
          void load().then((still) => {
            if (!still && timer !== null) {
              clearInterval(timer);
              timer = null;
            }
          });
        }, QUEUE_REFRESH_MS);
      }
    });

    return () => {
      cancelled = true;
      if (timer !== null) clearInterval(timer);
    };
  }, [gstin, fp]);

  async function fileGstr1() {
    setBusy("gstr1");
    setFileError(null);
    setFileResult(null);
    try {
      const res = await fileGstr1ViaGsp(gstin, fp);
      setFileResult(res);
    } catch (err) {
      setFileError(err instanceof ApiError ? err.message : "GSTR-1 filing failed");
    } finally {
      setBusy(null);
    }
  }

  async function fileGstr3b() {
    setBusy("gstr3b");
    setFileError(null);
    setFileResult(null);
    try {
      const res = await fileGstr3bViaGsp(gstin, fp);
      setFileResult(res);
    } catch (err) {
      setFileError(err instanceof ApiError ? err.message : "GSTR-3B filing failed");
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <main className="mx-auto w-full max-w-4xl px-6 py-8">
        <p className="text-sm text-slate-500">Loading documents…</p>
      </main>
    );
  }

  return (
    <main className="mx-auto w-full max-w-4xl px-6 py-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Documents</h1>
        <Link
          href={`/app/upload/${gstin}/${fp}`}
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700"
          data-testid="upload-more"
        >
          + Upload
        </Link>
      </div>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        GSTIN: <span className="font-mono">{gstin}</span> · Period:{" "}
        <span className="font-mono">{fp}</span>
      </p>

      {error !== null && (
        <p className="mt-6 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
          {error}
        </p>
      )}

      {loading ? null : (
        <div className="mt-6">
          <BatchConfirmPanel gstin={gstin} fp={fp} />
        </div>
      )}

      {rows.length === 0 ? (
        <div className="mt-8 rounded-xl border border-dashed border-slate-300 p-10 text-center dark:border-slate-700">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            No documents yet.
          </p>
          <Link
            href={`/app/upload/${gstin}/${fp}`}
            className="mt-4 inline-block text-sm font-medium text-indigo-600 hover:underline dark:text-indigo-400"
          >
            Upload your first invoice →
          </Link>
        </div>
      ) : (
        <div className="mt-6 space-y-3">
          {rows.map((doc) => (
            <div
              key={doc.id}
              className="flex items-center justify-between rounded-lg border border-slate-200 p-4 dark:border-slate-800"
              data-testid="document-row"
            >
              <div>
                <p className="text-sm font-medium">{doc.doc_type}</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {doc.capture_source} · {doc.page_count ?? 1} page
                  {doc.page_count === 1 ? "" : "s"}
                </p>
              </div>
              <div className="flex items-center gap-4">
                <span className={`text-sm font-semibold ${statusColor(doc.job?.status ?? "UNKNOWN")}`}>
                  {doc.job?.status ?? "UNKNOWN"}
                </span>
                <Link
                  href={`/app/review/${gstin}/${fp}/${doc.id}`}
                  className="text-sm font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                  data-testid="review-link"
                >
                  Review →
                </Link>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Phase-4 GSP filing hub (restored additively by 8.11). */}
      <section className="mt-10 border-t border-slate-200 pt-8 dark:border-slate-800" data-testid="gsp-filing-hub">
        <h2 className="text-2xl font-semibold">Direct filing</h2>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Sandbox GSP filing for this GSTIN and period.
        </p>

        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          <div className="rounded-xl border border-slate-200 p-6 dark:border-slate-800">
            <h3 className="font-semibold">GSTR-1</h3>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              File outward return via GSP sandbox.
            </p>
            <button
              type="button"
              onClick={fileGstr1}
              disabled={busy !== null}
              className="mt-4 w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
              data-testid="file-gstr1-btn"
            >
              {busy === "gstr1" ? "Filing…" : "File GSTR-1"}
            </button>
          </div>

          <div className="rounded-xl border border-slate-200 p-6 dark:border-slate-800">
            <h3 className="font-semibold">GSTR-3B</h3>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              File summary return via GSP sandbox.
            </p>
            <button
              type="button"
              onClick={fileGstr3b}
              disabled={busy !== null}
              className="mt-4 w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
              data-testid="file-gstr3b-btn"
            >
              {busy === "gstr3b" ? "Filing…" : "File GSTR-3B"}
            </button>
          </div>
        </div>

        {fileResult !== null && (
          <div
            className="mt-6 rounded-xl border border-green-200 bg-green-50 p-4 dark:border-green-900 dark:bg-green-950"
            data-testid="file-result"
          >
            <p className="text-sm font-medium text-green-800 dark:text-green-200">
              {fileResult.status}
            </p>
            <p className="mt-1 font-mono text-xs text-green-700 dark:text-green-300">
              Ref: {fileResult.ref_id} · ACK: {fileResult.ack_no}
            </p>
          </div>
        )}

        {fileError !== null && (
          <p
            className="mt-6 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
            role="alert"
            data-testid="file-error"
          >
            {fileError}
          </p>
        )}
      </section>
    </main>
  );
}
