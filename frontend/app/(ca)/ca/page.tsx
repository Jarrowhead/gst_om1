"use client";

/**
 * CA shell home — client roster placeholder with live /auth/me data.
 * Roster grid/search arrive in later tasks (FRONTEND_SPECIFICATION.md §3.3).
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

export default function CaHomePage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

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