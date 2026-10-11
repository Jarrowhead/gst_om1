"use client";

/**
 * Phase 4 & Phase 13 IRN / E-Way Bill Board — list confirmed outward invoices
 * for a GSTIN + period, generate IRNs, and generate sandbox E-Way Bills (rec 6).
 */
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import {
  ApiError,
  EInvoiceDto,
  EwbDto,
  InvoiceDto,
  generateEwb,
  generateIrn,
  listEinvoices,
  listInvoices,
} from "@/lib/api/client";

/** Integer paise → rupee string with Indian grouping, e.g. ₹1,18,000.00. */
function rupees(paise: number): string {
  return `₹${(paise / 100).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export default function EInvoiceBoardPage() {
  const params = useParams<{ gstin: string; fp: string }>();
  const { gstin, fp } = params;

  const [invoices, setInvoices] = useState<InvoiceDto[]>([]);
  const [einvoices, setEinvoices] = useState<EInvoiceDto[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generated, setGenerated] = useState<EInvoiceDto | null>(null);

  // EWB modal & state
  const [ewbModalInv, setEwbModalInv] = useState<InvoiceDto | null>(null);
  const [vehicleNo, setVehicleNo] = useState("DL01AB1234");
  const [transporterId, setTransporterId] = useState("");
  const [distanceKm, setDistanceKm] = useState("");
  const [ewbBusy, setEwbBusy] = useState(false);
  const [ewbError, setEwbError] = useState<string | null>(null);
  const [generatedEwb, setGeneratedEwb] = useState<EwbDto | null>(null);

  async function load() {
    try {
      const [inv, irnList] = await Promise.all([
        listInvoices(gstin, fp),
        listEinvoices(gstin, fp),
      ]);
      setInvoices(inv);
      setEinvoices(irnList);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load IRNs");
    }
  }

  useEffect(() => {
    load();
  }, [gstin, fp]);

  async function generate(invoiceId: string) {
    setBusyId(invoiceId);
    setError(null);
    setGenerated(null);
    try {
      const res = await generateIrn(invoiceId);
      setGenerated(res);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "IRN generation failed");
    } finally {
      setBusyId(null);
    }
  }

  async function handleGenerateEwb() {
    if (!ewbModalInv) return;
    setEwbBusy(true);
    setEwbError(null);
    try {
      const dist = distanceKm ? parseInt(distanceKm, 10) : undefined;
      const res = await generateEwb(gstin, fp, ewbModalInv.id, {
        vehicle_no: vehicleNo || undefined,
        transporter_id: transporterId || undefined,
        distance_km: dist,
      });
      setGeneratedEwb(res);
      setEwbModalInv(null);
      await load();
    } catch (err) {
      setEwbError(err instanceof ApiError ? err.message : "E-Way Bill generation failed");
    } finally {
      setEwbBusy(false);
    }
  }

  return (
    <main className="mx-auto w-full max-w-4xl px-6 py-8">
      <h1 className="text-2xl font-semibold">E-Invoices / IRN & E-Way Bills</h1>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        GSTIN: <span className="font-mono">{gstin}</span> · Period: <span className="font-mono">{fp}</span>
      </p>

      {error !== null && (
        <p className="mt-6 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300" role="alert">
          {error}
        </p>
      )}

      {generated !== null && (
        <div className="mt-6 rounded-xl border border-green-200 bg-green-50 p-4 dark:border-green-900 dark:bg-green-950">
          <p className="text-sm font-medium text-green-800 dark:text-green-200">IRN generated</p>
          <p className="mt-1 break-all font-mono text-xs text-green-700 dark:text-green-300">{generated.irn}</p>
        </div>
      )}

      {generatedEwb !== null && (
        <div className="mt-6 rounded-xl border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950" data-testid="ewb-success-card">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-emerald-800 dark:text-emerald-200">E-Way Bill Generated Successfully</p>
            <span className="rounded bg-emerald-200 px-2 py-0.5 text-xs font-semibold text-emerald-800 dark:bg-emerald-800 dark:text-emerald-100">
              {generatedEwb.status}
            </span>
          </div>
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 text-xs">
            <div>
              <span className="text-slate-500 dark:text-slate-400">EWB Number:</span>
              <p className="font-mono font-bold text-emerald-900 dark:text-emerald-100" data-testid="ewb-number-display">
                {generatedEwb.ewb_no}
              </p>
            </div>
            <div>
              <span className="text-slate-500 dark:text-slate-400">Valid Until:</span>
              <p className="font-mono text-slate-700 dark:text-slate-300" data-testid="ewb-validity-display">
                {new Date(generatedEwb.valid_until).toLocaleString()}
              </p>
            </div>
            <div>
              <span className="text-slate-500 dark:text-slate-400">Vehicle:</span>
              <p className="font-mono text-slate-700 dark:text-slate-300">{generatedEwb.part_b.vehicle_no ?? "—"}</p>
            </div>
            <div>
              <span className="text-slate-500 dark:text-slate-400">Distance:</span>
              <p className="font-mono text-slate-700 dark:text-slate-300">{generatedEwb.part_b.distance_km} km</p>
            </div>
          </div>
        </div>
      )}

      {/* EWB Generation Modal */}
      {ewbModalInv && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl dark:bg-slate-900 border border-slate-200 dark:border-slate-800">
            <h3 className="text-lg font-bold text-slate-900 dark:text-white">
              Generate E-Way Bill (Sandbox)
            </h3>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              Invoice #{ewbModalInv.invoice_no} · Value: {rupees(ewbModalInv.total_value_minor)}
            </p>

            {ewbError && (
              <p className="mt-3 rounded bg-red-50 p-2 text-xs text-red-700 dark:bg-red-950 dark:text-red-300">
                {ewbError}
              </p>
            )}

            <div className="mt-4 space-y-3">
              <div>
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300">
                  Vehicle Number (Format: DL01AB1234)
                </label>
                <input
                  type="text"
                  value={vehicleNo}
                  onChange={(e) => setVehicleNo(e.target.value)}
                  className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm uppercase dark:border-slate-700 dark:bg-slate-800"
                  data-testid="ewb-vehicle-input"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300">
                  Transporter ID / GSTIN (Optional)
                </label>
                <input
                  type="text"
                  value={transporterId}
                  onChange={(e) => setTransporterId(e.target.value)}
                  placeholder="e.g. 07AAAAA0000A1Z5"
                  className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm uppercase dark:border-slate-700 dark:bg-slate-800"
                  data-testid="ewb-transporter-input"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300">
                  Distance in km (Auto-estimated if blank)
                </label>
                <input
                  type="number"
                  value={distanceKm}
                  onChange={(e) => setDistanceKm(e.target.value)}
                  placeholder="Auto-estimate via OSRM"
                  className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800"
                  data-testid="ewb-distance-input"
                />
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setEwbModalInv(null)}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleGenerateEwb}
                disabled={ewbBusy}
                className="rounded-md bg-emerald-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                data-testid="submit-ewb-btn"
              >
                {ewbBusy ? "Generating…" : "Generate Sandbox EWB"}
              </button>
            </div>
          </div>
        </div>
      )}

      <h2 className="mt-8 font-semibold">Confirmed invoices</h2>
      <div className="mt-4 overflow-x-auto" data-testid="einvoice-invoices-table">
        {invoices.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">
            No confirmed invoices for this period.
          </p>
        ) : (
          <table className="w-full min-w-[700px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
                <th className="py-2 pr-4 font-medium">Invoice No</th>
                <th className="py-2 pr-4 font-medium">Date</th>
                <th className="py-2 pr-4 font-medium">Buyer GSTIN</th>
                <th className="py-2 pr-4 font-medium text-right">Total</th>
                <th className="py-2 pr-4 font-medium">IRN</th>
                <th className="py-2 font-medium">E-Way Bill</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((inv) => {
                const alreadyDone = inv.irn !== null;
                const isEwbEligible = inv.total_value_minor > 5000000;
                const isEwbDone = !!inv.ewb_no;

                return (
                  <tr
                    key={inv.id}
                    className="border-b border-slate-100 dark:border-slate-800"
                    data-testid="einvoice-invoice-row"
                  >
                    <td className="py-2 pr-4 font-medium">{inv.invoice_no}</td>
                    <td className="py-2 pr-4">{inv.invoice_date}</td>
                    <td className="py-2 pr-4 font-mono text-xs">
                      {inv.buyer_gstin ?? "—"}
                    </td>
                    <td className="py-2 pr-4 text-right">
                      {rupees(inv.total_value_minor)}
                    </td>
                    <td className="py-2 pr-4">
                      {alreadyDone ? (
                        <span
                          className="inline-block max-w-[140px] truncate align-middle font-mono text-xs text-green-700 dark:text-green-300"
                          title={inv.irn ?? ""}
                          data-testid="invoice-irn-badge"
                        >
                          {inv.irn_status === "CANCELLED"
                            ? "Cancelled"
                            : "Generated ✓"}
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => generate(inv.id)}
                          disabled={busyId !== null}
                          className="rounded-md bg-indigo-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                          data-testid="generate-irn-btn"
                        >
                          {busyId === inv.id ? "Generating…" : "Generate IRN"}
                        </button>
                      )}
                    </td>
                    <td className="py-2">
                      {!isEwbEligible ? (
                        <span
                          className="text-xs text-slate-400 dark:text-slate-500"
                          data-testid="ewb-not-required-badge"
                        >
                          Not Required (≤ ₹50k)
                        </span>
                      ) : isEwbDone ? (
                        <span
                          className="font-mono text-xs text-emerald-700 dark:text-emerald-300 font-medium"
                          data-testid="ewb-generated-badge"
                        >
                          EWB: {inv.ewb_no} ✓
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            setEwbModalInv(inv);
                            setEwbError(null);
                          }}
                          className="rounded-md bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-emerald-700"
                          data-testid="generate-ewb-btn"
                        >
                          Generate EWB
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <h2 className="mt-8 font-semibold">IRN list</h2>
      <div className="mt-4 grid gap-3">
        {einvoices.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">No e-invoices for this period.</p>
        ) : (
          einvoices.map((e) => (
            <div
              key={e.irn}
              className="rounded-lg border border-slate-200 p-4 dark:border-slate-800"
              data-testid="einvoice-row"
            >
              <p className="break-all font-mono text-sm">{e.irn}</p>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                ACK: {e.ack_no ?? "—"} · {e.cancelled_at ? `Cancelled ${e.cancelled_at}` : "Active"}
              </p>
            </div>
          ))
        )}
      </div>
    </main>
  );
}
