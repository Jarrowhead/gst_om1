"use client";

/**
 * Client shell home. Month cards arrive in a later task.
 *
 * Flow:
 *   1. On load, silentRefresh. Failure clears the role cookie and opens /login.
 *   2. fetchMe fills the nav name.
 *   3. signOut drops the memory token and role cookie, then opens /login.
 *
 * Debug:
 *   "Checking session…" that never ends means silentRefresh or fetchMe did not settle.
 */
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ShellNav } from "@/components/shared/ShellNav";
import { ApiError, fetchMe, setAccessToken, silentRefresh } from "@/lib/api/client";
import { clearSession } from "@/lib/auth/session";

interface Me {
  user: { full_name: string };
  businesses: string[];
}

/**
 * Client home. Confirms the refresh cookie still works, then shows the signed-in name.
 *
 * Flow:
 *   1. On load, silentRefresh. Failure clears the role cookie and opens /login.
 *   2. fetchMe fills the nav name.
 *   3. signOut drops the memory token and role cookie, then opens /login.
 *
 * Debug:
 *   "Checking session…" that never ends means silentRefresh or fetchMe did not settle.
 */
export default function ClientHomePage() {
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
        roleLabel="Business"
        userName={me?.user.full_name ?? "…"}
        onSignOut={signOut}
      />
      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">
        <h1 className="text-2xl font-semibold">Your GST months</h1>
        <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
          Registration month cards and deadlines land with the month workspace.
        </p>
        <div className="mt-8 rounded-xl border border-dashed border-slate-300 p-10 text-center dark:border-slate-700">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            No registrations linked yet — businesses:{" "}
            {me !== null ? me.businesses.length : "…"}
          </p>
        </div>
      </main>
    </div>
  );
}