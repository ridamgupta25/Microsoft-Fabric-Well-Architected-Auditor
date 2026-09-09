/**
 * The check catalog and service health.
 *
 * These endpoints answer from registered metadata alone — no tenant, no
 * sign-in, no audit run — so they are safe to call on app load.
 */
import { apiClient } from "./apiClient";
import type {
  AdminCategoryInfo,
  CatalogSummary,
  CheckSpec,
  Health,
  LayerInfo,
  PillarInfo,
} from "@/types/api";

export async function getHealth(): Promise<Health> {
  const { data } = await apiClient.get<Health>("/health");
  return data;
}

export async function listPillars(): Promise<PillarInfo[]> {
  const { data } = await apiClient.get<PillarInfo[]>("/catalog/pillars");
  return data;
}

export async function listLayers(): Promise<LayerInfo[]> {
  const { data } = await apiClient.get<LayerInfo[]>("/catalog/layers");
  return data;
}

export async function listChecks(filters: {
  pillar?: string;
  layer?: string;
  scope?: string;
} = {}): Promise<CheckSpec[]> {
  const { data } = await apiClient.get<CheckSpec[]>("/catalog/checks", { params: filters });
  return data;
}

export async function describeCheck(checkId: string): Promise<CheckSpec> {
  const { data } = await apiClient.get<CheckSpec>(`/catalog/checks/${checkId}`);
  return data;
}

export async function getCatalogSummary(): Promise<CatalogSummary> {
  const { data } = await apiClient.get<CatalogSummary>("/catalog/summary");
  return data;
}

/**
 * The elevated-access families and how many checks each holds.
 *
 * Always returns every family, including those with no checks yet — the screen
 * shows an empty one as not-yet-available rather than hiding it, so a family
 * added later appears with no frontend change.
 */
export async function listAdminCategories(): Promise<AdminCategoryInfo[]> {
  const { data } = await apiClient.get<AdminCategoryInfo[]>("/catalog/admin-categories");
  return data;
}

export async function listAdminChecks(category?: string): Promise<CheckSpec[]> {
  const { data } = await apiClient.get<CheckSpec[]>("/catalog/admin-checks", {
    params: category ? { category } : {},
  });
  return data;
}
