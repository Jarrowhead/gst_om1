"use client";

/**
 * Login — email-only OTP for the unified user model (API_SPECIFICATION.md §1).
 * Dev mode: the backend echoes dev_otp, shown inline for testing.
 */
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  ApiError,
  requestOtp,
  setAccessToken,
  verifyOtp,
} from "@/lib/api/client";
import { setSessionCookie } from "@/lib/auth/session";

type Stage = "identifier" | "otp";

export default function LoginPage() {
  const router = useRouter();
  const [stage, setStage] = useState<Stage>("identifier");
  const [identifier, setIdentifier] = useState("");
  const [otp, setOtp] = useState("");
  const [devOtp, setDevOtp] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function request(e: React.FormEvent) {
    e.preventDefault();
    if (!identifier.trim()) {
      setError("Please enter a valid email address");
      return;
    }
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
      // One user type — session cookie is routing only (FRONTEND_SPEC §4).
      setSessionCookie();
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "OTP verification failed");
    } finally {
      setBusy(false);
    }
  }

  async function quickDemoLogin() {
    setBusy(true);
    setError(null);
    try {
      const email = "demo.tony@example.com";
      let res;
      try {
        res = await requestOtp(email, "LOGIN");
      } catch {
        res = await requestOtp(email, "REGISTER");
      }
      const code = res.dev_otp ?? "123456";
      const ver = await verifyOtp(email, code);
      setAccessToken(ver.access_token);
      setSessionCookie();
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Quick demo sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (done) {
      router.push("/app");
      router.refresh();
    }
  }, [done, router]);

  return (
    <main className="mx-auto flex min-h-[calc(100vh-3rem)] max-w-md flex-col justify-center px-4">
      <div className="rounded-xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <h1 className="text-xl font-semibold">Sign in</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Email address — we send a 6-digit code.
        </p>

        {stage === "identifier" ? (
          <form onSubmit={request} className="mt-6 space-y-4" noValidate>
            <label className="block text-sm font-medium">
              Email
              <input
                type="email"
                required
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800"
                placeholder="you@firm.com"
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

        <div className="relative my-6">
          <div className="absolute inset-0 flex items-center">
            <div className="w-full border-t border-slate-200 dark:border-slate-800" />
          </div>
          <div className="relative flex justify-center text-xs uppercase">
            <span className="bg-white px-2 text-slate-500 dark:bg-slate-900 dark:text-slate-400">
              Or instant access
            </span>
          </div>
        </div>

        <button
          type="button"
          disabled={busy}
          onClick={quickDemoLogin}
          className="w-full rounded-md border border-indigo-200 bg-indigo-50 px-4 py-2.5 text-sm font-semibold text-indigo-700 hover:bg-indigo-100 disabled:opacity-50 dark:border-indigo-900 dark:bg-indigo-950/60 dark:text-indigo-300 dark:hover:bg-indigo-900/80 transition-colors"
          data-testid="skip-login-button"
        >
          ⚡ Skip Login &amp; Open App (Demo)
        </button>

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