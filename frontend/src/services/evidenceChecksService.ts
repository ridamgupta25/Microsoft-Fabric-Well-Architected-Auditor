/**
 * Evidence checks — score manual checklist points from uploaded documents.
 *
 * The user uploads supporting documents (multipart), the backend drafts a 0-3
 * result per check with quoted evidence, and the user approves. An optional
 * per-request AI key (never stored) is sent as a JSON form field.
 */
import { apiClient } from "./apiClient";
import type {
  AiConfigInput,
  EvidenceChecksResult,
  EvidenceRequirementOut,
} from "@/types/api";

export async function getEvidenceCatalog(): Promise<EvidenceRequirementOut[]> {
  const { data } = await apiClient.get<EvidenceRequirementOut[]>("/evidence-checks/catalog");
  return data;
}

/** Remember approved manual checks so the next audit folds them in. */
export async function saveApprovedEvidence(
  rows: EvidenceChecksResult["ledger"],
  workspaceIds: string[],
): Promise<number> {
  const { data } = await apiClient.post<{ saved: number }>("/evidence-checks/save", {
    rows,
    workspace_ids: workspaceIds,
  });
  return data.saved;
}

export interface GitRepoInput {
  url: string;
  pat?: string;
  branch?: string;
}

export interface RunEvidenceChecksInput {
  files: File[];
  refs?: string[] | null;
  workspaceIds?: string[] | null;
  git?: GitRepoInput | null;
  manualScores?: Record<string, { score: number; note?: string }> | null;
  approvedRefs?: string[] | null;
  ai?: AiConfigInput | null;
}

export async function runEvidenceChecks(
  input: RunEvidenceChecksInput,
): Promise<EvidenceChecksResult> {
  const form = new FormData();
  for (const file of input.files) form.append("files", file);
  if (input.refs) form.append("refs", JSON.stringify(input.refs));
  if (input.workspaceIds) form.append("workspace_ids", JSON.stringify(input.workspaceIds));
  if (input.git?.url) {
    form.append("git_url", input.git.url);
    if (input.git.pat) form.append("git_pat", input.git.pat);
    if (input.git.branch) form.append("git_branch", input.git.branch);
  }
  if (input.manualScores) form.append("manual_scores", JSON.stringify(input.manualScores));
  if (input.approvedRefs) form.append("approved_refs", JSON.stringify(input.approvedRefs));
  if (input.ai) form.append("ai", JSON.stringify(input.ai));
  // Reading many documents through an LLM can be slow; override the default 60s.
  // The client defaults to application/json — override so the browser sends a
  // real multipart body (with boundary) instead of serialising the FormData.
  const { data } = await apiClient.post<EvidenceChecksResult>("/evidence-checks", form, {
    timeout: 300_000,
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}
