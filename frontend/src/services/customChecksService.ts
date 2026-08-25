/**
 * Custom checks — run plain-English checks through the pipeline.
 *
 * Token-free and offline: the backend evaluates the checks over the cached
 * knowledge base and returns a lifecycle ledger plus a rendered report. An
 * optional per-request AI key (never stored) unlocks the AI-only steps.
 */
import { apiClient } from "./apiClient";
import type {
  AiConfigInput,
  CustomChecksRequest,
  CustomChecksResult,
  VerifyAiResult,
} from "@/types/api";

export async function runCustomChecks(
  request: CustomChecksRequest,
): Promise<CustomChecksResult> {
  const { data } = await apiClient.post<CustomChecksResult>("/custom-checks", request);
  return data;
}

export async function verifyAi(ai: AiConfigInput): Promise<VerifyAiResult> {
  const { data } = await apiClient.post<VerifyAiResult>("/custom-checks/verify-ai", { ai });
  return data;
}
