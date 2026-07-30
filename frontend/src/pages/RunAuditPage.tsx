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

import { ErrorBanner, Section, Spinner } from "@/components/ui";
import { useAuditContext } from "@/context/AuditContext";
import { useAsync } from "@/hooks/useAsync";
import { listLiveWorkspaces, pollAudit, submitAudit } from "@/services/auditService";
import { listLayers, listPillars } from "@/services/catalogService";
import type { AuditJob, Workspace } from "@/types/api";

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
  const [chosenPillars, setChosenPillars] = useState<Record<string, boolean>>({});
  const [job, setJob] = useState<AuditJob | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // The audit queue: live workspaces plus any added by hand, minus any removed.
  const allWorkspaces = useMemo(() => {
    const map = new Map<string, Workspace>();
    for (const w of [...(workspaces.data ?? []), ...manual]) {
      if (!removed[w.id]) map.set(w.id, w);
    }
    return [...map.values()];
  }, [workspaces.data, manual, removed]);

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

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const accepted = await submitAudit({
          pillars: chosen,
          workspaces: specs,
          auth_session: session,
        });
        setLastAuditId(accepted.audit_id);

        const finished = await pollAudit(accepted.audit_id, setJob, controller.signal);
        if (finished.status === "failed" && !finished.report) {
          setError(finished.error ?? "The audit failed.");
          return;
        }
        // Open the report on success, on a still-running timeout, or on a failure
        // that still produced results — it renders whatever completed (a partial
        // report shows a banner and the workspaces evaluated so far).
        setReport(finished.report ?? null);
        navigate(`/report/${accepted.audit_id}`);
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setSubmitting(false);
      }
    },
    [chosenPillars, isSignedIn, session, setLastAuditId, setReport, navigate],
  );

  const specsFor = useCallback(
    (list: Workspace[]) => list.map((w) => ({ id: w.id, role: roles[w.id] ?? w.role ?? "Mixed" })),
    [roles],
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

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <Section
        title="Workspaces"
        description="Every workspace your account can see. Each is audited in the context of the layer role you assign it."
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
          <div className="card scroll-x">
            <p className="mb-2 text-sm text-slate-500">
              {allWorkspaces.filter((w) => selected[w.id]).length} of {allWorkspaces.length}{" "}
              selected for this audit.
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
                {allWorkspaces.map((workspace) => (
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
