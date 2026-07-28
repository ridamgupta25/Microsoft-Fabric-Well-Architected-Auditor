/**
 * Failing and partial checks, worst first.
 *
 * Evidence and recommendation are shown together because a finding without its
 * evidence is an assertion, and a finding without a recommendation is a
 * complaint — the pair is what makes the report actionable.
 */
import { useMemo, useState } from "react";

import type { CheckResult, CheckStatus } from "@/types/api";
import { SEVERITY_RANK } from "@/utils/format";
import { EmptyState, SeverityBadge, StatusBadge } from "./ui";

type SeverityFilter = "all" | "Critical" | "High" | "Medium" | "Low";
type StatusFilter = "all" | "actionable" | CheckStatus;

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All statuses" },
  { value: "actionable", label: "Failed & partial" },
  { value: "PASS", label: "Passed" },
  { value: "PARTIAL", label: "Partial" },
  { value: "FAIL", label: "Failed" },
  { value: "INFO", label: "Informational" },
  { value: "N/A", label: "Not applicable" },
];

function matchesStatus(status: CheckStatus, filter: StatusFilter): boolean {
  if (filter === "all") return true;
  if (filter === "actionable") return status === "FAIL" || status === "PARTIAL";
  return status === filter;
}

/** One check's 0-3 score, coloured by band. "—" when the check is not scored. */
function CheckScore({ score }: { score: number | null }) {
  if (score === null || score === undefined) {
    return <span className="text-xs text-slate-400">—</span>;
  }
  const pct = Math.round((score / 3) * 100);
  const color =
    score >= 3
      ? "text-green-600 dark:text-green-400"
      : score >= 1
        ? "text-yellow-600 dark:text-yellow-400"
        : "text-red-600 dark:text-red-400";
  return (
    <span className={`font-mono text-xs font-semibold ${color}`} title={`${pct}% of the maximum`}>
      {score}/3
    </span>
  );
}

export function FindingsTable({ results }: { results: CheckResult[] }) {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [severity, setSeverity] = useState<SeverityFilter>("all");
  const [query, setQuery] = useState("");

  const findings = useMemo(() => {
    const needle = query.trim().toLowerCase();

    return results
      .filter((r) => matchesStatus(r.status, statusFilter))
      .filter((r) => severity === "all" || r.severity === severity)
      .filter((r) =>
        needle === ""
          ? true
          : [r.title, r.workspace, r.obj, r.check_id, r.evidence]
              .join(" ")
              .toLowerCase()
              .includes(needle),
      )
      .sort(
        (a, b) =>
          SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] ||
          (a.score ?? 9) - (b.score ?? 9),
      );
  }, [results, statusFilter, severity, query]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filter checks…"
          className="input max-w-xs"
          aria-label="Filter checks"
        />
        <select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
          className="input max-w-[11rem]"
          aria-label="Filter by status"
        >
          {STATUS_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <select
          value={severity}
          onChange={(event) => setSeverity(event.target.value as SeverityFilter)}
          className="input max-w-[10rem]"
          aria-label="Filter by severity"
        >
          <option value="all">All severities</option>
          <option value="Critical">Critical</option>
          <option value="High">High</option>
          <option value="Medium">Medium</option>
          <option value="Low">Low</option>
        </select>
        <span className="text-sm text-slate-500">
          Showing {findings.length} of {results.length} checks
        </span>
      </div>

      {findings.length === 0 ? (
        <EmptyState
          title="No checks match"
          hint="Adjust the status, severity, or search filters to see more."
        />
      ) : (
        <div className="card scroll-x">
          <table className="table-base">
            <thead>
              <tr>
                <th scope="col">Severity</th>
                <th scope="col">Ref</th>
                <th scope="col">Check</th>
                <th scope="col">Pillar</th>
                <th scope="col">Workspace</th>
                <th scope="col">Object</th>
                <th scope="col">Status</th>
                <th scope="col">Score</th>
                <th scope="col">Evidence</th>
                <th scope="col">Recommendation</th>
              </tr>
            </thead>
            <tbody>
              {findings.map((finding, index) => (
                <tr key={`${finding.check_id}-${finding.workspace}-${finding.obj}-${index}`}>
                  <td><SeverityBadge severity={finding.severity} /></td>
                  <td className="whitespace-nowrap font-mono text-xs">{finding.ref}</td>
                  <td className="min-w-[14rem]">{finding.title}</td>
                  <td className="whitespace-nowrap">{finding.pillar}</td>
                  <td className="whitespace-nowrap">{finding.workspace}</td>
                  <td className="whitespace-nowrap">{finding.obj || finding.workspace}</td>
                  <td><StatusBadge status={finding.status} /></td>
                  <td className="whitespace-nowrap"><CheckScore score={finding.score} /></td>
                  <td className="min-w-[16rem]">{finding.evidence}</td>
                  <td className="min-w-[18rem] text-slate-600 dark:text-slate-400">
                    {finding.recommendation || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
