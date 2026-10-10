"use client";

/**
 * Upload page — the entry to the upload-to-output happy path (Task 7.2).
 * User picks a capture source, drops/uploads a file, and the backend stores
 * it, spawns an extraction job, then starts polling the document status.
 *
 * Task 11.1 adds a second tab ("Bulk Excel/CSV") that ingests a spreadsheet
 * export straight into the draft queue. The capture tab is the default and
 * keeps every existing testid/behaviour on this page unchanged.
 */
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { BatchUploadQueue } from "@/components/upload/BatchUploadQueue";
import { BulkImportTab } from "@/components/upload/BulkImportTab";
import {
  ApiError,
  getDocument,
  triggerExtraction,
  uploadDocument,
  UploadResult,
} from "@/lib/api/client";
import {
  isDraftReady,
  PIPELINE_STAGES,
  stageState,
  useExtractionStatusPolling,
} from "@/lib/extraction/status";

type CaptureSource = "PDF_SCAN" | "DIGITAL" | "PHOTO" | "WHATSAPP";
type UploadTab = "CAPTURE" | "BULK";

const CAPTURE_LABELS: Record<CaptureSource, string> = {
  PDF_SCAN: "PDF scan",
  DIGITAL: "Native PDF / digital",
  PHOTO: "Phone photo",
  WHATSAPP: "WhatsApp photo",
};

export default function UploadPage() {
  const params = useParams<{ gstin: string; fp: string }>();
  const router = useRouter();
  const { gstin, fp } = params;

  // Task 11.1: tab switch. CAPTURE is the default so the single-file card,
  // the batch queue and their testids stay visible exactly as before.
  const [tab, setTab] = useState<UploadTab>("CAPTURE");
  const [captureSource, setCaptureSource] = useState<CaptureSource>("PDF_SCAN");
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);

  // Task 9.2: poll the extraction job until a terminal status, cap 5 min.
  const live = useExtractionStatusPolling(
    result?.id ?? null,
    result?.job?.status ?? "UNKNOWN",
  );

  function handleFile(selected: File | null) {
    if (selected) {
      setFile(selected);
      setError(null);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (file === null) {
      setError("choose a file to upload");
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const res = await uploadDocument(gstin, fp, captureSource, file);
      try {
        await triggerExtraction(res.id);
        const detail = await getDocument(res.id);
        setResult({ ...res, job: detail.job });
      } catch {
        setResult(res);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "upload failed");
    } finally {
      setUploading(false);
    }
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-6 py-8">
      <h1 className="text-2xl font-semibold">Upload invoice / statement</h1>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        GSTIN: <span className="font-mono">{gstin}</span> · Period:{" "}
        <span className="font-mono">{fp}</span>
      </p>

      {/* Task 11.1: capture (default) vs bulk spreadsheet import. */}
      <div
        className="mt-6 flex gap-2 border-b border-slate-200 dark:border-slate-800"
        role="tablist"
        data-testid="upload-tabs"
      >
        <button
          type="button"
          role="tab"
          aria-selected={tab === "CAPTURE"}
          onClick={() => setTab("CAPTURE")}
          className={`px-3 py-2 text-sm font-medium ${
            tab === "CAPTURE"
              ? "border-b-2 border-indigo-600 text-indigo-700 dark:text-indigo-300"
              : "text-slate-500 dark:text-slate-400"
          }`}
          data-testid="upload-tab-capture"
        >
          Photo / PDF upload
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "BULK"}
          onClick={() => setTab("BULK")}
          className={`px-3 py-2 text-sm font-medium ${
            tab === "BULK"
              ? "border-b-2 border-indigo-600 text-indigo-700 dark:text-indigo-300"
              : "text-slate-500 dark:text-slate-400"
          }`}
          data-testid="upload-tab-bulk"
        >
          Bulk Excel/CSV
        </button>
      </div>

      {tab === "BULK" && <BulkImportTab gstin={gstin} fp={fp} />}

      {tab === "CAPTURE" && (
        <>
      <form onSubmit={submit} className="mt-8 space-y-6">
        <div>
          <label className="block text-sm font-medium">Capture source</label>
          <select
            value={captureSource}
            onChange={(e) => setCaptureSource(e.target.value as CaptureSource)}
            className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800"
            data-testid="capture-source"
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
            handleFile(e.dataTransfer.files[0] ?? null);
          }}
          className={`
            cursor-pointer rounded-xl border-2 border-dashed p-10 text-center
            ${dragOver ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950/30" : "border-slate-300 dark:border-slate-700"}
          `}
        >
          <input
            type="file"
            accept=".pdf,image/jpeg,image/png"
            className="hidden"
            id="file-input"
            onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
            data-testid="file-input"
          />
          <label htmlFor="file-input" className="cursor-pointer">
            {file === null ? (
              <>
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  Drag & drop a PDF or image, or click to browse
                </p>
                <p className="mt-2 text-xs text-slate-400">
                  PDF, JPG, PNG · max 25 MB
                </p>
              </>
            ) : (
              <p className="text-sm font-medium text-indigo-700 dark:text-indigo-300">
                {file.name}
              </p>
            )}
          </label>
        </div>

        <button
          type="submit"
          disabled={uploading || file === null}
          className="w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
          data-testid="upload-submit"
        >
          {uploading ? "Uploading…" : "Upload & extract"}
        </button>
      </form>

      {error !== null && (
        <p
          className="mt-6 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
          role="alert"
          data-testid="upload-error"
        >
          {error}
        </p>
      )}

      {result !== null && (
        <div
          className="mt-6 rounded-xl border border-green-200 bg-green-50 p-4 dark:border-green-900 dark:bg-green-950"
          data-testid="upload-success"
        >
          <p className="text-sm font-medium text-green-800 dark:text-green-200">
            Uploaded
          </p>
          <p className="mt-1 text-xs text-green-700 dark:text-green-300">
            Document ID: {result.id}
          </p>
          <p className="text-xs text-green-700 dark:text-green-300" data-testid="upload-status">
            Status: {live.status}
          </p>

          {/* Live pipeline chips (Task 9.2). Values mirror the backend
              JobStatus enum — never invented strings. */}
          <div className="mt-3 flex flex-wrap items-center gap-1" data-testid="pipeline-stages">
            {PIPELINE_STAGES.map((stage, i) => {
              const st = stageState(stage, live.status);
              return (
                <span key={stage} className="flex items-center gap-1">
                  <span
                    className={
                      st === "done"
                        ? "rounded-full bg-green-600 px-2 py-0.5 text-[10px] font-semibold text-white"
                        : st === "active"
                          ? "rounded-full bg-amber-500 px-2 py-0.5 text-[10px] font-semibold text-white"
                          : "rounded-full bg-slate-300 px-2 py-0.5 text-[10px] font-semibold text-slate-600 dark:bg-slate-700 dark:text-slate-300"
                    }
                    data-testid={`pipeline-stage-${stage}`}
                  >
                    {stage}
                  </span>
                  {i < PIPELINE_STAGES.length - 1 && (
                    <span className="text-[10px] text-slate-400">→</span>
                  )}
                </span>
              );
            })}
          </div>

          {live.polling && (
            <p className="mt-2 flex items-center gap-2 text-xs text-green-700 dark:text-green-300">
              <span
                className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-green-500 border-t-transparent"
                aria-hidden="true"
              />
              <span data-testid="extraction-polling-hint">
                Extraction in progress… this page refreshes the status every 2s.
              </span>
            </p>
          )}
          {live.capped && (
            <p className="mt-2 text-xs text-amber-700 dark:text-amber-300" data-testid="poll-cap-notice">
              Still processing after 5 minutes — check the documents queue for
              the latest status.
            </p>
          )}

          <button
            type="button"
            onClick={() => {
              if (result?.id) {
                router.push(`/app/review/${gstin}/${fp}/${result.id}`);
              } else {
                router.push(`/app/file/${gstin}/${fp}`);
              }
            }}
            disabled={!isDraftReady(live.status)}
            className="mt-3 rounded-md bg-green-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-green-800 disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="go-to-queue"
          >
            Review / confirm →
          </button>
          {!isDraftReady(live.status) && (
            <p className="mt-2 text-xs text-green-800/70 dark:text-green-200/70" data-testid="go-to-queue-hint">
              Extraction is still running — review unlocks once the draft is
              ready.
            </p>
          )}
        </div>
      )}

      {/* Task 9.2B: local batch queue (multi-file picker/drop, per-file
          upload → extraction rows). Additive — the single-file card above is
          unchanged and keeps its testids/contract. */}
      <BatchUploadQueue gstin={gstin} fp={fp} />
        </>
      )}
    </main>
  );
}
