/**
 * Assess a checklist point.
 *
 * Ask whether the auditor already covers a best-practice point; if not, see a
 * draft check proposal and the steps to promote it. The assessment is token-free
 * — it reads the catalog (and an optional model), never the tenant — so it always
 * returns a result and never fails on a Fabric read.
 */
import { useState } from "react";

import { ErrorBanner, Section, SeverityBadge, Spinner } from "@/components/ui";
import { assessChecklistPoint } from "@/services/checklistService";
import type { ChecklistAssessment } from "@/types/api";

const EXAMPLES = [
  "Delta tables are OPTIMIZE-compacted after large writes",
  "Row-level security is enforced on the semantic model",
  "Pipelines retry failed activities with backoff",
];

export function ChecklistPage() {
  const [point, setPoint] = useState("");
  const [result, setResult] = useState<ChecklistAssessment | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const assess = async (value: string) => {
    const text = value.trim();
    if (!text) return;
    setLoading(true);
    setError(null);
    try {
      setResult(await assessChecklistPoint(text));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Section
      title="Assess a checklist point"
      description="Check whether the auditor already covers a best practice — and if not, get a draft check to promote."
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void assess(point);
        }}
        className="space-y-2"
      >
        <textarea
          value={point}
          onChange={(event) => setPoint(event.target.value)}
          placeholder="e.g. Notebooks cache reused dataframes before wide joins"
          className="input min-h-[5rem] w-full"
          aria-label="Checklist point"
          maxLength={500}
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="submit"
            className="btn-primary px-4 py-1.5 text-sm"
            disabled={loading || point.trim() === ""}
          >
            Assess
          </button>
          <span className="text-xs text-slate-400">Try:</span>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => {
                setPoint(example);
                void assess(example);
              }}
              className="text-xs text-slate-500 underline hover:text-slate-700 dark:hover:text-slate-300"
            >
              {example}
            </button>
          ))}
        </div>
      </form>

      {loading && <Spinner label="Assessing…" />}
      {error && <ErrorBanner message={error} onRetry={() => void assess(point)} />}
      {result && !loading && <AssessmentResult result={result} />}
    </Section>
  );
}

function AssessmentResult({ result }: { result: ChecklistAssessment }) {
  const covered = result.covered;
  const bannerClass = covered
    ? "border-green-200 bg-green-50 text-green-800 dark:border-green-900 dark:bg-green-950 dark:text-green-300"
    : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300";

  return (
    <div className="space-y-5">
      <div className={`flex flex-wrap items-center gap-2 rounded-md border p-3 text-sm ${bannerClass}`}>
        <span className="font-medium">
          {covered ? "Already covered by an existing check" : "Not yet a deterministic check"}
        </span>
        {!result.ai_enabled && (
          <span className="text-xs opacity-70">AI advisory off — deterministic guidance shown</span>
        )}
      </div>

      {result.matches.length > 0 && (
        <div className="space-y-1">
          <h3 className="text-sm font-semibold">
            {covered ? "Matched checks" : "Closest existing checks"}
          </h3>
          <div className="card scroll-x">
            <table className="table-base">
              <thead>
                <tr>
                  <th scope="col">ID</th>
                  <th scope="col">Ref</th>
                  <th scope="col">Title</th>
                  <th scope="col">Pillar</th>
                  <th scope="col">Severity</th>
                  <th scope="col">Match</th>
                </tr>
              </thead>
              <tbody>
                {result.matches.map((match) => (
                  <tr key={match.check_id}>
                    <td className="whitespace-nowrap font-mono text-xs">{match.check_id}</td>
                    <td className="whitespace-nowrap font-mono text-xs">{match.ref}</td>
                    <td className="min-w-[14rem]">{match.title}</td>
                    <td className="whitespace-nowrap">{match.pillar}</td>
                    <td><SeverityBadge severity={match.severity} /></td>
                    <td className="whitespace-nowrap">{Math.round(match.confidence * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {result.advisory && (
        <div className="space-y-1">
          <h3 className="text-sm font-semibold">Assessment</h3>
          <p className="whitespace-pre-wrap text-sm text-slate-600 dark:text-slate-300">
            {result.advisory}
          </p>
        </div>
      )}

      {result.proposal && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold">Draft check proposal</h3>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="badge bg-slate-100 font-mono dark:bg-slate-800">
              {result.proposal.suggested_id}
            </span>
            <span className="badge bg-slate-100 dark:bg-slate-800">{result.proposal.pillar}</span>
            <span className="badge bg-slate-100 dark:bg-slate-800">scope: {result.proposal.scope}</span>
            <SeverityBadge severity={result.proposal.severity} />
          </div>
          <p className="text-sm text-slate-500">{result.proposal.rationale}</p>
          <pre className="scroll-x rounded-md bg-slate-900 p-3 text-xs leading-relaxed text-slate-100">
            {result.proposal.code_skeleton}
          </pre>
        </div>
      )}

      {result.next_steps.length > 0 && (
        <div className="space-y-1">
          <h3 className="text-sm font-semibold">Next steps</h3>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-slate-600 dark:text-slate-300">
            {result.next_steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
