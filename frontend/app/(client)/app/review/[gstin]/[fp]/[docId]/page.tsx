"use client";

/**
 * Review-confirm screen for a single document (Task 7.2, reworked Phase 9 9.1).
 * Shows extracted fields + validation flags and lets the user confirm
 * (if clean) or edit ANY draft field and re-confirm.
 *
 * Phase 9 9.1 — full-field review editor:
 *   - All editable draft fields save via PUT draft (updateDraft) on blur.
 *   - GSTIN fields are checksum-validated client-side (mod-36) BEFORE any
 *     network call; the server re-validates regardless.
 *   - Paise fields are integers end-to-end (no float crosses the boundary).
 *   - The two original quick-edit inputs (invoice_no, total_value_paise) keep
 *     their behaviour; everything else is additive.
 *   - Flags panel + confirm gating (BLOCK flags) unchanged.
 */
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  ApiError,
  confirmDocument,
  DraftDto,
  getDraft,
  updateDraft,
} from "@/lib/api/client";
import { gstinChecksumValid } from "@/lib/validation/gstin";

function paiseText(value: unknown): string {
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n)) return "—";
  return `₹${(n / 100).toFixed(2)}`;
}

/** Paise → editable rupee string (no ₹ symbol; the field label carries ₹). */
function rupeeInput(value: unknown): string {
  const n = typeof value === "number" ? value : 0;
  return (n / 100).toFixed(2);
}

/** Client-side validation message for invalid Rupee amounts. */
const AMOUNT_ERROR = "Please enter a valid amount in Rupees (e.g. 1250.00)";

/** Plain-English tooltips for SME tax terms (audit gap: SME owners). */
const POS_HELP = {
  text: "Place of Supply: 2-digit state code where goods/services were delivered.",
  testId: "help-pos",
};
const RCHRG_HELP = {
  text: "Reverse charge: the buyer pays tax directly to the government instead of the seller.",
  testId: "help-rchrg",
};
const INTERSTATE_HELP = {
  text: "Inter-state: deliveries outside your home state attract IGST instead of CGST + SGST.",
  testId: "help-inter-state",
};

/** Hover helper icon (?) with a native title tooltip + screen-reader label. */
function HelpTip({ text, testId }: { text: string; testId: string }) {
  return (
    <span
      role="img"
      aria-label={text}
      title={text}
      data-testid={testId}
      className="ml-1 inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full bg-slate-200 align-middle text-[10px] font-semibold leading-none text-slate-600 dark:bg-slate-700 dark:text-slate-300"
    >
      ?
    </span>
  );
}

function FieldRow({
  label,
  value,
  confidence,
  help,
}: {
  label: string;
  value: React.ReactNode;
  confidence?: number;
  help?: { text: string; testId: string };
}) {
  const display = value ?? "—";
  return (
    <div className="grid grid-cols-3 gap-2 py-2 text-sm">
      <span className="text-slate-500 dark:text-slate-400">
        {label}
        {help !== undefined && <HelpTip text={help.text} testId={help.testId} />}
      </span>
      <span className="col-span-2 font-medium">{display}</span>
      {confidence !== undefined && (
        <span className="col-span-3 text-xs">
          confidence: {" "}
          <span
            className={`
              ${confidence >= 0.9 ? "text-green-600" : confidence >= 0.7 ? "text-amber-600" : "text-red-600"}
            `}
          >
            {Math.round(confidence * 100)}%
          </span>
        </span>
      )}
    </div>
  );
}

/** One editable field row: label + input, saved on blur. */
function EditField({
  label,
  testid,
  value,
  type,
  onCommit,
  invalid,
  hint,
  help,
}: {
  label: string;
  testid: string;
  value: string;
  type: "text" | "number";
  onCommit: (raw: string) => void;
  invalid?: boolean;
  hint?: string;
  help?: { text: string; testId: string };
}) {
  return (
    <div>
      <label className="block text-xs" htmlFor={`edit-${testid}`}>
        {label}
        {help !== undefined && <HelpTip text={help.text} testId={help.testId} />}
      </label>
      <input
        id={`edit-${testid}`}
        data-testid={`edit-${testid}`}
        type={type}
        defaultValue={value}
        onBlur={(e) => onCommit(e.target.value)}
        className={`w-full rounded-md border px-3 py-2 text-sm dark:bg-slate-800 ${
          invalid
            ? "border-red-400 dark:border-red-600"
            : "border-slate-300 dark:border-slate-700"
        }`}
      />
      {hint !== undefined && (
        <p
          className={`mt-1 text-xs ${invalid ? "text-red-600 dark:text-red-400" : "text-slate-500 dark:text-slate-400"}`}
          data-testid={`${testid}-hint`}
        >
          {hint}
        </p>
      )}
    </div>
  );
}

export default function ReviewPage() {
  const params = useParams<{ gstin: string; fp: string; docId: string }>();
  const router = useRouter();
  const { gstin, fp, docId } = params;

  const [draft, setDraft] = useState<DraftDto | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);
  // Per-field transient messages, keyed by field name (e.g. buyer_gstin).
  const [fieldNotes, setFieldNotes] = useState<Record<string, string>>({});

  async function load() {
    try {
      const data = await getDraft(docId);
      setDraft(data);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "failed to load draft");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [docId]);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      const res = await confirmDocument(docId);
      setSuccess(`Confirmed. Invoice ID: ${res.invoice_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "confirm failed");
    } finally {
      setBusy(false);
    }
  }

  /**
   * Save one field on blur. GSTIN fields go through the client-side mod-36
   * checksum first — a corrupted GSTIN never reaches the network. Paise
   * fields must parse as integer paise (no floats cross the boundary).
   */
  async function saveField(key: string, raw: string) {
    if (draft === null) return;
    const fields = draft.fields ?? {};
    const current = fields[key];

    if (key === "buyer_gstin" || key === "supplier_gstin") {
      const next = raw.trim() === "" ? null : raw.trim().toUpperCase();
      // B2C invoices legitimately have no buyer GSTIN; empty is allowed there.
      if (next !== null && !gstinChecksumValid(next)) {
        setFieldNotes((n) => ({
          ...n,
          [key]: "Invalid GSTIN — format or checksum fails the mod-36 rule. Not saved.",
        }));
        return;
      }
      if (current === next) {
        // Unchanged (after normalisation) — nothing to send.
        setFieldNotes((n) => ({ ...n, [key]: "" }));
        return;
      }
      try {
        const updated = await updateDraft(docId, { [key]: next });
        setDraft(updated);
        setFieldNotes((n) => ({ ...n, [key]: "" }));
      } catch (err) {
        setFieldNotes((n) => ({
          ...n,
          [key]: err instanceof ApiError ? err.message : "update failed",
        }));
      }
      return;
    }

    if (key === "is_inter_state" || key === "rchrg") {
      // Toggle rows render as buttons; they use toggleField instead.
      return;
    }

    if (
      key === "taxable_value_paise" ||
      key === "total_value_paise" ||
      key === "cgst_paise" ||
      key === "sgst_paise" ||
      key === "igst_paise" ||
      key === "cess_paise"
    ) {
      const trimmed = raw.trim();
      if (trimmed === "") return;
      // Accept ₹1,18,000.00 style input: strip ₹, commas and whitespace, then
      // require a plain decimal number before converting to integer paise.
      const cleaned = trimmed.replace(/[₹,\s]/g, "");
      if (!/^-?\d+(\.\d+)?$/.test(cleaned)) {
        setFieldNotes((n) => ({ ...n, [key]: AMOUNT_ERROR }));
        return;
      }
      const parsedFloat = parseFloat(cleaned);
      if (parsedFloat < 0) {
        setFieldNotes((n) => ({ ...n, [key]: "Amount cannot be negative. Not saved." }));
        return;
      }
      const next = Math.round(parsedFloat * 100);
      if (current === next) {
        setFieldNotes((n) => ({ ...n, [key]: "" }));
        return;
      }
      try {
        const updated = await updateDraft(docId, { [key]: next });
        setDraft(updated);
        setFieldNotes((n) => ({ ...n, [key]: "" }));
      } catch (err) {
        setFieldNotes((n) => ({
          ...n,
          [key]: err instanceof ApiError ? err.message : "update failed",
        }));
      }
      return;
    }

    // invoice_no / invoice_date / place_of_supply
    const next = raw.trim() === "" ? null : raw.trim();
    if (current === next) {
      setFieldNotes((n) => ({ ...n, [key]: "" }));
      return;
    }
    try {
      const updated = await updateDraft(docId, { [key]: next });
      setDraft(updated);
      setFieldNotes((n) => ({ ...n, [key]: "" }));
    } catch (err) {
      setFieldNotes((n) => ({
        ...n,
        [key]: err instanceof ApiError ? err.message : "update failed",
      }));
    }
  }

  /** Boolean toggle rows (is_inter_state / rchrg) — save on click. */
  async function toggleField(key: "is_inter_state" | "rchrg") {
    if (draft === null) return;
    const next = !(draft.fields?.[key] as boolean | undefined);
    try {
      const updated = await updateDraft(docId, { [key]: next });
      setDraft(updated);
      setFieldNotes((n) => ({ ...n, [key]: "" }));
    } catch (err) {
      setFieldNotes((n) => ({
        ...n,
        [key]: err instanceof ApiError ? err.message : "update failed",
      }));
    }
  }

  if (loading) {
    return (
      <main className="mx-auto w-full max-w-4xl px-6 py-8">
        <p className="text-sm text-slate-500">Loading review…</p>
      </main>
    );
  }

  const fields = draft?.fields ?? {};
  const flags = draft?.flags ?? [];
  // Only BLOCK flags lock confirmation (Phase 9 9.5): WARN-tier advisory flags
  // and FLAG/SUGGEST/AUTO_CORRECT are non-blocking.
  const hasBlockingFlag = flags.some((f) => f.severity === "BLOCK");
  const advisoryFlags = flags.filter((f) => f.severity === "WARN");

  return (
    <main className="mx-auto w-full max-w-4xl px-6 py-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Review &amp; confirm</h1>
        <button
          type="button"
          onClick={() => router.push(`/app/file/${gstin}/${fp}`)}
          className="text-sm text-slate-500 hover:underline"
        >
          ← Back to documents
        </button>
      </div>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        GSTIN: <span className="font-mono">{gstin}</span> · Period:{" "}
        <span className="font-mono">{fp}</span> · Doc:{" "}
        <span className="font-mono">{docId}</span>
      </p>

      {error !== null && (
        <p
          className="mt-6 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
          role="alert"
          data-testid="review-error"
        >
          {error}
        </p>
      )}

      {success !== null && (
        <div
          className="mt-6 rounded-xl border border-green-200 bg-green-50 p-4 dark:border-green-900 dark:bg-green-950"
          data-testid="review-success"
        >
          <p className="text-sm font-medium text-green-800 dark:text-green-200">
            {success}
          </p>
          <button
            type="button"
            onClick={() => router.push(`/app/returns/${gstin}/${fp}`)}
            className="mt-3 rounded-md bg-green-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-green-800"
            data-testid="go-to-returns"
          >
            Download returns →
          </button>
        </div>
      )}

      {flags.length > 0 && (
        <div
          className="mt-6 rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950"
          data-testid="review-flags"
        >
          <h2 className="text-sm font-semibold text-amber-800 dark:text-amber-200">
            Validation flags
          </h2>
          <ul className="mt-2 list-inside list-disc text-sm text-amber-700 dark:text-amber-300">
            {flags.map((f, i) => (
              <li key={i} data-testid="review-flag">
                {f.rule} ({f.severity}): {f.message}
              </li>
            ))}
          </ul>
          {advisoryFlags.length > 0 && (
            <p
              className="mt-3 text-xs text-amber-700 dark:text-amber-300"
              data-testid="review-advisory-note"
            >
              {advisoryFlags.length} advisory flag
              {advisoryFlags.length === 1 ? "" : "s"} (WARN) — non-blocking.
              These do not prevent confirmation; review them at your
              discretion.
            </p>
          )}
        </div>
      )}

      {draft !== null && (
        <div className="mt-6 grid gap-6 md:grid-cols-2">
          <div className="rounded-xl border border-slate-200 p-5 dark:border-slate-800">
            <h2 className="font-semibold">Parties</h2>
            <FieldRow
              label="Supplier GSTIN"
              value={String(fields.supplier_gstin ?? "")}
              confidence={draft.confidence.supplier_gstin}
            />
            <FieldRow
              label="Buyer GSTIN"
              value={String(fields.buyer_gstin ?? "")}
              confidence={draft.confidence.buyer_gstin}
            />
          </div>

          <div className="rounded-xl border border-slate-200 p-5 dark:border-slate-800">
            <h2 className="font-semibold">Invoice</h2>
            <FieldRow label="Invoice no" value={String(fields.invoice_no ?? "")} confidence={draft.confidence.invoice_no} />
            <FieldRow label="Date" value={String(fields.invoice_date ?? "")} confidence={draft.confidence.invoice_date} />
            <FieldRow label="POS" value={String(fields.place_of_supply ?? "")} confidence={draft.confidence.place_of_supply} help={POS_HELP} />
            <FieldRow label="Inter-state" value={fields.is_inter_state ? "Yes" : "No"} help={INTERSTATE_HELP} />
            <FieldRow label="Reverse charge" value={fields.rchrg ? "Yes" : "No"} help={RCHRG_HELP} />
            <FieldRow label="Type" value={String(fields.inv_typ ?? "")} />
          </div>

          <div className="rounded-xl border border-slate-200 p-5 dark:border-slate-800">
            <h2 className="font-semibold">Tax totals</h2>
            <FieldRow label="Taxable" value={paiseText(fields.taxable_value_paise)} />
            <FieldRow label="Total" value={paiseText(fields.total_value_paise)} />
            <FieldRow label="CGST" value={paiseText(fields.cgst_paise)} />
            <FieldRow label="SGST" value={paiseText(fields.sgst_paise)} />
            <FieldRow label="IGST" value={paiseText(fields.igst_paise)} />
            <FieldRow label="CESS" value={paiseText(fields.cess_paise)} />
          </div>

          <div className="rounded-xl border border-slate-200 p-5 dark:border-slate-800">
            <h2 className="font-semibold">Derived</h2>
            <FieldRow label="Supply type" value={String(draft.derived.supply_type ?? "")} />
            <FieldRow label="Period" value={String(draft.derived.fp ?? "")} />
            <FieldRow label="Auto-confirm" value={String(draft.auto_confirm ?? "")} />
          </div>
        </div>
      )}

      <div className="mt-8 flex items-center gap-4">
        <button
          type="button"
          onClick={confirm}
          disabled={busy || hasBlockingFlag || draft === null}
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
          data-testid="confirm-document"
        >
          {busy ? "Confirming…" : "Confirm"}
        </button>
        <p className="text-sm text-slate-500">
          {hasBlockingFlag
            ? "Blocking validation errors must be resolved before confirming."
            : advisoryFlags.length > 0
              ? "Advisory flags noted (non-blocking) — you can still confirm."
              : "All checks passed — confirm to add to the ledger."}
        </p>
      </div>

      {draft !== null && (
        <div className="mt-8 rounded-xl border border-slate-200 p-5 dark:border-slate-800">
          <h2 className="font-semibold">Edit draft fields</h2>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Every field saves individually on blur (PUT draft). GSTINs are
            checksum-validated client-side before sending; the server
            re-validates. Amounts are entered in Rupees and stored as integer
            paise.
          </p>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <EditField
              label="Invoice no"
              testid="invoice_no"
              type="text"
              value={String(fields.invoice_no ?? "")}
              onCommit={(raw) => saveField("invoice_no", raw)}
            />
            <EditField
              label="Invoice date (YYYY-MM-DD)"
              testid="invoice_date"
              type="text"
              value={String(fields.invoice_date ?? "")}
              onCommit={(raw) => saveField("invoice_date", raw)}
            />
            <EditField
              label="Supplier GSTIN"
              testid="supplier_gstin"
              type="text"
              value={String(fields.supplier_gstin ?? "")}
              onCommit={(raw) => saveField("supplier_gstin", raw)}
              invalid={!!fieldNotes.supplier_gstin?.startsWith("Invalid")}
              hint={fieldNotes.supplier_gstin}
            />
            <EditField
              label="Buyer GSTIN"
              testid="buyer_gstin"
              type="text"
              value={String(fields.buyer_gstin ?? "")}
              onCommit={(raw) => saveField("buyer_gstin", raw)}
              invalid={!!fieldNotes.buyer_gstin?.startsWith("Invalid")}
              hint={fieldNotes.buyer_gstin}
            />
            <EditField
              label="Place of supply (2-digit state code)"
              testid="place_of_supply"
              type="text"
              value={String(fields.place_of_supply ?? "")}
              onCommit={(raw) => saveField("place_of_supply", raw)}
              help={POS_HELP}
            />

            <div>
              <span className="block text-xs">
                Inter-state
                <HelpTip text={INTERSTATE_HELP.text} testId={`${INTERSTATE_HELP.testId}-edit`} />
              </span>
              <button
                type="button"
                data-testid="edit-is_inter_state"
                onClick={() => toggleField("is_inter_state")}
                className="mt-1 rounded-md border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800"
              >
                {fields.is_inter_state ? "Yes" : "No"}
              </button>
            </div>

            <div>
              <span className="block text-xs">
                Reverse charge (rchrg)
                <HelpTip text={RCHRG_HELP.text} testId={`${RCHRG_HELP.testId}-edit`} />
              </span>
              <button
                type="button"
                data-testid="edit-rchrg"
                onClick={() => toggleField("rchrg")}
                className="mt-1 rounded-md border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800"
              >
                {fields.rchrg ? "Yes" : "No"}
              </button>
            </div>

            <EditField
              label="Taxable value (₹)"
              testid="taxable_value_paise"
              type="text"
              value={rupeeInput(fields.taxable_value_paise)}
              onCommit={(raw) => saveField("taxable_value_paise", raw)}
              hint={fieldNotes.taxable_value_paise}
              invalid={fieldNotes.taxable_value_paise === AMOUNT_ERROR}
            />
            <EditField
              label="Total value (₹)"
              testid="total_value_paise"
              type="text"
              value={rupeeInput(fields.total_value_paise)}
              onCommit={(raw) => saveField("total_value_paise", raw)}
              hint={fieldNotes.total_value_paise}
              invalid={fieldNotes.total_value_paise === AMOUNT_ERROR}
            />
            <EditField
              label="CGST (₹)"
              testid="cgst_paise"
              type="text"
              value={rupeeInput(fields.cgst_paise)}
              onCommit={(raw) => saveField("cgst_paise", raw)}
              hint={fieldNotes.cgst_paise}
              invalid={fieldNotes.cgst_paise === AMOUNT_ERROR}
            />
            <EditField
              label="SGST (₹)"
              testid="sgst_paise"
              type="text"
              value={rupeeInput(fields.sgst_paise)}
              onCommit={(raw) => saveField("sgst_paise", raw)}
              hint={fieldNotes.sgst_paise}
              invalid={fieldNotes.sgst_paise === AMOUNT_ERROR}
            />
            <EditField
              label="IGST (₹)"
              testid="igst_paise"
              type="text"
              value={rupeeInput(fields.igst_paise)}
              onCommit={(raw) => saveField("igst_paise", raw)}
              hint={fieldNotes.igst_paise}
              invalid={fieldNotes.igst_paise === AMOUNT_ERROR}
            />
            <EditField
              label="CESS (₹)"
              testid="cess_paise"
              type="text"
              value={rupeeInput(fields.cess_paise)}
              onCommit={(raw) => saveField("cess_paise", raw)}
              hint={fieldNotes.cess_paise}
              invalid={fieldNotes.cess_paise === AMOUNT_ERROR}
            />
          </div>
        </div>
      )}
    </main>
  );
}