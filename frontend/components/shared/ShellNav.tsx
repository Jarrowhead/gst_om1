"use client";

/**
 * Shell chrome shared by both role shells — FRONTEND_SPECIFICATION.md §1.
 * Phase 8.5 (PHASE8_PRODUCT_COMPLETENESS.md §3.8.5): persistent primary nav —
 * Dashboard · Businesses · Upload · Returns · Profile · Settings — plus a
 * GSTIN switcher and active-route highlight. Additive: the existing logo,
 * role chip, user name, and Sign out button are unchanged. Every Phase-8
 * route is reachable by clicking the nav, not only by direct URL.
 */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import type { GstinRefDto } from "@/lib/api/client";

import { GstinSwitcher } from "./GstinSwitcher";

const DEFAULT_FP = "092026";

/** GSTIN-scoped workspaces: /app/<section>/<gstin>[/<fp>[/<docId>]]. */
const SCOPED_RE = /^\/app\/(?:upload|returns|file|einvoice|review|itc)\/([^/]+)(?:\/([^/]+))?/;

/** Sections the switcher may preserve when jumping to another GSTIN. */
const SWITCHER_SECTIONS = ["upload", "returns", "file", "einvoice", "itc"];

function gstinFromPath(pathname: string): string | null {
  const m = SCOPED_RE.exec(pathname);
  return m === null ? null : m[1];
}

function fpFromPath(pathname: string): string {
  const m = SCOPED_RE.exec(pathname);
  return m !== null && m[2] !== undefined ? m[2] : DEFAULT_FP;
}

function sectionFromPath(pathname: string): string {
  const section = pathname.split("/")[2] ?? "";
  return SWITCHER_SECTIONS.includes(section) ? section : "upload";
}

interface NavItem {
  key: string;
  label: string;
  href: string;
  isActive: (pathname: string) => boolean;
}

export function ShellNav({
  roleLabel,
  userName,
  onSignOut,
  gstAccounts = [],
}: {
  roleLabel: string;
  userName: string;
  onSignOut: () => void;
  gstAccounts?: GstinRefDto[];
}) {
  const pathname = usePathname() ?? "/";
  const router = useRouter();

  const activeGstin = gstinFromPath(pathname) ?? gstAccounts[0]?.gstin ?? null;
  const fp = fpFromPath(pathname);

  const items: NavItem[] = [
    {
      key: "dashboard",
      label: "Dashboard",
      href: "/app",
      isActive: (p) => p === "/app" || p === "/app/" || p === "/",
    },
    {
      key: "businesses",
      label: "Businesses",
      href: "/app/businesses",
      isActive: (p) => p === "/app/businesses" || p.startsWith("/app/businesses/"),
    },
    // CA roster: surfaced only for multi-account users (the /ca route is
    // otherwise undiscoverable — the CA-firm audit gap). Additive nav item.
    ...(gstAccounts.length > 1
      ? [
          {
            key: "ca-clients",
            label: "Clients",
            href: "/ca",
            isActive: (p: string) => p === "/ca" || p.startsWith("/ca/"),
          },
        ]
      : []),
    {
      key: "upload",
      label: "Upload",
      // Workspace route /app/upload/<gstin>/<fp>; falls back to the shell
      // until the user has a GSTIN to work in.
      href: activeGstin !== null ? `/app/upload/${activeGstin}/${fp}` : "/app",
      isActive: (p) => p.startsWith("/app/upload"),
    },
    {
      key: "returns",
      label: "Returns",
      href: activeGstin !== null ? `/app/returns/${activeGstin}/${fp}` : "/app",
      isActive: (p) => p.startsWith("/app/returns"),
    },
    {
      key: "file",
      label: "Filing",
      href: activeGstin !== null ? `/app/file/${activeGstin}/${fp}` : "/app",
      isActive: (p) => p.startsWith("/app/file"),
    },
    {
      key: "itc",
      label: "ITC",
      // Task 9.4: the reconciliation dashboard, GSTIN + period scoped like the
      // other workspaces; falls back to the shell until a GSTIN exists.
      href: activeGstin !== null ? `/app/itc/${activeGstin}/${fp}` : "/app",
      isActive: (p) => p.startsWith("/app/itc"),
    },
    {
      key: "profile",
      label: "Profile",
      href: "/app/profile",
      isActive: (p) => p === "/app/profile" || p.startsWith("/app/profile/"),
    },
    {
      key: "settings",
      label: "Settings",
      href: "/app/settings",
      isActive: (p) => p === "/app/settings" || p.startsWith("/app/settings/"),
    },
  ];

  const switcherValue =
    activeGstin !== null && gstAccounts.some((a) => a.gstin === activeGstin)
      ? activeGstin
      : (gstAccounts[0]?.gstin ?? "");

  return (
    <nav className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-slate-200 bg-white px-6 py-3 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-4">
        <span className="text-lg font-semibold tracking-tight">GST Filing</span>
        <span
          className="rounded-full bg-indigo-100 px-2.5 py-0.5 text-xs font-medium text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
          data-testid="shell-role"
        >
          {roleLabel}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-1" data-testid="shell-nav-links">
        {items.map((item) => {
          const active = item.isActive(pathname);
          return (
            <Link
              key={item.key}
              href={item.href}
              data-testid={`nav-link-${item.key}`}
              aria-current={active ? "page" : undefined}
              className={
                active
                  ? "rounded-md bg-indigo-50 px-3 py-1.5 text-sm font-semibold text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
                  : "rounded-md px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
              }
            >
              {item.label}
            </Link>
          );
        })}
      </div>
      <div className="flex items-center gap-3">
        {gstAccounts.length > 0 ? (
          <GstinSwitcher
            accounts={gstAccounts}
            value={switcherValue}
            onSelect={(g) => {
              if (g !== "") {
                router.push(`/app/${sectionFromPath(pathname)}/${g}/${fp}`);
              }
            }}
          />
        ) : null}
        <span
          className="text-sm text-slate-600 dark:text-slate-300"
          data-testid="shell-user"
        >
          {userName}
        </span>
        <button
          type="button"
          onClick={onSignOut}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
          data-testid="signout-btn"
        >
          Sign out
        </button>
      </div>
    </nav>
  );
}