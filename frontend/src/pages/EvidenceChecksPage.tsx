/**
 * Evidence checks — upload supporting documents, let AI draft a 0-3 score with
 * quoted evidence, then approve. The manual (Cat 3) checklist points that live
 * outside Fabric telemetry are scored here from the documents the user provides.
 *
 * Flow the user follows:
 *   1. Pick which manual checks to assess (default: all 45).
 *   2. See the exact list of documents to prepare for that selection.
 *   3. Upload once — one file can answer many checks.
 *   4. Review the AI drafts (score + quotes) and approve.
 *
 * Nothing is ever scored without a human approving it: the AI drafts, you confirm.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { AiSettings } from "@/components/AiSettings";
import { ErrorBanner, Section, Spinner } from "@/components/ui";
import { listKbWorkspaces } from "@/services/auditService";
import {
  getEvidenceCatalog,
  runEvidenceChecks,
  saveApprovedEvidence,
} from "@/services/evidenceChecksService";
import type {
  AiConfigInput,
  EvidenceChecksResult,
  EvidenceCheckRow,
  EvidenceRequirementOut,
  Workspace,
} from "@/types/api";

export function EvidenceChecksPage() {
  const [catalog, setCatalog] = useState<EvidenceRequirementOut[] | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [selectedWs, setSelectedWs] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [sourceMode, setSourceMode] = useState<"git" | "upload">("git");
  const [files, setFiles] = useState<File[]>([]);
  const [gitUrl, setGitUrl] = useState("");
  const [gitPat, setGitPat] = useState("");
  const [gitBranch, setGitBranch] = useState("");
  const [manualScores, setManualScores] = useState<Record<string, { score: number; note?: string }>>({});
  const [ai, setAi] = useState<AiConfigInput | null>(null);
  const [result, setResult] = useState<EvidenceChecksResult | null>(null);
  const [finalReport, setFinalReport] = useState<string | null>(null);
  const [approved, setApproved] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getEvidenceCatalog()
      .then((c) => {
        setCatalog(c);
        setSelected(new Set(c.map((r) => r.ref))); // default: assess everything
      })
      .catch(() => setCatalogError("Could not load the checklist. Is the backend running?"));
    listKbWorkspaces()
      .then(setWorkspaces)
      .catch(() => setWorkspaces([]));
  }, []);

  const toggleWs = (id: string) =>
    setSelectedWs((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const acceptedExts = useMemo(() => {
    const set = new Set<string>();
    catalog?.forEach((c) => c.accepted_types.forEach((t) => set.add(t)));
    return [...set].map((e) => `.${e}`).join(",");
  }, [catalog]);

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    setFiles((prev) => {
      const byName = new Map(prev.map((f) => [f.name, f]));
      for (const f of [...list]) byName.set(f.name, f);
      return [...byName.values()];
    });
  };
  const removeFile = (name: string) => setFiles((prev) => prev.filter((f) => f.name !== name));

  // The checks matching the current search, grouped by their real pillar.
  const grouped = useMemo(() => {
    if (!catalog) return [];
    const q = search.trim().toLowerCase();
    const rows = catalog.filter(
      (r) =>
        !q ||
        r.ref.includes(q) ||
        r.title.toLowerCase().includes(q) ||
        r.ask_for.toLowerCase().includes(q),
    );
    const byPillar = new Map<string, EvidenceRequirementOut[]>();
    for (const r of rows) {
      const p = r.pillar || "Other";
      if (!byPillar.has(p)) byPillar.set(p, []);
      byPillar.get(p)!.push(r);
    }
    // Order pillars by the lowest ref section they contain.
    const sectionOf = (rows: EvidenceRequirementOut[]) =>
      Math.min(...rows.map((r) => Number(r.ref.split(".")[0])));
    return [...byPillar.entries()].sort((a, b) => sectionOf(a[1]) - sectionOf(b[1]));
  }, [catalog, search]);

  // The distinct documents the user must prepare for the selected checks.
  const documentsToPrepare = useMemo(() => {
    if (!catalog) return [];
    const asks = new Map<string, string[]>();
    for (const r of catalog) {
      if (!selected.has(r.ref)) continue;
      if (!asks.has(r.ask_for)) asks.set(r.ask_for, []);
      asks.get(r.ask_for)!.push(r.ref);
    }
    return [...asks.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [catalog, selected]);

  const toggleCheck = (ref: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(ref) ? next.delete(ref) : next.add(ref);
      return next;
    });

  const selectAll = () => setSelected(new Set(catalog?.map((r) => r.ref) ?? []));
  const clearAll = () => setSelected(new Set());

  const run = async (approvedRefs?: string[]) => {
    setLoading(true);
    setError(null);
    setFinalReport(null); // a fresh analysis invalidates any built report
    try {
      const res = await runEvidenceChecks({
        files: sourceMode === "upload" ? files : [],
        refs: [...selected],
        workspaceIds: [...selectedWs],
        git: sourceMode === "git" && gitUrl.trim() ? { url: gitUrl.trim(), pat: gitPat, branch: gitBranch } : null,
        manualScores: Object.keys(manualScores).length ? manualScores : null,
        ai,
        approvedRefs,
      });
      setResult(res);
      // Pre-tick checks already approved for these workspaces in an earlier run.
      const remembered = res.ledger.filter((r) => r.previously_approved).map((r) => r.ref);
      if (remembered.length) setApproved((prev) => new Set([...prev, ...remembered]));
    } catch {
      setError("The run failed. Check the backend and, if using AI, your key.");
    } finally {
      setLoading(false);
    }
  };

  const toggleApprove = (ref: string) =>
    setApproved((prev) => {
      const next = new Set(prev);
      next.has(ref) ? next.delete(ref) : next.add(ref);
      return next;
    });

  const drafted = result?.ledger.filter((r) => r.status === "DRAFTED") ?? [];
  const needs = result?.ledger.filter((r) => r.status !== "DRAFTED") ?? [];

  if (!catalog && !catalogError) return <Spinner label="Loading the checklist…" />;

  return (
    <div className="space-y-8">
      <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">
        <span className="font-semibold">Manual checks</span> — the governance/documentation points
        that can't be read from Fabric telemetry. Point at your documentation repo and AI drafts a
        0–3 score with quotes for each; you review and confirm.
      </div>

      {/* STEP 0 — which workspaces this evidence is for */}
      <Section
        title="Workspaces this evidence covers"
        description="These results are attested for the workspaces you select — pick one or more. Select several for cross-workspace (solution-wide) evidence."
      >
        <WorkspacePicker
          workspaces={workspaces}
          selected={selectedWs}
          onToggle={toggleWs}
          onClear={() => setSelectedWs(new Set())}
          onAll={() => setSelectedWs(new Set(workspaces?.map((w) => w.id) ?? []))}
        />
      </Section>

      {/* STEP 1 — pick the checks */}
      <Section
        title="1. Choose which checks to assess"
        description="Pick only the checks you have documents for. Fewer checks means fewer documents to upload."
        actions={
          <div className="flex items-center gap-2 text-sm">
            <button type="button" className="btn-secondary px-2 py-1" onClick={selectAll}>
              Select all
            </button>
            <button type="button" className="btn-secondary px-2 py-1" onClick={clearAll}>
              Clear
            </button>
            <span className="text-slate-400">{selected.size} selected</span>
          </div>
        }
      >
        {catalogError && <ErrorBanner message={catalogError} />}
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search checks (e.g. runbook, ownership, retention)…"
          className="input w-full text-sm"
        />
        <div className="space-y-4 rounded-md border border-slate-200 p-2 dark:border-slate-700" style={{ maxHeight: "20rem", overflowY: "auto" }}>
          {grouped.map(([pillar, rows]) => (
            <div key={pillar} className="space-y-1">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                {pillar}
              </p>
              {rows.map((r) => (
                <label
                  key={r.ref}
                  className="flex cursor-pointer items-start gap-2 rounded p-1 hover:bg-slate-50 dark:hover:bg-slate-800"
                >
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={selected.has(r.ref)}
                    onChange={() => toggleCheck(r.ref)}
                  />
                  <span className="text-sm">
                    <span className="text-slate-400">{r.ref}</span> {r.title}
                    <span className="block text-xs text-sky-600 dark:text-sky-400">
                      📎 Upload: {r.ask_for}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          ))}
        </div>
      </Section>

      {/* STEP 2 — provide evidence: a Git repo or uploaded files */}
      <Section
        title="2. Provide your documentation"
        description="Point at a GitHub / Azure DevOps repo, or upload documents. The AI reads them and scores the selected checks."
      >
        {documentsToPrepare.length > 0 && (
          <details className="card p-3">
            <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-400">
              Documentation your repo should contain ({documentsToPrepare.length})
            </summary>
            <ul className="mt-2 space-y-1 text-sm">
              {documentsToPrepare.map(([ask, refs]) => (
                <li key={ask} className="flex items-start gap-2">
                  <span className="text-sky-600">📄</span>
                  <span>
                    {ask}
                    <span className="text-xs text-slate-400">
                      {" "}
                      — covers {refs.length} check{refs.length > 1 ? "s" : ""} ({refs.join(", ")})
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </details>
        )}

        {/* Source toggle: Git repo or upload files */}
        <div role="tablist" className="flex gap-1 border-b border-slate-200 dark:border-slate-700">
          <button
            type="button"
            className={`px-3 py-1.5 text-sm font-medium ${sourceMode === "git" ? "border-b-2 border-blue-600 text-blue-600" : "text-slate-500"}`}
            onClick={() => setSourceMode("git")}
          >
            From Git repo
          </button>
          <button
            type="button"
            className={`px-3 py-1.5 text-sm font-medium ${sourceMode === "upload" ? "border-b-2 border-blue-600 text-blue-600" : "text-slate-500"}`}
            onClick={() => setSourceMode("upload")}
          >
            Upload files
          </button>
        </div>

        {sourceMode === "git" ? (
          <div className="card space-y-3 p-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-sm sm:col-span-2">
                <span className="text-slate-500">Repository URL</span>
                <input
                  value={gitUrl}
                  onChange={(e) => setGitUrl(e.target.value)}
                  placeholder="https://github.com/org/repo  or  https://dev.azure.com/org/proj/_git/repo"
                  className="input w-full"
                />
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-slate-500">Personal access token (read-only)</span>
                <input
                  type="password"
                  value={gitPat}
                  autoComplete="off"
                  onChange={(e) => setGitPat(e.target.value)}
                  placeholder="read-only token for private repos (never stored)"
                  className="input w-full"
                />
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-slate-500">Branch (optional)</span>
                <input
                  value={gitBranch}
                  onChange={(e) => setGitBranch(e.target.value)}
                  placeholder="defaults to the repo default"
                  className="input w-full"
                />
              </label>
            </div>
            <p className="text-xs text-slate-400">
              We only read the repo — a <span className="font-medium">read-only</span> token is enough
              (GitHub <code>repo:read</code> / <code>Contents: Read</code>, Azure DevOps <code>Code: Read</code>).
              No write, and the token is used for this run only and never stored.
            </p>
            <p className="text-xs text-slate-400">
              No documentation for a check? You can attest a score by hand below after running.
            </p>
          </div>
        ) : (
          <>
            <div
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                addFiles(e.dataTransfer.files);
              }}
              className="card flex flex-col items-center justify-center gap-2 border-2 border-dashed border-slate-300 p-8 text-center dark:border-slate-600"
            >
              <p className="text-sm text-slate-600 dark:text-slate-300">Drag &amp; drop your documents here, or</p>
              <button type="button" className="btn-secondary px-3 py-1.5 text-sm" onClick={() => inputRef.current?.click()}>
                Choose files
              </button>
              <input
                ref={inputRef}
                type="file"
                multiple
                accept={acceptedExts || undefined}
                className="hidden"
                onChange={(e) => addFiles(e.target.files)}
              />
              <p className="text-xs text-slate-400">Accepted: PDF, Word, Markdown. One combined file or several separate files both work.</p>
            </div>
            {files.length > 0 && (
              <ul className="flex flex-wrap gap-2">
                {files.map((f) => (
                  <li key={f.name} className="badge flex items-center gap-2">
                    {f.name}
                    <button
                      type="button"
                      aria-label={`Remove ${f.name}`}
                      className="text-slate-400 hover:text-red-500"
                      onClick={() => removeFile(f.name)}
                    >
                      ✕
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}

        <AiSettings onChange={setAi} />

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn-primary px-4 py-1.5 text-sm"
            disabled={
              loading ||
              selected.size === 0 ||
              selectedWs.size === 0 ||
              (sourceMode === "git" ? !gitUrl.trim() : files.length === 0)
            }
            onClick={() => void run()}
          >
            {loading
              ? sourceMode === "git"
                ? "Reading repo…"
                : "Reading documents…"
              : `Analyze ${selected.size} check${selected.size === 1 ? "" : "s"}`}
          </button>
          {selectedWs.size === 0 && (
            <span className="text-xs text-amber-600">Pick at least one workspace above first.</span>
          )}
          {!ai && (
            <span className="text-xs text-amber-600">
              Add an AI key above to draft scores — without it, checks show “needs AI”.
            </span>
          )}
        </div>

        {loading && <Spinner label={sourceMode === "git" ? "Pulling docs from the repo and drafting scores…" : "Reading your documents and drafting scores…"} />}
        {error && <ErrorBanner message={error} onRetry={() => void run()} />}
        {result?.git_error && <ErrorBanner message={`Git: ${result.git_error}`} />}
        {result && result.git_files > 0 && (
          <p className="text-xs text-slate-500">Pulled {result.git_files} documentation file(s) from the repo.</p>
        )}
      </Section>

      {/* STEP 3 — results */}
      {result && (
        <Section
          title="3. Review and approve"
          description="Each score is a draft with the exact quotes it is based on. Approve the ones you agree with, then update the report."
          actions={
            drafted.length > 0 ? (
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className="btn-secondary px-3 py-1.5 text-sm"
                  onClick={() => setApproved(new Set(drafted.map((r) => r.ref)))}
                >
                  Approve all ({drafted.length})
                </button>
                <button
                  type="button"
                  className="btn-secondary px-3 py-1.5 text-sm"
                  disabled={approved.size === 0}
                  onClick={() => setApproved(new Set())}
                >
                  Clear
                </button>
                <button
                  type="button"
                  className="btn-primary px-3 py-1.5 text-sm"
                  disabled={approved.size === 0}
                  onClick={() => {
                    setFinalReport(buildReportMarkdown(result.ledger, approved, result.workspaces));
                    // Remember approved rows so the next audit for these workspaces folds them in.
                    const rows = result.ledger.filter((r) => approved.has(r.ref));
                    void saveApprovedEvidence(rows, [...selectedWs]).catch(() => undefined);
                  }}
                >
                  Update report ({approved.size} approved)
                </button>
              </div>
            ) : undefined
          }
        >
          {result.workspaces.length > 0 && (
            <p className="text-xs text-slate-500">
              Attested for: <span className="font-medium">{result.workspaces.join(", ")}</span>
            </p>
          )}
          {finalReport && (
            <div className="card border-emerald-300 bg-emerald-50/50 p-3 dark:border-emerald-800 dark:bg-emerald-950/20">
              <p className="mb-2 text-sm font-semibold text-emerald-800 dark:text-emerald-300">
                ✓ Report updated ({approved.size} approved)
              </p>
              <pre className="scroll-x whitespace-pre-wrap text-xs">{finalReport}</pre>
            </div>
          )}
          {drafted.length === 0 && (
            <p className="text-sm text-slate-500">
              No document-backed scores were drafted. See “Still needs evidence” below.
            </p>
          )}
          <div className="space-y-3">
            {drafted.map((row) => (
              <DraftCard
                key={row.ref}
                row={row}
                approved={approved.has(row.ref)}
                onToggle={() => toggleApprove(row.ref)}
              />
            ))}
          </div>

          {needs.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-slate-600 dark:text-slate-300">Still needs evidence</h3>
              <p className="text-xs text-slate-400">
                No document covered these. You can attest a score manually (recorded as a human attestation, not AI).
              </p>
              <ul className="space-y-2 text-sm">
                {needs.map((row) => (
                  <li key={row.ref} className="flex flex-wrap items-center gap-2">
                    <span className="text-slate-500">
                      <span className="font-medium">{row.ref}</span> {row.title} —{" "}
                      <span className="text-amber-600">
                        {row.status === "AI_REQUIRED" ? "add an AI key" : "no matching document"}
                      </span>
                    </span>
                    <select
                      className="input w-auto py-1 text-xs"
                      value={manualScores[row.ref]?.score ?? ""}
                      onChange={(e) => {
                        const v = e.target.value;
                        setManualScores((prev) => {
                          const next = { ...prev };
                          if (v === "") delete next[row.ref];
                          else next[row.ref] = { score: Number(v), note: prev[row.ref]?.note };
                          return next;
                        });
                      }}
                    >
                      <option value="">Set score…</option>
                      <option value="0">0</option>
                      <option value="1">1</option>
                      <option value="2">2</option>
                      <option value="3">3</option>
                    </select>
                  </li>
                ))}
              </ul>
              {Object.keys(manualScores).length > 0 && (
                <button
                  type="button"
                  className="btn-secondary px-3 py-1.5 text-sm"
                  disabled={loading}
                  onClick={() => void run()}
                >
                  Apply {Object.keys(manualScores).length} manual score(s)
                </button>
              )}
            </div>
          )}
        </Section>
      )}
    </div>
  );
}

function buildReportMarkdown(
  rows: EvidenceCheckRow[],
  approvedSet: Set<string>,
  workspaces: string[],
): string {
  const approved = rows
    .filter((r) => approvedSet.has(r.ref) && r.status === "DRAFTED")
    .sort((a, b) => a.ref.localeCompare(b.ref));
  const header = "| Ref | Title | Score | Rung | Evidence | Recommendations |";
  const sep = "|-----|-------|-------|------|----------|-----------------|";
  const rowMd = (r: EvidenceCheckRow) => {
    const score = r.score == null ? "N/A" : `${r.score}/3`;
    const evidence =
      r.source === "manual"
        ? "_manually attested_"
        : r.citations.map((c) => `"${c.quote}" (${c.source} p.${c.page})`).join("; ") || "—";
    const recs = r.recommendations.join("; ") || "—";
    return `| ${r.ref} | ${r.title} | ${score} | ${r.rung_matched ?? "—"} | ${evidence} | ${recs} |`;
  };

  const lines = ["## Document-evidenced checks (AI-assisted, human-confirmed)", ""];
  if (workspaces.length) lines.push(`**Attested for workspaces:** ${workspaces.join(", ")}`, "");
  if (!approved.length) {
    lines.push("_No approved results yet._");
  } else if (workspaces.length) {
    for (const ws of workspaces) {
      lines.push(`### Workspace: ${ws}`, "", header, sep, ...approved.map(rowMd), "");
    }
  } else {
    lines.push(header, sep, ...approved.map(rowMd));
  }
  return lines.join("\n") + "\n";
}

function DraftCard({
  row,
  approved,
  onToggle,
}: {
  row: EvidenceCheckRow;
  approved: boolean;
  onToggle: () => void;
}) {
  const scoreColor =
    row.score === null
      ? "bg-slate-300"
      : row.score >= 3
        ? "bg-emerald-500"
        : row.score >= 2
          ? "bg-amber-500"
          : "bg-red-500";

  return (
    <div className="card space-y-2 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-medium">
            <span className="text-slate-400">{row.ref}</span> {row.title}
            {row.previously_approved && (
              <span className="ml-2 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                ✓ previously approved
              </span>
            )}
          </p>
          <p className="text-xs text-slate-400">
            Rung {row.rung_matched ?? "—"} · confidence {Math.round(row.confidence * 100)}%
            {row.source === "manual" && <span className="ml-1 text-slate-500">· manually attested</span>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-sm font-bold text-white ${scoreColor}`}>
            Score: {row.score ?? "—"}/3
          </span>
          <label className="flex items-center gap-1 text-sm">
            <input type="checkbox" checked={approved} onChange={onToggle} />
            Approve
          </label>
        </div>
      </div>

      {row.citations.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-slate-500">Evidence</p>
          <ul className="space-y-1 text-sm">
            {row.citations.map((c, i) => (
              <li key={i} className="border-l-2 border-slate-200 pl-2 italic text-slate-600 dark:text-slate-300">
                “{c.quote}”
                <span className="not-italic text-xs text-slate-400"> — {c.source} p.{c.page}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {row.recommendations.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-slate-500">Recommendations</p>
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-slate-600 dark:text-slate-300">
            {row.recommendations.map((rec, i) => (
              <li key={i}>{rec}</li>
            ))}
          </ul>
        </div>
      )}

      {row.gaps.length > 0 && <p className="text-xs text-amber-600">Gaps: {row.gaps.join("; ")}</p>}
    </div>
  );
}

function WorkspacePicker({
  workspaces,
  selected,
  onToggle,
  onClear,
  onAll,
}: {
  workspaces: Workspace[] | null;
  selected: Set<string>;
  onToggle: (id: string) => void;
  onClear: () => void;
  onAll: () => void;
}) {
  if (workspaces === null) {
    return <p className="text-sm text-slate-500">Loading crawled workspaces…</p>;
  }
  if (workspaces.length === 0) {
    return (
      <div className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm dark:border-amber-900 dark:bg-amber-950/30">
        <p className="font-medium text-amber-800 dark:text-amber-300">No crawled workspaces yet</p>
        <p className="mt-1 text-amber-700 dark:text-amber-300/80">
          Evidence results are attested against workspaces you have already audited. Crawl one on
          the Run audit page, then come back here.
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
          {selected.size} of {workspaces.length} selected
        </span>
        <div className="flex gap-2">
          <button type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={onAll}>
            Select all (cross-workspace)
          </button>
          <button
            type="button"
            className="btn-secondary px-2.5 py-1 text-xs"
            onClick={onClear}
            disabled={selected.size === 0}
          >
            Clear
          </button>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        {workspaces.map((w) => (
          <label
            key={w.id}
            className="flex items-center gap-2 rounded-md border border-slate-200 px-2.5 py-1 text-sm dark:border-slate-700"
          >
            <input type="checkbox" checked={selected.has(w.id)} onChange={() => onToggle(w.id)} />
            <span>{w.name || w.id}</span>
            {typeof w.items === "number" && <span className="text-xs text-slate-400">{w.items} items</span>}
          </label>
        ))}
      </div>
    </div>
  );
}
