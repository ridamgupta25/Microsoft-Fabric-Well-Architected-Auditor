/**
 * Assess a checklist point, or a whole custom checklist.
 *
 * The single-point tool asks whether the auditor already covers a best practice;
 * if not, it shows a draft check proposal. The batch tool takes a user-supplied
 * checklist file (CSV / JSON / Markdown), dedups every point, and runs the
 * covered checks over the offline knowledge base — the "the client handed us
 * their own checklist" path. Both are token-free by default and never change a
 * score.
 */
import { useRef, useState } from "react";

import { ErrorBanner, Section, SeverityBadge, Spinner, StatusBadge } from "@/components/ui";
import { useAuditContext } from "@/context/AuditContext";
import { assessChecklistPoint, runChecklistBatch } from "@/services/checklistService";
import type {
  ChecklistAssessment,
  ChecklistBatchItem,
  ChecklistBatchResult,
  CheckStatus,
} from "@/types/api";

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
    <div className="space-y-8">
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

      <BatchChecklistSection />
    </div>
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

// -- batch: upload a whole checklist and run it over the knowledge base -------

const ACCEPT = ".csv,.json,.md,.markdown,.txt";

function BatchChecklistSection() {
  const { session } = useAuditContext();
  const [content, setContent] = useState("");
  const [filename, setFilename] = useState<string | null>(null);
  const [runChecks, setRunChecks] = useState(true);
  const [result, setResult] = useState<ChecklistBatchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const readFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      setContent(String(reader.result ?? ""));
      setFilename(file.name);
    };
    reader.readAsText(file);
  };

  const run = async () => {
    const text = content.trim();
    if (!text) return;
    setLoading(true);
    setError(null);
    try {
      setResult(
        await runChecklistBatch({
          content: text,
          filename: filename ?? "checklist.txt",
          run_checks: runChecks,
          auth_session: session ?? undefined,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Section
      title="Run a custom checklist"
      description="Upload your own checklist (CSV, JSON, or Markdown). Every point is deduped against the catalog, and the covered checks are run over the offline knowledge base."
    >
      <div className="space-y-3">
        <div
          role="button"
          tabIndex={0}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const file = e.dataTransfer.files?.[0];
            if (file) readFile(file);
          }}
          className={`flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed p-6 text-center text-sm transition ${
            dragging
              ? "border-sky-400 bg-sky-50 dark:bg-sky-950"
              : "border-slate-300 hover:border-slate-400 dark:border-slate-700"
          }`}
        >
          <span className="font-medium">
            {filename ? `Loaded: ${filename}` : "Drop a checklist file here, or click to browse"}
          </span>
          <span className="mt-1 text-xs text-slate-400">CSV · JSON · Markdown / plain text</span>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) readFile(file);
            }}
          />
        </div>

        <textarea
          value={content}
          onChange={(event) => {
            setContent(event.target.value);
            if (!filename) setFilename("checklist.txt");
          }}
          placeholder={"…or paste points here, one per line:\nDelta tables are OPTIMIZE-compacted\nRow-level security is enforced"}
          className="input min-h-[6rem] w-full font-mono text-xs"
          aria-label="Checklist content"
        />

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn-primary px-4 py-1.5 text-sm"
            disabled={loading || content.trim() === ""}
            onClick={() => void run()}
          >
            Run over knowledge base
          </button>
          <label className="flex items-center gap-1.5 text-xs text-slate-500">
            <input
              type="checkbox"
              checked={runChecks}
              onChange={(e) => setRunChecks(e.target.checked)}
            />
            Evaluate covered checks (uncheck to only dedup)
          </label>
          {!session && (
            <span className="text-xs text-slate-400">
              Offline (no sign-in) — snapshots only; sign in to read uncached workspaces live.
            </span>
          )}
        </div>

        {loading && <Spinner label="Assessing checklist…" />}
        {error && <ErrorBanner message={error} onRetry={() => void run()} />}
        {result && !loading && <BatchResult result={result} />}
      </div>
    </Section>
  );
}

function BatchResult({ result }: { result: ChecklistBatchResult }) {
  const { summary } = result;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Points" value={summary.total_points} />
        <Stat label="Covered" value={summary.covered} />
        <Stat label="Not covered" value={summary.not_covered} />
        <Stat label="Evaluated" value={summary.evaluated_points} />
      </div>

      {Object.keys(summary.verdicts).length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-400">
            Workspace verdicts across {summary.workspaces} workspace(s):
          </span>
          {Object.entries(summary.verdicts).map(([status, count]) => (
            <span key={status} className="flex items-center gap-1">
              <StatusBadge status={status as CheckStatus} />
              <span className="text-slate-500">×{count}</span>
            </span>
          ))}
        </div>
      )}

      <div className="space-y-3">
        {result.items.map((item, index) => (
          <BatchItemCard key={`${index}-${item.point}`} item={item} />
        ))}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="card p-3">
      <div className="text-2xl font-semibold">{value}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </div>
  );
}

function BatchItemCard({ item }: { item: ChecklistBatchItem }) {
  const covered = item.status === "covered";
  const top = item.matches[0];
  const badge = covered
    ? "border-green-200 bg-green-50 text-green-800 dark:border-green-900 dark:bg-green-950 dark:text-green-300"
    : item.status === "invalid"
      ? "border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400"
      : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300";

  return (
    <div className="card space-y-2 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-sm font-medium">{item.point}</p>
        <span className={`badge whitespace-nowrap border ${badge}`}>
          {covered ? "Covered" : item.status === "invalid" ? "Invalid" : "Not covered"}
        </span>
      </div>

      {covered && top && (
        <p className="text-xs text-slate-500">
          <span className="font-mono">{top.check_id}</span> ({top.ref}, {top.pillar}) ·{" "}
          {Math.round(top.confidence * 100)}% match
          {item.evaluated_check ? " · evaluated" : " · attestation-only"}
        </p>
      )}

      {item.evaluations.length > 0 && (
        <div className="scroll-x">
          <table className="table-base text-xs">
            <thead>
              <tr>
                <th scope="col">Workspace</th>
                <th scope="col">Source</th>
                <th scope="col">Verdict</th>
                <th scope="col">Objects</th>
                <th scope="col">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {item.evaluations.map((ev, i) => (
                <tr key={`${ev.workspace}-${i}`}>
                  <td className="whitespace-nowrap">{ev.workspace}</td>
                  <td className="whitespace-nowrap uppercase text-slate-400">{ev.source}</td>
                  <td><StatusBadge status={ev.status as CheckStatus} /></td>
                  <td className="text-right">{ev.objects}</td>
                  <td className="min-w-[16rem]">{ev.evidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!covered && item.proposal && (
        <p className="text-xs text-slate-500">
          Draft proposal:{" "}
          <span className="font-mono">{item.proposal.suggested_id}</span> ({item.proposal.pillar}{" "}
          / {item.proposal.scope}). Author it with the checklist-author agent.
        </p>
      )}
    </div>
  );
}
