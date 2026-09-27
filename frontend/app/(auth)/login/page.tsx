"use client";

/**
 * Login — mobile/email OTP for both roles (API_SPECIFICATION.md §1).
 * Dev mode: the backend echoes dev_otp, shown inline for testing.
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

type Stage = "identifier" | "otp";

export default function LoginPage() {
  const router = useRouter();
  const [stage, setStage] = useState<Stage>("identifier");
  const [identifier, setIdentifier] = useState("");
  const [otp, setOtp] = useState("");
  const [devOtp, setDevOtp] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function request(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await requestOtp(identifier, "LOGIN");
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
      // Role comes from server truth (/auth/me), then drives the shell routing.
      const { fetchMe } = await import("@/lib/api/client");
      const me = await fetchMe();
      const role = me.firm !== null ? "CA" : "CLIENT";
      setRoleCookie(role);
      router.push(role === "CA" ? "/ca" : "/app");
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
        <h1 className="text-xl font-semibold">Sign in</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Mobile number or email — we send a 6-digit code.
        </p>

        {stage === "identifier" ? (
          <form onSubmit={request} className="mt-6 space-y-4">
            <label className="block text-sm font-medium">
              Mobile or email
              <input
                type="text"
                required
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800"
                placeholder="98xxxxxxxx or you@firm.com"
                data-testid="login-identifier"
              />
            </label>
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
              data-testid="login-request-otp"
            >
              {busy ? "Sending…" : "Send code"}
            </button>
          </form>
        ) : (
          <form onSubmit={verify} className="mt-6 space-y-4">
            {devOtp !== null && (
              <p
                className="rounded-md bg-amber-50 px-3 py-2 font-mono text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200"
                data-testid="dev-otp"
              >
                dev code: {devOtp}
              </p>
            )}
            <label className="block text-sm font-medium">
              6-digit code
              <input
                type="text"
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                required
                value={otp}
                onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-lg tracking-widest dark:border-slate-700 dark:bg-slate-800"
                data-testid="login-otp"
              />
            </label>
            <button
              type="submit"
              disabled={busy || otp.length !== 6}
              className="w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
              data-testid="login-verify"
            >
              {busy ? "Verifying…" : "Verify & sign in"}
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
            data-testid="login-error"
          >
            {error}
          </p>
        )}

        <p className="mt-6 text-sm text-slate-500 dark:text-slate-400">
          New here?{" "}
          <a href="/register" className="font-medium text-indigo-600 hover:underline dark:text-indigo-400">
            Create an account
          </a>
        </p>
      </div>
    </main>
  );
}