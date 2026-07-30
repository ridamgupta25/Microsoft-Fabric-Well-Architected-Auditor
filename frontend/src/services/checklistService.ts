/**
 * Checklist intake — assess a best-practice point (or a whole file) against the tool.
 *
 * ``assessChecklistPoint`` is token-free: it answers from the registered catalog
 * (and an optional model), never contacting Fabric. ``runChecklistBatch`` assesses
 * a whole uploaded checklist and evaluates the covered checks over the offline
 * knowledge base, falling back to a live read only for a workspace with no
 * cached snapshot (and only when a session is supplied).
 */
import { apiClient } from "./apiClient";
import type {
  ChecklistAssessment,
  ChecklistBatchRequest,
  ChecklistBatchResult,
} from "@/types/api";

export async function assessChecklistPoint(point: string): Promise<ChecklistAssessment> {
  const { data } = await apiClient.post<ChecklistAssessment>("/checklist/assess", { point });
  return data;
}

export async function runChecklistBatch(
  request: ChecklistBatchRequest,
): Promise<ChecklistBatchResult> {
  const { data } = await apiClient.post<ChecklistBatchResult>("/checklist/batch", request);
  return data;
}
