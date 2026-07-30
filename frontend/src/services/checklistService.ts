/**
 * Checklist intake — assess a best-practice point against the tool.
 *
 * Token-free: the endpoint answers from the registered catalog (and an optional
 * model), never contacting Fabric, so this call always returns a result.
 */
import { apiClient } from "./apiClient";
import type { ChecklistAssessment } from "@/types/api";

export async function assessChecklistPoint(point: string): Promise<ChecklistAssessment> {
  const { data } = await apiClient.post<ChecklistAssessment>("/checklist/assess", { point });
  return data;
}
