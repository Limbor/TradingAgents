const API_BASE = "/api/v1";

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
  api_keys: Record<string, boolean>;
}

export interface UserProfile {
  investment_style: "short_term" | "medium_term" | "long_term";
  risk_tolerance: "low" | "moderate" | "high";
  sector_prefs: string[];
  updated_at: string | null;
}

export interface Holding {
  symbol: string;
  quantity: number;
  avg_cost: number;
  current_price: number | null;
  notes: string | null;
  updated_at: string;
}

export interface RefreshHoldingPricesResponse {
  updated: number;
  failed: Array<{ symbol: string; reason: string }>;
  holdings: Holding[];
}

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, options);
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

export async function listRuns(limit = 50): Promise<RunResponse[]> {
  return fetchJson(`${API_BASE}/runs?limit=${limit}`);
}

export async function getRun(runId: string): Promise<RunResponse> {
  return fetchJson(`${API_BASE}/runs/${runId}`);
}

export async function cancelRun(runId: string): Promise<void> {
  await fetch(`${API_BASE}/runs/${runId}`, { method: "DELETE" });
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

export async function getConfig(): Promise<ConfigResponse> {
  return fetchJson(`${API_BASE}/config`);
}

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

export async function upsertHolding(holding: Omit<Holding, "updated_at">): Promise<Holding> {
  return fetchJson(`${API_BASE}/holdings/${encodeURIComponent(holding.symbol)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(holding),
  });
}

export async function deleteHolding(symbol: string): Promise<void> {
  await fetch(`${API_BASE}/holdings/${encodeURIComponent(symbol)}`, { method: "DELETE" });
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
