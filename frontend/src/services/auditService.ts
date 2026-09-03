/**
 * Audit submission, polling, reports, and history.
 *
 * Audits are fire-and-poll: the API accepts the request and returns an id, and
 * the client polls until it reaches a terminal state. A tenant-wide run can take
 * minutes, which no single HTTP request should be holding open.
 */
import { apiClient, downloadUrl } from "./apiClient";
import type {
  AdvisoryRun,
  AdvisoryRunRequest,
  AuditAccepted,
  AuditAnswersRequest,
  AuditJob,
  AuditJobSummary,
  AuditReport,
  AuditRequest,
  CheckResult,
  Diagnostics,
  KBUploadResponse,
  Page,
  RecommendationList,
  Workspace,
} from "@/types/api";

export async function listLiveWorkspaces(session: string): Promise<Workspace[]> {
  const { data } = await apiClient.get<Workspace[]>("/workspaces/live", {
    params: { session },
  });
  return data;
}

/**
 * List workspaces already crawled to the saved knowledge base.
 *
 * No sign-in: these are replayed from disk, so the picker works offline and
 * without a token. Powers the "Saved KB" audit source.
 */
export async function listKbWorkspaces(): Promise<Workspace[]> {
  const { data } = await apiClient.get<Workspace[]>("/workspaces/kb");
  return data;
}

/**
 * Validate an uploaded workspace snapshot and get it back normalized.
 *
 * The returned `snapshot` is what a `source: "kb"` audit must carry in
 * `AuditRequest.snapshots`; re-normalizing server-side means the run reads
 * exactly what was validated here.
 */
export async function uploadKbSnapshot(
  snapshot: Record<string, unknown>,
): Promise<KBUploadResponse> {
  const { data } = await apiClient.post<KBUploadResponse>(
    "/workspaces/kb/upload",
    snapshot,
  );
  return data;
}

/**
 * Probe what the signed-in token can actually read.
 *
 * Reports per-resource HTTP status codes, so partial permissions (items
 * readable but role assignments forbidden) are visible instead of silently
 * producing an incomplete audit.
 */
export async function getDiagnostics(session: string): Promise<Diagnostics> {
  const { data } = await apiClient.get<Diagnostics>("/workspaces/diagnostics", {
    params: { session },
  });
  return data;
}

export async function submitAudit(request: AuditRequest): Promise<AuditAccepted> {
  const { data } = await apiClient.post<AuditAccepted>("/audit", request);
  return data;
}

export async function getAudit(auditId: string): Promise<AuditJob> {
  const { data } = await apiClient.get<AuditJob>(`/audit/${auditId}`);
  return data;
}

/**
 * Record the reviewer's answers to a run's interactive questionnaire.
 *
 * Answers map each interactive check id to a chosen option value (or the skip
 * sentinel). Safe to call while the audit is still running — scoring folds them
 * in as soon as the automated crawl finishes.
 */
export async function submitAuditAnswers(
  auditId: string,
  answers: Record<string, string>,
): Promise<AuditJob> {
  const body: AuditAnswersRequest = { answers };
  const { data } = await apiClient.post<AuditJob>(`/audit/${auditId}/answers`, body);
  return data;
}

export async function getReport(auditId: string): Promise<AuditReport> {
  const { data } = await apiClient.get<AuditReport>(`/reports/${auditId}`);
  return data;
}

export async function getRecommendations(auditId: string): Promise<RecommendationList> {
  const { data } = await apiClient.get<RecommendationList>(`/recommendations/${auditId}`);
  return data;
}

export async function getHistory(limit = 25, offset = 0): Promise<Page<AuditJobSummary>> {
  const { data } = await apiClient.get<Page<AuditJobSummary>>("/history", {
    params: { limit, offset },
  });
  return data;
}

export async function runSingleCheck(
  checkId: string,
  workspaceId: string,
  authSession: string,
  layer?: string,
): Promise<CheckResult[]> {
  const { data } = await apiClient.post<CheckResult[]>("/audit/check", {
    check_id: checkId,
    workspace_id: workspaceId,
    auth_session: authSession,
    layer: layer ?? null,
  });
  return data;
}

export function reportDownloadUrl(
  auditId: string,
  kind:
    | "markdown"
    | "excel"
    | "advisory-markdown"
    | "advisory-excel"
    | "advisory-judged-markdown"
    | "advisory-judged-excel",
): string {
  return downloadUrl(`/reports/${auditId}/download/${kind}`);
}

/**
 * Start advisory judging for a finished audit.
 *
 * Deliberately separate from the audit: it costs tokens against a key the
 * reviewer supplies, so it is something they choose after seeing the
 * deterministic report. The key is sent for this run and never stored.
 */
export async function runAdvisory(
  auditId: string,
  request: AdvisoryRunRequest,
): Promise<AdvisoryRun> {
  const { data } = await apiClient.post<AdvisoryRun>(
    `/audit/${auditId}/advisory`,
    request,
  );
  return data;
}

export async function getAdvisory(auditId: string): Promise<AdvisoryRun> {
  const { data } = await apiClient.get<AdvisoryRun>(`/audit/${auditId}/advisory`);
  return data;
}

/**
 * Poll advisory judging until it finishes.
 *
 * Slower interval than the audit poll: judging makes one model call per chunk
 * across fifty checks, so it is minutes of work and a tight loop would only add
 * request noise.
 */
export async function pollAdvisory(
  auditId: string,
  onProgress?: (run: AdvisoryRun) => void,
  signal?: AbortSignal,
  intervalMs = 3000,
): Promise<AdvisoryRun> {
  for (;;) {
    if (signal?.aborted) throw new DOMException("Polling aborted", "AbortError");

    const run = await getAdvisory(auditId);
    onProgress?.(run);
    if (run.advisory_status && TERMINAL_STATUSES.has(run.advisory_status)) return run;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

const TERMINAL_STATUSES = new Set(["succeeded", "failed"]);

/**
 * Poll an audit until it finishes.
 *
 * @param onProgress Called on each poll so the UI can show live status.
 * @param signal Abort to stop polling when the user navigates away — otherwise
 *   the loop outlives the component that started it.
 * @param timeoutMs Optional upper bound. Defaults to `0` — **no timeout**: the
 *   audit now runs off a disk-cached knowledge base and refreshes in the
 *   background, so polling simply continues until it reaches a terminal state
 *   (or `signal` aborts). Pass a positive value only to cap the wait.
 */
export async function pollAudit(
  auditId: string,
  onProgress?: (job: AuditJob) => void,
  signal?: AbortSignal,
  intervalMs = 1000,
  timeoutMs = 0,
): Promise<AuditJob> {
  const deadline = timeoutMs > 0 ? Date.now() + timeoutMs : Number.POSITIVE_INFINITY;
  let last: AuditJob | null = null;

  for (;;) {
    if (signal?.aborted) throw new DOMException("Polling aborted", "AbortError");

    const job = await getAudit(auditId);
    last = job;
    onProgress?.(job);
    if (TERMINAL_STATUSES.has(job.status)) return job;

    // Only relevant when a positive timeoutMs was supplied. Past the deadline,
    // stop waiting but do NOT error: the audit keeps running on the server and
    // has usually produced a partial report already. Return the last status so
    // the caller can show what completed so far.
    if (Date.now() > deadline) return last;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
