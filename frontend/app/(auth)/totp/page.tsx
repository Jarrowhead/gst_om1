"use client";

/**
 * TOTP onboarding — QR + verify (SECURITY §1: mandatory for CA firm
 * membership, client-side verify before enabling). After enablement the CA
 * creates their firm; PARTNER membership activates on success.
 */
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import QRCode from "qrcode";

import { ApiError, createFirm, totpSetup, totpVerify } from "@/lib/api/client";
import { setRoleCookie } from "@/lib/auth/session";

type Stage = "setup" | "verify" | "firm";

export default function TotpPage() {
  const router = useRouter();
  const [stage, setStage] = useState<Stage>("setup");
  const [secret, setSecret] = useState("");
  const [qrUri, setQrUri] = useState("");
  const [code, setCode] = useState("");
  const [firmName, setFirmName] = useState("");
  const [pan, setPan] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const qrCanvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    if (stage === "verify" && qrCanvasRef.current !== null && qrUri !== "") {
      QRCode.toCanvas(qrCanvasRef.current, qrUri, { width: 220 }).catch(() => {
        // QR render failure keeps the secret fallback visible below.
      });
    }
  }, [stage, qrUri]);

  /**
   * POST /auth/totp/setup and show the QR stage.
   *
   * Flow:
   *   totpSetup → secret + qr_uri → stage verify. Canvas draw is a separate effect.
   *
   * Debug:
   *   409 means TOTP was already enabled for this user.
   */
  async function begin(e: React.MouseEvent<HTMLButtonElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await totpSetup();
      setSecret(res.secret);
      setQrUri(res.qr_uri);
      setStage("verify");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "TOTP setup failed");
    } finally {
      setBusy(false);
    }
  }

  /**
   * POST /auth/totp/verify. Success moves to the firm form.
   *
   * Debug:
   *   invalid TOTP code near a 30s boundary — generate a fresh code and retry.
   */
  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await totpVerify(code);
      setStage("firm");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "invalid TOTP code");
    } finally {
      setBusy(false);
    }
  }

  /**
   * POST /firm then open the CA shell.
   *
   * Flow:
   *   createFirm → setRoleCookie CA → /ca.
   *
   * Debug:
   *   TOTP_REQUIRED means verify() did not set totp_enabled_at. 409 is a PAN conflict.
   */
  async function createTheFirm(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await createFirm(firmName, pan);
      setRoleCookie("CA");
      router.push("/ca");
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "firm creation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-[calc(100vh-3rem)] max-w-md flex-col justify-center px-4">
      <div className="rounded-xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <h1 className="text-xl font-semibold">Two-factor authentication</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Required for CA firm access — scan with any authenticator app.
        </p>

        {stage === "setup" && (
          <button
            type="button"
            onClick={begin}
            disabled={busy}
            className="mt-6 w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
            data-testid="totp-begin"
          >
            {busy ? "Generating…" : "Generate QR code"}
          </button>
        )}

        {stage === "verify" && (
          <form onSubmit={verify} className="mt-6 space-y-4">
            <div className="flex justify-center">
              <canvas ref={qrCanvasRef} data-testid="totp-qr" />
            </div>
            <p
              className="break-all rounded-md bg-slate-50 px-3 py-2 font-mono text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300"
              data-testid="totp-secret"
            >
              {secret}
            </p>
            <input
              type="text"
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              required
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-lg tracking-widest dark:border-slate-700 dark:bg-slate-800"
              placeholder="6-digit code"
              data-testid="totp-code"
            />
            <button
              type="submit"
              disabled={busy || code.length !== 6}
              className="w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
              data-testid="totp-verify-btn"
            >
              {busy ? "Verifying…" : "Verify & enable"}
            </button>
          </form>
        )}

        {stage === "firm" && (
          <form onSubmit={createTheFirm} className="mt-6 space-y-4">
            <p
              className="text-sm font-medium text-green-700 dark:text-green-400"
              data-testid="totp-enabled-banner"
            >
              ✓ TOTP enabled
            </p>
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Create your firm to finish onboarding.
            </p>
            <label className="block text-sm font-medium">
              Firm name
              <input
                type="text"
                required
                value={firmName}
                onChange={(e) => setFirmName(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800"
                data-testid="firm-name"
              />
            </label>
            <label className="block text-sm font-medium">
              Firm PAN
              <input
                type="text"
                required
                value={pan}
                onChange={(e) => setPan(e.target.value.toUpperCase())}
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm uppercase dark:border-slate-700 dark:bg-slate-800"
                placeholder="AAAAA9999A"
                data-testid="firm-pan"
              />
            </label>
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
              data-testid="firm-create"
            >
              {busy ? "Creating…" : "Create firm →"}
            </button>
          </form>
        )}

        {error !== null && (
          <p
            className="mt-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
            role="alert"
            data-testid="totp-error"
          >
            {error}
          </p>
        )}
      </div>
    </main>
  );
}