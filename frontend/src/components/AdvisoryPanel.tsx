/**
 * Advisory judging: the button, the key form, and the result.
 *
 * Judging is deliberately not part of the audit. It costs tokens against a key
 * the reviewer supplies, so it is something they choose to do once they have
 * seen the deterministic score - not a side effect of running one. That is why
 * this is a panel with a button rather than a spinner that appears on its own.
 *
 * The key is held in component state for the length of one request and sent
 * over the wire. The server uses it and drops it: it is never persisted, never
 * logged, and never returned by any endpoint. Nothing here writes it to
 * localStorage, which would outlive the session that chose it.
 */
import { useEffect, useRef, useState } from "react";

import { ErrorBanner, Section, Spinner } from "@/components/ui";
import {
  getAdvisory,
  pollAdvisory,
  reportDownloadUrl,
  runAdvisory,
} from "@/services/auditService";
import type { AdvisoryRun, JobStatus } from "@/types/api";

type Provider = "azure" | "openai";

export function AdvisoryPanel({
  auditId,
  auditStatus,
  initialStatus,
}: {
  auditId: string;
  auditStatus: JobStatus;
  initialStatus?: JobStatus | null;
}) {
  const [provider, setProvider] = useState<Provider>("azure");
  const [apiKey, setApiKey] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [deployment, setDeployment] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");

  const [run, setRun] = useState<AdvisoryRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const auditFinished = auditStatus === "succeeded";
  const status = run?.advisory_status ?? initialStatus ?? null;
  const running = status === "running";
  const done = status === "succeeded";

  // A judging run started earlier - on another tab, or before a refresh - is
  // still going on the server, so pick it up rather than offering to start a
  // second one.
  useEffect(() => {
    if (!auditFinished) return;
    let cancelled = false;
    getAdvisory(auditId)
      .then((current) => {
        if (cancelled) return;
        setRun(current);
        if (current.advisory_status === "running") void watch();
      })
      .catch(() => {
        /* Nothing has been requested yet; the button handles it. */
      });
    return () => {
      cancelled = true;
      abort.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auditId, auditFinished]);

  async function watch() {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    try {
      const finished = await pollAdvisory(auditId, setRun, controller.signal);
      if (finished.advisory_status === "failed") {
        setError(finished.advisory_error ?? "Advisory judging failed.");
      }
    } catch (caught) {
      if ((caught as DOMException)?.name !== "AbortError") {
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    }
  }

  async function start() {
    setError(null);
    setBusy(true);
    try {
      const started = await runAdvisory(auditId, {
        provider,
        api_key: apiKey || null,
        endpoint: provider === "azure" ? endpoint || null : null,
        deployment: provider === "azure" ? deployment || null : null,
        base_url: provider === "openai" ? baseUrl || null : null,
        model: provider === "openai" ? model || null : null,
      });
      setRun(started);
      // The key has done its job. Drop it so it is not sitting in memory for
      // however long the tab stays open.
      setApiKey("");
      void watch();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="AI advisory review">
      <div className="card space-y-4">
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Advisory checks are the ones a fixed rule can only guess at. A model
          re-reads the evidence and labels each object; the score is still
          computed in code, so the model cannot invent a number.
        </p>

        {!auditFinished && (
          <p className="text-sm text-slate-500">
            Available once the audit finishes — judging reads the files the audit
            writes when it completes.
          </p>
        )}

        {auditFinished && !done && (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-sm">
                <span className="mb-1 block font-medium">Provider</span>
                <select
                  className="input"
                  value={provider}
                  onChange={(event) => setProvider(event.target.value as Provider)}
                  disabled={running || busy}
                >
                  <option value="azure">Azure OpenAI</option>
                  <option value="openai">OpenAI-compatible</option>
                </select>
              </label>

              <label className="text-sm">
                <span className="mb-1 block font-medium">API key</span>
                <input
                  className="input"
                  type="password"
                  autoComplete="off"
                  placeholder="Azure key"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  disabled={running || busy}
                />
              </label>

              {provider === "azure" ? (
                <>
                  <label className="text-sm">
                    <span className="mb-1 block font-medium">Endpoint</span>
                    <input
                      className="input"
                      placeholder="https://….openai.azure.com"
                      value={endpoint}
                      onChange={(event) => setEndpoint(event.target.value)}
                      disabled={running || busy}
                    />
                  </label>
                  <label className="text-sm">
                    <span className="mb-1 block font-medium">Deployment</span>
                    <input
                      className="input"
                      placeholder="gpt-4o"
                      value={deployment}
                      onChange={(event) => setDeployment(event.target.value)}
                      disabled={running || busy}
                    />
                  </label>
                </>
              ) : (
                <>
                  <label className="text-sm">
                    <span className="mb-1 block font-medium">Base URL</span>
                    <input
                      className="input"
                      placeholder="https://…/v1"
                      value={baseUrl}
                      onChange={(event) => setBaseUrl(event.target.value)}
                      disabled={running || busy}
                    />
                  </label>
                  <label className="text-sm">
                    <span className="mb-1 block font-medium">Model</span>
                    <input
                      className="input"
                      placeholder="gpt-4o"
                      value={model}
                      onChange={(event) => setModel(event.target.value)}
                      disabled={running || busy}
                    />
                  </label>
                </>
              )}
            </div>

            <p className="text-xs text-slate-500">
              Used for this run only — never stored, logged, or returned. Leave
              blank to use the server's own model, if one is configured.
            </p>
          </>
        )}

        {error && <ErrorBanner message={error} />}

        <div className="flex items-center gap-3">
          {running ? (
            <Spinner label="Judging — this takes a few minutes" />
          ) : (
            <button
              type="button"
              className="btn-primary"
              onClick={start}
              disabled={!auditFinished || busy}
              title={
                auditFinished
                  ? "Judge the advisory checks with a model"
                  : "Available when the audit finishes"
              }
            >
              {done ? "Run again" : "Run AI advisory"}
            </button>
          )}

          {done && (
            <>
              <a
                className="btn-secondary"
                href={reportDownloadUrl(auditId, "advisory-judged-excel")}
              >
                Judged report (Excel)
              </a>
              <a
                className="btn-secondary"
                href={reportDownloadUrl(auditId, "advisory-judged-markdown")}
                target="_blank"
                rel="noreferrer"
              >
                Markdown
              </a>
            </>
          )}
        </div>

        {run?.summary && <AdvisorySummaryLine summary={run.summary} />}
      </div>
    </Section>
  );
}

function AdvisorySummaryLine({
  summary,
}: {
  summary: NonNullable<AdvisoryRun["summary"]>;
}) {
  return (
    <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
      <Stat label="Checks judged" value={`${summary.checks_judged} of ${summary.checks_total}`} />
      <Stat label="Objects labelled" value={summary.objects_labelled} />
      <Stat
        label="Changed by the model"
        value={summary.findings_changed}
        hint="Findings where the model disagreed with the rule — the point of the exercise"
      />
      <Stat
        label="Could not judge"
        value={summary.objects_undetermined}
        hint="Excluded from the score entirely, never counted against the estate"
      />
    </dl>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div title={hint}>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}

/** Prefer the API's own explanation - it says exactly which field is missing. */
function messageOf(caught: unknown): string {
  const detail = (caught as { response?: { data?: { detail?: string } } })?.response
    ?.data?.detail;
  if (detail) return detail;
  return caught instanceof Error ? caught.message : String(caught);
}
