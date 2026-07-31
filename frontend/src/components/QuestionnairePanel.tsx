/**
 * The interactive, self-assessed checklist.
 *
 * Shown while the automated audit crawls the tenant. The reviewer answers the
 * points a machine cannot verify from the workspace alone (governance process,
 * DR drills, cost reviews, …). Each answer carries a score; picking "Skip this
 * check" records it as N/A and leaves it out of the score.
 *
 * Nothing here is hard-coded: the questions, options, pillars, and layers all
 * come from the run's `questionnaire`, which the backend derives from the
 * registered interactive checks that apply to the selected workspaces.
 */
import { useMemo } from "react";

import { SKIP_ANSWER, type QuestionnaireItem } from "@/types/api";

const SEVERITY_TINT: Record<string, string> = {
  Critical: "text-red-600 dark:text-red-400",
  High: "text-orange-600 dark:text-orange-400",
  Medium: "text-amber-600 dark:text-amber-400",
  Low: "text-slate-500",
  Informational: "text-slate-500",
};

function layerLabel(layers: string[]): string {
  if (layers.length === 0 || layers.includes("*")) return "All layers";
  return layers.join(" · ");
}

/** Group items by pillar, then by their applies-to layers — the folder shape. */
function groupItems(items: QuestionnaireItem[]) {
  const byPillar = new Map<string, Map<string, QuestionnaireItem[]>>();
  for (const item of items) {
    const layerKey = layerLabel(item.layers);
    const layers = byPillar.get(item.pillar) ?? new Map<string, QuestionnaireItem[]>();
    const bucket = layers.get(layerKey) ?? [];
    bucket.push(item);
    layers.set(layerKey, bucket);
    byPillar.set(item.pillar, layers);
  }
  return [...byPillar.entries()].map(([pillar, layers]) => ({
    pillar,
    layers: [...layers.entries()].map(([layer, group]) => ({ layer, items: group })),
  }));
}

export function QuestionnairePanel({
  items,
  answers,
  onChange,
  disabled = false,
}: {
  items: QuestionnaireItem[];
  answers: Record<string, string>;
  onChange: (id: string, value: string) => void;
  disabled?: boolean;
}) {
  const groups = useMemo(() => groupItems(items), [items]);
  const answeredCount = items.filter((item) => item.id in answers).length;

  if (items.length === 0) return null;

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-500">
        {answeredCount} of {items.length} answered. Unanswered points are treated as
        skipped and left out of the score.
      </p>

      {groups.map(({ pillar, layers }) => (
        <div key={pillar} className="space-y-3">
          <h3 className="text-base font-semibold text-slate-800 dark:text-slate-200">
            {pillar}
          </h3>
          {layers.map(({ layer, items: layerItems }) => (
            <div key={layer} className="space-y-3">
              <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
                {layer}
              </div>
              {layerItems.map((item) => {
                const chosen = answers[item.id];
                return (
                  <fieldset
                    key={item.id}
                    className="card space-y-3"
                    disabled={disabled}
                  >
                    <legend className="sr-only">{item.title}</legend>
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <div className="font-medium">{item.title}</div>
                        <div className="mt-1 text-sm text-slate-600 dark:text-slate-300">
                          {item.question}
                        </div>
                      </div>
                      <span
                        className={`whitespace-nowrap text-xs ${
                          SEVERITY_TINT[item.severity] ?? "text-slate-500"
                        }`}
                      >
                        {item.severity}
                      </span>
                    </div>

                    <div className="space-y-1.5">
                      {item.options.map((option) => (
                        <label
                          key={option.value}
                          className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 hover:bg-slate-50 dark:hover:bg-slate-800/60"
                        >
                          <input
                            type="radio"
                            className="mt-1"
                            name={item.id}
                            value={option.value}
                            checked={chosen === option.value}
                            onChange={() => onChange(item.id, option.value)}
                          />
                          <span>
                            <span className="text-sm font-medium">{option.label}</span>
                            {option.score !== null && (
                              <span className="ml-2 text-xs text-slate-400">
                                {option.score}/3
                              </span>
                            )}
                            {option.guidance && (
                              <span className="block text-xs text-slate-500">
                                {option.guidance}
                              </span>
                            )}
                          </span>
                        </label>
                      ))}

                      <label className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 hover:bg-slate-50 dark:hover:bg-slate-800/60">
                        <input
                          type="radio"
                          className="mt-1"
                          name={item.id}
                          value={SKIP_ANSWER}
                          checked={chosen === SKIP_ANSWER}
                          onChange={() => onChange(item.id, SKIP_ANSWER)}
                        />
                        <span className="text-sm text-slate-500">
                          Skip this check
                          <span className="block text-xs text-slate-400">
                            Recorded as N/A — not counted in the score.
                          </span>
                        </span>
                      </label>
                    </div>
                  </fieldset>
                );
              })}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
