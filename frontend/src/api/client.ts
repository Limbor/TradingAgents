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

export interface ConfigResponse {
  llm_provider: string;
  deep_think_llm: string;
  quick_think_llm: string;
  output_language: string;
  max_debate_rounds: number;
  max_risk_discuss_rounds: number;
  checkpoint_enabled: boolean;
  backend_url: string | null;
  api_keys: Record<string, boolean>;
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

export async function healthCheck(): Promise<{ status: string }> {
  return fetchJson(`${API_BASE}/health`);
}

export async function listProviders(): Promise<ProviderDetail[]> {
  return fetchJson(`${API_BASE}/config/providers`);
}
