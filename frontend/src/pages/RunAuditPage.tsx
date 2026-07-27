/**
 * Submit an audit: pick workspaces and pillars, then watch it run.
 *
 * Submission is fire-and-poll, so the page shows live status while the backend
 * works rather than freezing on a long request.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ErrorBanner, Section, Spinner } from "@/components/ui";
import { useAuditContext } from "@/context/AuditContext";
import { useAsync } from "@/hooks/useAsync";
import { listWorkspaces, pollAudit, submitAudit } from "@/services/auditService";
import { listLayers, listPillars } from "@/services/catalogService";
import type { AuditJob } from "@/types/api";

export function RunAuditPage() {
  const navigate = useNavigate();
  const { mode, session, isSignedIn, setLastAuditId, setReport } = useAuditContext();

  const workspaces = useAsync(() => listWorkspaces(mode), [mode]);
  const pillars = useAsync(() => listPillars(), []);
  const layers = useAsync(() => listLayers(), []);

  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [chosenPillars, setChosenPillars] = useState<Record<string, boolean>>({});
  const [job, setJob] = useState<AuditJob | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Select every workspace by default — auditing the whole project is the
  // common case, and deselecting is easier than hunting for the right ones.
  useEffect(() => {
    if (!workspaces.data) return;
    setSelected(Object.fromEntries(workspaces.data.map((w) => [w.id, true])));
    setRoles(Object.fromEntries(workspaces.data.map((w) => [w.id, w.role || "Mixed"])));
  }, [workspaces.data]);

  // Performance Efficiency has no checks yet, so leave it off by default rather
  // than showing a permanently empty pillar in every report.
  useEffect(() => {
    if (!pillars.data) return;
    setChosenPillars(
      Object.fromEntries(pillars.data.map((p) => [p.name, p.checks > 0])),
    );
  }, [pillars.data]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const run = useCallback(async () => {
    const chosenWorkspaces = Object.entries(selected)
      .filter(([, isOn]) => isOn)
      .map(([id]) => ({ id, role: roles[id] ?? "Mixed" }));
    const chosen = Object.entries(chosenPillars)
      .filter(([, isOn]) => isOn)
      .map(([name]) => name);

    if (chosenWorkspaces.length === 0) {
      setError("Select at least one workspace.");
      return;
    }
    if (chosen.length === 0) {
      setError("Select at least one pillar.");
      return;
    }
    if (mode === "live" && !isSignedIn) {
      setError("Sign in before running a live audit.");
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
        mode,
        pillars: chosen,
        workspaces: chosenWorkspaces,
        auth_session: session,
      });
      setLastAuditId(accepted.audit_id);

      const finished = await pollAudit(accepted.audit_id, setJob, controller.signal);
      if (finished.status === "failed") {
        setError(finished.error ?? "The audit failed.");
        return;
      }
      setReport(finished.report ?? null);
      navigate(`/report/${accepted.audit_id}`);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }, [
    selected, roles, chosenPillars, mode, isSignedIn, session,
    setLastAuditId, setReport, navigate,
  ]);

  const selectAll = (value: boolean) => {
    if (!workspaces.data) return;
    setSelected(Object.fromEntries(workspaces.data.map((w) => [w.id, value])));
  };

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}
      {mode === "live" && !isSignedIn && (
        <ErrorBanner message="Live mode needs a read-only Microsoft sign-in. Switch to Mock to explore offline." />
      )}

      <Section
        title="Workspaces"
        description="Each workspace is audited in the context of the layer it represents."
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
        {workspaces.loading && <Spinner label="Loading workspaces…" />}
        {workspaces.error && (
          <ErrorBanner message={workspaces.error} onRetry={workspaces.reload} />
        )}
        {workspaces.data && (
          <div className="card scroll-x">
            <table className="table-base">
              <thead>
                <tr>
                  <th scope="col" className="w-10">Audit</th>
                  <th scope="col">Workspace</th>
                  <th scope="col">Layer role</th>
                  <th scope="col">Items</th>
                  <th scope="col">Pipelines</th>
                </tr>
              </thead>
              <tbody>
                {workspaces.data.map((workspace) => (
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

      <div className="flex items-center gap-3">
        <button type="button" className="btn-primary" onClick={run} disabled={submitting}>
          {submitting ? "Running…" : "Run audit"}
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
