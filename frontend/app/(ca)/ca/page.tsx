"use client";

/**
 * CA shell home. The client roster arrives in a later task.
 *
 * Flow:
 *   1. On load, silentRefresh. Failure clears the role cookie and opens /login.
 *   2. fetchMe. firm null still renders this page if the user opened /ca directly.
 *   3. signOut drops the memory token and role cookie, then opens /login.
 *
 * Debug:
 *   A CA who has not created a firm yet is routed to /app by login, not by this page.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ShellNav } from "@/components/shared/ShellNav";
import { ApiError, fetchMe, setAccessToken, silentRefresh } from "@/lib/api/client";
import { clearSession } from "@/lib/auth/session";

interface Me {
  user: { full_name: string; totp_enabled: boolean };
  firm: string | null;
}

/**
 * CA home. Confirms the refresh cookie still works, then shows the signed-in name.
 *
 * Flow:
 *   1. On load, silentRefresh. Failure clears the role cookie and opens /login.
 *   2. fetchMe. A null firm still renders this page.
 *   3. signOut drops the memory token and role cookie, then opens /login.
 *
 * Debug:
 *   A CA who has not created a firm is sent to /app by login, not by this page.
 */
export default function CaHomePage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  /**
   * Load the signed-in profile or leave for /login.
   *
   * Flow:
   *   1. silentRefresh using the httpOnly cookie.
   *   2. fetchMe. Ignore the result if the effect was cleaned up.
   *   3. 401 or a failed refresh → clearSession and replace /login.
   */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (!(await silentRefresh())) {
          clearSession();
          router.replace("/login");
          return;
        }
        const data = await fetchMe();
        if (!cancelled) setMe(data);
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError && err.status === 401) {
            clearSession();
            router.replace("/login");
            return;
          }
          setError(err instanceof ApiError ? err.message : "failed to load profile");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  /**
   * Drop the in-memory token and role cookie, then go to /login.
   *
   * Flow:
   *   setAccessToken(null), clearSession(), router.push("/login").
   *
   * Debug:
   *   The httpOnly refresh cookie is not cleared here. A later silentRefresh can log the user back in.
   */
  async function signOut() {
    setAccessToken(null);
    clearSession();
    router.push("/login");
  }

  if (me === null && error === null) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-500">
        Checking session…
      </div>
    );
  }

  if (error !== null) {
    return (
      <div className="flex min-h-[calc(100vh-3rem)] items-center justify-center">
        <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col">
      <ShellNav
        roleLabel="CA firm"
        userName={me?.user.full_name ?? "…"}
        onSignOut={signOut}
      />
      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">
        <h1 className="text-2xl font-semibold">Client roster</h1>
        <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
          Firms linked to your account appear here; add clients via GSTIN
          request or invite code (Phase-1 flows).
        </p>
        <div className="mt-8 rounded-xl border border-dashed border-slate-300 p-10 text-center dark:border-slate-700">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            No clients yet — firm id: {me?.firm ?? "—"}
          </p>
        </div>
        <div className="mt-6 grid gap-3 sm:grid-cols-3">
          <Link
            href="/totp"
            className="rounded-lg border border-slate-200 p-4 text-sm hover:border-indigo-400 dark:border-slate-800"
          >
            TOTP status: {me?.user.totp_enabled === true ? "enabled" : "not set up"}
          </Link>
          <div className="rounded-lg border border-slate-200 p-4 text-sm text-slate-500 dark:border-slate-800 dark:text-slate-400">
            Return prep — upcoming
          </div>
          <div className="rounded-lg border border-slate-200 p-4 text-sm text-slate-500 dark:border-slate-800 dark:text-slate-400">
            2B / ITC — upcoming
          </div>
        </div>
      </main>
    </div>
  );
}