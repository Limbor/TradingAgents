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
  completed: "border-ui-success/30 bg-ui-success/10 text-ui-success",
  running: "border-ui-accent/30 bg-ui-accent/10 text-ui-accent",
  failed: "border-ui-danger/30 bg-ui-danger/10 text-ui-danger",
};

export function TimelineItem({ run, onClick }: TimelineItemProps) {
  const time = new Date(run.created_at).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
  const ticker = run.params?.ticker ?? run.params?.symbol;
  const tickerStr = ticker ? String(ticker) : null;
  const tickerName = typeof run.params?.ticker_name === "string" ? run.params.ticker_name : null;

  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-3 rounded-lg border border-ui-line bg-ui-subtle px-3 py-2.5 text-left transition hover:border-ui-accent/40"
    >
      <span className="w-11 shrink-0 text-xs font-mono text-ui-faint">{time}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm text-ui-ink truncate">
            {skillLabels[run.skill_id] ?? run.skill_id}
            {tickerStr && (
              <span className="ml-1 text-ui-accent">
                {tickerName && tickerName !== tickerStr ? (
                  <>
                    {tickerName}
                    <span className="ml-1 font-mono text-xs text-ui-accent/60">{tickerStr}</span>
                  </>
                ) : (
                  <span className="font-mono">{tickerStr}</span>
                )}
              </span>
            )}
          </span>
          <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs ${statusColors[run.status] ?? "text-ui-muted"}`}>
            {run.status}
          </span>
        </div>
      </div>
      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-ui-faint" />
    </button>
  );
}
