/**
 * Submit an audit: pick workspaces and pillars, then watch it run.
 *
 * Always audits the live tenant. Workspaces are only ever fetched once the user
 * has a real sign-in session — there is no sample/fixture data shown here, so
 * what you see on this page is always what your account can actually reach.
 *
 * Submission is fire-and-poll, so the page shows live status while the backend
 * works rather than freezing on a long request.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { QuestionnairePanel } from "@/components/QuestionnairePanel";
import { ErrorBanner, Section, Spinner } from "@/components/ui";
import { useAuditContext } from "@/context/AuditContext";
import { useAsync } from "@/hooks/useAsync";
import {
  listLiveWorkspaces,
  pollAudit,
  submitAudit,
  submitAuditAnswers,
} from "@/services/auditService";
import { listLayers, listPillars } from "@/services/catalogService";
import type { AuditJob, Workspace } from "@/types/api";

const TERMINAL_STATUSES = new Set<AuditJob["status"]>(["succeeded", "failed"]);

interface WorkspaceGroup {
  id: string;
  name: string;
  members: Record<string, number>;
}

const environmentLabel = (level: number) => {
  if (level <= 3) return "Development";
  if (level <= 6) return "Test / staging";
  if (level <= 8) return "Pre-production";
  return "Production";
};

export function RunAuditPage() {
  const navigate = useNavigate();
  const { session, isSignedIn, setLastAuditId, setReport } = useAuditContext();

  // Only fetch once there is a real session — never fall back to sample data.
  const workspaces = useAsync(
    () => (session ? listLiveWorkspaces(session) : Promise.resolve([])),
    [session],
    isSignedIn,
  );
  const pillars = useAsync(() => listPillars(), []);
  const layers = useAsync(() => listLayers(), []);

  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [manual, setManual] = useState<Workspace[]>([]);
  const [removed, setRemoved] = useState<Record<string, boolean>>({});
  const [newWsId, setNewWsId] = useState("");
  const [newWsRole, setNewWsRole] = useState("Mixed");
  const [groups, setGroups] = useState<WorkspaceGroup[]>([]);
  const [showGroupBuilder, setShowGroupBuilder] = useState(false);
  const [showGroupHelp, setShowGroupHelp] = useState(false);
  const [groupName, setGroupName] = useState("");
  const [groupMembers, setGroupMembers] = useState<Record<string, number>>({});
  //: Opt-in cross-workspace scoring — weight each workspace by its env level.
  const [weightByEnv, setWeightByEnv] = useState(false);
  const [chosenPillars, setChosenPillars] = useState<Record<string, boolean>>({});
  const [externalChecksPath, setExternalChecksPath] = useState<string>("");
  const [job, setJob] = useState<AuditJob | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // -- interactive questionnaire (self-assessed points) --------------------
  // While the automated crawl runs, the reviewer answers the points a machine
  // cannot verify. `phase` flips the page from selection to the live view.
  const [phase, setPhase] = useState<"select" | "running">("select");
  const [auditId, setAuditId] = useState<string | null>(null);
  const [finalJob, setFinalJob] = useState<AuditJob | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [answersSubmitted, setAnswersSubmitted] = useState(false);
  const [finishing, setFinishing] = useState(false);

  const questionnaire = job?.questionnaire ?? [];

  // The audit queue: live workspaces plus any added by hand, minus any removed.
  const allWorkspaces = useMemo(() => {
    const map = new Map<string, Workspace>();
    for (const w of [...(workspaces.data ?? []), ...manual]) {
      if (!removed[w.id]) map.set(w.id, w);
    }
    return [...map.values()];
  }, [workspaces.data, manual, removed]);

  const groupedWorkspaceIds = useMemo(
    () => new Set(groups.flatMap((group) => Object.keys(group.members))),
    [groups],
  );

  const isolatedWorkspaces = useMemo(
    () => allWorkspaces.filter((workspace) => !groupedWorkspaceIds.has(workspace.id)),
    [allWorkspaces, groupedWorkspaceIds],
  );

  // Workspace id -> its project group and environment position, for submission.
  const memberGroup = useMemo(() => {
    const map = new Map<string, { group: string; level: number }>();
    for (const group of groups)
      for (const [workspaceId, level] of Object.entries(group.members))
        map.set(workspaceId, { group: group.name, level });
    return map;
  }, [groups]);

  // Select every workspace by default — auditing everything you can see is the
  // common case — while preserving choices the user has already made.
  useEffect(() => {
    setSelected((prev) => {
      const next = { ...prev };
      for (const w of allWorkspaces) if (!(w.id in next)) next[w.id] = true;
      return next;
    });
    setRoles((prev) => {
      const next = { ...prev };
      for (const w of allWorkspaces) if (!(w.id in next)) next[w.id] = w.role || "Mixed";
      return next;
    });
  }, [allWorkspaces]);

  // A pillar with no runnable checks is left off by default rather than showing
  // a permanently empty pillar in every report.
  useEffect(() => {
    if (!pillars.data) return;
    setChosenPillars(
      Object.fromEntries(pillars.data.map((p) => [p.name, p.checks > 0])),
    );
  }, [pillars.data]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const submitFor = useCallback(
    async (specs: { id: string; role: string }[]) => {
      if (!isSignedIn || !session) {
        setError("Connect to Fabric before running an audit.");
        return;
      }
      const chosen = Object.entries(chosenPillars)
        .filter(([, isOn]) => isOn)
        .map(([name]) => name);

      if (specs.length === 0) {
        setError("Add or select at least one workspace.");
        return;
      }
      if (chosen.length === 0) {
        setError("Select at least one pillar.");
        return;
      }

      setError(null);
      setSubmitting(true);
      setJob(null);
      setFinalJob(null);
      setAnswers({});
      setAnswersSubmitted(false);

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const accepted = await submitAudit({
          pillars: chosen,
          workspaces: specs,
          auth_session: session,
          weight_by_environment: weightByEnv,
          external_checks_csv: externalChecksPath || null,
        });
        setLastAuditId(accepted.audit_id);
        setAuditId(accepted.audit_id);
        // Show the live view with the questionnaire straight away; the poll runs
        // in the background and records the terminal job when the crawl finishes.
        setPhase("running");
        pollAudit(accepted.audit_id, setJob, controller.signal)
          .then(setFinalJob)
          .catch((err) => {
            if (err instanceof DOMException && err.name === "AbortError") return;
            setError(err instanceof Error ? err.message : String(err));
          });
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setSubmitting(false);
      }
    },
    [chosenPillars, isSignedIn, session, setLastAuditId, weightByEnv, externalChecksPath],
  );
  const specsFor = useCallback(
    (list: Workspace[]) =>
      list.map((w) => {
        const membership = memberGroup.get(w.id);
        return {
          id: w.id,
          role: roles[w.id] ?? w.role ?? "Mixed",
          name: w.name,
          ...(membership
            ? { group: membership.group, environment_level: membership.level }
            : {}),
        };
      }),
    [roles, memberGroup],
  );

  const run = useCallback(
    () => submitFor(specsFor(allWorkspaces.filter((w) => selected[w.id]))),
    [submitFor, specsFor, allWorkspaces, selected],
  );

  // Requirement 5: iterate the entire tenant in a single action, regardless of
  // the individual ticks above.
  const runAll = useCallback(
    () => submitFor(specsFor(allWorkspaces)),
    [submitFor, specsFor, allWorkspaces],
  );

  // Once the crawl has finished, open the report — but only after the reviewer
  // has answered (or when there is nothing to answer). A failed run with no
  // report drops back to selection with the error shown.
  useEffect(() => {
    if (phase !== "running" || !finalJob || !auditId) return;
    if (finalJob.status === "failed" && !finalJob.report) {
      setError(finalJob.error ?? "The audit failed.");
      setPhase("select");
      return;
    }
    const hasQuestions = (job?.questionnaire ?? finalJob.questionnaire ?? []).length > 0;
    if (!hasQuestions || answersSubmitted) {
      setReport(finalJob.report ?? null);
      navigate(`/report/${auditId}`);
    }
  }, [phase, finalJob, auditId, job, answersSubmitted, navigate, setReport]);

  // Send the reviewer's self-assessed answers, then let the effect above open
  // the report as soon as the crawl is also done (it may already be).
  const submitAnswers = useCallback(async () => {
    if (!auditId) return;
    setFinishing(true);
    setError(null);
    try {
      const updated = await submitAuditAnswers(auditId, answers);
      setAnswersSubmitted(true);
      if (TERMINAL_STATUSES.has(updated.status)) setFinalJob(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setFinishing(false);
    }
  }, [auditId, answers]);

  const setAnswer = useCallback((id: string, value: string) => {
    setAnswers((prev) => ({ ...prev, [id]: value }));
  }, []);

  const cancelRun = useCallback(() => {
    abortRef.current?.abort();
    setPhase("select");
    setJob(null);
    setFinalJob(null);
    setAuditId(null);
    setAnswersSubmitted(false);
  }, []);

  const selectAll = (value: boolean) => {
    setSelected(Object.fromEntries(allWorkspaces.map((w) => [w.id, value])));
  };

  const addWorkspace = () => {
    const id = newWsId.trim();
    if (!id) return;
    setRemoved((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    setRoles((prev) => ({ ...prev, [id]: newWsRole }));
    setManual((prev) =>
      prev.some((w) => w.id === id)
        ? prev
        : [
            ...prev,
            { id, name: id, role: newWsRole, layer: newWsRole, items: null, pipelines: null },
          ],
    );
    setSelected((prev) => ({ ...prev, [id]: true }));
    setNewWsId("");
  };

  const removeWorkspace = (id: string) => {
    setRemoved((prev) => ({ ...prev, [id]: true }));
    setManual((prev) => prev.filter((w) => w.id !== id));
    setSelected((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  };

  const toggleGroupMember = (id: string) => {
    setGroupMembers((prev) => {
      const next = { ...prev };
      if (id in next) delete next[id];
      else next[id] = 1;
      return next;
    });
  };

  const addGroup = () => {
    const name = groupName.trim();
    if (!name || Object.keys(groupMembers).length === 0) return;
    setGroups((prev) => [
      ...prev,
      { id: crypto.randomUUID(), name, members: groupMembers },
    ]);
    setGroupName("");
    setGroupMembers({});
    setShowGroupBuilder(false);
  };

  const removeGroup = (id: string) => {
    setGroups((prev) => prev.filter((group) => group.id !== id));
  };

  if (!isSignedIn) {
    return (
      <div className="card flex flex-col items-center gap-3 py-12 text-center">
        <div className="rounded-full bg-orange-100 p-3 dark:bg-orange-950">
          <span className="block h-2.5 w-2.5 rounded-full bg-orange-500" aria-hidden="true" />
        </div>
        <h2 className="text-lg font-semibold">Connect to Microsoft Fabric</h2>
        <p className="max-w-md text-sm text-slate-600 dark:text-slate-400">
          Sign in to see the workspaces you have access to, then choose which ones to audit.
          Read-only — the tool never writes to your tenant.
        </p>
        <Link to="/sign-in" className="btn-primary mt-2">
          Connect to Fabric
        </Link>
      </div>
    );
  }

  if (phase === "running") {
    const crawlDone = finalJob != null;
    const statusLabel = crawlDone
      ? "Automated checks complete"
      : job?.report?.partial
        ? "Auditing your workspaces…"
        : "Starting the audit…";
    return (
      <div className="space-y-6">
        {error && <ErrorBanner message={error} />}

        <Section
          title="Audit in progress"
          description="The automated checks run against your workspaces while you answer the self-assessed points below. Your answers are scored alongside the automated results."
          actions={
            <button type="button" className="btn-secondary" onClick={cancelRun}>
              Cancel
            </button>
          }
        >
          <div className="card flex items-center gap-3">
            {!crawlDone ? (
              <Spinner label={statusLabel} />
            ) : (
              <span className="flex items-center gap-2 text-sm font-medium text-green-700 dark:text-green-400">
                <span className="block h-2.5 w-2.5 rounded-full bg-green-500" aria-hidden="true" />
                {statusLabel}
              </span>
            )}
            {auditId && (
              <span className="ml-auto font-mono text-xs text-slate-400">{auditId}</span>
            )}
          </div>
        </Section>

        {questionnaire.length > 0 ? (
          <Section
            title="Self-assessed checklist"
            description="Points a machine cannot verify from the workspace. Choose the option that best matches, or skip. Grouped by pillar and layer."
          >
            <QuestionnairePanel
              items={questionnaire}
              answers={answers}
              onChange={setAnswer}
              disabled={answersSubmitted}
            />
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                className="btn-primary"
                onClick={submitAnswers}
                disabled={finishing || answersSubmitted}
              >
                {answersSubmitted
                  ? crawlDone
                    ? "Opening report…"
                    : "Answers saved — finishing audit…"
                  : finishing
                    ? "Saving…"
                    : "Submit answers & view report"}
              </button>
              {answersSubmitted && !crawlDone && <Spinner label="Waiting for the crawl to finish…" />}
              {!answersSubmitted && (
                <span className="text-sm text-slate-500">
                  Unanswered points are recorded as skipped (N/A).
                </span>
              )}
            </div>
          </Section>
        ) : (
          <div className="card text-sm text-slate-600 dark:text-slate-400">
            No self-assessed points apply to the selected workspaces. The report opens
            automatically once the automated checks finish.
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Section
        title="Workspaces"
        description="Organize related environments as projects, or audit standalone workspaces independently."
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

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn-primary"
            onClick={() => setShowGroupBuilder((value) => !value)}
            disabled={isolatedWorkspaces.length === 0}
          >
            <span aria-hidden="true">＋</span> Add project workspace
          </button>
          <div className="relative">
            <button
              type="button"
              className="flex h-9 w-9 items-center justify-center rounded-full border border-slate-300 text-sm font-semibold text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
              aria-label="About project workspaces"
              aria-expanded={showGroupHelp}
              onClick={() => setShowGroupHelp((value) => !value)}
            >
              ?
            </button>
            {showGroupHelp && (
              <div className="fixed inset-x-4 top-1/2 z-20 w-auto -translate-y-1/2 rounded-md border border-slate-200 bg-white p-4 text-sm shadow-lg sm:absolute sm:inset-x-auto sm:left-0 sm:top-11 sm:w-80 sm:translate-y-0 dark:border-slate-700 dark:bg-slate-900">
                <p className="font-semibold">Project workspace groups</p>
                <p className="mt-1 text-slate-600 dark:text-slate-400">
                  Group the workspaces that represent one solution across environments. A
                  workspace can belong to only one group and is removed from the isolated
                  list, preventing it from being audited twice.
                </p>
                <p className="mt-2 text-slate-600 dark:text-slate-400">
                  Environment position is flexible: 1 is development or least critical;
                  10 is production or most critical. Use the values between them for QA,
                  staging, UAT, pre-production, or your own lifecycle.
                </p>
              </div>
            )}
          </div>
        </div>

        {showGroupBuilder && (
          <div className="rounded-md border border-blue-200 bg-blue-50/60 p-4 dark:border-blue-900 dark:bg-blue-950/30">
            <div className="flex flex-wrap items-end gap-3">
              <div className="min-w-64 flex-1">
                <label htmlFor="group-name" className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
                  Project name
                </label>
                <input
                  id="group-name"
                  className="input"
                  placeholder="e.g. Customer Insights Platform"
                  value={groupName}
                  onChange={(event) => setGroupName(event.target.value)}
                />
              </div>
              <button type="button" className="btn-secondary" onClick={() => setShowGroupBuilder(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={addGroup}
                disabled={!groupName.trim() || Object.keys(groupMembers).length === 0}
              >
                Create project
              </button>
            </div>
            <div className="mt-4 scroll-x rounded-md border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
              <table className="table-base">
                <thead>
                  <tr>
                    <th scope="col" className="w-10">Add</th>
                    <th scope="col">Workspace</th>
                    <th scope="col">Environment position</th>
                  </tr>
                </thead>
                <tbody>
                  {isolatedWorkspaces.map((workspace) => (
                    <tr key={workspace.id}>
                      <td>
                        <input
                          type="checkbox"
                          checked={workspace.id in groupMembers}
                          onChange={() => toggleGroupMember(workspace.id)}
                          aria-label={`Add ${workspace.name} to project`}
                        />
                      </td>
                      <td>
                        <div className="font-medium">{workspace.name}</div>
                        <div className="font-mono text-xs text-slate-500">{workspace.id}</div>
                      </td>
                      <td>
                        <div className="flex min-w-72 items-center gap-3">
                          <input
                            type="range"
                            min="1"
                            max="10"
                            value={groupMembers[workspace.id] ?? 1}
                            disabled={!(workspace.id in groupMembers)}
                            onChange={(event) => setGroupMembers((prev) => ({
                              ...prev,
                              [workspace.id]: Number(event.target.value),
                            }))}
                            className="w-36 accent-blue-600"
                            aria-label={`Environment position for ${workspace.name}`}
                          />
                          <span className="w-32 text-sm">
                            <strong>{groupMembers[workspace.id] ?? 1}</strong>
                            <span className="ml-2 text-slate-500">
                              {environmentLabel(groupMembers[workspace.id] ?? 1)}
                            </span>
                          </span>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div className="space-y-3">
          <div>
            <h3 className="font-semibold">Grouped workspaces</h3>
            <p className="text-sm text-slate-500">Projects spanning multiple lifecycle environments.</p>
          </div>
          {groups.length === 0 ? (
            <div className="rounded-md border border-dashed border-slate-300 px-4 py-5 text-sm text-slate-500 dark:border-slate-700">
              No project workspaces created yet.
            </div>
          ) : (
            groups.map((group) => (
              <div key={group.id} className="card p-0">
                <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
                  <div>
                    <h4 className="font-semibold">{group.name}</h4>
                    <p className="text-xs text-slate-500">{Object.keys(group.members).length} workspaces</p>
                  </div>
                  <button
                    type="button"
                    className="text-sm font-medium text-red-600 hover:underline dark:text-red-400"
                    onClick={() => removeGroup(group.id)}
                    title="Remove the group and return its members to isolated workspaces"
                  >
                    Remove group
                  </button>
                </div>
                <div className="scroll-x">
                  <table className="table-base">
                    <thead>
                      <tr>
                        <th scope="col" className="w-10">Audit</th>
                        <th scope="col">Workspace</th>
                        <th scope="col">Environment</th>
                        <th scope="col">Layer role</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(group.members)
                        .sort(([, left], [, right]) => left - right)
                        .map(([workspaceId, level]) => {
                          const workspace = allWorkspaces.find((item) => item.id === workspaceId);
                          if (!workspace) return null;
                          return (
                            <tr key={workspace.id}>
                              <td>
                                <input
                                  type="checkbox"
                                  checked={selected[workspace.id] ?? false}
                                  onChange={(event) => setSelected((prev) => ({
                                    ...prev,
                                    [workspace.id]: event.target.checked,
                                  }))}
                                  aria-label={`Audit ${workspace.name}`}
                                />
                              </td>
                              <td>
                                <div className="font-medium">{workspace.name}</div>
                                <div className="font-mono text-xs text-slate-500">{workspace.id}</div>
                              </td>
                              <td>
                                <span className="badge bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300">
                                  {level} · {environmentLabel(level)}
                                </span>
                              </td>
                              <td>
                                <select
                                  value={roles[workspace.id] ?? "Mixed"}
                                  onChange={(event) => setRoles((prev) => ({
                                    ...prev,
                                    [workspace.id]: event.target.value,
                                  }))}
                                  className="input w-auto py-1 text-sm"
                                  aria-label={`Layer role for ${workspace.name}`}
                                >
                                  {(layers.data ?? []).map((layer) => (
                                    <option key={layer.name} value={layer.name}>{layer.name}</option>
                                  ))}
                                </select>
                              </td>
                            </tr>
                          );
                        })}
                    </tbody>
                  </table>
                </div>
              </div>
            ))
          )}
        </div>

        {/* Add a workspace by name or id — for one that is not listed, or to
            build a specific audit queue by hand. */}
        <div className="card flex flex-wrap items-end gap-2">
          <div className="flex-1">
            <label
              htmlFor="add-workspace"
              className="mb-1 block text-xs font-medium text-slate-500"
            >
              Add a workspace by name or ID
            </label>
            <input
              id="add-workspace"
              className="input"
              placeholder="e.g. Sales-Prod-DataPrep or a workspace GUID"
              value={newWsId}
              onChange={(event) => setNewWsId(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  addWorkspace();
                }
              }}
            />
          </div>
          <select
            value={newWsRole}
            onChange={(event) => setNewWsRole(event.target.value)}
            className="input w-auto py-2 text-sm"
            aria-label="Layer role for the new workspace"
          >
            {(layers.data ?? [{ name: "Mixed", checks: 0 }]).map((layer) => (
              <option key={layer.name} value={layer.name}>
                {layer.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn-secondary"
            onClick={addWorkspace}
            disabled={!newWsId.trim()}
          >
            Add to queue
          </button>
        </div>

        {allWorkspaces.length === 0 && !workspaces.loading && (
          <div className="card text-sm text-slate-600 dark:text-slate-400">
            No workspaces are visible to the signed-in account. Add one above, confirm you
            have at least Viewer access, or check{" "}
            <Link to="/sign-in" className="font-medium underline">
              access diagnostics
            </Link>
            .
          </div>
        )}
        {allWorkspaces.length > 0 && (
          <div className="space-y-3">
            <div>
              <h3 className="font-semibold">Isolated workspaces</h3>
              <p className="text-sm text-slate-500">Standalone workspaces not assigned to a project.</p>
            </div>
          <div className="card scroll-x">
            <p className="mb-2 text-sm text-slate-500">
              {isolatedWorkspaces.filter((workspace) => selected[workspace.id]).length} of{" "}
              {isolatedWorkspaces.length} isolated workspaces selected.
            </p>
            <table className="table-base">
              <thead>
                <tr>
                  <th scope="col" className="w-10">Audit</th>
                  <th scope="col">Workspace</th>
                  <th scope="col">Layer role</th>
                  <th scope="col">Items</th>
                  <th scope="col">Pipelines</th>
                  <th scope="col" className="w-10">
                    <span className="sr-only">Remove</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {isolatedWorkspaces.map((workspace) => (
                  <tr key={workspace.id}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selected[workspace.id] ?? false}
                        onChange={(event) =>
                          setSelected((prev) => ({
                            ...prev,
                            [workspace.id]: event.target.checked,
                          }))
                        }
                        aria-label={`Audit ${workspace.name}`}
                      />
                    </td>
                    <td>
                      <div className="font-medium">{workspace.name}</div>
                      <div className="font-mono text-xs text-slate-500">{workspace.id}</div>
                    </td>
                    <td>
                      <select
                        value={roles[workspace.id] ?? "Mixed"}
                        onChange={(event) =>
                          setRoles((prev) => ({
                            ...prev,
                            [workspace.id]: event.target.value,
                          }))
                        }
                        className="input w-auto py-1 text-sm"
                        aria-label={`Layer role for ${workspace.name}`}
                      >
                        {(layers.data ?? []).map((layer) => (
                          <option key={layer.name} value={layer.name}>
                            {layer.name}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>{workspace.items ?? "—"}</td>
                    <td>{workspace.pipelines ?? "—"}</td>
                    <td>
                      <button
                        type="button"
                        className="text-slate-400 hover:text-red-600 dark:hover:text-red-400"
                        onClick={() => removeWorkspace(workspace.id)}
                        aria-label={`Remove ${workspace.name} from the queue`}
                        title="Remove from the audit queue"
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {isolatedWorkspaces.length === 0 && (
            <div className="rounded-md border border-dashed border-slate-300 px-4 py-5 text-sm text-slate-500 dark:border-slate-700">
              Every workspace is assigned to a project group.
            </div>
          )}
          </div>
        )}
      </Section>

      <Section title="Pillars" description="Deselecting a pillar skips its checks entirely.">
        {pillars.loading && <Spinner />}
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {(pillars.data ?? []).map((pillar) => (
            <label
              key={pillar.name}
              className="card flex cursor-pointer items-start gap-3"
            >
              <input
                type="checkbox"
                className="mt-1"
                checked={chosenPillars[pillar.name] ?? false}
                disabled={pillar.checks === 0}
                onChange={(event) =>
                  setChosenPillars((prev) => ({
                    ...prev,
                    [pillar.name]: event.target.checked,
                  }))
                }
              />
              <span>
                <span className="block font-medium">{pillar.name}</span>
                <span className="text-xs text-slate-500">
                  {pillar.checks > 0
                    ? `${pillar.checks} checks`
                    : "No checks yet — nothing to score"}
                </span>
              </span>
            </label>
          ))}
        </div>
      </Section>

      {groups.length > 0 && (
        <label className="card flex cursor-pointer items-start gap-3">
          <input
            type="checkbox"
            className="mt-1"
            checked={weightByEnv}
            onChange={(event) => setWeightByEnv(event.target.checked)}
          />
          <span>
            <span className="block font-medium">Weight score by environment</span>
            <span className="text-xs text-slate-500">
              Grouped workspaces count toward the overall score by their environment
              level (1–10): production weighs more than development. Off = every
              workspace counts equally. Per-workspace scores are unaffected.
            </span>
          </span>
        </label>
      )}

      <div className="card space-y-2">
        <label htmlFor="external-checks" className="block">
          <span className="text-sm font-medium">External checks (optional)</span>
          <span className="text-xs text-slate-500">
            Path to a CSV file with admin-verified checks (e.g., AdminChecks.csv). These will be merged with automated results and override any checks with the same ID.
          </span>
        </label>
        <input
          id="external-checks"
          type="text"
          className="input"
          placeholder="e.g., /shared/audits/AdminChecks.csv or ./AdminChecks.csv"
          value={externalChecksPath}
          onChange={(event) => setExternalChecksPath(event.target.value)}
        />
        {externalChecksPath && (
          <p className="text-xs text-slate-500">
            Will load: <span className="font-mono">{externalChecksPath}</span>
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className="btn-primary"
          onClick={run}
          disabled={submitting || workspaces.loading}
        >
          {submitting ? "Running…" : "Run audit on selected"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={runAll}
          disabled={submitting || workspaces.loading || allWorkspaces.length === 0}
          title="Audit every workspace in the queue, regardless of the ticks above"
        >
          Audit all workspaces
        </button>
        {job && (
          <span className="text-sm text-slate-500">
            Audit {job.audit_id} — {job.status}
          </span>
        )}
      </div>
    </div>
  );
}
