/**
 * Audit submission, polling, reports, and history.
 *
 * Audits are fire-and-poll: the API accepts the request and returns an id, and
 * the client polls until it reaches a terminal state. A tenant-wide run can take
 * minutes, which no single HTTP request should be holding open.
 */
import { apiClient, downloadUrl } from "./apiClient";
import type {
  AuditAccepted,
  AuditJob,
  AuditJobSummary,
  AuditReport,
  AuditRequest,
  CheckResult,
  Diagnostics,
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

export function reportDownloadUrl(auditId: string, kind: "markdown" | "excel"): string {
  return downloadUrl(`/reports/${auditId}/download/${kind}`);
}

const TERMINAL_STATUSES = new Set(["succeeded", "failed"]);

/**
 * Poll an audit until it finishes.
 *
 * @param onProgress Called on each poll so the UI can show live status.
 * @param signal Abort to stop polling when the user navigates away — otherwise
 *   the loop outlives the component that started it.
 */
export async function pollAudit(
  auditId: string,
  onProgress?: (job: AuditJob) => void,
  signal?: AbortSignal,
  intervalMs = 1000,
  timeoutMs = 600_000,
): Promise<AuditJob> {
  const deadline = Date.now() + timeoutMs;

  for (;;) {
    if (signal?.aborted) throw new DOMException("Polling aborted", "AbortError");

    const job = await getAudit(auditId);
    onProgress?.(job);
    if (TERMINAL_STATUSES.has(job.status)) return job;

    if (Date.now() > deadline) {
      throw new Error(`Audit ${auditId} did not finish within ${timeoutMs / 1000}s.`);
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
