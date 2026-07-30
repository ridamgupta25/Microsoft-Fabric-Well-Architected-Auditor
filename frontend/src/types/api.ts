/**
 * The API contract, mirrored from the backend's Pydantic schemas.
 *
 * Kept hand-written rather than generated so the shapes stay readable and
 * documented. If they drift, `GET /openapi.json` is the source of truth — the
 * backend publishes a full schema and these types should be reconciled against
 * it whenever an endpoint changes.
 */

// -- shared -------------------------------------------------------------------

/** Every failed request returns this shape, whatever went wrong. */
export interface ApiError {
  detail: string;
  code: string;
  /** Ties the response to its server log lines. Quote it in bug reports. */
  correlation_id?: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export type CheckStatus = "PASS" | "PARTIAL" | "FAIL" | "N/A" | "INFO";

export type Severity = "Critical" | "High" | "Medium" | "Low" | "Informational";

// -- health -------------------------------------------------------------------

export interface Health {
  status: "ok" | "degraded";
  version: string;
  environment: string;
  checks_registered: number;
  timestamp: string;
}

// -- catalog ------------------------------------------------------------------

export interface CheckSpec {
  id: string;
  ref: string;
  title: string;
  pillar: string;
  scope: string;
  severity: Severity;
  layers: string[];
  requires: string[];
  weight: number;
  required: boolean;
  manual: boolean;
  automation: "automated" | "roadmap" | "manual";
  description: string;
}

export interface PillarInfo {
  name: string;
  checks: number;
}

export interface LayerInfo {
  name: string;
  checks: number;
}

export interface CatalogSummary {
  total: number;
  by_pillar: Record<string, number>;
  by_scope: Record<string, number>;
}

// -- workspaces ---------------------------------------------------------------

export interface Workspace {
  id: string;
  name: string;
  role: string;
  layer: string;
  items: number | null;
  pipelines: number | null;
}

// -- audit --------------------------------------------------------------------

export interface WorkspaceSelection {
  id: string;
  role?: string | null;
  name?: string | null;
}

export interface AuditRequest {
  project?: string | null;
  pillars: string[];
  workspaces: WorkspaceSelection[];
  /** Completed sign-in session id. Every audit reads the live tenant. */
  auth_session?: string | null;
}

export interface AuditAccepted {
  audit_id: string;
  status: JobStatus;
  submitted_at: string;
}

export interface CheckResult {
  check_id: string;
  ref: string;
  title: string;
  pillar: string;
  status: CheckStatus;
  /** 0-3, or null for informational results. */
  score: number | null;
  /** 0..1 for proportional checks. */
  coverage: number | null;
  evidence: string;
  recommendation: string;
  severity: Severity;
  workspace: string;
  workspace_role: string;
  layer: string;
  /** Object name; empty for workspace-level checks. */
  obj: string;
  scope: string;
  weight: number;
  scored: boolean;
  /** True for checks that apply to every project. */
  common: boolean;
}

export interface WorkspaceError {
  workspace: string;
  role: string;
  message: string;
  recommendation: string;
}

/** `pct: null` means *not assessed*, which is different from a score of zero. */
export interface PillarScore {
  pct: number | null;
  count: number;
}

export interface WorkspaceScore {
  role: string;
  layer: string;
  pct: number | null;
  count: number;
  by_pillar: Record<string, number | null>;
}

export interface AuditReport {
  audit_id?: string | null;
  /** True while the audit is still running — results so far only. */
  partial?: boolean;
  project_name: string;
  overall: number | null;
  by_pillar: Record<string, PillarScore>;
  by_workspace: Record<string, WorkspaceScore>;
  by_layer: Record<string, PillarScore>;
  /** Pillar -> layer -> score. The "inner pillar" view. */
  matrix: Record<string, Record<string, number | null>>;
  layers: string[];
  counts: Record<string, number>;
  total_scored: number;
  results: CheckResult[];
  errors: WorkspaceError[];
  files: Record<string, string>;
}

export interface AuditJob {
  audit_id: string;
  status: JobStatus;
  submitted_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  duration_seconds?: number | null;
  error?: string | null;
  report?: AuditReport | null;
}

export interface AuditJobSummary {
  audit_id: string;
  status: JobStatus;
  submitted_at: string;
  finished_at?: string | null;
  duration_seconds?: number | null;
  project_name?: string | null;
  overall?: number | null;
  workspaces: number;
}

// -- recommendations ----------------------------------------------------------

export interface Recommendation {
  check_id: string;
  ref: string;
  title: string;
  pillar: string;
  severity: Severity;
  workspace: string;
  obj: string;
  evidence: string;
  recommendation: string;
  /** "rule" for deterministic guidance; "ai" once generated. */
  source: string;
}

export interface RecommendationList {
  audit_id: string;
  total: number;
  ai_enabled: boolean;
  items: Recommendation[];
}

// -- auth ---------------------------------------------------------------------

export type SignInStatus = "pending" | "done" | "error";

export interface SessionResponse {
  session: string;
  message: string;
  status: SignInStatus;
  user_code?: string | null;
  verification_uri?: string | null;
  expires_in?: number | null;
}

export interface SessionStatusResponse {
  status: SignInStatus;
  error?: string | null;
}

/** The signed-in user's display identity. Never carries a token. */
export interface UserProfile {
  signed_in: boolean;
  name: string | null;
  username: string | null;
}

/** What the signed-in token could read, per workspace. */
export interface DiagnosticSample {
  name: string;
  items_status: number | null;
  items: number;
  pipelines: number;
  roles_status: number | null;
}

export interface Diagnostics {
  list_status: number | null;
  count: number;
  samples: DiagnosticSample[];
  error?: string | null;
}
