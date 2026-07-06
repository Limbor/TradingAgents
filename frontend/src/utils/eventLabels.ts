/**
 * Unified event/agent stage label mapping.
 * Single source of truth for both Chat and Dashboard.
 */

import type { WSMessage } from "@/api/ws";
import type { ChatTaskStatus } from "@/stores/useChatStore";

export const AGENT_STAGE_LABELS: Record<string, string> = {
  "Market Analyst": "市场分析",
  "Sentiment Analyst": "情绪分析",
  "News Analyst": "新闻与公告分析",
  "Fundamentals Analyst": "基本面分析",
  "Bull Researcher": "多方观点",
  "Bear Researcher": "空方观点",
  "Research Manager": "研究经理裁决",
  Trader: "交易计划",
  "Aggressive Analyst": "激进风控观点",
  "Conservative Analyst": "保守风控观点",
  "Neutral Analyst": "中性风控观点",
  "Portfolio Manager": "组合经理决策",
};

export const SKILL_TITLES: Record<string, string> = {
  stock_analysis: "股票深度分析",
  market_scanner: "市场机会扫描",
  portfolio_management: "持仓管理",
  daily_pipeline: "每日选股管线",
  daily_review: "收盘复盘",
  risk_monitor: "持仓风险监控",
};

export function skillTitle(skillId?: string): string {
  return skillId ? SKILL_TITLES[skillId] ?? "交易任务" : "交易任务";
}

export function eventStepLabel(message: WSMessage): string {
  const typeLabels: Record<string, string> = {
    agent_status: "Agent 状态更新",
    tool_call: `调用数据工具：${toolLabel(String(message.payload.tool ?? "unknown"))}`,
    report_chunk: `生成 ${String(message.payload.section ?? "报告")} 片段`,
    skill_complete: "技能执行完成",
    report_complete: "报告汇总完成",
  };
  if (message.type === "agent_status") {
    const agent = String(message.payload.agent ?? "Agent");
    const label = AGENT_STAGE_LABELS[agent] ?? agent;
    const status = String(message.payload.status ?? "running");
    if (status === "completed") return `${label}完成`;
    if (status === "failed") return `${label}失败`;
    return `${label}进行中`;
  }
  return typeLabels[message.type] ?? message.type;
}

export function progressStepLabel(payload: Record<string, unknown>): string {
  const step = payload.step_label ?? payload.stepLabel;
  if (typeof step === "string" && step.trim()) return step;
  const stage = payload.stage_label ?? payload.stageLabel;
  const base = typeof stage === "string" && stage.trim() ? stage : "任务进度";
  const status = String(payload.status ?? "running");
  if (status === "completed") return `${base}完成`;
  if (status === "failed") return `${base}失败`;
  if (status === "queued") return `${base}排队中`;
  return `${base}进行中`;
}

export function progressStepDetail(payload: Record<string, unknown>): string | undefined {
  const detail = payload.detail;
  const progress = payload.progress_pct ?? payload.progressPct;
  const parts: string[] = [];
  if (typeof detail === "string" && detail.trim()) parts.push(detail);
  if (typeof progress === "number") parts.push(`${Math.round(progress)}%`);
  return parts.length ? parts.join(" · ") : undefined;
}

export function normalizeTaskStatus(status: unknown): ChatTaskStatus {
  if (status === "queued" || status === "completed" || status === "failed") return status;
  return "running";
}

export function formatParams(params: unknown): string | undefined {
  if (!params || typeof params !== "object") return undefined;
  return JSON.stringify(params);
}

export function formatPayloadBrief(payload: Record<string, unknown>): string | undefined {
  if ("agent" in payload && "status" in payload) return undefined;
  if ("skill_id" in payload) return undefined;
  if ("tool" in payload) {
    const args = payload.args;
    if (!args || typeof args !== "object") return String(payload.tool);
    const entries = Object.entries(args as Record<string, unknown>)
      .filter(([, value]) => value !== undefined && value !== null && value !== "")
      .slice(0, 5)
      .map(([key, value]) => `${key}=${String(value)}`);
    return entries.length ? entries.join(", ") : String(payload.tool);
  }
  const keys = Object.keys(payload);
  if (keys.length === 0) return undefined;
  return JSON.stringify(payload).slice(0, 240);
}

function toolLabel(tool: string): string {
  const normalized = tool.toLowerCase();
  const labels: Record<string, string> = {
    get_stock_data: "行情数据",
    get_verified_market_snapshot: "市场快照",
    get_market_structure_snapshot: "市场结构",
    get_indicators: "技术指标",
    get_theme_heat: "主题热度",
    get_news: "新闻",
    get_announcements: "公告",
    get_risk_announcements: "风险公告",
    get_fundamentals: "基本面",
    get_balance_sheet: "资产负债表",
    get_cashflow: "现金流",
    get_income_statement: "利润表",
    get_northbound_flow: "北向资金",
    get_institutional_flow: "机构资金流",
  };
  for (const [key, label] of Object.entries(labels)) {
    if (normalized.includes(key)) return label;
  }
  return tool;
}
