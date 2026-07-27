/**
 * Past audit runs, newest first.
 *
 * Summaries only — report bodies are large, so the full scorecard is fetched
 * only when a row is opened.
 */
import { useState } from "react";
import { Link } from "react-router-dom";

import { EmptyState, ErrorBanner, Section, Spinner } from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { getHistory } from "@/services/auditService";
import { formatDateTime, formatDuration, formatPercent, ratingFor } from "@/utils/format";

const PAGE_SIZE = 25;

export function HistoryPage() {
  const [offset, setOffset] = useState(0);
  const { data, loading, error, reload } = useAsync(
    () => getHistory(PAGE_SIZE, offset),
    [offset],
  );

  const hasMore = data ? offset + PAGE_SIZE < data.total : false;

  return (
    <Section title="Audit history" description="Every run recorded by this instance.">
      {loading && <Spinner />}
      {error && <ErrorBanner message={error} onRetry={reload} />}
      {data && data.items.length === 0 && (
        <EmptyState title="No audits yet" hint="Run one from the Run audit page." />
      )}

      {data && data.items.length > 0 && (
        <>
          <div className="card scroll-x">
            <table className="table-base">
              <thead>
                <tr>
                  <th scope="col">Audit</th>
                  <th scope="col">Project</th>
                  <th scope="col">Submitted</th>
                  <th scope="col">Duration</th>
                  <th scope="col">Workspaces</th>
                  <th scope="col">Status</th>
                  <th scope="col">Score</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.audit_id}>
                    <td>
                      <Link
                        to={`/report/${item.audit_id}`}
                        className="font-mono text-xs text-blue-600 hover:underline dark:text-blue-400"
                      >
                        {item.audit_id}
                      </Link>
                    </td>
                    <td>{item.project_name ?? "—"}</td>
                    <td className="whitespace-nowrap">{formatDateTime(item.submitted_at)}</td>
                    <td>{formatDuration(item.duration_seconds)}</td>
                    <td>{item.workspaces}</td>
                    <td>{item.status}</td>
                    <td className={ratingFor(item.overall).textClass}>
                      {formatPercent(item.overall)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              className="btn-secondary"
              disabled={offset === 0}
              onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
            >
              Previous
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={!hasMore}
              onClick={() => setOffset((value) => value + PAGE_SIZE)}
            >
              Next
            </button>
            <span className="text-sm text-slate-500">
              {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {data.total}
            </span>
          </div>
        </>
      )}
    </Section>
  );
}
