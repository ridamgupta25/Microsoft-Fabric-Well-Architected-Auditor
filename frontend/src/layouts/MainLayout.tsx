/**
 * Application shell: header, navigation, and a health indicator.
 *
 * The health badge is deliberately prominent — a degraded rule library means
 * audits would silently score nothing, so it must be visible without digging.
 */
import { NavLink, Outlet } from "react-router-dom";

import { useAuditContext } from "@/context/AuditContext";
import { useAsync } from "@/hooks/useAsync";
import { getHealth } from "@/services/catalogService";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/run", label: "Run audit" },
  { to: "/catalog", label: "Checks" },
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

export function MainLayout() {
  const { isSignedIn } = useAuditContext();

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
            <NavLink
              to="/sign-in"
              className={`badge gap-1.5 ${
                isSignedIn
                  ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300"
                  : "bg-orange-100 text-orange-800 hover:bg-orange-200 dark:bg-orange-950 dark:text-orange-300"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  isSignedIn ? "bg-green-600" : "bg-orange-500"
                }`}
                aria-hidden="true"
              />
              {isSignedIn ? "Connected to Fabric" : "Connect to Fabric"}
            </NavLink>
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
