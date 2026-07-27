/**
 * Failing and partial checks, worst first.
 *
 * Evidence and recommendation are shown together because a finding without its
 * evidence is an assertion, and a finding without a recommendation is a
 * complaint — the pair is what makes the report actionable.
 */
import { useMemo, useState } from "react";

import type { CheckResult } from "@/types/api";
import { SEVERITY_RANK } from "@/utils/format";
import { EmptyState, SeverityBadge, StatusBadge } from "./ui";

type SeverityFilter = "all" | "Critical" | "High" | "Medium" | "Low";

export function FindingsTable({ results }: { results: CheckResult[] }) {
  const [severity, setSeverity] = useState<SeverityFilter>("all");
  const [query, setQuery] = useState("");

  const findings = useMemo(() => {
    const failing = results.filter((r) => r.status === "FAIL" || r.status === "PARTIAL");
    const needle = query.trim().toLowerCase();

    return failing
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
  }, [results, severity, query]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filter findings…"
          className="input max-w-xs"
          aria-label="Filter findings"
        />
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
          {findings.length} of {results.filter((r) => r.status !== "PASS").length} findings
        </span>
      </div>

      {findings.length === 0 ? (
        <EmptyState
          title="No findings match"
          hint="Every scored check passed, or your filters exclude the rest."
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
                  <td className="whitespace-nowrap">{finding.obj || "—"}</td>
                  <td><StatusBadge status={finding.status} /></td>
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
