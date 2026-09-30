/**
 * Shell chrome shared by both role shells — FRONTEND_SPECIFICATION.md §1.
 */
/**
 * Top bar: product name, role chip, user name, sign-out.
 *
 * Flow:
 *   Renders data-testid shell-role and shell-user. onSignOut is the button handler.
 *
 * Debug:
 *   Playwright asserts these test ids. Role text is the label prop, not the cookie.
 */
export function ShellNav({
  roleLabel,
  userName,
  onSignOut,
}: {
  roleLabel: string;
  userName: string;
  onSignOut: () => void;
}) {
  return (
    <nav className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-4">
        <span className="text-lg font-semibold tracking-tight">GST Filing</span>
        <span
          className="rounded-full bg-indigo-100 px-2.5 py-0.5 text-xs font-medium text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
          data-testid="shell-role"
        >
          {roleLabel}
        </span>
      </div>
      <div className="flex items-center gap-3">
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