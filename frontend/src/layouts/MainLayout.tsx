/**
 * Application shell: header, navigation, and a health indicator.
 *
 * The health badge is deliberately prominent — a degraded rule library means
 * audits would silently score nothing, so it must be visible without digging.
 */
import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAuditContext } from "@/context/AuditContext";
import { useAsync } from "@/hooks/useAsync";
import { logout } from "@/services/authService";
import { getHealth } from "@/services/catalogService";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/run", label: "Run audit" },
  { to: "/catalog", label: "Checks" },
  { to: "/checklist", label: "Checklist" },
  { to: "/history", label: "History" },
];

function HealthIndicator() {
  const { data, error } = useAsync(() => getHealth(), []);

  if (error) {
    return (
      <span className="badge bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300">
        API unreachable
      </span>
    );
  }
  if (!data) return null;

  const healthy = data.status === "ok";
  return (
    <span
      className={`badge ${
        healthy
          ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300"
          : "bg-yellow-100 text-yellow-800 dark:bg-yellow-950 dark:text-yellow-300"
      }`}
      title={`${data.checks_registered} checks loaded · v${data.version} · ${data.environment}`}
    >
      {healthy ? `${data.checks_registered} checks` : "Degraded"}
    </span>
  );
}

/**
 * The account control: a profile menu with sign-out when authenticated, and a
 * Login button otherwise. The display name comes from the server-side session
 * — the browser still never holds a Fabric token.
 */
function AccountMenu() {
  const { session, user, setSession, setUser } = useAuditContext();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  const signOut = async () => {
    setOpen(false);
    if (session) {
      try {
        await logout(session);
      } catch {
        /* signing out locally is enough even if the server call fails */
      }
    }
    setSession(null);
    setUser(null);
    navigate("/");
  };

  if (!session) {
    return (
      <NavLink to="/sign-in" className="btn-primary px-3 py-1.5 text-sm">
        Login
      </NavLink>
    );
  }

  const name = user?.name || user?.username || "Account";
  const initials = name
    .split(/\s+/)
    .map((part) => part[0] ?? "")
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <div
      className="relative"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false);
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex items-center gap-2 rounded-full border border-slate-200 py-1 pl-1 pr-3 text-sm font-medium hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
      >
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-blue-600 text-xs font-semibold text-white">
          {initials || "?"}
        </span>
        <span className="max-w-[10rem] truncate">{name}</span>
        <span aria-hidden="true" className="text-slate-400">▾</span>
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 z-20 mt-2 w-64 overflow-hidden rounded-md border border-slate-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-900"
        >
          <div className="border-b border-slate-100 px-4 py-3 dark:border-slate-800">
            <p className="truncate text-sm font-medium">{user?.name ?? "Signed in"}</p>
            {user?.username && (
              <p className="truncate text-xs text-slate-500">{user.username}</p>
            )}
            <span className="mt-1 inline-flex items-center gap-1 text-xs text-green-700 dark:text-green-400">
              <span className="h-1.5 w-1.5 rounded-full bg-green-600" aria-hidden="true" />
              Connected to Fabric
            </span>
          </div>
          <NavLink
            to="/sign-in"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="block px-4 py-2 text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            Account &amp; diagnostics
          </NavLink>
          <button
            type="button"
            role="menuitem"
            onClick={signOut}
            className="block w-full px-4 py-2 text-left text-sm text-red-600 hover:bg-slate-100 dark:text-red-400 dark:hover:bg-slate-800"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export function MainLayout() {
  return (
    <div className="flex min-h-full flex-col">
      <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-4 px-4 py-3">
          <div className="flex items-center gap-3">
            <span className="text-base font-semibold">Fabric Well-Architected Auditor</span>
            <HealthIndicator />
          </div>

          <nav className="flex flex-wrap gap-1" aria-label="Main">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                    isActive
                      ? "bg-blue-600 text-white"
                      : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <AccountMenu />
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl flex-1 space-y-6 px-4 py-6">
        <Outlet />
      </main>

      <footer className="border-t border-slate-200 px-4 py-3 text-center text-xs text-slate-500 dark:border-slate-800">
        Deterministic, read-only auditing — the same input always produces the same score.
      </footer>
    </div>
  );
}
