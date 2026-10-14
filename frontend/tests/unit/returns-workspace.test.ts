import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  getMonthSummary,
  getGstr1Export,
  getGstr3bExport,
  getHsnSummary,
  getReturnsValidation,
  getGstr3bSetoff,
  generateReturns,
} from "@/lib/api/client";

describe("Returns Workspace API Client", () => {
  const gstin = "33AAACI1607G2Z5";
  const fp = "092026";
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("getMonthSummary requests correct path and returns data", async () => {
    const mockData = {
      confirmed_count: 0,
      total_taxable_minor: 0,
      status: "OPEN",
    };
    (globalThis.fetch as any).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ success: true, data: mockData }),
    });

    const res = await getMonthSummary(gstin, fp);
    expect(res).toEqual(mockData);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining(`/gst-accounts/${gstin}/months/${fp}/summary`),
      expect.any(Object),
    );
  });

  it("getGstr1Export requests correct path and returns data", async () => {
    const mockData = { gstin, fp, b2b: [] };
    (globalThis.fetch as any).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ success: true, data: mockData }),
    });

    const res = await getGstr1Export(gstin, fp);
    expect(res).toEqual(mockData);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining(`/gst-accounts/${gstin}/months/${fp}/gstr1/export.json`),
      expect.any(Object),
    );
  });

  it("getGstr3bExport requests correct path and returns data", async () => {
    const mockData = { gstin, fp, sup_details: {} };
    (globalThis.fetch as any).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ success: true, data: mockData }),
    });

    const res = await getGstr3bExport(gstin, fp);
    expect(res).toEqual(mockData);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining(`/gst-accounts/${gstin}/months/${fp}/gstr3b/export.json`),
      expect.any(Object),
    );
  });

  it("getHsnSummary requests correct path and returns HSN data", async () => {
    const mockData = {
      rows: [],
      totals: { txval_paise: 0, iamt_paise: 0, camt_paise: 0, samt_paise: 0, csamt_paise: 0 },
    };
    (globalThis.fetch as any).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ success: true, data: mockData }),
    });

    const res = await getHsnSummary(gstin, fp);
    expect(res).toEqual(mockData);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining(`/gst-accounts/${gstin}/months/${fp}/gstr1/hsn-summary`),
      expect.any(Object),
    );
  });

  it("getReturnsValidation requests correct path and returns validation result", async () => {
    const mockData = { blocking: [], warnings: [] };
    (globalThis.fetch as any).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ success: true, data: mockData }),
    });

    const res = await getReturnsValidation(gstin, fp);
    expect(res).toEqual(mockData);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining(`/gst-accounts/${gstin}/months/${fp}/returns/validation`),
      expect.any(Object),
    );
  });

  it("getGstr3bSetoff requests correct path and returns Rule 88A setoff matrix", async () => {
    const mockData = {
      gstin,
      fp,
      itc_credits: { igst_paise: 0, cgst_paise: 0, sgst_paise: 0, cess_paise: 0 },
      liability: { igst_paise: 0, cgst_paise: 0, sgst_paise: 0, cess_paise: 0 },
      matrix: { steps: [] },
      challan: {
        total_net_paise: 0,
        zero_payable: true,
        pmt06_note: "No cash payable",
        cpin_note: "",
        deposit: { igst_paise: 0, cgst_paise: 0, sgst_paise: 0, cess_paise: 0 },
      },
    };
    (globalThis.fetch as any).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ success: true, data: mockData }),
    });

    const res = await getGstr3bSetoff(gstin, fp);
    expect(res).toEqual(mockData);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining(`/gst-accounts/${gstin}/months/${fp}/gstr3b/setoff`),
      expect.any(Object),
    );
  });

  it("generateReturns calls POST /returns/generate", async () => {
    const mockData = {
      gstin,
      fp,
      returns: {
        gstr1: { status: "READY", invoice_count: 0, totals: {} },
        gstr3b: { status: "READY", invoice_count: 0, totals: {} },
      },
      gstr1_export_id: null,
    };
    (globalThis.fetch as any).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ success: true, data: mockData }),
    });

    const res = await generateReturns(gstin, fp);
    expect(res).toEqual(mockData);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining(`/gst-accounts/${gstin}/months/${fp}/returns/generate`),
      expect.objectContaining({ method: "POST" }),
    );
  });
});
