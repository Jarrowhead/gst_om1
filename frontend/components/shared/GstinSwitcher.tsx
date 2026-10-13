"use client";

/**
 * Searchable GSTIN switcher for the shell header (replaces the native
 * <select>). Native selects cannot host a search bar, so this renders a
 * compact trigger button showing the active GSTIN and, when opened, a
 * panel with a search input plus a filtered, keyboard-navigable list of
 * GSTINs (GSTIN + legal name). Behaviour is otherwise unchanged: picking a
 * GSTIN fires onSelect and the shell navigates to the same section scoped
 * to that GSTIN.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import type { GstinRefDto } from "@/lib/api/client";

interface GstinSwitcherProps {
  accounts: GstinRefDto[];
  /** Currently active GSTIN (the trigger label). */
  value: string;
  onSelect: (gstin: string) => void;
}

function ChevronDown() {
  return (
    <svg
      aria-hidden="true"
      className="h-3.5 w-3.5 shrink-0 text-slate-400"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <path d="M4 6l4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function GstinSwitcher({ accounts, value, onSelect }: GstinSwitcherProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);

  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q === "") return accounts;
    return accounts.filter(
      (a) =>
        a.gstin.toLowerCase().includes(q) ||
        a.legal_name.toLowerCase().includes(q),
    );
  }, [accounts, query]);

  // Reset the highlighted row whenever the query (and thus the list) changes.
  useEffect(() => {
    setHighlight(0);
  }, [query]);

  // Close on outside pointer-down; reset the query so the next open is clean.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  // Focus the search input once the panel is mounted.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setHighlight(0);
    const raf = requestAnimationFrame(() => searchRef.current?.focus());
    return () => cancelAnimationFrame(raf);
  }, [open]);

  const commit = (gstin: string) => {
    setOpen(false);
    setQuery("");
    onSelect(gstin);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const target = filtered[highlight];
      if (target) commit(target.gstin);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      setQuery("");
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        data-testid="gstin-switcher"
        aria-label="Active GSTIN"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex max-w-52 items-center gap-1 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
      >
        <span className="truncate font-mono">{value || "Select GSTIN"}</span>
        <ChevronDown />
      </button>

      {open ? (
        <div className="absolute right-0 top-full z-50 mt-1 w-80 overflow-hidden rounded-md border border-slate-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-900">
          <div className="border-b border-slate-200 p-2 dark:border-slate-700">
            <input
              ref={searchRef}
              type="text"
              data-testid="gstin-switcher-search"
              autoComplete="off"
              placeholder="Search GSTIN or business name"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-700 placeholder:text-slate-400 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:placeholder:text-slate-500"
            />
          </div>
          <ul
            id="gstin-switcher-listbox"
            role="listbox"
            aria-label="GSTINs"
            className="max-h-64 overflow-auto py-1"
          >
            {filtered.length === 0 ? (
              <li className="px-3 py-2 text-sm text-slate-500 dark:text-slate-400">
                No GSTIN matches
              </li>
            ) : (
              filtered.map((a, i) => {
                const selected = a.gstin === value;
                const highlighted = i === highlight;
                return (
                  <li
                    key={a.gstin}
                    id={`gstin-switcher-option-${i}`}
                    role="option"
                    aria-selected={selected}
                    data-testid={`gstin-option-${a.gstin}`}
                    onMouseEnter={() => setHighlight(i)}
                    onClick={() => commit(a.gstin)}
                    className={`cursor-pointer px-3 py-1.5 ${
                      highlighted
                        ? "bg-indigo-50 dark:bg-indigo-950"
                        : "bg-transparent"
                    }`}
                  >
                    <div
                      className={`truncate font-mono text-sm ${
                        selected
                          ? "font-semibold text-indigo-700 dark:text-indigo-300"
                          : "text-slate-700 dark:text-slate-200"
                      }`}
                    >
                      {a.gstin}
                    </div>
                    {a.legal_name ? (
                      <div className="truncate text-xs text-slate-500 dark:text-slate-400">
                        {a.legal_name}
                      </div>
                    ) : null}
                  </li>
                );
              })
            )}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
