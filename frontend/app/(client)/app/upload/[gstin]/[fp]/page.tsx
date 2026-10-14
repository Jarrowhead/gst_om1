"use client";

/**
 * Upload page — unified batch upload & bulk import tabs.
 *
 * Captures PDF/image invoices via multi-file BatchUploadQueue or ingests
 * spreadsheet exports via BulkImportTab.
 */
import { useParams } from "next/navigation";
import { useState } from "react";

import { BatchUploadQueue } from "@/components/upload/BatchUploadQueue";
import { BulkImportTab } from "@/components/upload/BulkImportTab";

type UploadTab = "CAPTURE" | "BULK";

export default function UploadPage() {
  const params = useParams<{ gstin: string; fp: string }>();
  const { gstin, fp } = params;

  // Tabs: CAPTURE (unified batch upload) vs BULK (spreadsheet import).
  const [tab, setTab] = useState<UploadTab>("CAPTURE");

  return (
    <main className="mx-auto w-full max-w-3xl px-6 py-8">
      <h1 className="text-2xl font-semibold">Upload invoice / statement</h1>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        GSTIN: <span className="font-mono">{gstin}</span> · Period:{" "}
        <span className="font-mono">{fp}</span>
      </p>

      {/* Tabs: Photo / PDF upload vs bulk spreadsheet import */}
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
        <BatchUploadQueue gstin={gstin} fp={fp} />
      )}
    </main>
  );
}

