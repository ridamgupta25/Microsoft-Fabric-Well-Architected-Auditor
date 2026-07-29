/**
 * Browse the rule library.
 *
 * Answered entirely from registered metadata, so this page needs no tenant, no
 * sign-in, and no audit run.
 */
import { useState } from "react";

import { EmptyState, ErrorBanner, SeverityBadge, Section, Spinner } from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { listChecks, listLayers, listPillars } from "@/services/catalogService";

export function CatalogPage() {
  const [pillar, setPillar] = useState("");
  const [layer, setLayer] = useState("");
  const [query, setQuery] = useState("");

  const pillars = useAsync(() => listPillars(), []);
  const layers = useAsync(() => listLayers(), []);
  const checks = useAsync(
    () => listChecks({ pillar: pillar || undefined, layer: layer || undefined }),
    [pillar, layer],
  );

  const needle = query.trim().toLowerCase();
  const visible = (checks.data ?? []).filter((check) =>
    needle === ""
      ? true
      : [check.id, check.title, check.ref, check.description]
          .join(" ")
          .toLowerCase()
          .includes(needle),
  );

  return (
    <Section
      title="Check catalog"
      description="Every rule the auditor can apply, with the checklist reference it traces to."
    >
      <div className="flex flex-wrap gap-2">
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search checks…"
          className="input max-w-xs"
          aria-label="Search checks"
        />
        <select
          value={pillar}
          onChange={(event) => setPillar(event.target.value)}
          className="input w-auto"
          aria-label="Filter by pillar"
        >
          <option value="">All pillars</option>
          {(pillars.data ?? []).map((item) => (
            <option key={item.name} value={item.name}>
              {item.name} ({item.checks})
            </option>
          ))}
        </select>
        <select
          value={layer}
          onChange={(event) => setLayer(event.target.value)}
          className="input w-auto"
          aria-label="Filter by layer"
        >
          <option value="">All layers</option>
          {(layers.data ?? []).map((item) => (
            <option key={item.name} value={item.name}>
              {item.name} ({item.checks})
            </option>
          ))}
        </select>
      </div>

      {checks.loading && <Spinner />}
      {checks.error && <ErrorBanner message={checks.error} onRetry={checks.reload} />}
      {!checks.loading && visible.length === 0 && (
        <EmptyState title="No checks match" hint="Try clearing a filter." />
      )}

      {visible.length > 0 && (
        <div className="card scroll-x">
          <table className="table-base">
            <thead>
              <tr>
                <th scope="col">ID</th>
                <th scope="col">Ref</th>
                <th scope="col">Title</th>
                <th scope="col">Pillar</th>
                <th scope="col">Scope</th>
                <th scope="col">Severity</th>
                <th scope="col">Required</th>
                <th scope="col">Type</th>
                <th scope="col">Layers</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((check) => (
                <tr key={check.id}>
                  <td className="whitespace-nowrap font-mono text-xs">{check.id}</td>
                  <td className="whitespace-nowrap font-mono text-xs">{check.ref}</td>
                  <td className="min-w-[16rem]">
                    <div className="font-medium">{check.title}</div>
                    {check.description && (
                      <div className="text-xs text-slate-500">{check.description}</div>
                    )}
                  </td>
                  <td className="whitespace-nowrap">{check.pillar}</td>
                  <td className="whitespace-nowrap">{check.scope}</td>
                  <td><SeverityBadge severity={check.severity} /></td>
                  <td className="whitespace-nowrap">
                    {check.required ? "Required" : "Optional"}
                  </td>
                  <td className="whitespace-nowrap text-xs text-slate-500">
                    {check.automation === "automated"
                      ? "Automated"
                      : check.automation === "roadmap"
                        ? "Automatable (planned)"
                        : "Manual"}
                  </td>
                  <td className="text-xs text-slate-500">
                    {check.layers.includes("*") ? "All" : check.layers.join(", ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-sm text-slate-500">{visible.length} check(s).</p>
    </Section>
  );
}
