/**
 * Elevated ("admin") checks — pick a family, pick workspaces, run.
 *
 * A deliberately separate screen from the standard audit, because an elevated
 * run is a separate thing: it runs ONLY the family you choose, crawls only what
 * that family reads, and scores on its own. Mixing it into the standard audit
 * would make the deterministic score depend on who happened to sign in.
 *
 * The three families are fetched from `/catalog/admin-categories` rather than
 * hardcoded here, so a family populated later (Tenant, Capacity) appears with a
 * live count and no change to this file.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ErrorBanner, Section, Spinner } from "@/components/ui";
import { useAuditContext } from "@/context/AuditContext";
import { useAsync } from "@/hooks/useAsync";
import {
  getDiagnostics,
  listLiveWorkspaces,
  pollAudit,
  submitAudit,
} from "@/services/auditService";
import { listAdminCategories, listAdminChecks } from "@/services/catalogService";
import type {
  AdminCategoryInfo,
  AdminReadiness,
  AuditJob,
  CheckSpec,
  Workspace,
} from "@/types/api";

/**
 * What each family needs the signed-in user to be able to read.
 *
 * These are documented Fabric requirements, not guesses: role assignments need
 * Member or higher, connections and gateways need their own delegated scope plus
 * a role on each object. Showing the requirement up front turns a run that comes
 * back all-N/A into a decision the user makes knowingly.
 */
const REQUIREMENTS: Record<string, string> = {
  "Workspace Admin":
    "Member or higher on the workspaces, plus Connection.Read.All and Gateway.Read.All for the connection and gateway checks.",
  Tenant: "Fabric tenant administrator — tenant settings, scanner and audit-log APIs.",
  Capacity: "Capacity administrator — capacity metrics and SKU details.",
};

/** Whether the probe says this family's data is readable. */
function readiness(
  category: string,
  admin: AdminReadiness | undefined,
): { ready: boolean; detail: string } {
  if (!admin) {
    return { ready: false, detail: "Sign in to check what your account can read." };
  }
  if (category === "Workspace Admin") {
    const parts: string[] = [];
    parts.push(
      admin.role_assignments_readable
        ? `role assignments readable on ${admin.member_workspaces} of ${admin.sampled_workspaces} sampled workspace(s)`
        : "role assignments not readable — you need Member or higher",
    );
    parts.push(
      admin.connections_readable
        ? "connections readable"
        : `connections not readable (HTTP ${admin.connections_status ?? "?"})`,
    );
    parts.push(
      admin.gateways_readable
        ? "gateways readable"
        : `gateways not readable (HTTP ${admin.gateways_status ?? "?"})`,
    );
    // Partial access still runs: whatever cannot be read reports N/A rather than
    // failing, so a workspace Admin with no gateway rights gets a useful subset.
    return { ready: admin.role_assignments_readable, detail: parts.join(" · ") };
  }
  return { ready: true, detail: "Readiness for this family is not probed yet." };
}

function CategoryCard({
  info,
  selected,
  probe,
  onToggle,
}: {
  info: AdminCategoryInfo;
  selected: boolean;
  probe: AdminReadiness | undefined;
  onToggle: () => void;
}) {
  const { ready, detail } = readiness(info.category, probe);
  const disabled = !info.available;

  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      aria-pressed={selected}
      className={[
        "w-full rounded-lg border p-4 text-left transition",
        disabled
          ? "cursor-not-allowed border-slate-200 bg-slate-50 opacity-60"
          : selected
            ? "border-sky-500 bg-sky-50 ring-1 ring-sky-500"
            : "border-slate-200 bg-white hover:border-slate-300",
      ].join(" ")}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-slate-900">{info.category}</h3>
          <p className="mt-1 text-sm text-slate-500">{REQUIREMENTS[info.category]}</p>
        </div>
        <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
          {info.checks} {info.checks === 1 ? "check" : "checks"}
        </span>
      </div>

      {disabled ? (
        <p className="mt-3 text-xs font-medium text-slate-500">
          No checks registered yet — nothing would run.
        </p>
      ) : (
        <p
          className={[
            "mt-3 text-xs",
            ready ? "text-emerald-700" : "text-amber-700",
          ].join(" ")}
        >
          {ready ? "✓ " : "⚠ "}
          {detail}
        </p>
      )}
    </button>
  );
}

export function AdminChecksPage() {
  const { session, isSignedIn, setLastAuditId } = useAuditContext();
  const [selected, setSelected] = useState<string[]>([]);
  const [chosenWorkspaces, setChosenWorkspaces] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<AuditJob | null>(null);
  const [preview, setPreview] = useState<CheckSpec[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const categories = useAsync(() => listAdminCategories(), []);
  const diagnostics = useAsync(
    () => (session ? getDiagnostics(session) : Promise.resolve(null)),
    [session],
  );
  const workspaces = useAsync(
    () => (session ? listLiveWorkspaces(session) : Promise.resolve<Workspace[]>([])),
    [session],
  );

  useEffect(() => () => abortRef.current?.abort(), []);

  // Show which checks the current selection would actually run. Without this the
  // user picks a family blind and only learns the scope from the report.
  useEffect(() => {
    let active = true;
    if (selected.length === 0) {
      setPreview([]);
      return;
    }
    Promise.all(selected.map((category) => listAdminChecks(category)))
      .then((lists) => {
        if (active) setPreview(lists.flat());
      })
      .catch(() => {
        if (active) setPreview([]);
      });
    return () => {
      active = false;
    };
  }, [selected]);

  const toggleCategory = useCallback((category: string) => {
    setSelected((current) =>
      current.includes(category)
        ? current.filter((name) => name !== category)
        : [...current, category],
    );
  }, []);

  const selectedWorkspaces = useMemo(
    () => (workspaces.data ?? []).filter((w) => chosenWorkspaces[w.id]),
    [workspaces.data, chosenWorkspaces],
  );

  const run = useCallback(async () => {
    if (!isSignedIn || !session) {
      setError("Connect to Fabric before running elevated checks.");
      return;
    }
    if (selected.length === 0) {
      setError("Choose at least one category to run.");
      return;
    }
    if (selectedWorkspaces.length === 0) {
      setError("Select at least one workspace.");
      return;
    }

    setError(null);
    setSubmitting(true);
    setJob(null);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const accepted = await submitAudit({
        pillars: [],
        workspaces: selectedWorkspaces.map((w) => ({
          id: w.id,
          role: w.role ?? "Mixed",
          name: w.name,
        })),
        auth_session: session,
        source: "live",
        check_set: "admin",
        admin_categories: selected,
      });
      setLastAuditId(accepted.audit_id);
      const finished = await pollAudit(accepted.audit_id, setJob, controller.signal);
      setJob(finished);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }, [isSignedIn, session, selected, selectedWorkspaces, setLastAuditId]);

  const probe = diagnostics.data?.admin;

  return (
    <div className="space-y-8">
      <Section
        title="Run elevated checks"
        description="These read data an ordinary reviewer cannot see. They run on their own, with their own crawl and their own score — a standard audit is unaffected."
      >
        {categories.loading && <Spinner label="Loading categories…" />}
        {categories.error && (
          <ErrorBanner message={categories.error} onRetry={categories.reload} />
        )}
        {categories.data && (
          <div className="grid gap-3 md:grid-cols-3">
            {categories.data.map((info) => (
              <CategoryCard
                key={info.category}
                info={info}
                selected={selected.includes(info.category)}
                probe={probe}
                onToggle={() => toggleCategory(info.category)}
              />
            ))}
          </div>
        )}
        {!isSignedIn && (
          <p className="text-sm text-amber-700">
            <Link to="/sign-in" className="underline">
              Connect to Fabric
            </Link>{" "}
            to check what your account can read and to run these checks.
          </p>
        )}
      </Section>

      {preview.length > 0 && (
        <Section
          title={`${preview.length} check${preview.length === 1 ? "" : "s"} will run`}
          description="Anything your account cannot read is reported as N/A, never as a failure."
        >
          <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
            {preview.map((spec) => (
              <li key={spec.id} className="flex items-baseline gap-3 px-4 py-2 text-sm">
                <span className="font-mono text-xs text-slate-400">{spec.ref}</span>
                <span className="text-slate-800">{spec.title}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Workspaces" description="Which workspaces to check.">
        {workspaces.loading && <Spinner label="Loading workspaces…" />}
        {workspaces.error && (
          <ErrorBanner message={workspaces.error} onRetry={workspaces.reload} />
        )}
        {workspaces.data && workspaces.data.length > 0 && (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {workspaces.data.map((w) => (
              <label
                key={w.id}
                className="flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-2 text-sm"
              >
                <input
                  type="checkbox"
                  checked={Boolean(chosenWorkspaces[w.id])}
                  onChange={(event) =>
                    setChosenWorkspaces((current) => ({
                      ...current,
                      [w.id]: event.target.checked,
                    }))
                  }
                />
                <span className="truncate">{w.name ?? w.id}</span>
              </label>
            ))}
          </div>
        )}
      </Section>

      {error && <ErrorBanner message={error} />}

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={run}
          disabled={submitting || !isSignedIn}
          className="rounded bg-sky-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {submitting ? "Running…" : "Run elevated checks"}
        </button>
        {job && (
          <span className="text-sm text-slate-600">
            {job.status}
            {job.status === "succeeded" && (
              <>
                {" — "}
                <Link to={`/report/${job.audit_id}`} className="underline">
                  view report
                </Link>
              </>
            )}
          </span>
        )}
      </div>
    </div>
  );
}
