/**
 * Landing page: what the tool covers, and the most recent runs.
 */
import { Link } from "react-router-dom";

import { EmptyState, ErrorBanner, Section, Spinner } from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { getHistory } from "@/services/auditService";
import { getCatalogSummary } from "@/services/catalogService";
import { formatDateTime, formatPercent, ratingFor } from "@/utils/format";

export function DashboardPage() {
  const summary = useAsync(() => getCatalogSummary(), []);
  const history = useAsync(() => getHistory(5), []);

  return (
    <div className="space-y-6">
      <section className="card">
        <h1 className="text-xl font-semibold">Fabric Well-Architected Auditor</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600 dark:text-slate-400">
          Deterministic, read-only auditing of Microsoft Fabric workspaces. Every check is a
          fixed rule with a fixed threshold and pre-written remediation, so the same input
          always produces the same score.
        </p>
        <div className="mt-4 flex gap-2">
          <Link to="/run" className="btn-primary">Run an audit</Link>
          <Link to="/catalog" className="btn-secondary">Browse checks</Link>
        </div>
      </section>

      <Section title="Coverage" description="What the rule library can currently assess.">
        {summary.loading && <Spinner />}
        {summary.error && <ErrorBanner message={summary.error} onRetry={summary.reload} />}
        {summary.data && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <div className="card">
              <p className="text-sm text-slate-500">Checks registered</p>
              <p className="text-3xl font-bold">{summary.data.total}</p>
            </div>
            {Object.entries(summary.data.by_pillar).map(([pillar, count]) => (
              <div key={pillar} className="card">
                <p className="text-sm text-slate-500">{pillar}</p>
                <p className="text-2xl font-semibold">{count}</p>
                {count === 0 && (
                  <p className="text-xs text-slate-500">Not yet automated</p>
                )}
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section
        title="Recent audits"
        actions={<Link to="/history" className="btn-secondary">View all</Link>}
      >
        {history.loading && <Spinner />}
        {history.error && <ErrorBanner message={history.error} onRetry={history.reload} />}
        {history.data && history.data.items.length === 0 && (
          <EmptyState title="No audits yet" hint="Run one to see it here." />
        )}
        {history.data && history.data.items.length > 0 && (
          <div className="card scroll-x">
            <table className="table-base">
              <thead>
                <tr>
                  <th scope="col">Audit</th>
                  <th scope="col">Submitted</th>
                  <th scope="col">Status</th>
                  <th scope="col">Score</th>
                </tr>
              </thead>
              <tbody>
                {history.data.items.map((item) => (
                  <tr key={item.audit_id}>
                    <td>
                      <Link
                        to={`/report/${item.audit_id}`}
                        className="font-mono text-xs text-blue-600 hover:underline dark:text-blue-400"
                      >
                        {item.audit_id}
                      </Link>
                    </td>
                    <td className="whitespace-nowrap">{formatDateTime(item.submitted_at)}</td>
                    <td>{item.status}</td>
                    <td className={ratingFor(item.overall).textClass}>
                      {formatPercent(item.overall)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}
