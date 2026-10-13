"use client";

/**
 * Batch upload queue — Task 9.2B (PHASE9_QA_SWEEP_FIXES.md §3.9.2B).
 *
 * Multi-file picker + drop for a small batch (≤10 files, 25 MB/file cap
 * unchanged — the backend enforces the cap and rejects oversized files
 * per-row). Files are uploaded one at a time through the existing
 * `uploadDocument` + `triggerExtraction` client calls; each queued file has
 * its own row showing upload → extraction status, its own failure state and
 * a per-row retry.
 *
 * Additive: this is a *separate* component. The single-file card on the
 * upload page (testids `file-input` / `upload-submit` / `upload-success`)
 * is untouched and keeps working exactly as before.
 *
 * ZIP-archive expansion is out of scope (backend single-document API
 * unchanged); only PDF/JPEG/PNG are accepted, same as the single-file card.
 * No new backend endpoints. No fabricated statuses: every label rendered
 * here is a backend JobStatus value.
 */
import { useRouter } from "next/navigation";
import { useState } from "react";

import {
  ApiError,
  getDocument,
  triggerExtraction,
  uploadDocument,
} from "@/lib/api/client";
import {
  ExtractionPollState,
  isDraftReady,
  isTerminalStatus,
  useExtractionStatusPollingMulti,
} from "@/lib/extraction/status";

/** Max files per batch (doc: small batch ≤10 files). */
export const MAX_BATCH_FILES = 200;
/** Same accept list as the single-file card. */
export const ACCEPTED_UPLOAD_TYPES = ".pdf,image/jpeg,image/png";

type CaptureSource = "PDF_SCAN" | "DIGITAL" | "PHOTO" | "WHATSAPP";

const CAPTURE_LABELS: Record<CaptureSource, string> = {
  PDF_SCAN: "PDF scan",
  DIGITAL: "Native PDF / digital",
  PHOTO: "Phone photo",
  WHATSAPP: "WhatsApp photo",
};

export type BatchRowStatus =
  | "PENDING"
  | "UPLOADING"
  | "WAITING"
  | "UPLOAD_FAILED";

export interface BatchRow {
  id: string;
  name: string;
  size: number;
  file: File;
  status: BatchRowStatus;
  docId: string | null;
  /** Job status captured at upload time; seeds the row poller. */
  initialStatus: string;
  error: string | null;
}

let rowSeq = 0;

function nextRowId(): string {
  rowSeq += 1;
  return `batch-row-${Date.now()}-${rowSeq}`;
}

function sameFile(a: { name: string; size: number; lastModified: number }, b: BatchRow): boolean {
  return a.name === b.name && a.size === b.size && a.lastModified === b.file.lastModified;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Live status label for a row: upload phase wins, then the polled job. */
function rowLabel(row: BatchRow, poll: ExtractionPollState | undefined): string {
  if (row.status === "PENDING") return "PENDING";
  if (row.status === "UPLOADING") return "UPLOADING";
  if (row.status === "UPLOAD_FAILED") return "UPLOAD_FAILED";
  return poll?.status ?? row.initialStatus;
}

/** A row is settled when it failed to upload or its job reached a terminal status. */
function rowSettled(row: BatchRow, poll: ExtractionPollState | undefined): boolean {
  if (row.status === "UPLOAD_FAILED") return true;
  if (row.docId === null) return false;
  return isTerminalStatus(poll?.status ?? row.initialStatus);
}

function labelColor(label: string): string {
  if (label === "UPLOAD_FAILED" || label === "FAILED") {
    return "text-red-700 dark:text-red-300";
  }
  if (label === "EXTRACTED" || label === "CONFIRMED") {
    return "text-green-700 dark:text-green-300";
  }
  if (label === "NEEDS_REVIEW") return "text-amber-700 dark:text-amber-300";
  return "text-slate-500 dark:text-slate-400";
}

export function BatchUploadQueue({ gstin, fp }: { gstin: string; fp: string }) {
  const router = useRouter();
  const [captureSource, setCaptureSource] = useState<CaptureSource>("PDF_SCAN");
  const [rows, setRows] = useState<BatchRow[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const docIds = rows
    .map((r) => r.docId)
    .filter((id): id is string => id !== null);
  const initialStatuses: Record<string, string> = {};
  for (const row of rows) {
    if (row.docId !== null) initialStatuses[row.docId] = row.initialStatus;
  }
  const live = useExtractionStatusPollingMulti(docIds, initialStatuses);

  /** Upload + trigger extraction for each target row, sequentially. */
  async function runQueue(targets: BatchRow[]) {
    const CONCURRENCY = 4;
    let nextIdx = 0;

    async function processTarget(target: BatchRow) {
      setRows((prev) =>
        prev.map((r) =>
          r.id === target.id ? { ...r, status: "UPLOADING", error: null } : r,
        ),
      );
      try {
        const res = await uploadDocument(gstin, fp, captureSource, target.file);
        const seeded = res.job?.status ?? "QUEUED";
        setRows((prev) =>
          prev.map((r) =>
            r.id === target.id
              ? {
                  ...r,
                  status: "WAITING",
                  docId: res.id,
                  initialStatus: seeded,
                  error: null,
                }
              : r,
          ),
        );
        try {
          await triggerExtraction(res.id);
          const detail = await getDocument(res.id);
          const after = detail.job?.status ?? seeded;
          setRows((prev) =>
            prev.map((r) =>
              r.id === target.id ? { ...r, initialStatus: after } : r,
            ),
          );
        } catch {
          // Extraction trigger/read failed
        }
      } catch (err) {
        const message = err instanceof ApiError ? err.message : "upload failed";
        setRows((prev) =>
          prev.map((r) =>
            r.id === target.id
              ? { ...r, status: "UPLOAD_FAILED", docId: null, error: message }
              : r,
          ),
        );
      }
    }

    async function worker() {
      while (nextIdx < targets.length) {
        const target = targets[nextIdx++];
        if (target) {
          await processTarget(target);
        }
      }
    }

    const workerCount = Math.min(CONCURRENCY, targets.length);
    await Promise.all(Array.from({ length: workerCount }, () => worker()));
  }

  /** Add picked files to the queue and start uploading them. */
  function enqueue(picked: File[]) {
    setNotice(null);
    const room = Math.max(0, MAX_BATCH_FILES - rows.length);
    const accepted: BatchRow[] = [];
    let skipped = 0;
    for (const file of picked) {
      const mimeOk =
        file.type === "application/pdf" ||
        file.type === "image/jpeg" ||
        file.type === "image/png";
      const alreadyQueued = rows.some((r) => sameFile(file, r));
      if (accepted.length >= room || !mimeOk || alreadyQueued) {
        skipped += 1;
        continue;
      }
      accepted.push({
        id: nextRowId(),
        name: file.name,
        size: file.size,
        file,
        status: "PENDING",
        docId: null,
        initialStatus: "QUEUED",
        error: null,
      });
    }
    if (skipped > 0) {
      setNotice(
        `${skipped} file(s) skipped — PDF/JPG/PNG only, max ${MAX_BATCH_FILES} per batch, no duplicates.`,
      );
    }
    if (accepted.length === 0) return;
    setRows((prev) => [...prev, ...accepted]);
    void runQueue(accepted);
  }

  function retryRow(rowId: string) {
    const row = rows.find((r) => r.id === rowId);
    if (!row) return;
    setRows((prev) =>
      prev.map((r) =>
        r.id === rowId
          ? { ...r, status: "PENDING", docId: null, error: null }
          : r,
      ),
    );
    void runQueue([{ ...row, status: "PENDING", docId: null, error: null }]);
  }

  const settledCount = rows.filter((r) =>
    rowSettled(r, r.docId !== null ? live.get(r.docId) : undefined),
  ).length;

  return (
    <section
      className="mt-10 rounded-xl border border-slate-200 p-4 dark:border-slate-800"
      data-testid="batch-upload"
    >
      <h2 className="text-sm font-semibold">Batch upload</h2>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Select or drop up to {MAX_BATCH_FILES} files — uploaded in parallel and processed via async worker queue.
      </p>

      <div className="mt-4">
        <label className="block text-sm font-medium" htmlFor="batch-source">
          Capture source
        </label>
        <select
          id="batch-source"
          value={captureSource}
          onChange={(e) => setCaptureSource(e.target.value as CaptureSource)}
          className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800"
          data-testid="batch-capture-source"
        >
          {(Object.keys(CAPTURE_LABELS) as CaptureSource[]).map((k) => (
            <option key={k} value={k}>
              {CAPTURE_LABELS[k]}
            </option>
          ))}
        </select>
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          enqueue(Array.from(e.dataTransfer.files));
        }}
        className={`
          mt-4 cursor-pointer rounded-xl border-2 border-dashed p-6 text-center
          ${dragOver ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950/30" : "border-slate-300 dark:border-slate-700"}
        `}
      >
        <input
          type="file"
          multiple
          accept={ACCEPTED_UPLOAD_TYPES}
          className="hidden"
          id="batch-file-input"
          onChange={(e) => {
            enqueue(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
          data-testid="batch-file-input"
        />
        <label htmlFor="batch-file-input" className="cursor-pointer">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Drag & drop several PDFs or images here, or click to browse
          </p>
          <p className="mt-2 text-xs text-slate-400">
            PDF, JPG, PNG · max 25 MB per file · up to {MAX_BATCH_FILES} files
          </p>
        </label>
      </div>

      {notice !== null && (
        <p
          className="mt-4 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200"
          data-testid="batch-notice"
        >
          {notice}
        </p>
      )}

      {rows.length > 0 && (
        <div className="mt-4">
          <p className="text-xs text-slate-500 dark:text-slate-400" data-testid="batch-summary">
            {settledCount}/{rows.length} file(s) finished
          </p>
          <table className="mt-2 w-full text-left text-xs">
            <thead className="text-slate-500 dark:text-slate-400">
              <tr>
                <th className="py-1 pr-2 font-medium">File</th>
                <th className="py-1 pr-2 font-medium">Size</th>
                <th className="py-1 pr-2 font-medium">Upload</th>
                <th className="py-1 pr-2 font-medium">Extraction</th>
                <th className="py-1 pr-2 font-medium">Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const poll =
                  row.docId !== null ? live.get(row.docId) : undefined;
                const label = rowLabel(row, poll);
                const failed = row.status === "UPLOAD_FAILED";
                return (
                  <tr
                    key={row.id}
                    className="border-t border-slate-100 dark:border-slate-800"
                    data-testid="batch-row"
                  >
                    <td className="py-1 pr-2" data-testid="batch-row-name">
                      {row.name}
                    </td>
                    <td className="py-1 pr-2 text-slate-500 dark:text-slate-400">
                      {formatBytes(row.size)}
                    </td>
                    <td
                      className={`py-1 pr-2 ${failed ? "text-red-700 dark:text-red-300" : "text-slate-500 dark:text-slate-400"}`}
                      data-testid="batch-row-upload"
                    >
                      {row.status === "UPLOAD_FAILED"
                        ? "upload failed"
                        : row.status === "UPLOADING"
                          ? "uploading…"
                          : row.status === "WAITING"
                            ? "uploaded"
                            : "queued"}
                    </td>
                    <td className="py-1 pr-2" data-testid="batch-row-extraction">
                      <span className={labelColor(label)}>{label}</span>
                      {poll?.polling && (
                        <span
                          className="ml-2 inline-block h-3 w-3 animate-spin rounded-full border-2 border-amber-500 border-t-transparent align-middle"
                          aria-hidden="true"
                        />
                      )}
                    </td>
                    <td className="py-1 pr-2">
                      {failed && (
                        <button
                          type="button"
                          onClick={() => retryRow(row.id)}
                          className="rounded-md bg-red-700 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-red-800"
                          data-testid="batch-retry"
                        >
                          Retry
                        </button>
                      )}
                      {row.docId !== null && poll?.status === "FAILED" && (
                        <button
                          type="button"
                          onClick={() => retryRow(row.id)}
                          className="rounded-md bg-slate-700 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-slate-800"
                          data-testid="batch-retry"
                        >
                          Retry
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {rows.every((r) =>
            rowSettled(r, r.docId !== null ? live.get(r.docId) : undefined),
          ) && (
            <button
              type="button"
              onClick={() => router.push(`/app/file/${gstin}/${fp}`)}
              className="mt-3 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700"
              data-testid="batch-go-to-queue"
            >
              Review queue →
            </button>
          )}

          {rows.some(
            (r) =>
              r.docId !== null &&
              isDraftReady(live.get(r.docId)?.status ?? r.initialStatus),
          ) && (
            <p className="mt-2 text-xs text-green-700 dark:text-green-300" data-testid="batch-draft-ready">
              At least one draft is ready to review.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
