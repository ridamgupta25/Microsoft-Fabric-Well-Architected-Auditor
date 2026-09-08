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
import { Link, useNavigate } from "react-router-dom";

import { ErrorBanner, Section, Spinner } from "@/components/ui";
import { useAuditContext } from "@/context/AuditContext";
import { useAsync } from "@/hooks/useAsync";
import {
  getDiagnostics,
  listLiveWorkspaces,
  pollAudit,
  submitAudit,
} from "@/services/auditService";
import { listAdminCategories } from "@/services/catalogService";
import type {
  AdminCategoryInfo,
  AdminReadiness,
  AdminSettings,
  AuditJob,
  Workspace,
} from "@/types/api";

/**
 * What each family needs the signed-in user to be able to read.
 *
 * These are documented Fabric requirements, not guesses: role assignments need
 * Member or higher, connections and gateways need their own delegated scope plus
 * a role on each object.
 */
const REQUIREMENTS: Record<string, string> = {
  "Workspace Admin":
    "Needs Member or higher on the workspaces. The connection and gateway checks also need Connection.Read.All and Gateway.Read.All.",
  Tenant: "Needs Fabric tenant administrator — tenant settings, scanner and audit-log APIs.",
  Capacity: "Needs capacity administrator — capacity metrics and SKU details.",
};

/**
 * A standing note for the families whose access cannot be probed up front.
 *
 * Workspace Admin is probed (see {@link gaps}); Tenant and Capacity are not,
 * because their APIs are tenant-wide and one call would not tell the reviewer
 * which of the nine/five checks it unlocks. So the requirement is stated plainly
 * here, up front, rather than only becoming visible as N/A after the run.
 */
const ROLE_NOTES: Record<string, string> = {
  Tenant:
    "Without the Fabric tenant administrator role, these checks cannot read the admin APIs and will report N/A.",
  Capacity:
    "Without capacity administrator access to the Capacity Metrics app, these checks will report N/A.",
};

/**
 * What this account cannot read, if anything.
 *
 * Only gaps are reported. A working account sees nothing here — a permanent
 * banner saying everything is fine is noise that trains people to ignore the one
 * time it matters.
 */
function gaps(category: string, probe: AdminReadiness | undefined): string[] {
  if (!probe || category !== "Workspace Admin") return [];
  const found: string[] = [];
  if (!probe.role_assignments_readable) {
    found.push("workspace role assignments (needs Member or higher)");
  }
  if (!probe.connections_readable) found.push("connections (needs Connection.Read.All)");
  if (!probe.gateways_readable) found.push("gateways (needs Gateway.Read.All)");
  return found;
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
  const disabled = !info.available;
  const missing = gaps(info.category, probe);
  const roleNote = ROLE_NOTES[info.category];

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

      {disabled && (
        <p className="mt-3 text-xs font-medium text-slate-500">
          No checks registered yet — nothing would run.
        </p>
      )}
      {!disabled && roleNote && (
        <p className="mt-3 text-xs text-amber-700">{roleNote}</p>
      )}
      {missing.length > 0 && (
        <p className="mt-3 text-xs text-amber-700">
          Your account cannot read {missing.join("; ")}. Those checks will report N/A.
        </p>
      )}
    </button>
  );
}

/** A comma-separated free-text field for a list of security-group names. */
function GroupField({
  label,
  hint,
  placeholder,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  placeholder: string;
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-sm font-medium text-slate-800">{label}</span>
      <span className="block text-xs text-slate-500">{hint}</span>
      <input
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="input"
      />
    </label>
  );
}

export function AdminChecksPage() {
  const { session, isSignedIn, setLastAuditId, setReport } = useAuditContext();
  const navigate = useNavigate();
  const [selected, setSelected] = useState<string[]>([]);
  const [chosenWorkspaces, setChosenWorkspaces] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<AuditJob | null>(null);
  // Mirrors the standard audit: once submitted the page switches to a progress
  // view and opens the report itself, rather than leaving the reviewer to notice
  // a status word and click a link.
  const [phase, setPhase] = useState<"select" | "running">("select");
  const [auditId, setAuditId] = useState<string | null>(null);
  // The three group lists are typed as text and split on submit; production
  // workspaces are picked from the selection instead, because a typed name that
  // does not match Manage access exactly reads as "no group holds access" —
  // which looks like a pass rather than a mistake.
  const [devGroups, setDevGroups] = useState("");
  const [opsGroups, setOpsGroups] = useState("");
  const [consumerGroups, setConsumerGroups] = useState("");
  const [prodWorkspaces, setProdWorkspaces] = useState<Record<string, boolean>>({});
  const [appReviewed, setAppReviewed] = useState(false);
  const [sizingConfirmed, setSizingConfirmed] = useState(false);
  const [codeScanClean, setCodeScanClean] = useState(false);
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

  const toggleCategory = useCallback((category: string) => {
    setSelected((current) =>
      current.includes(category)
        ? current.filter((name) => name !== category)
        : [...current, category],
    );
  }, []);

  const allWorkspaces = workspaces.data ?? [];
  const selectAll = (value: boolean) =>
    setChosenWorkspaces(Object.fromEntries(allWorkspaces.map((w) => [w.id, value])));

  const selectedWorkspaces = useMemo(
    () => allWorkspaces.filter((w) => chosenWorkspaces[w.id]),
    [allWorkspaces, chosenWorkspaces],
  );

  /** Only send what was filled in, so an untouched field leaves the YAML alone. */
  const adminSettings = useMemo<AdminSettings>(() => {
    const split = (text: string) =>
      text.split(",").map((part) => part.trim()).filter(Boolean);
    const settings: AdminSettings = {};
    const prod = selectedWorkspaces.filter((w) => prodWorkspaces[w.id]).map((w) => w.name);
    if (prod.length) settings.production_workspaces = prod;
    if (split(devGroups).length) settings.developer_groups = split(devGroups);
    if (split(opsGroups).length) settings.operations_groups = split(opsGroups);
    if (split(consumerGroups).length) {
      settings.report_consumer_groups = split(consumerGroups);
    }
    if (appReviewed) settings.app_access_reviewed = true;
    if (sizingConfirmed) settings.gateway_sizing_confirmed = true;
    if (codeScanClean) settings.code_scan_clean = true;
    return settings;
  }, [
    selectedWorkspaces, prodWorkspaces, devGroups, opsGroups, consumerGroups,
    appReviewed, sizingConfirmed, codeScanClean,
  ]);

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
        admin_settings: adminSettings,
      });
      setLastAuditId(accepted.audit_id);
      setAuditId(accepted.audit_id);
      setPhase("running");
      const finished = await pollAudit(accepted.audit_id, setJob, controller.signal);
      setJob(finished);
      if (finished.status === "failed" && !finished.report) {
        setError(finished.error ?? "The elevated run failed.");
        setPhase("select");
        return;
      }
      // Nothing to answer on an elevated run — no questionnaire — so the report
      // opens as soon as the crawl finishes.
      setReport(finished.report ?? null);
      navigate(`/report/${accepted.audit_id}`);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : String(err));
      setPhase("select");
    } finally {
      setSubmitting(false);
    }
  }, [
    isSignedIn, session, selected, selectedWorkspaces, adminSettings,
    setLastAuditId, setReport, navigate,
  ]);

  const cancelRun = useCallback(() => {
    abortRef.current?.abort();
    setPhase("select");
    setJob(null);
    setAuditId(null);
    setSubmitting(false);
  }, []);

  const probe = diagnostics.data?.admin;
  const wantsWorkspaceAdmin = selected.includes("Workspace Admin");

  if (phase === "running") {
    const running = job?.status === "queued" || job?.status === "running" || !job;
    return (
      <div className="space-y-6">
        {error && <ErrorBanner message={error} />}
        <Section
          title="Elevated checks in progress"
          description="Only the categories you chose are running, against their own crawl. The report opens automatically when they finish."
          actions={
            <button type="button" className="btn-secondary" onClick={cancelRun}>
              Cancel
            </button>
          }
        >
          <div className="card flex items-center gap-3">
            {running ? (
              <Spinner label="Reading your workspaces…" />
            ) : (
              <span className="flex items-center gap-2 text-sm font-medium text-green-700 dark:text-green-400">
                <span
                  className="block h-2.5 w-2.5 rounded-full bg-green-500"
                  aria-hidden="true"
                />
                Elevated checks complete — opening the report…
              </span>
            )}
            {auditId && (
              <span className="ml-auto font-mono text-xs text-slate-400">{auditId}</span>
            )}
          </div>
        </Section>
      </div>
    );
  }

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
            to run these checks.
          </p>
        )}
      </Section>

      <Section
        title="Workspaces"
        description="Which workspaces to check."
        actions={
          <div className="flex gap-2">
            <button type="button" className="btn-secondary" onClick={() => selectAll(true)}>
              Select all
            </button>
            <button type="button" className="btn-secondary" onClick={() => selectAll(false)}>
              Clear
            </button>
          </div>
        }
      >
        {workspaces.loading && <Spinner label="Loading your workspaces…" />}
        {workspaces.error && (
          <ErrorBanner message={workspaces.error} onRetry={workspaces.reload} />
        )}
        {allWorkspaces.length > 0 && (
          <div className="scroll-x rounded-md border border-slate-200 dark:border-slate-800">
            <table className="table-base">
              <thead>
                <tr>
                  <th scope="col" className="w-10">Check</th>
                  <th scope="col">Workspace</th>
                  <th scope="col">Items</th>
                  <th scope="col">Pipelines</th>
                </tr>
              </thead>
              <tbody>
                {allWorkspaces.map((workspace) => (
                  <tr key={workspace.id}>
                    <td>
                      <input
                        type="checkbox"
                        checked={chosenWorkspaces[workspace.id] ?? false}
                        onChange={(event) =>
                          setChosenWorkspaces((prev) => ({
                            ...prev,
                            [workspace.id]: event.target.checked,
                          }))
                        }
                        aria-label={`Check ${workspace.name}`}
                      />
                    </td>
                    <td>
                      <div className="font-medium">{workspace.name}</div>
                      <div className="font-mono text-xs text-slate-500">{workspace.id}</div>
                    </td>
                    <td>{workspace.items ?? "—"}</td>
                    <td>{workspace.pipelines ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {wantsWorkspaceAdmin && (
        <Section
          title="Tell us about your teams"
          description="Fabric shows us who has access, but not who they are — it sees a group name, not whether that group is your developers or your finance team. Name them below and we can check the right people have the right level of access. Anything you skip is simply not assessed; it never counts against your score."
        >
          <div className="space-y-5 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-950">
            <div>
              <span className="text-sm font-medium text-slate-800">
                Which of these are your live production workspaces?
              </span>
              <p className="mb-2 text-xs text-slate-500">
                Tick the ones holding real data the business depends on. We check that
                developers cannot change them directly. Leave sandboxes, demos and personal
                workspaces unticked.
              </p>
              {selectedWorkspaces.length === 0 ? (
                <p className="text-xs text-slate-500">
                  Select a workspace above and it will appear here.
                </p>
              ) : (
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {selectedWorkspaces.map((w) => (
                    <label key={w.id} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={Boolean(prodWorkspaces[w.id])}
                        onChange={(event) =>
                          setProdWorkspaces((current) => ({
                            ...current,
                            [w.id]: event.target.checked,
                          }))
                        }
                      />
                      <span className="truncate">{w.name}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>

            <p className="border-t border-slate-100 pt-4 text-xs text-slate-500 dark:border-slate-800">
              For the three boxes below, copy the group name exactly as it appears in a
              workspace's <strong>Manage access</strong> pane. Capitals do not matter,
              spelling does. Separate several names with commas. These are Entra security
              groups, so they apply to every workspace you selected.
            </p>

            <GroupField
              label="Which security group are your developers in?"
              hint="We check they cannot edit production directly — changes there should arrive through your deployment pipeline, not by hand."
              placeholder="e.g. SG-Fabric-Developers"
              value={devGroups}
              onChange={setDevGroups}
            />
            <GroupField
              label="Which security group is your support or operations team?"
              hint="We check they can open these workspaces, so nobody is locked out of the monitoring dashboards during an incident."
              placeholder="e.g. SG-DataOps"
              value={opsGroups}
              onChange={setOpsGroups}
            />
            <GroupField
              label="Which security group only reads reports?"
              hint="Business users who view reports but never build anything. We check they hold Viewer and nothing higher."
              placeholder="e.g. SG-Sales-Report-Readers"
              value={consumerGroups}
              onChange={setConsumerGroups}
            />

            <label className="flex items-start gap-2 border-t border-slate-100 pt-4 text-sm dark:border-slate-800">
              <input
                type="checkbox"
                checked={appReviewed}
                onChange={(event) => setAppReviewed(event.target.checked)}
                className="mt-1"
              />
              <span>
                I have checked who can see reports through <strong>Power BI apps</strong>.
                <span className="block text-xs text-slate-500">
                  An app shares reports with people who hold no workspace role at all, and
                  Fabric does not expose that audience list. Tick this only if you have
                  opened the app and looked.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={sizingConfirmed}
                onChange={(event) => setSizingConfirmed(event.target.checked)}
                className="mt-1"
              />
              <span>
                I have confirmed the <strong>gateway machines</strong> are powerful enough.
                <span className="block text-xs text-slate-500">
                  Fabric tells us how many machines run a gateway, but never whether they
                  can handle the load. Tick this only if you have asked whoever runs them.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={codeScanClean}
                onChange={(event) => setCodeScanClean(event.target.checked)}
                className="mt-1"
              />
              <span>
                A <strong>code scan for hardcoded secrets</strong> came back clean.
                <span className="block text-xs text-slate-500">
                  A password pasted into a notebook or pipeline never becomes a connection,
                  so the connection check cannot see it. The standard audit covers this —
                  tick once it has run clean.
                </span>
              </span>
            </label>
          </div>
        </Section>
      )}

      {error && <ErrorBanner message={error} />}

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={run}
          disabled={submitting || !isSignedIn}
          className="btn-primary"
        >
          {submitting ? "Starting…" : "Run elevated checks"}
        </button>
      </div>
    </div>
  );
}
