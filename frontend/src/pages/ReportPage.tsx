/**
 * A completed audit: overall score, pillar scorecard, the pillar x layer matrix,
 * per-workspace breakdown, and every finding.
 */
import { useEffect } from "react";
import { useParams } from "react-router-dom";

import { AdvisoryPanel } from "@/components/AdvisoryPanel";
import { FindingsTable } from "@/components/FindingsTable";
import { PillarMatrix } from "@/components/PillarMatrix";
import { EmptyState, ErrorBanner, ScoreBar, Section, Spinner } from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { getAudit, reportDownloadUrl } from "@/services/auditService";
import { formatPercent, ratingFor } from "@/utils/format";

/** Human label for an environment position (1 = dev .. 10 = prod). */
function environmentLabel(level: number): string {
  if (level <= 3) return "Development";
  if (level <= 6) return "Test / staging";
  if (level <= 8) return "Pre-production";
  return "Production";
}

export function ReportPage() {
  const { auditId = "" } = useParams();
  const { data: job, loading, error, reload } = useAsync(
    () => getAudit(auditId),
    [auditId],
  );

  const report = job?.report ?? null;
  const running = job?.status === "queued" || job?.status === "running";

  // While the audit is still running, refresh so partial results appear and
  // grow with each completed workspace instead of showing a "no report" error.
  useEffect(() => {
    if (!running) return;
    const timer = setTimeout(reload, 2500);
    return () => clearTimeout(timer);
  }, [running, job, reload]);

  // The title reflects what was actually audited — the workspace name when a
  // single one is in scope, otherwise a neutral heading — instead of the static
  // project.name config label that was reused for every run.
  const auditedWorkspaces = report ? Object.keys(report.by_workspace) : [];
  const heading =
    auditedWorkspaces.length === 1 ? auditedWorkspaces[0] : "Fabric Well-Architected Audit";

  useEffect(() => {
    if (report) document.title = `${heading} — Audit`;
  }, [report, heading]);

  if (loading && !job) return <Spinner label="Loading report…" />;
  if (!report) {
    if (running) {
      return <Spinner label="Audit running — waiting for the first workspace to finish…" />;
    }
    if (job?.status === "failed") {
      return <ErrorBanner message={job.error ?? "The audit failed."} onRetry={reload} />;
    }
    if (error) return <ErrorBanner message={error} onRetry={reload} />;
    return <EmptyState title="No report" />;
  }

  const rating = ratingFor(report.overall);

  // Correlate group members (sent as name/id) to the by-workspace scores, which
  // are keyed by display name. Members that match claim their row; everything
  // left over is shown as isolated. Purely for display — no score is computed here.
  const groups = report.groups ?? [];
  const scoreKeyFor = (member: { id: string; name?: string | null }): string | null => {
    if (member.name && report.by_workspace[member.name]) return member.name;
    if (report.by_workspace[member.id]) return member.id;
    return null;
  };
  const groupedKeys = new Set<string>();
  for (const group of groups)
    for (const member of group.workspaces) {
      const key = scoreKeyFor(member);
      if (key) groupedKeys.add(key);
    }
  const isolatedRows = Object.entries(report.by_workspace).filter(
    ([name]) => !groupedKeys.has(name),
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">{heading}</h1>
        <p className="text-sm text-slate-500">
          {auditedWorkspaces.length > 0
            ? `${auditedWorkspaces.length} workspace${auditedWorkspaces.length === 1 ? "" : "s"} audited`
            : "Fabric Well-Architected audit results"}
          {report.errors.length > 0 && ` · ${report.errors.length} skipped for access`}
        </p>
      </div>

      {report.partial && (
        <section className="rounded-md border border-blue-200 bg-blue-50 p-4 dark:border-blue-900 dark:bg-blue-950">
          <h2 className="flex items-center gap-2 text-base font-semibold text-blue-900 dark:text-blue-300">
            <span aria-hidden="true">⏳</span>
            Partial report — audit still running
          </h2>
          <p className="mt-1 text-xs text-blue-700 dark:text-blue-400">
            These are the workspaces evaluated so far; the run is still in progress
            on the server. Use <strong>Reload results</strong> to fetch the latest.
          </p>
          <button
            type="button"
            onClick={reload}
            className="mt-3 rounded-md border border-blue-300 bg-white px-3 py-1.5 text-xs font-medium text-blue-800 hover:bg-blue-100 dark:border-blue-800 dark:bg-blue-900 dark:text-blue-200"
          >
            Reload results
          </button>
        </section>
      )}

      {report.errors.length > 0 && (
        <section className="rounded-md border border-orange-200 bg-orange-50 p-4 dark:border-orange-900 dark:bg-orange-950">
          <h2 className="flex items-center gap-2 text-base font-semibold text-orange-900 dark:text-orange-300">
            <span aria-hidden="true">⚠</span>
            Audit Read Limitations ({report.errors.length})
          </h2>
          <p className="mt-1 text-xs text-orange-700 dark:text-orange-400">
            These resources or artifacts could not be assessed and are excluded from the scores below.
            Review the recorded cause and remediation, then run a live audit again.
          </p>
          <div className="mt-3 scroll-x">
            <table className="table-base">
              <thead>
                <tr>
                  <th scope="col">Workspace</th>
                  <th scope="col">Layer role</th>
                  <th scope="col">Why it was skipped</th>
                  <th scope="col">What to do</th>
                </tr>
              </thead>
              <tbody>
                {report.errors.map((item, index) => (
                  <tr key={`${item.workspace}-${index}`}>
                    <td className="font-medium">{item.workspace}</td>
                    <td>
                      <span className="badge bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200">
                        {item.role || "—"}
                      </span>
                    </td>
                    <td className="min-w-[16rem]">{item.message}</td>
                    <td className="min-w-[16rem] text-orange-800 dark:text-orange-300">
                      {item.recommendation || "Review the recorded cause, then run a live audit again."}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="card">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-sm text-slate-500">Overall score</p>
            <p className={`text-4xl font-bold ${rating.textClass}`}>
              {formatPercent(report.overall)}
            </p>
            <p className="text-sm text-slate-500">
              {report.counts.PASS ?? 0} pass · {report.counts.PARTIAL ?? 0} partial ·{" "}
              {report.counts.FAIL ?? 0} fail · {report.total_scored} scored
            </p>
            {report.weighted_by_environment && (
              <p className="mt-1 inline-flex items-center gap-1 rounded bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800 dark:bg-blue-950 dark:text-blue-300">
                Environment-weighted — production workspaces count more toward this score
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className={`badge ${rating.bgClass}`}>{rating.label}</span>
            <a
              className="btn-primary"
              href={reportDownloadUrl(auditId, "html")}
              target="_blank"
              rel="noreferrer"
              title="Interactive readout — opens in a new tab, works offline"
            >
              HTML Readout
            </a>
            <a
              className="btn-secondary"
              href={reportDownloadUrl(auditId, "markdown")}
              target="_blank"
              rel="noreferrer"
            >
              Markdown
            </a>
            <a className="btn-secondary" href={reportDownloadUrl(auditId, "excel")}>
              Excel
            </a>
            {(report.advisory?.results.length ?? 0) > 0 && (
              <a
                className="btn-secondary"
                href={reportDownloadUrl(auditId, "advisory-excel")}
                title="Non-deterministic checks — reviewed separately from the score"
              >
                Advisory (Excel)
              </a>
            )}
          </div>
        </div>
      </section>

      {job && (
        <AdvisoryPanel
          auditId={auditId}
          auditStatus={job.status}
          initialStatus={job.advisory_status}
        />
      )}

      <Section title="Pillar scorecard">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(report.by_pillar).map(([pillar, score]) => (
            <div key={pillar} className="card">
              <p className="mb-2 text-sm font-medium">{pillar}</p>
              <ScoreBar pct={score.pct} />
              <p className="mt-1 text-xs text-slate-500">
                {score.count > 0 ? `${score.count} checks` : "Not assessed — no checks yet"}
              </p>
            </div>
          ))}
        </div>
      </Section>

      <Section
        title="Pillar by layer"
        description="Where each architecture layer is strong or weak, per pillar."
      >
        <PillarMatrix report={report} />
      </Section>

      <Section title="Per-workspace breakdown">
        {groups.length === 0 ? (
          <div className="card scroll-x">
            <table className="table-base">
              <thead>
                <tr>
                  <th scope="col">Workspace</th>
                  <th scope="col">Layer role</th>
                  <th scope="col">Checks</th>
                  <th scope="col" className="min-w-[12rem]">Score</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(report.by_workspace).map(([name, score]) => (
                  <tr key={name}>
                    <td className="font-medium">{name}</td>
                    <td><span className="badge bg-slate-100 dark:bg-slate-800">{score.layer}</span></td>
                    <td>{score.count}</td>
                    <td><ScoreBar pct={score.pct} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="space-y-4">
            {groups.map((group) => (
              <div key={group.name} className="card p-0">
                <div className="border-b border-slate-200 px-4 py-3 dark:border-slate-800">
                  <h3 className="font-semibold">{group.name}</h3>
                  <p className="text-xs text-slate-500">
                    Project group · {group.workspaces.length} workspaces
                  </p>
                </div>
                <div className="scroll-x">
                  <table className="table-base">
                    <thead>
                      <tr>
                        <th scope="col">Workspace</th>
                        <th scope="col">Environment</th>
                        <th scope="col">Layer role</th>
                        <th scope="col">Checks</th>
                        <th scope="col" className="min-w-[12rem]">Score</th>
                      </tr>
                    </thead>
                    <tbody>
                      {group.workspaces.map((member) => {
                        const key = scoreKeyFor(member);
                        const score = key ? report.by_workspace[key] : undefined;
                        return (
                          <tr key={member.id}>
                            <td className="font-medium">{member.name ?? member.id}</td>
                            <td>
                              {member.environment_level != null ? (
                                <span className="badge bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300">
                                  {member.environment_level} · {environmentLabel(member.environment_level)}
                                </span>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td>
                              {score ? (
                                <span className="badge bg-slate-100 dark:bg-slate-800">{score.layer}</span>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td>{score ? score.count : "—"}</td>
                            <td>
                              {score ? (
                                <ScoreBar pct={score.pct} />
                              ) : (
                                <span className="text-xs text-slate-400">Not in this run</span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}

            {isolatedRows.length > 0 && (
              <div className="card p-0">
                <div className="border-b border-slate-200 px-4 py-3 dark:border-slate-800">
                  <h3 className="font-semibold">Isolated workspaces</h3>
                  <p className="text-xs text-slate-500">Not assigned to a project group.</p>
                </div>
                <div className="scroll-x">
                  <table className="table-base">
                    <thead>
                      <tr>
                        <th scope="col">Workspace</th>
                        <th scope="col">Layer role</th>
                        <th scope="col">Checks</th>
                        <th scope="col" className="min-w-[12rem]">Score</th>
                      </tr>
                    </thead>
                    <tbody>
                      {isolatedRows.map(([name, score]) => (
                        <tr key={name}>
                          <td className="font-medium">{name}</td>
                          <td><span className="badge bg-slate-100 dark:bg-slate-800">{score.layer}</span></td>
                          <td>{score.count}</td>
                          <td><ScoreBar pct={score.pct} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </Section>

      <Section
        title="Check results"
        description="Every check that ran, including passes. Filter by status, severity, or text."
      >
        <FindingsTable results={report.results} />
      </Section>
    </div>
  );
}
