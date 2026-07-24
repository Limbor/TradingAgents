import { authHeaders } from "./auth";
import { getBackendRuntime } from "./runtime";

// Origin of the backend. Empty in dev/web builds so requests stay relative
// (`/api/v1`) and flow through the Vite dev proxy or a same-origin deploy.
// Desktop builds receive an absolute per-launch origin from the Rust shell;
// the packaged frontend itself contains no fixed port.
const API_ORIGIN = (
  getBackendRuntime()?.api_origin ??
  (import.meta as unknown as { env?: Record<string, string | undefined> }).env
    ?.VITE_API_BASE_URL ??
  ""
).replace(/\/$/, "");
const API_BASE = `${API_ORIGIN}/api/v1`;

export interface SkillInfo {
  id: string;
  name: string;
  description: string;
  version: string;
  category: string;
  icon: string;
}

export interface RunResponse {
  id: string;
  skill_id: string;
  status: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  params: Record<string, unknown> | null;
}

export interface ReportInfo {
  id: string;
  run_id: string;
  ticker: string;
  ticker_name: string | null;
  rating: string | null;
  report_path: string | null;
  created_at: string;
}

export interface ReportDetail extends ReportInfo {
  content: string;
}

export interface ArtifactInfo {
  id: string;
  run_id: string;
  skill_id: string;
  artifact_type: string;
  title: string;
  subtitle: string | null;
  subject_type: string | null;
  subject_id: string | null;
  subject_name: string | null;
  status: string;
  summary: string | null;
  content_markdown: string | null;
  payload: Record<string, unknown>;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export interface StrategyLesson {
  id: string;
  lesson_type: string;
  scope: string;
  target: string;
  finding: string;
  suggested_adjustment: string;
  evidence_count: number;
  confidence: string;
  active: boolean;
  governance_status: "candidate" | "validated" | "approved" | "retired";
  expires_at: string | null;
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ReflectionCase {
  id: string;
  source_type: string;
  reflection_scope: string;
  eligible_for_strategy_learning: boolean;
  status: string;
  symbol: string;
  name: string | null;
  signal_date: string;
  horizon_days: number;
  due_date: string | null;
  source_run_id: string;
  source_artifact_id: string;
  snapshot_payload: Record<string, unknown>;
  outcome_payload: Record<string, unknown>;
  attribution_payload: Record<string, unknown>;
  lesson_payload: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ReflectionSummary {
  total: number;
  correct: number;
  incorrect: number;
  accuracy: number;
  lookback_days: number;
}

export interface ScorecardAggregate {
  count: number;
  directional_count: number;
  hit_rate: number | null;
  avg_return: number | null;
  avg_excess: number | null;
}

export interface ScorecardBucket extends ScorecardAggregate {
  bucket: string;
}

export interface ScorecardRankIC {
  value: number | null;
  n: number;
}

export interface AlphaSuggestion {
  suggested_alpha: number;
  static_alpha: number;
  alpha_data: number | null;
  data_weight: number;
  delta: number;
  quant_ic: number | null;
  llm_ic: number | null;
  n: number;
  applicable: boolean;
  reason: string;
  style: string | null;
}

export interface PredictionScorecard {
  available: boolean;
  reason: string | null;
  n_evaluated: number;
  min_samples: number;
  lookback_days: number;
  as_of: string;
  overall: ScorecardAggregate | null;
  rank_ic: Record<string, ScorecardRankIC>;
  fusion_comparison: Record<string, ScorecardBucket | null>;
  buckets: Record<string, ScorecardBucket[]>;
  horizon_distribution: Record<string, number>;
  alpha_suggestion: AlphaSuggestion | null;
}

export interface TradeCondition {
  kind?: string;
  description?: string;
  source?: string;
}

export interface Plan {
  id: string;
  symbol: string;
  name: string | null;
  entry_zone: number[];
  stop_loss: number | null;
  targets: number[];
  position_pct: number | null;
  conditions: TradeCondition[];
  rating: string | null;
  status: string;
  source: string;
  artifact_id: string;
  reflection_case_id: string;
  created_at: string;
  updated_at: string;
  triggered_at: string | null;
  trigger_reason: string | null;
  last_checked_trade_date: string | null;
  last_checked_at: string | null;
}

export interface ModelOption {
  label: string;
  value: string;
}

export interface ProviderDetail {
  id: string;
  name: string;
  quick_models: ModelOption[];
  deep_models: ModelOption[];
}

export interface DailyPipelineFilters {
  board_filter?: "" | "main_board" | "chinext_star";
  exclude_st?: boolean;
  exclude_suspended?: boolean;
  exclude_one_price_limit?: boolean;
  min_amount_20d?: number;
  min_price?: number;
  max_price?: number;
  min_market_cap?: number;
  max_market_cap?: number;
  min_listing_days?: number;
  max_pe?: number;
  max_pb?: number;
  max_turnover_rate?: number;
  include_industries?: string[];
  exclude_industries?: string[];
}

export interface ConfigResponse {
  llm_provider: string;
  deep_think_llm: string;
  quick_think_llm: string;
  output_language: string;
  max_debate_rounds: number;
  max_risk_discuss_rounds: number;
  checkpoint_enabled: boolean;
  backend_url: string | null;
  stockmanager_mcp_url: string | null;
  stockmanager_mcp_enabled: boolean;
  stockmanager_mcp_timeout: number;
  daily_pipeline_filters: DailyPipelineFilters;
  adaptive_alpha_enabled: boolean;
  api_keys: Record<string, boolean>;
}

export interface TradingTemporalContext {
  market: string;
  now: string;
  timezone: string;
  market_asof_date: string;
  latest_close_date: string;
  decision_target_date: string;
  info_cutoff: string;
  calendar_state: "trading_day" | "holiday";
  session_state: "before_close_data" | "after_close_data" | "non_trading";
  source: string;
  warnings: string[];
  data_policy: Record<string, string>;
}

export interface UserProfile {
  investment_style: "short_term" | "medium_term" | "long_term";
  risk_tolerance: "low" | "moderate" | "high";
  sector_prefs: string[];
  updated_at: string | null;
}

export interface Holding {
  symbol: string;
  name?: string | null;
  quantity: number;
  avg_cost: number;
  current_price: number | null;
  notes: string | null;
  updated_at: string;
  latest_analysis?: {
    artifact_id?: string | null;
    run_id?: string | null;
    date?: string | null;
    created_at?: string | null;
    rating?: string | null;
    summary?: string | null;
    title?: string | null;
  } | null;
}

export interface RefreshHoldingPricesResponse {
  updated: number;
  failed: Array<{ symbol: string; reason: string }>;
  holdings: Holding[];
}

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const headers = { ...authHeaders(), ...(options?.headers ?? {}) };
  const res = await fetch(url, { ...options, headers });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || res.statusText);
  }
  return res.json();
}

export async function listSkills(): Promise<SkillInfo[]> {
  return fetchJson(`${API_BASE}/skills`);
}

export async function createRun(
  skillId: string,
  params: Record<string, unknown>
): Promise<RunResponse> {
  return fetchJson(`${API_BASE}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ skill_id: skillId, params }),
  });
}

export async function listRuns(limit = 50, offset = 0): Promise<RunResponse[]> {
  return fetchJson(`${API_BASE}/runs?limit=${limit}&offset=${offset}`);
}

export async function getRun(runId: string): Promise<RunResponse> {
  return fetchJson(`${API_BASE}/runs/${runId}`);
}

export async function cancelRun(runId: string): Promise<void> {
  await fetch(`${API_BASE}/runs/${runId}`, { method: "DELETE", headers: authHeaders() });
}

export async function listReports(
  limit = 50,
  ticker?: string
): Promise<ReportInfo[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (ticker) params.set("ticker", ticker);
  return fetchJson(`${API_BASE}/reports?${params}`);
}

export async function getReport(reportId: string): Promise<ReportDetail> {
  return fetchJson(`${API_BASE}/reports/${reportId}`);
}

export async function listArtifacts(params: {
  limit?: number;
  offset?: number;
  skill_id?: string;
  artifact_type?: string;
  subject_type?: string;
  subject_id?: string;
  run_id?: string;
  q?: string;
} = {}): Promise<ArtifactInfo[]> {
  const search = new URLSearchParams({
    limit: String(params.limit ?? 50),
    offset: String(params.offset ?? 0),
  });
  for (const key of ["skill_id", "artifact_type", "subject_type", "subject_id", "run_id", "q"] as const) {
    const value = params[key];
    if (value) search.set(key, value);
  }
  return fetchJson(`${API_BASE}/artifacts?${search}`);
}

export interface ArtifactVersion {
  id: number;
  artifact_id: string;
  version: number;
  title: string | null;
  subtitle: string | null;
  status: string | null;
  summary: string | null;
  content_markdown: string | null;
  payload: Record<string, unknown>;
  saved_at: string;
}

export async function listArtifactVersions(artifactId: string): Promise<ArtifactVersion[]> {
  return fetchJson(`${API_BASE}/artifacts/${encodeURIComponent(artifactId)}/versions`);
}

export async function getArtifact(artifactId: string): Promise<ArtifactInfo> {
  return fetchJson(`${API_BASE}/artifacts/${artifactId}`);
}

export async function listRunArtifacts(runId: string): Promise<ArtifactInfo[]> {
  return fetchJson(`${API_BASE}/runs/${runId}/artifacts`);
}

export async function listStrategyLessons(params: {
  active_only?: boolean;
  lesson_type?: string;
  limit?: number;
} = {}): Promise<StrategyLesson[]> {
  const search = new URLSearchParams({
    active_only: String(params.active_only ?? true),
    limit: String(params.limit ?? 20),
  });
  if (params.lesson_type) search.set("lesson_type", params.lesson_type);
  return fetchJson(`${API_BASE}/strategy-lessons?${search}`);
}

export async function listReflectionCases(params: {
  status?: string;
  symbol?: string;
  reflection_scope?: string;
  eligible_only?: boolean;
  limit?: number;
} = {}): Promise<ReflectionCase[]> {
  const search = new URLSearchParams({ limit: String(params.limit ?? 50) });
  if (params.status) search.set("status", params.status);
  if (params.symbol) search.set("symbol", params.symbol);
  if (params.reflection_scope) search.set("reflection_scope", params.reflection_scope);
  if (params.eligible_only !== undefined) search.set("eligible_only", String(params.eligible_only));
  return fetchJson(`${API_BASE}/reflection-cases?${search}`);
}

export async function getReflectionSummary(lookbackDays = 30): Promise<ReflectionSummary> {
  return fetchJson(`${API_BASE}/reflections/summary?lookback_days=${lookbackDays}`);
}

export async function getPredictionScorecard(lookbackDays = 90): Promise<PredictionScorecard> {
  return fetchJson(`${API_BASE}/prediction-scorecard?lookback_days=${lookbackDays}`);
}

export async function triggerReflection(): Promise<{ status: string; message: string }> {
  return fetchJson(`${API_BASE}/reflections/trigger`, { method: "POST" });
}

export async function minePatterns(): Promise<{ status: string; message: string }> {
  return fetchJson(`${API_BASE}/reflections/mine-patterns`, { method: "POST" });
}

export async function deactivateLesson(
  lessonId: string
): Promise<{ status: string; lesson_id: string; message: string }> {
  return fetchJson(`${API_BASE}/strategy-lessons/${encodeURIComponent(lessonId)}/deactivate`, {
    method: "POST",
  });
}

export async function approveLesson(
  lessonId: string
): Promise<{ status: string; lesson_id: string; message: string }> {
  return fetchJson(`${API_BASE}/strategy-lessons/${encodeURIComponent(lessonId)}/approve`, {
    method: "POST",
  });
}

/** The reflection cases that produced a lesson (its supporting evidence). */
export async function listLessonCases(lessonId: string): Promise<ReflectionCase[]> {
  return fetchJson(`${API_BASE}/strategy-lessons/${encodeURIComponent(lessonId)}/cases`);
}

export async function saveCandidateAction(body: {
  action: "adopt" | "plan" | "executed" | "watch" | "observe" | "private" | "private_review" | "ignore" | "dismiss";
  symbol: string;
  name?: string;
  run_id?: string;
  artifact_id?: string;
  trade_date?: string;
  payload?: Record<string, unknown>;
}): Promise<{ status: string; case_id?: string | null; message: string }> {
  return fetchJson(`${API_BASE}/candidate-actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function createPlan(body: {
  symbol: string;
  name?: string;
  entry_zone?: number[];
  stop_loss?: number;
  targets?: number[];
  position_pct?: number;
  conditions?: TradeCondition[];
  rating?: string;
  status?: string;
  source?: string;
  artifact_id?: string;
  reflection_case_id?: string;
}): Promise<Plan> {
  return fetchJson(`${API_BASE}/plans`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function listPlans(params: {
  status?: string;
  symbol?: string;
  source?: string;
  limit?: number;
} = {}): Promise<Plan[]> {
  const search = new URLSearchParams({ limit: String(params.limit ?? 50) });
  if (params.status) search.set("status", params.status);
  if (params.symbol) search.set("symbol", params.symbol);
  if (params.source) search.set("source", params.source);
  return fetchJson(`${API_BASE}/plans?${search}`);
}

export async function updatePlan(
  planId: string,
  body: { status?: string; reflection_case_id?: string },
): Promise<Plan> {
  return fetchJson(`${API_BASE}/plans/${planId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deletePlan(planId: string): Promise<{ deleted: number }> {
  return fetchJson(`${API_BASE}/plans/${planId}`, { method: "DELETE" });
}

export interface PlanAlert {
  plan_id: string;
  symbol: string;
  name: string | null;
  reason: string;
  details: string[];
  price: number | null;
  trade_date: string | null;
  triggered_at: string;
}

export async function advanceTradingDay(): Promise<{
  refreshed_prices: {
    updated: number;
    failed: { symbol: string; reason: string }[];
    holdings: Record<string, unknown>[];
  };
  plan_alerts: PlanAlert[];
  temporal_context: TradingTemporalContext;
}> {
  return fetchJson(`${API_BASE}/portfolio/advance-trading-day`, { method: "POST" });
}

export async function getConfig(): Promise<ConfigResponse> {
  return fetchJson(`${API_BASE}/config`);
}

export async function getTradingTime(market = "cn_a"): Promise<TradingTemporalContext> {
  const params = new URLSearchParams({ market });
  return fetchJson(`${API_BASE}/trading-time?${params}`);
}

export interface DecisionAuditSummary {
  decision_count: number;
  open_count: number;
  realized_count: number;
  execution_count: number;
  linked_execution_count: number;
  execution_link_rate: number;
  execution_validation: { sample_count: number; win_rate: number; average_directional_return: number; statistically_usable: boolean };
  validation: {
    overall: { sample_count: number; win_rate: number; average_return: number; statistically_usable: boolean };
    by_decision: Record<string, { sample_count: number; win_rate: number; average_return: number; average_directional_return: number }>;
    strategy_claims_allowed: boolean;
    effectiveness_claim_allowed?: boolean;
    warnings: string[];
  };
}

export interface DecisionRecord {
  id: string; source_type: string; symbol: string; name: string | null;
  decision_date: string; decision: string; horizon_days: number;
  reference_price: number | null; status: string; reflection_case_id: string;
  execution_count: number; outcome_count: number; final_return: number | null;
  final_excess_return: number | null; payload: Record<string, unknown>;
}

export interface BacktestRun {
  id: string; job_id: string; strategy_type: string; status: string;
  start_date: string; end_date: string; config: Record<string, unknown>;
  result: Record<string, unknown>; error: string | null; created_at: string; updated_at: string;
}

export interface BacktestCatalog {
  strategies: { name: string; sha1: string }[];
  configs: { name: string; sha1: string }[];
}

export const getDecisionAuditSummary = (): Promise<DecisionAuditSummary> =>
  fetchJson(`${API_BASE}/decision-audit/summary`);

export const listDecisionRecords = (): Promise<DecisionRecord[]> =>
  fetchJson(`${API_BASE}/decision-audit/decisions?limit=100`);

export const evaluateDecisionAudit = (): Promise<Record<string, unknown>> =>
  fetchJson(`${API_BASE}/decision-audit/evaluate`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}),
  });

export const recordDecisionExecution = (body: {
  decision_id: string; symbol: string; action: string; quantity: number; price: number;
}): Promise<Record<string, unknown>> => fetchJson(`${API_BASE}/decision-audit/executions`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

export const listBacktests = (): Promise<BacktestRun[]> => fetchJson(`${API_BASE}/backtests`);
export const getBacktestCatalog = (): Promise<BacktestCatalog> => fetchJson(`${API_BASE}/backtests/catalog`);

export const createBacktest = (body: Record<string, unknown>): Promise<BacktestRun> =>
  fetchJson(`${API_BASE}/backtests`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });

export async function updateConfig(
  config: Partial<ConfigResponse>
): Promise<ConfigResponse> {
  return fetchJson(`${API_BASE}/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}

export async function getProfile(): Promise<UserProfile> {
  return fetchJson(`${API_BASE}/profile`);
}

export async function updateProfile(
  profile: Partial<UserProfile>
): Promise<UserProfile> {
  return fetchJson(`${API_BASE}/profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });
}

export async function listHoldings(): Promise<Holding[]> {
  return fetchJson(`${API_BASE}/holdings`);
}

export interface RiskEvent {
  id: string;
  symbol: string;
  name?: string | null;
  level: string;
  event_type: string;
  title: string;
  source: string;
  event_date?: string | null;
  status: "open" | "acknowledged" | "monitoring" | "resolved";
  first_seen_at: string;
  last_seen_at: string;
  resolved_at?: string | null;
  payload: Record<string, unknown>;
}

export async function listRiskEvents(status = "open"): Promise<RiskEvent[]> {
  return fetchJson(`${API_BASE}/risk-events?status=${encodeURIComponent(status)}`);
}

export async function updateRiskEventStatus(
  id: string,
  status: RiskEvent["status"],
): Promise<RiskEvent> {
  return fetchJson(`${API_BASE}/risk-events/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
}

export async function upsertHolding(holding: Omit<Holding, "updated_at">): Promise<Holding> {
  return fetchJson(`${API_BASE}/holdings/${encodeURIComponent(holding.symbol)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(holding),
  });
}

export interface AdjustPositionResult {
  symbol: string;
  action: "add" | "reduce";
  holding: Holding | null;
  realized_pnl: number | null;
  closed: boolean;
}

export async function adjustHolding(
  symbol: string,
  body: { action: "add" | "reduce"; quantity: number; price: number; decision_id?: string },
): Promise<AdjustPositionResult> {
  return fetchJson(`${API_BASE}/holdings/${encodeURIComponent(symbol)}/adjust`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deleteHolding(symbol: string): Promise<void> {
  await fetch(`${API_BASE}/holdings/${encodeURIComponent(symbol)}`, { method: "DELETE", headers: authHeaders() });
}

export async function refreshHoldingPrices(): Promise<RefreshHoldingPricesResponse> {
  return fetchJson(`${API_BASE}/portfolio/refresh-prices`, {
    method: "POST",
  });
}

export async function healthCheck(): Promise<Record<string, unknown>> {
  return fetchJson(`${API_BASE}/health`);
}

export async function listProviders(): Promise<ProviderDetail[]> {
  return fetchJson(`${API_BASE}/config/providers`);
}
