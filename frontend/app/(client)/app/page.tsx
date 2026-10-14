"use client";

/**
 * Unified shell home — one user type (v4 model). Shows the user's GST
 * accounts from /auth/me (gst_accounts: GSTIN + role + legal_name); the
 * month workspace + deadlines arrive in later tasks (FRONTEND_SPEC §1).
 *
 * Phase 10.2: the header now lives in the shared (client)/app layout; this
 * page renders only the dashboard body and pushes the user name + GST accounts
 * into the ShellContext so the nav's user chip and GSTIN switcher stay in sync
 * (the profile/businesses pages mutate the same context on rename/add).
 * No <ShellNav> here — no double header.
 */
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useShell } from "@/components/shared/ShellContext";
import {
  ApiError,
  fetchMe,
  getGstAccountOverview,
  silentRefresh,
} from "@/lib/api/client";
import type { MeDto, FyPeriodStatus } from "@/lib/api/client";
import { clearSession } from "@/lib/auth/session";
import Link from "next/link";

/**
 * Filing-status tracker chip (PHASE8 8.10): renders filed / draft / pending
 * per period. The backend reports OPEN / READY_FOR_FILING / FILED
 * (FilingStatus); we map READY_FOR_FILING → DRAFT (prepared, not yet filed)
 * and OPEN → PENDING (no filing activity yet). FILED stays FILED.
 * Status colors follow FRONTEND_SPECIFICATION.md §2 (FILED blue, others slate).
 */
function trackerClass(status: string): string {
  if (status === "FILED") return "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300";
  if (status === "DRAFT") return "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300";
  return "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300";
}

function trackerLabel(status: string): string {
  if (status === "FILED") return "filed";
  if (status === "DRAFT") return "draft";
  return "pending";
}

/** Backend FilingStatus → tracker filed/draft/pending vocabulary. */
function trackerStatus(periodStatus: string): "FILED" | "DRAFT" | "PENDING" {
  if (periodStatus === "FILED") return "FILED";
  if (periodStatus === "READY_FOR_FILING") return "DRAFT";
  return "PENDING";
}

/** "092026" → "Sep 2026". */
function fpLabel(fp: string): string {
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  const m = Number(fp.slice(0, 2));
  const y = fp.slice(2);
  const name = months[m - 1] ?? fp.slice(0, 2);
  return `${name} ${y}`;
}

/** The per-GSTIN × period roll-up grid (8.10) — one row per FY period. */
function FilingStatusGrid({ periods, gstin, currentFp }: {
  periods: FyPeriodStatus[];
  gstin: string;
  currentFp: string;
}) {
  return (
    <div className="mt-3 overflow-x-auto" data-testid={`filing-status-grid-${gstin}`}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500 dark:border-slate-700 dark:text-slate-400">
            <th className="py-2 pr-4 font-medium">Period</th>
            <th className="py-2 pr-4 font-medium">Status</th>
            <th className="py-2 pr-4 font-medium">GSTR-1 due</th>
            <th className="py-2 pr-4 font-medium">GSTR-3B due</th>
            <th className="py-2 font-medium">Quick actions</th>
          </tr>
        </thead>
        <tbody>
          {periods.map((p) => {
            const status = trackerStatus(p.status);
            return (
              <tr
                key={p.fp}
                className="border-b border-slate-100 last:border-b-0 dark:border-slate-800"
                data-testid="filing-status-row"
                data-fp={p.fp}
              >
                <td className="py-2 pr-4 font-medium" data-testid="filing-status-fp">
                  {fpLabel(p.fp)}
                </td>
                <td className="py-2 pr-4">
                  <span
                    className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${trackerClass(status)}`}
                    data-testid="filing-status-chip"
                    data-status={trackerLabel(status)}
                  >
                    {trackerLabel(status)}
                  </span>
                </td>
                <td className="py-2 pr-4 text-slate-500 dark:text-slate-400">
                  {p.gstr1_due_date === null ? "—" : p.gstr1_due_date.slice(0, 10)}
                </td>
                <td className="py-2 pr-4 text-slate-500 dark:text-slate-400">
                  {p.gstr3b_due_date === null ? "—" : p.gstr3b_due_date.slice(0, 10)}
                </td>
                <td className="py-2">
                  <div className="flex flex-wrap gap-2">
                    <Link
                      href={`/app/upload/${gstin}/${p.fp}`}
                      className="text-xs font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                      data-testid="grid-upload-link"
                    >
                      Upload
                    </Link>
                    <Link
                      href={`/app/returns/${gstin}/${p.fp}`}
                      className="text-xs font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                      data-testid="grid-returns-link"
                    >
                      Returns
                    </Link>
                    {p.fp === currentFp ? null : (
                      <span className="text-xs text-slate-400" data-testid="grid-period-note">
                        historical period
                      </span>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function ClientHomePage() {
  const router = useRouter();
  const shell = useShell();
  // Stable state setters (identity never changes) so the mount fetch below
  // cannot re-fire when the shell value object changes — that would loop.
  const setShellUserName = shell?.setUserName;
  const setShellGstAccounts = shell?.setGstAccounts;
  const [me, setMe] = useState<MeDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const currentFp = "092026"; // default demo period (MMYYYY)
  // Per-GSTIN filing-status roll-ups (8.10): gstin -> overview periods.
  const [grids, setGrids] = useState<Record<string, FyPeriodStatus[]>>({});

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
        if (!cancelled) {
          setMe(data);
          // 10.2: feed the shared shell header (user chip + GSTIN switcher).
          setShellUserName?.(data.user.full_name);
          setShellGstAccounts?.(data.gst_accounts);
        }
        // 8.10: fetch each GSTIN's this-FY period statuses. Failures here are
        // non-fatal — the cards render without a grid and the card error
        // surface (toast/inline per FRONTEND_SPEC §4) stays for profile
        // failures; a per-GSTIN fetch error just leaves that grid absent.
        for (const acc of data.gst_accounts ?? []) {
          try {
            const overview = await getGstAccountOverview(acc.gstin);
            if (!cancelled) {
              setGrids((prev) => ({ ...prev, [acc.gstin]: overview.periods }));
            }
          } catch {
            // overview is an optional enhancement on this screen; the card
            // itself still renders from /auth/me data above.
          }
        }
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
  }, [router, setShellUserName, setShellGstAccounts]);

  if (me === null && error === null) {
    return (
      <div className="flex min-h-[calc(100vh-3rem)] items-center justify-center text-sm text-slate-500">
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
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">
      <h1 className="text-2xl font-semibold">Your GST accounts</h1>
      <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
        Registration month cards and deadlines land with the month workspace.
      </p>
      <div className="mt-8 grid gap-4">
        {me !== null && (me.gst_accounts?.length ?? 0) > 0 ? (
          me.gst_accounts.map((acc) => (
            <div
              key={acc.gstin}
              className="rounded-lg border border-slate-200 p-4 dark:border-slate-800"
              data-testid="gst-account-card"
            >
              <div className="flex items-center justify-between">
                <div>
                  <Link
                    href={`/app/businesses/${acc.gstin}`}
                    className="font-semibold text-slate-900 hover:text-indigo-600 hover:underline dark:text-white dark:hover:text-indigo-400"
                    data-testid="business-title-link"
                  >
                    {acc.legal_name}
                  </Link>
                  <div className="text-sm text-slate-500 dark:text-slate-400">
                    {acc.gstin}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <Link
                    href={`/app/businesses/${acc.gstin}`}
                    className="text-sm font-medium text-slate-600 hover:text-indigo-600 hover:underline dark:text-slate-300 dark:hover:text-indigo-400"
                    data-testid="manage-business-link"
                  >
                    Manage
                  </Link>
                  <Link
                    href={`/app/upload/${acc.gstin}/${currentFp}`}
                    className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700"
                    data-testid="upload-link"
                  >
                    Upload
                  </Link>
                  <Link
                    href={`/app/file/${acc.gstin}/${currentFp}`}
                    className="text-sm font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                    data-testid="documents-link"
                  >
                    File & Docs
                  </Link>
                  <div className="text-sm text-slate-500 dark:text-slate-400">
                    {acc.role}
                  </div>
                </div>
              </div>
              {grids[acc.gstin] !== undefined ? (
                <div className="mt-4 border-t border-slate-100 pt-3 dark:border-slate-800">
                  <h2 className="text-sm font-semibold" data-testid="filing-status-title">
                    Filing status this FY
                  </h2>
                  <FilingStatusGrid
                    periods={grids[acc.gstin]}
                    gstin={acc.gstin}
                    currentFp={currentFp}
                  />
                </div>
              ) : null}
            </div>
          ))
        ) : (
          // 9.3: designed empty state (FRONTEND_SPECIFICATION.md §4 — every
          // list/table has an empty state with a next action). The dashed
          // box + copy stay; a primary CTA routes to the Phase-8 add-GSTIN
          // flow at /app/businesses. Additive — no testid removed.
          <div className="rounded-xl border border-dashed border-slate-300 p-10 text-center dark:border-slate-700">
            <p className="text-sm text-slate-500 dark:text-slate-400">
              No GST accounts linked yet — attach a GSTIN to begin.
            </p>
            <Link
              href="/app/businesses"
              className="mt-4 inline-flex rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700"
              data-testid="empty-link-gstin"
            >
              + Link or Register GSTIN
            </Link>
          </div>
        )}
      </div>
    </main>
  );
}
