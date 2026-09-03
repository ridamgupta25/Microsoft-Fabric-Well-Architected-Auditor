/**
 * Custom checks — type checks in plain English, run them, review, get a report.
 *
 * Sends the checks (and an optional per-request AI key) to the pipeline, shows the
 * lifecycle ledger, lets you approve/reject each check, and renders the returned
 * Markdown report. Token-free and read-only; never changes the deterministic score.
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { AiSettings } from "@/components/AiSettings";
import { ErrorBanner, Section, Spinner } from "@/components/ui";
import { listKbWorkspaces } from "@/services/auditService";
import { runCustomChecks } from "@/services/customChecksService";
import type {
  AiConfigInput,
  CustomCheckRow,
  CustomChecksResult,
  Workspace,
} from "@/types/api";

const EXAMPLES = [
  "Ensure all semantic models have incremental refresh policies",
  "Verify Git integration is enabled on every workspace",
  "Check that warehouse SQL audit logging is turned on",
];

const STATUS_CLASS: Record<string, string> = {
  DROPPED_GUARDRAIL: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  ROUTED_DEFAULT: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  PROCESSED_CUSTOM: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  KB_AUGMENTED: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  KB_FETCH_FAILED: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  AI_REQUIRED: "bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300",
  PENDING: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
};

export function CustomChecksPage() {
  const [text, setText] = useState("");
  const [ai, setAi] = useState<AiConfigInput | null>(null);
  const [result, setResult] = useState<CustomChecksResult | null>(null);
  const [approved, setApproved] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [selectedWs, setSelectedWs] = useState<Set<string>>(new Set());

  // Custom checks read the crawled KB snapshots — load them so the user can see
  // (and pick) what data is available, or be told to crawl one first.
  useEffect(() => {
    let active = true;
    listKbWorkspaces()
      .then((ws) => {
        if (!active) return;
        setWorkspaces(ws);
        setSelectedWs(new Set(ws.map((w) => w.id)));
      })
      .catch(() => active && setWorkspaces([]));
    return () => {
      active = false;
    };
  }, []);

  const toggleWs = (id: string) =>
    setSelectedWs((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const clearWs = () => setSelectedWs(new Set());

  const prompts = useMemo(
    () => text.split("\n").map((line) => line.trim()).filter(Boolean),
    [text],
  );

  const run = async (approvedIds?: string[]) => {
    if (prompts.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const data = await runCustomChecks({
        prompts,
        workspace_ids: selectedWs.size > 0 ? [...selectedWs] : undefined,
        approved_check_ids: approvedIds,
        ai: ai ?? undefined,
      });
      setResult(data);
      if (!approvedIds) {
        // Pre-tick checks the reviewer approved in a past run so trusted checks
        // stay approved without re-ticking them every time.
        const remembered = data.ledger
          .filter((row) => row.previously_approved)
          .map((row) => row.check_id);
        setApproved(new Set(remembered));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const toggle = (id: string) => {
    setApproved((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="space-y-8">
      <Section
        title="Custom checks"
        description="Type audit checks in plain English. They run read-only over the knowledge base — never changing a score or your Fabric."
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void run();
          }}
          className="space-y-3"
        >
          <WorkspacePicker
            workspaces={workspaces}
            selected={selectedWs}
            onToggle={toggleWs}
            onClear={clearWs}
          />
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={"One check per line, e.g.\n" + EXAMPLES.join("\n")}
            className="input min-h-[7rem] w-full font-mono text-sm"
            aria-label="Custom checks, one per line"
          />
          <AiSettings onChange={setAi} />
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="submit"
              className="btn-primary px-4 py-1.5 text-sm"
              disabled={loading || prompts.length === 0 || !workspaces || workspaces.length === 0}
            >
              Run {prompts.length > 0 ? `(${prompts.length})` : ""}
            </button>
            <button
              type="button"
              className="btn-secondary px-3 py-1.5 text-sm"
              onClick={() => setText(EXAMPLES.join("\n"))}
            >
              Load examples
            </button>
          </div>
        </form>

        {loading && <Spinner label="Running the pipeline…" />}
        {error && <ErrorBanner message={error} onRetry={() => void run()} />}
      </Section>

      {result && !loading && (
        <>
          <Section
            title="Results"
            description={`${result.prompts} check(s) across ${result.workspaces} workspace(s).`}
          >
            <div className="mb-4 flex flex-wrap gap-2">
              {Object.entries(result.summary).map(([status, count]) => (
                <span key={status} className={`badge ${STATUS_CLASS[status] ?? "badge"}`}>
                  {status}: {count}
                </span>
              ))}
            </div>

            <div className="space-y-3">
              {result.ledger.map((row) => (
                <CheckCard
                  key={row.check_id}
                  row={row}
                  approved={approved.has(row.check_id)}
                  onToggle={() => toggle(row.check_id)}
                />
              ))}
            </div>

            <div className="mt-4 space-y-1">
              <button
                type="button"
                className="btn-primary px-4 py-1.5 text-sm"
                onClick={() => void run([...approved])}
                disabled={loading}
              >
                Update report with {approved.size} approved
              </button>
              <p className="text-xs text-slate-500">
                Tick <strong>Approve</strong> on the AI-generated checks you trust, then
                update the report — only approved checks are included.
              </p>
            </div>
          </Section>

          <Section title="Report">
            <details className="card p-4">
              <summary className="cursor-pointer text-sm font-medium text-slate-600 dark:text-slate-300">
                Show raw Markdown report
              </summary>
              <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs text-slate-600 dark:text-slate-400">
                {result.report_markdown}
              </pre>
            </details>
          </Section>
        </>
      )}
    </div>
  );
}

function scoreColor(score: number): string {
  if (score >= 80) return "bg-green-500";
  if (score >= 50) return "bg-amber-500";
  return "bg-red-500";
}

function CheckCard({
  row,
  approved,
  onToggle,
}: {
  row: CustomCheckRow;
  approved: boolean;
  onToggle: () => void;
}) {
  const evaluation = row.evaluation ?? null;
  const canApprove = Boolean(row.generated_code);

  return (
    <div className="card space-y-3 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="min-w-0 flex-1 font-medium text-slate-800 dark:text-slate-100">
          {row.raw_prompt}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`badge ${STATUS_CLASS[row.lifecycle_status] ?? "badge"}`}>
            {row.lifecycle_status}
          </span>
          {row.feasibility && (
            <span className="badge bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
              {row.feasibility}
            </span>
          )}
          {row.previously_approved && (
            <span
              className="badge bg-green-100 text-green-700 dark:bg-green-950/60 dark:text-green-400"
              title="You approved this check in an earlier run — pre-selected for you."
            >
              Previously approved
            </span>
          )}
          {canApprove && (
            <button
              type="button"
              onClick={onToggle}
              aria-pressed={approved}
              title="Approve this AI-generated check to include it in the report"
              className={
                approved
                  ? "inline-flex items-center gap-1.5 rounded-md bg-green-600 px-3 py-1.5 text-sm font-semibold text-white shadow-sm hover:bg-green-700"
                  : "inline-flex items-center gap-1.5 rounded-md border border-green-600 px-3 py-1.5 text-sm font-semibold text-green-700 hover:bg-green-50 dark:text-green-400 dark:hover:bg-green-950/40"
              }
            >
              {approved ? "✓ Approved" : "Approve"}
            </button>
          )}
        </div>
      </div>

      {evaluation && (
        <div className="space-y-2 rounded-md bg-slate-50 p-3 dark:bg-slate-900/40">
          <div className="flex items-center gap-3">
            <span className="text-sm font-semibold text-slate-700 dark:text-slate-200">
              {evaluation.status}
            </span>
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
              <div
                className={`h-full ${scoreColor(evaluation.score)}`}
                style={{ width: `${Math.max(0, Math.min(100, evaluation.score))}%` }}
              />
            </div>
            <span className="text-sm font-semibold tabular-nums text-slate-700 dark:text-slate-200">
              {evaluation.score.toFixed(0)} / 100
            </span>
          </div>

          {evaluation.findings.length > 0 ? (
            <DetailList label="Evidence / findings" items={evaluation.findings} />
          ) : (
            <p className="text-sm text-slate-500">
              Evidence: no issues found — all evaluated items are compliant.
            </p>
          )}
          {evaluation.recommendations.length > 0 && (
            <DetailList label="Recommendations" items={evaluation.recommendations} />
          )}
        </div>
      )}

      {row.lifecycle_status === "DROPPED_GUARDRAIL" && row.guardrail && (
        <p className="rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950/40 dark:text-red-300">
          Blocked by <strong>{row.guardrail.failed_validator ?? "guardrail"}</strong>
          {row.guardrail.reason ? ` — ${row.guardrail.reason}` : ""}
        </p>
      )}

      {row.lifecycle_status === "ROUTED_DEFAULT" && row.routing && (
        <div className="rounded-md bg-slate-50 p-3 text-sm text-slate-600 dark:bg-slate-900/40 dark:text-slate-300">
          Already covered by an existing check
          {row.routing.matched_default_id ? (
            <> (<code>{row.routing.matched_default_id}</code>)</>
          ) : null}
          {typeof row.routing.similarity_score === "number" && (
            <> · similarity {row.routing.similarity_score.toFixed(2)}</>
          )}
          {row.routing.stage ? <> · via {row.routing.stage}</> : null}
          {row.routing.reasoning ? (
            <div className="mt-1 text-slate-500">{row.routing.reasoning}</div>
          ) : null}
        </div>
      )}

      {row.kb_update && row.lifecycle_status === "KB_FETCH_FAILED" && (
        <div className="space-y-1 rounded-md bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          <div>
            <strong>Data unavailable</strong>
            {row.kb_update.diagnostic ? ` (${row.kb_update.diagnostic})` : ""}
          </div>
          {row.kb_update.root_cause && <div>Root cause: {row.kb_update.root_cause}</div>}
          {row.kb_update.remediation && (
            <div>Fix: {row.kb_update.remediation}</div>
          )}
          {row.kb_update.apis_called.length > 0 && (
            <div className="text-amber-700 dark:text-amber-300/80">
              Tried: {row.kb_update.apis_called.join(", ")}
            </div>
          )}
        </div>
      )}

      {row.kb_update && row.lifecycle_status === "KB_AUGMENTED" &&
        row.kb_update.fields_added.length > 0 && (
          <p className="rounded-md bg-green-50 p-3 text-sm text-green-800 dark:bg-green-950/40 dark:text-green-300">
            Fetched read-only: {row.kb_update.fields_added.join(", ")}
          </p>
        )}

      {row.lifecycle_status === "AI_REQUIRED" && (
        <p className="rounded-md bg-purple-50 p-3 text-sm text-purple-800 dark:bg-purple-950/40 dark:text-purple-300">
          Needs an LLM to generate — enable “Use my AI key” above and re-run.
        </p>
      )}

      {row.code_gen && row.code_gen.status === "FAILED" && row.code_gen.reason && (
        <p className="rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950/40 dark:text-red-300">
          Code generation failed
          {row.code_gen.stage_failed ? ` at the ${row.code_gen.stage_failed} stage` : ""}:{" "}
          {row.code_gen.reason}
        </p>
      )}

      {row.generated_code && (
        <details>
          <summary className="cursor-pointer text-sm font-medium text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
            View generated code
          </summary>
          <pre className="mt-2 max-h-80 overflow-auto rounded-md bg-slate-900 p-3 text-xs text-slate-100">
            {row.generated_code}
          </pre>
        </details>
      )}
    </div>
  );
}

function DetailList({ label, items }: { label: string; items: string[] }) {
  return (
    <div className="text-sm">
      <span className="font-medium text-slate-600 dark:text-slate-300">{label}</span>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-slate-600 dark:text-slate-400">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function WorkspacePicker({
  workspaces,
  selected,
  onToggle,
  onClear,
}: {
  workspaces: Workspace[] | null;
  selected: Set<string>;
  onToggle: (id: string) => void;
  onClear: () => void;
}) {
  if (workspaces === null) {
    return <p className="text-sm text-slate-500">Loading crawled workspaces…</p>;
  }
  if (workspaces.length === 0) {
    return (
      <div className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm dark:border-amber-900 dark:bg-amber-950/30">
        <p className="font-medium text-amber-800 dark:text-amber-300">
          No crawled workspaces yet
        </p>
        <p className="mt-1 text-amber-700 dark:text-amber-300/80">
          Custom checks run against workspaces you have already audited. Crawl one on the
          Run audit page, then come back here.
        </p>
        <Link to="/run" className="btn-primary mt-3 inline-block px-3 py-1.5 text-sm">
          Go to Run audit
        </Link>
      </div>
    );
  }
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-slate-600 dark:text-slate-300">
          Workspaces to check ({selected.size} of {workspaces.length})
        </span>
        <button
          type="button"
          className="btn-secondary px-2.5 py-1 text-xs"
          onClick={onClear}
          disabled={selected.size === 0}
        >
          Clear
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        {workspaces.map((w) => (
          <label
            key={w.id}
            className="flex items-center gap-2 rounded-md border border-slate-200 px-2.5 py-1 text-sm dark:border-slate-700"
          >
            <input type="checkbox" checked={selected.has(w.id)} onChange={() => onToggle(w.id)} />
            <span>{w.name || w.id}</span>
            {typeof w.items === "number" && (
              <span className="text-xs text-slate-400">{w.items} items</span>
            )}
          </label>
        ))}
      </div>
    </div>
  );
}
