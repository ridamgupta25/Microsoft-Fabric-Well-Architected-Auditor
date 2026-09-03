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

/** How a check's verdict is reached. */
export type Automation = "automated" | "roadmap" | "interactive" | "manual";

// -- custom checks ------------------------------------------------------------

/** A per-request AI key + model. Sent only with the run; never stored server-side. */
export interface AiConfigInput {
  provider: "openai" | "azure";
  api_key: string;
  model: string;
  base_url?: string | null;
  endpoint?: string | null;
  deployment?: string | null;
}

export interface CustomChecksRequest {
  prompts: string[];
  workspace_ids?: string[] | null;
  approved_check_ids?: string[] | null;
  ai?: AiConfigInput | null;
}

/** The 0-100 result a generated custom check produces when run over the KB. */
export interface CustomCheckEvaluation {
  status: string;
  score: number;
  findings: string[];
  recommendations: string[];
}

export interface GuardrailVerdictView {
  passed: boolean;
  reason?: string | null;
  matched_rule?: string | null;
  failed_validator?: string | null;
  layer?: string | null;
}

export interface RoutingResultView {
  is_duplicate: boolean;
  matched_default_id?: string | null;
  similarity_score?: number | null;
  stage?: string | null;
  reasoning?: string | null;
}

export interface KbUpdateView {
  attempt_count: number;
  status: string;
  apis_called: string[];
  fields_added: string[];
  diagnostic?: string | null;
  root_cause?: string | null;
  remediation?: string | null;
  provenance: Record<string, unknown>[];
}

export interface CodeGenView {
  attempts: number;
  status: string;
  stage_failed?: string | null;
  reason?: string | null;
}

/** One row of the lifecycle ledger. Extra node detail is present but optional. */
export interface CustomCheckRow {
  check_id: string;
  raw_prompt: string;
  lifecycle_status: string;
  feasibility?: string | null;
  approved?: boolean | null;
  guardrail?: GuardrailVerdictView | null;
  routing?: RoutingResultView | null;
  kb_update?: KbUpdateView | null;
  code_gen?: CodeGenView | null;
  generated_code?: string | null;
  /** Present for generated checks: the 0-100 evaluation over the KB. */
  evaluation?: CustomCheckEvaluation | null;
  [key: string]: unknown;
}

export interface CustomChecksResult {
  prompts: number;
  workspaces: number;
  summary: Record<string, number>;
  ledger: CustomCheckRow[];
  pending_review_ids: string[];
  report_markdown: string;
}

/** Result of testing an AI key. Never echoes the key. */
export interface VerifyAiResult {
  ok: boolean;
  message: string;
}

// -- health -------------------------------------------------------------------

export interface Health {
  status: "ok" | "degraded";
  version: string;
  environment: string;
  checks_registered: number;
  timestamp: string;
}

// -- catalog ------------------------------------------------------------------

/** One selectable answer for an interactive (self-assessed) check. */
export interface CheckOption {
  value: string;
  label: string;
  /** 0-3 the answer contributes; null for a not-applicable choice. */
  score: number | null;
  /** Recommendation shown when the choice does not fully pass. */
  guidance: string;
}

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
  automation: Automation;
  /** True when the reviewer answers this via a scored question. */
  interactive: boolean;
  /** The question shown for an interactive check. */
  question: string;
  /** The scored answers for an interactive check; empty otherwise. */
  options: CheckOption[];
  /** True once the check has completed Phase 1 validation; false while pending. */
  validated: boolean;
  description: string;
}

// -- checklist intake ---------------------------------------------------------

export interface CheckMatch {
  check_id: string;
  ref: string;
  title: string;
  pillar: string;
  scope: string;
  severity: Severity;
  automation: Automation;
  /** 0-1 similarity to the submitted point; higher is closer. */
  confidence: number;
  reason: string;
}

export interface CheckProposal {
  point: string;
  suggested_id: string;
  suggested_ref: string;
  pillar: string;
  scope: string;
  severity: Severity;
  requires: string[];
  title: string;
  rationale: string;
  code_skeleton: string;
  remediation_stub: string;
}

export interface ChecklistAssessment {
  point: string;
  status: "covered" | "not_covered" | "invalid";
  covered: boolean;
  /** Whether AI-authored advisory was available; false = deterministic text. */
  ai_enabled: boolean;
  matches: CheckMatch[];
  proposal: CheckProposal | null;
  advisory: string;
  next_steps: string[];
}

// -- checklist batch (a whole uploaded checklist) -----------------------------

export interface ChecklistEvaluation {
  workspace: string;
  /** 'kb' (offline snapshot), 'live', or 'none' (no data). */
  source: string;
  status: string;
  objects: number;
  counts: Record<string, number>;
  evidence: string;
  recommendation: string;
}

export interface ChecklistBatchItem {
  point: string;
  hint_pillar: string | null;
  hint_scope: string | null;
  notes: string | null;
  status: "covered" | "not_covered" | "invalid";
  covered: boolean;
  matches: CheckMatch[];
  proposal: CheckProposal | null;
  advisory: string;
  next_steps: string[];
  evaluated_check: string | null;
  evaluations: ChecklistEvaluation[];
}

export interface ChecklistBatchSummary {
  total_points: number;
  covered: number;
  not_covered: number;
  invalid: number;
  evaluated_points: number;
  workspaces: number;
  run_checks: boolean;
  verdicts: Record<string, number>;
}

export interface ChecklistWorkspace {
  id: string;
  name: string;
  layer?: string | null;
  items?: number | null;
  pipelines?: number | null;
}

export interface ChecklistBatchResult {
  summary: ChecklistBatchSummary;
  workspaces: ChecklistWorkspace[];
  items: ChecklistBatchItem[];
}

export interface ChecklistBatchRequest {
  content?: string;
  filename?: string;
  points?: string[];
  workspace_ids?: string[];
  run_checks?: boolean;
  auth_session?: string | null;
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
  /** Whether the saved snapshot was fully crawled. KB source only. */
  complete?: boolean | null;
  /** When the snapshot was captured (YYYYMMDD_HHMMSS). KB source only. */
  captured_at?: string | null;
}

/** Where a run's data comes from: the live tenant or a saved/uploaded KB. */
export type AuditSource = "live" | "kb";

/** The echo of one validated KB upload, ready to submit with a `kb` audit. */
export interface KBUploadResponse {
  workspace: Workspace;
  /** The normalized snapshot to pass back in `AuditRequest.snapshots`. */
  snapshot: Record<string, unknown>;
}

// -- audit --------------------------------------------------------------------

/** One interactive, self-assessed checklist point to answer during a run. */
export interface QuestionnaireItem {
  id: string;
  ref: string;
  title: string;
  pillar: string;
  scope: string;
  severity: Severity;
  layers: string[];
  question: string;
  options: CheckOption[];
  required: boolean;
  automation: Automation;
  description: string;
}

/** Sentinel answer value that skips an interactive check (records it as N/A). */
export const SKIP_ANSWER = "__skip__";

export interface AuditAnswersRequest {
  /** Interactive check id -> chosen option value (or SKIP_ANSWER). */
  answers: Record<string, string>;
}

export interface WorkspaceSelection {
  id: string;
  role?: string | null;
  name?: string | null;
  /** Project group name (cross-workspace). Absent for an isolated workspace. */
  group?: string | null;
  /** Environment position within its group: 1 = dev .. 10 = prod. */
  environment_level?: number | null;
}

export interface AuditRequest {
  project?: string | null;
  pillars: string[];
  workspaces: WorkspaceSelection[];
  /** Completed sign-in session id. Required only for a `live` audit. */
  auth_session?: string | null;
  /** Opt-in: weight each workspace's checks by its environment level (1..10). */
  weight_by_environment?: boolean;
  /** Path to external checks CSV (e.g., AdminChecks.csv). */
  external_checks_csv?: string | null;
  /** `live` reads the tenant; `kb` replays saved snapshots with no sign-in. */
  source?: AuditSource;
  /** Uploaded snapshots to audit, when `source` is `kb`. */
  snapshots?: Record<string, unknown>[];
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
  /** True once the check has completed Phase 1 validation; false while pending. */
  validated: boolean;
  /** Source: 'automated' or 'external' (from CSV). */
  source?: string;
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

export interface GroupMember {
  id: string;
  name?: string | null;
  role?: string | null;
  environment_level?: number | null;
}

export interface WorkspaceGroup {
  name: string;
  workspaces: GroupMember[];
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
  /** Project workspace groups (cross-workspace). Empty for isolated-only runs. */
  groups?: WorkspaceGroup[];
  /** True when the roll-ups were weighted by environment level. */
  weighted_by_environment?: boolean;
  errors: WorkspaceError[];
  files: Record<string, string>;
  /** Provenance of the run's data (live crawl, cache, or saved-KB replay). */
  kb?: KBProvenance;
  /** Non-deterministic (advisory) checks, scored and reported separately. */
  advisory?: AdvisorySection;
}

/** The non-deterministic checks, kept out of the deterministic scorecard. */
export interface AdvisorySection {
  overall: number | null;
  by_pillar: Record<string, PillarScore>;
  counts: Record<string, number>;
  total_scored: number;
  results: CheckResult[];
}

/** Where a completed run's data came from. */
export interface KBProvenance {
  source: AuditSource;
  served_from_cache: boolean;
  refreshing: boolean;
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
  /** Interactive checklist points to answer while the automated audit runs. */
  questionnaire: QuestionnaireItem[];
  /** True once the reviewer's questionnaire answers have been recorded. */
  answers_submitted: boolean;
  /**
   * Advisory judging runs after the audit, on request. `null`/absent means it
   * has never been asked for — which is what gates the button.
   */
  advisory_status?: JobStatus | null;
}

/** Which model to judge with. The key is used for one run and never stored. */
export interface AdvisoryRunRequest {
  provider: "azure" | "openai";
  api_key?: string | null;
  /** Azure only. */
  endpoint?: string | null;
  /** Azure only. */
  deployment?: string | null;
  /** OpenAI-compatible gateways only. */
  base_url?: string | null;
  /** OpenAI-compatible gateways only. */
  model?: string | null;
}

/** Counts and file paths from one advisory judging run. */
export interface AdvisorySummary {
  checks_total: number;
  checks_labelled: number;
  checks_judged: number;
  findings_judged: number;
  findings_left_to_rules: number;
  objects_labelled: number;
  objects_undetermined: number;
  findings_changed: number;
  findings_agreed: number;
  files: Record<string, string>;
}

export interface AdvisoryRun {
  audit_id: string;
  advisory_status?: JobStatus | null;
  advisory_error?: string | null;
  summary?: AdvisorySummary | null;
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

/** Which sign-in methods the server offers, so the UI shows the right one. */
export interface LoginConfig {
  /** True when the redirect Authorization Code flow is configured on the server. */
  redirect_enabled: boolean;
}

/** The Microsoft URL to redirect the browser to, and the CSRF state. */
export interface AuthorizeResponse {
  auth_url: string;
  state: string;
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
