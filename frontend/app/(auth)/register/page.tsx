"use client";

/**
 * Registration — role pick (business / CA firm), then OTP with REGISTER
 * purpose. New users are auto-created by the backend on REGISTER verify.
 */
import { useRouter } from "next/navigation";
import { useState } from "react";

import {
  ApiError,
  requestOtp,
  setAccessToken,
  verifyOtp,
} from "@/lib/api/client";
import { clearSession, setRoleCookie } from "@/lib/auth/session";

type Role = "CLIENT" | "CA";
type Stage = "pick" | "identifier" | "otp";

export default function RegisterPage() {
  const router = useRouter();
  const [role, setRole] = useState<Role | null>(null);
  const [identifier, setIdentifier] = useState("");
  const [otp, setOtp] = useState("");
  const [devOtp, setDevOtp] = useState<string | null>(null);
  const [stage, setStage] = useState<Stage>("pick");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function pick(r: Role) {
    setRole(r);
    setStage("identifier");
  }

  async function request(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await requestOtp(identifier, "REGISTER");
      setDevOtp(res.dev_otp);
      setStage("otp");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "failed to send OTP");
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await verifyOtp(identifier, otp);
      setAccessToken(res.access_token);
      clearSession();
      if (role === "CA") {
        // TOTP is mandatory before any firm-join (SECURITY §1).
        setRoleCookie("CA");
        router.push("/totp");
      } else {
        setRoleCookie("CLIENT");
        router.push("/app");
      }
      // Force middleware to re-evaluate the new role cookie on the next render.
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "OTP verification failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-[calc(100vh-3rem)] max-w-md flex-col justify-center px-4">
      <div className="rounded-xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <h1 className="text-xl font-semibold">Create your account</h1>

        {stage === "pick" && (
          <div className="mt-6 space-y-3">
            <button
              type="button"
              onClick={() => pick("CLIENT")}
              className="w-full rounded-lg border border-slate-300 p-4 text-left hover:border-indigo-500 hover:bg-indigo-50/50 dark:border-slate-700 dark:hover:border-indigo-400 dark:hover:bg-indigo-950/50"
              data-testid="register-client"
            >
              <span className="font-semibold">Business (GST-registered)</span>
              <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                Upload documents, review ledgers, track filing deadlines.
              </p>
            </button>
            <button
              type="button"
              onClick={() => pick("CA")}
              className="w-full rounded-lg border border-slate-300 p-4 text-left hover:border-indigo-500 hover:bg-indigo-50/50 dark:border-slate-700 dark:hover:border-indigo-400 dark:hover:bg-indigo-950/50"
              data-testid="register-ca"
            >
              <span className="font-semibold">CA firm</span>
              <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                Manage clients, prepare returns, file GSTR-1/3B. TOTP required.
              </p>
            </button>
          </div>
        )}

        {stage === "identifier" && role !== null && (
          <form onSubmit={request} className="mt-6 space-y-4">
            <p className="text-sm text-slate-500 dark:text-slate-400">
              {role === "CA" ? "CA firm" : "Business"} account — where do we
              send the code?
            </p>
            <label className="block text-sm font-medium">
              Mobile or email
              <input
                type="text"
                required
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800"
                placeholder="98xxxxxxxx or you@firm.com"
                data-testid="reg-identifier"
              />
            </label>
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
              data-testid="reg-request-otp"
            >
              {busy ? "Sending…" : "Send code"}
            </button>
            <button
              type="button"
              onClick={() => setStage("pick")}
              className="text-sm text-slate-500 hover:underline"
            >
              ← change account type
            </button>
          </form>
        )}

        {stage === "otp" && role !== null && (
          <form onSubmit={verify} className="mt-6 space-y-4">
            <p className="text-sm text-slate-500 dark:text-slate-400">
              {role === "CA" ? "CA firm" : "Business"} — code sent to{" "}
              <span className="font-mono">{identifier}</span>
            </p>
            {devOtp !== null && (
              <p
                className="rounded-md bg-amber-50 px-3 py-2 font-mono text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200"
                data-testid="reg-dev-otp"
              >
                dev code: {devOtp}
              </p>
            )}
            <input
              type="text"
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              required
              value={otp}
              onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
              className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-lg tracking-widest dark:border-slate-700 dark:bg-slate-800"
              placeholder="6-digit code"
              data-testid="reg-otp"
            />
            <button
              type="submit"
              disabled={busy || otp.length !== 6}
              className="w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
              data-testid="reg-verify"
            >
              {busy ? "Verifying…" : role === "CA" ? "Verify → set up TOTP" : "Verify & continue"}
            </button>
            <button
              type="button"
              onClick={() => setStage("identifier")}
              className="text-sm text-slate-500 hover:underline"
            >
              ← change identifier
            </button>
          </form>
        )}

        {error !== null && (
          <p
            className="mt-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
            role="alert"
            data-testid="reg-error"
          >
            {error}
          </p>
        )}

        <p className="mt-6 text-sm text-slate-500 dark:text-slate-400">
          Already registered?{" "}
          <a href="/login" className="font-medium text-indigo-600 hover:underline dark:text-indigo-400">
            Sign in
          </a>
        </p>
      </div>
    </main>
  );
}