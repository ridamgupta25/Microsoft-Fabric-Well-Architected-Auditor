/**
 * A completed audit: overall score, pillar scorecard, the pillar x layer matrix,
 * per-workspace breakdown, and every finding.
 */
import { useParams } from "react-router-dom";

import { FindingsTable } from "@/components/FindingsTable";
import { PillarMatrix } from "@/components/PillarMatrix";
import { EmptyState, ErrorBanner, ScoreBar, Section, Spinner } from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { getReport, reportDownloadUrl } from "@/services/auditService";
import { formatPercent, ratingFor } from "@/utils/format";

export function ReportPage() {
  const { auditId = "" } = useParams();
  const { data: report, loading, error, reload } = useAsync(
    () => getReport(auditId),
    [auditId],
  );

  if (loading) return <Spinner label="Loading report…" />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;
  if (!report) return <EmptyState title="No report" />;

  const rating = ratingFor(report.overall);

  return (
    <div className="space-y-6">
      {report.errors.length > 0 && (
        <div className="rounded-md border border-orange-200 bg-orange-50 p-4 text-sm dark:border-orange-900 dark:bg-orange-950">
          <p className="font-semibold text-orange-900 dark:text-orange-300">
            {report.errors.length} workspace(s) could not be read
          </p>
          <ul className="mt-2 space-y-1 text-orange-800 dark:text-orange-400">
            {report.errors.map((item) => (
              <li key={item.workspace}>
                <span className="font-medium">{item.workspace}</span> — {item.message}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-orange-700 dark:text-orange-400">
            These were skipped and are excluded from the scores below.
          </p>
        </div>
      )}

      <section className="card">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-sm text-slate-500">{report.project_name}</p>
            <p className={`text-4xl font-bold ${rating.textClass}`}>
              {formatPercent(report.overall)}
            </p>
            <p className="text-sm text-slate-500">
              {report.counts.PASS ?? 0} pass · {report.counts.PARTIAL ?? 0} partial ·{" "}
              {report.counts.FAIL ?? 0} fail · {report.total_scored} scored
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className={`badge ${rating.bgClass}`}>{rating.label}</span>
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
          </div>
        </div>
      </section>

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
      </Section>

      <Section title="Findings">
        <FindingsTable results={report.results} />
      </Section>
    </div>
  );
}
