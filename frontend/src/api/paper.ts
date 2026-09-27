import { fetchJson, paperApiBase } from "./client";

export interface PaperSession {
  session_id: string;
  mode: "paper";
  strategy: string;
  config_name: string;
  initial_cash: number;
  last_date: string | null;
  params: Record<string, unknown>;
}

export interface PaperAllocatorConfig {
  name: string;
  path: string;
  description: string;
  status: string;
  initial_cash: number;
  start_date: string;
  sleeves: string[];
}

export interface PaperPosition {
  name?: string;
  shares: number;
  avg_cost: number;
  last_price: number;
  value: number;
  day_pnl?: number | null;
}

export interface PaperStatus {
  session: PaperSession;
  state_fingerprint: string | null;
  snapshot: {
    as_of_date: string | null;
    equity: number;
    cash: number;
    positions: Record<string, PaperPosition>;
  } | null;
  trades_count: number;
  kind?: "composite";
  decision?: {
    date?: string | null;
    active_sleeve?: string;
    switched?: boolean;
    switch_count?: number;
    relative_return?: number | null;
    fast_relative_return?: number | null;
    fast_gate_triggered?: boolean;
    blocked_until_rebalance?: boolean;
    exposure_fallback_triggered?: boolean;
  } | null;
  readiness?: {
    status?: string;
    can_reference_plan?: boolean;
    reasons?: string[];
  } | null;
  freshness?: {
    latest_common_date?: string | null;
    active_plan_lag_days?: number | null;
    is_shadow_aligned?: boolean;
    is_active_plan_current?: boolean;
  } | null;
  summary?: {
    total_return?: number | null;
    cagr?: number | null;
    max_drawdown?: number | null;
    sharpe?: number | null;
    switch_count?: number;
  } | null;
  sleeves?: Record<string, {
    strategy?: string;
    config_name?: string;
    equity?: number | null;
    last_date?: string | null;
  }>;
  caveat?: string;
}

export interface PaperCurve {
  daily_records: Array<{ date: string; equity: number; cash: number }>;
  benchmark_curve: Array<{ date: string; equity: number }>;
}

export interface PaperTrade {
  trade_date: string;
  code: string;
  name?: string;
  side: string;
  shares: number;
  price: number;
  amount: number;
  note?: string;
}

export interface PaperPlan {
  signal_date: string;
  equity: number;
  reason?: string;
  active_sleeve?: string;
  mode?: string;
  items: Array<{
    code: string;
    name: string;
    action: string;
    reason?: string;
    est_shares?: number;
    diff_value: number;
  }>;
}

export interface PaperJob {
  job_id: string;
  state: "queued" | "running" | "success" | "error";
  progress: number;
  message: string;
  result: { ok: boolean; data?: unknown } | null;
}

const root = paperApiBase;
const sessionUrl = (id: string) => `${root}/sessions/${encodeURIComponent(id)}`;
const postJson = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const listPaperSessions = () => fetchJson<PaperSession[]>(`${root}/sessions`);
export const listPaperStrategies = () => fetchJson<Array<{ name: string }>>(`${root}/strategies`);
export const listPaperConfigs = () => fetchJson<Array<{ name: string }>>(`${root}/configs`);
export const listPaperAllocators = () => fetchJson<PaperAllocatorConfig[]>(`${root}/allocator-configs`);
export const createPaperSession = (body: Record<string, unknown>) =>
  fetchJson<{ session_id: string }>(`${root}/sessions`, postJson(body));
export const getPaperStatus = (id: string) => fetchJson<PaperStatus>(`${sessionUrl(id)}/status`);
export const getPaperCurve = (id: string) => fetchJson<PaperCurve>(`${sessionUrl(id)}/equity`);
export const getPaperTrades = (id: string) => fetchJson<PaperTrade[]>(`${sessionUrl(id)}/trades`);
export const getPaperPlan = (id: string) => fetchJson<PaperPlan | null>(`${sessionUrl(id)}/next-plan`);
export const advancePaper = (id: string, target_date: string, expected_state_fingerprint: string) =>
  fetchJson<{ job_id: string }>(`${sessionUrl(id)}/advance`, postJson({ target_date, expected_state_fingerprint }));
export const getPaperJob = (id: string) => fetchJson<PaperJob>(`${root}/jobs/${encodeURIComponent(id)}`);
