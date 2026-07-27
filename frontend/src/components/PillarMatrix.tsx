/**
 * The pillar x layer heatmap.
 *
 * This is the view the whole audit model is built around: each Well-Architected
 * pillar scored against each architecture layer, so a weakness can be located
 * ("Reliability is failing in Data Prep") rather than just totalled.
 *
 * Empty cells mean *not assessed* — that layer has no checks for that pillar.
 */
import type { AuditReport } from "@/types/api";
import { formatPercent, ratingFor } from "@/utils/format";

export function PillarMatrix({ report }: { report: AuditReport }) {
  const pillars = Object.keys(report.matrix);
  const layers = report.layers;

  if (layers.length === 0) {
    return <p className="text-sm text-slate-500">No scored workspaces in this run.</p>;
  }

  return (
    <div className="card scroll-x">
      <table className="table-base">
        <thead>
          <tr>
            <th scope="col">Pillar</th>
            {layers.map((layer) => (
              <th key={layer} scope="col" className="text-center">
                {layer}
              </th>
            ))}
            <th scope="col" className="text-center">
              Overall
            </th>
          </tr>
        </thead>
        <tbody>
          {pillars.map((pillar) => {
            const overall = report.by_pillar[pillar]?.pct ?? null;
            return (
              <tr key={pillar}>
                <th scope="row" className="whitespace-nowrap font-medium normal-case tracking-normal">
                  {pillar}
                </th>
                {layers.map((layer) => {
                  const value = report.matrix[pillar]?.[layer] ?? null;
                  const rating = ratingFor(value);
                  return (
                    <td key={layer} className="text-center">
                      <span
                        className={`badge ${value === null ? "" : rating.bgClass}`}
                        title={`${pillar} in ${layer}: ${formatPercent(value)}`}
                      >
                        {formatPercent(value, 0)}
                      </span>
                    </td>
                  );
                })}
                <td className="text-center">
                  <span className={`badge ${ratingFor(overall).bgClass}`}>
                    {formatPercent(overall, 0)}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
