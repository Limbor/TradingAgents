import { ArrowRight } from "lucide-react";
import type { RunResponse } from "@/api/client";

interface TimelineItemProps {
  run: RunResponse;
  onClick: () => void;
}

const skillLabels: Record<string, string> = {
  stock_analysis: "股票分析",
  daily_pipeline: "每日选股",
  market_scanner: "市场扫描",
  risk_monitor: "风险监控",
  portfolio_management: "持仓管理",
  daily_review: "收盘复盘",
};

const statusColors: Record<string, string> = {
  completed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  running: "border-teal-500/30 bg-teal-500/10 text-teal-300",
  failed: "border-red-500/30 bg-red-500/10 text-red-300",
};

export function TimelineItem({ run, onClick }: TimelineItemProps) {
  const time = new Date(run.created_at).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
  const ticker = run.params?.ticker ?? run.params?.symbol;
  const tickerStr = ticker ? String(ticker) : null;

  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-3 rounded-lg border border-stone-800 bg-stone-950 px-3 py-2.5 text-left transition hover:border-teal-500/40"
    >
      <span className="w-11 shrink-0 text-xs font-mono text-stone-500">{time}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm text-stone-100 truncate">
            {skillLabels[run.skill_id] ?? run.skill_id}
            {tickerStr && <span className="ml-1 font-mono text-teal-300">{tickerStr}</span>}
          </span>
          <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs ${statusColors[run.status] ?? "text-stone-400"}`}>
            {run.status}
          </span>
        </div>
      </div>
      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-stone-600" />
    </button>
  );
}
