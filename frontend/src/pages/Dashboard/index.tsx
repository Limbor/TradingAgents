import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  getConfig,
  healthCheck,
  listHoldings,
  listRuns,
  refreshHoldingPrices,
  updateConfig,
  type DailyPipelineFilters,
  type Holding,
  type RunResponse,
} from "../../api/client";
import { FilterPanel } from "../../components/FilterPanel";
import {
  Activity,
  ArrowRight,
  Clock,
  Filter,
  PieChart,
  Play,
  RefreshCw,
  Rocket,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  WalletCards,
  X,
} from "lucide-react";

export default function Dashboard() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [refreshingPrices, setRefreshingPrices] = useState(false);
  const [refreshFeedback, setRefreshFeedback] = useState<string | null>(null);
  const [showFilters, setShowFilters] = useState(false);

  // Close modal on Escape key
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") setShowFilters(false);
    },
    []
  );
  useEffect(() => {
    if (showFilters) {
      document.addEventListener("keydown", handleKeyDown);
      return () => document.removeEventListener("keydown", handleKeyDown);
    }
  }, [showFilters, handleKeyDown]);

  const runsQuery = useQuery({
    queryKey: ["runs"],
    queryFn: () => listRuns(20),
    refetchInterval: 5000,
  });

  const holdingsQuery = useQuery({
    queryKey: ["holdings"],
    queryFn: listHoldings,
    refetchInterval: 10000,
  });

  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: healthCheck,
    refetchInterval: 15000,
  });

  const configQuery = useQuery({
    queryKey: ["config"],
    queryFn: getConfig,
  });

  const runs = runsQuery.data ?? [];
  const holdings = holdingsQuery.data ?? [];
  const portfolio = buildPortfolioSummary(holdings);
  const mcpStatus = healthQuery.data?.stockmanager_mcp as
    | { connected?: boolean; capability_flags?: Record<string, boolean> }
    | undefined;

  const todayRuns = runs.filter((run) => {
    const created = new Date(run.created_at);
    const today = new Date();
    return created.toDateString() === today.toDateString();
  });

  const goChat = (prompt: string) => {
    navigate("/chat", { state: { prompt, autoSend: true } });
  };

  const refreshPrices = async () => {
    setRefreshingPrices(true);
    setRefreshFeedback(null);
    try {
      const result = await refreshHoldingPrices();
      await holdingsQuery.refetch();
      const failedText = result.failed.length ? `，${result.failed.length} 个失败` : "";
      setRefreshFeedback(`已更新 ${result.updated} 个持仓收盘价${failedText}`);
    } catch (exc) {
      setRefreshFeedback(exc instanceof Error ? exc.message : "刷新价格失败");
    } finally {
      setRefreshingPrices(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5">
      {/* Header */}
      <div className="flex flex-col justify-between gap-4 border-b border-stone-800 pb-5 lg:flex-row lg:items-end">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-teal-300">
            Trading cockpit
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-stone-50">今日概览</h2>
          <p className="mt-1 max-w-2xl text-sm text-stone-400">
            持仓状态 · 今日动态 · 快捷操作
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-stone-800 bg-stone-900 px-3 py-2 text-xs">
          <ShieldCheck className={`h-3.5 w-3.5 ${mcpStatus?.connected ? "text-emerald-300" : "text-stone-500"}`} />
          <span className="text-stone-300">MCP: {mcpStatus?.connected ? "Online" : "Offline"}</span>
        </div>
      </div>

      {/* KPI Bar */}
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <KPICard
          icon={WalletCards}
          label="持仓市值"
          value={formatMoney(portfolio.value)}
          sub={`${portfolio.count} 个持仓`}
          tone="teal"
        />
        <KPICard
          icon={Activity}
          label="浮动盈亏"
          value={formatMoney(portfolio.pnl)}
          sub={`${portfolio.pnlPct >= 0 ? "+" : ""}${portfolio.pnlPct.toFixed(2)}%`}
          tone={portfolio.pnl >= 0 ? "emerald" : "red"}
        />
        <KPICard
          icon={PieChart}
          label="集中度"
          value={`${portfolio.concentration.toFixed(1)}%`}
          sub={portfolio.topSymbol ? `最大 ${portfolio.topSymbol}` : "暂无持仓"}
          tone={portfolio.concentration > 45 ? "amber" : "teal"}
        />
        <KPICard
          icon={Sparkles}
          label="今日任务"
          value={String(todayRuns.length)}
          sub={`${todayRuns.filter((r) => r.status === "completed").length} 已完成`}
          tone="teal"
        />
        <KPICard
          icon={ShieldCheck}
          label="风险等级"
          value={portfolio.count === 0 ? "-" : portfolio.concentration > 60 ? "高" : portfolio.concentration > 40 ? "中" : "低"}
          sub={mcpStatus?.capability_flags?.risk_announcement_available ? "风险公告可用" : "基础模式"}
          tone={portfolio.concentration > 60 ? "red" : portfolio.concentration > 40 ? "amber" : "emerald"}
        />
      </section>

      {/* Holdings detail comes first: this is the working surface users check most often. */}
      <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <WalletCards className="h-4 w-4 text-teal-300" />
            <div>
              <h3 className="text-sm font-semibold text-stone-100">持仓明细</h3>
              <p className="mt-1 text-xs text-stone-500">按市值排序，刷新后自动重算市值、盈亏和集中度</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {refreshFeedback && (
              <span className="rounded border border-stone-700 bg-stone-950 px-2 py-1 text-xs text-stone-400">
                {refreshFeedback}
              </span>
            )}
            <button
              onClick={refreshPrices}
              disabled={holdings.length === 0 || refreshingPrices}
              className="inline-flex items-center gap-2 rounded-lg border border-teal-500/30 bg-teal-500/10 px-3 py-2 text-sm font-medium text-teal-200 transition hover:border-teal-400/60 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${refreshingPrices ? "animate-spin" : ""}`} />
              {refreshingPrices ? "刷新中" : "刷新收盘价"}
            </button>
            <button
              onClick={() => navigate("/portfolio")}
              className="rounded-lg border border-stone-700 px-3 py-2 text-sm text-stone-300 transition hover:border-teal-500/50 hover:text-stone-100"
            >
              管理持仓
            </button>
          </div>
        </div>

        {holdings.length === 0 ? (
          <div className="rounded-lg border border-dashed border-stone-700 bg-stone-950 px-4 py-10 text-center">
            <WalletCards className="mx-auto h-8 w-8 text-stone-600" />
            <p className="mt-3 text-sm text-stone-400">暂无持仓记录</p>
            <button
              onClick={() => navigate("/portfolio")}
              className="mt-2 text-sm text-teal-300 hover:text-teal-200"
            >
              添加第一笔持仓
            </button>
          </div>
        ) : (
          <HoldingsTable
            holdings={holdings}
            totalValue={portfolio.value}
            onAnalyze={(symbol) => goChat(`帮我分析 ${symbol}`)}
            onManage={() => navigate("/portfolio")}
          />
        )}
      </section>

      {/* Lower grid: Timeline + Quick Actions */}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
          <div className="mb-4 flex items-center gap-2">
            <Clock className="h-4 w-4 text-teal-300" />
            <h3 className="text-sm font-semibold text-stone-100">今日动态</h3>
            <span className="text-xs text-stone-500">({todayRuns.length} 条)</span>
          </div>
          {todayRuns.length === 0 ? (
            <div className="py-8 text-center text-sm text-stone-500">
              <p>今日还没有任务记录</p>
              <p className="mt-1 text-xs">使用右侧快捷操作或前往 Chat 开始</p>
            </div>
          ) : (
            <div className="max-h-[420px] space-y-3 overflow-y-auto pr-1">
              {todayRuns.map((run) => (
                <TimelineItem key={run.id} run={run} onClick={() => navigate(`/analysis/${run.id}`)} />
              ))}
            </div>
          )}
        </section>

        <div className="flex flex-col gap-4">
          <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
            <div className="mb-3 flex items-center gap-2">
              <Rocket className="h-4 w-4 text-teal-300" />
              <h3 className="text-sm font-semibold text-stone-100">快捷操作</h3>
            </div>
            <div className="space-y-2">
              {/* 每日选股 + 过滤器 */}
              <div className="rounded-lg border border-stone-800 bg-stone-950">
                <div className="flex items-center gap-2 p-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-teal-500/30 bg-teal-500/10">
                    <Sparkles className="h-4 w-4 text-teal-300" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-stone-100">每日选股</div>
                    <div className="text-xs text-stone-500">DailyPipeline Top 5</div>
                  </div>
                  <button
                    onClick={() => setShowFilters(true)}
                    className="flex items-center gap-1 rounded border border-stone-700 px-2 py-1 text-xs text-stone-500 transition hover:border-stone-600 hover:text-stone-300"
                  >
                    <Filter className="h-3 w-3" />
                    过滤
                  </button>
                  <button
                    onClick={() => goChat("每日选股 top 5")}
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-teal-500/30 bg-teal-500/10 text-teal-300 transition hover:bg-teal-500/20"
                  >
                    <Play className="h-4 w-4" />
                  </button>
                </div>
              </div>
              <QuickActionBtn
                icon={ShieldAlert}
                label="风险扫描"
                desc="检查当前持仓的公告和风险事件"
                onClick={() => goChat("分析当前持仓风险")}
              />
              <QuickActionBtn
                icon={TrendingUp}
                label="分析个股"
                desc="深度 13-Agent 分析管道"
                onClick={() => navigate("/chat")}
              />
            </div>
          </section>
        </div>
      </div>

      {/* Filter Modal */}
      {showFilters && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => setShowFilters(false)}
        >
          <div
            className="relative mx-4 w-full max-w-3xl rounded-xl border border-stone-700 bg-stone-900 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal header */}
            <div className="flex items-center justify-between border-b border-stone-800 px-6 py-4">
              <div>
                <h3 className="text-lg font-semibold text-stone-50">选股过滤器</h3>
                <p className="mt-0.5 text-xs text-stone-500">选择预设方案或自定义过滤条件</p>
              </div>
              <button
                onClick={() => setShowFilters(false)}
                className="rounded-lg p-1.5 text-stone-500 transition hover:bg-stone-800 hover:text-stone-300"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            {/* Modal body */}
            <div className="max-h-[70vh] overflow-y-auto px-6 py-5">
              <FilterPanel
                filters={(configQuery.data?.daily_pipeline_filters ?? {}) as DailyPipelineFilters}
                onSave={async (filters) => {
                  await updateConfig({ daily_pipeline_filters: filters } as Parameters<typeof updateConfig>[0]);
                  queryClient.invalidateQueries({ queryKey: ["config"] });
                  setShowFilters(false);
                }}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─────────────── Sub-components ─────────────── */

function KPICard({
  icon: Icon,
  label,
  value,
  sub,
  tone = "teal",
}: {
  icon: typeof Activity;
  label: string;
  value: string;
  sub: string;
  tone?: "teal" | "emerald" | "amber" | "red";
}) {
  const tones = {
    teal: "text-teal-300",
    emerald: "text-emerald-300",
    amber: "text-amber-300",
    red: "text-red-300",
  };
  return (
    <div className="rounded-lg border border-stone-800 bg-stone-900 p-4">
      <div className={`mb-2 flex items-center gap-2 text-xs ${tones[tone]}`}>
        <Icon className="h-4 w-4" />
        <span>{label}</span>
      </div>
      <div className="font-mono text-xl font-semibold text-stone-50">{value}</div>
      <div className="mt-1 text-xs text-stone-500">{sub}</div>
    </div>
  );
}

function HoldingsTable({
  holdings,
  totalValue,
  onAnalyze,
  onManage,
}: {
  holdings: Holding[];
  totalValue: number;
  onAnalyze: (symbol: string) => void;
  onManage: () => void;
}) {
  const sorted = [...holdings].sort((a, b) => holdingMarketValue(b) - holdingMarketValue(a));
  return (
    <div className="overflow-x-auto rounded-lg border border-stone-800">
      <table className="w-full min-w-[860px] text-left text-sm">
        <thead className="bg-stone-950 text-xs uppercase text-stone-500">
          <tr>
            <th className="px-3 py-2">标的</th>
            <th className="px-3 py-2 text-right">数量</th>
            <th className="px-3 py-2 text-right">成本</th>
            <th className="px-3 py-2 text-right">现价</th>
            <th className="px-3 py-2 text-right">市值</th>
            <th className="px-3 py-2 text-right">盈亏</th>
            <th className="px-3 py-2 text-right">收益率</th>
            <th className="px-3 py-2 text-right">仓位</th>
            <th className="px-3 py-2">备注</th>
            <th className="px-3 py-2 text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((holding) => {
            const price = holding.current_price ?? holding.avg_cost;
            const value = holdingMarketValue(holding);
            const pnl = holdingPnl(holding);
            const pnlPct = holding.avg_cost ? ((price - holding.avg_cost) / holding.avg_cost) * 100 : 0;
            const weight = totalValue ? (value / totalValue) * 100 : 0;
            return (
              <tr key={holding.symbol} className="border-t border-stone-800">
                <td className="px-3 py-2">
                  <div className="font-mono font-semibold text-stone-100">{holding.symbol}</div>
                  <div className="mt-1 h-1.5 w-24 overflow-hidden rounded-full bg-stone-800">
                    <div
                      className={`h-full rounded-full ${weight > 50 ? "bg-amber-300" : "bg-teal-300"}`}
                      style={{ width: `${Math.min(weight, 100)}%` }}
                    />
                  </div>
                </td>
                <td className="px-3 py-2 text-right font-mono text-stone-300">{formatNumber(holding.quantity)}</td>
                <td className="px-3 py-2 text-right font-mono text-stone-300">{formatNumber(holding.avg_cost)}</td>
                <td className="px-3 py-2 text-right font-mono text-stone-300">{formatNumber(price)}</td>
                <td className="px-3 py-2 text-right font-mono text-stone-100">{formatMoney(value)}</td>
                <td className={`px-3 py-2 text-right font-mono ${pnl >= 0 ? "text-emerald-300" : "text-red-300"}`}>
                  {formatMoney(pnl)}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${pnlPct >= 0 ? "text-emerald-300" : "text-red-300"}`}>
                  {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
                </td>
                <td className="px-3 py-2 text-right font-mono text-stone-300">{weight.toFixed(1)}%</td>
                <td className="max-w-44 truncate px-3 py-2 text-stone-500">{holding.notes || "-"}</td>
                <td className="px-3 py-2">
                  <div className="flex justify-end gap-1.5">
                    <button
                      onClick={() => onAnalyze(holding.symbol)}
                      className="rounded border border-stone-700 px-2 py-1 text-xs text-stone-400 transition hover:border-teal-500/50 hover:text-teal-300"
                    >
                      分析
                    </button>
                    <button
                      onClick={onManage}
                      className="rounded border border-stone-700 px-2 py-1 text-xs text-stone-400 transition hover:border-teal-500/50 hover:text-stone-100"
                    >
                      编辑
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TimelineItem({ run, onClick }: { run: RunResponse; onClick: () => void }) {
  const time = new Date(run.created_at).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
  const skillLabels: Record<string, string> = {
    stock_analysis: "股票分析",
    daily_pipeline: "每日选股",
    market_scanner: "市场扫描",
    risk_monitor: "风险监控",
    portfolio_management: "持仓管理",
  };
  const statusColors: Record<string, string> = {
    completed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    running: "border-teal-500/30 bg-teal-500/10 text-teal-300",
    failed: "border-red-500/30 bg-red-500/10 text-red-300",
  };
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

function QuickActionBtn({
  icon: Icon,
  label,
  desc,
  onClick,
}: {
  icon: typeof Activity;
  label: string;
  desc: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-3 rounded-lg border border-stone-800 bg-stone-950 p-3 text-left transition hover:border-teal-500/50 hover:bg-stone-900"
    >
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-teal-500/30 bg-teal-500/10">
        <Icon className="h-4 w-4 text-teal-300" />
      </div>
      <div className="min-w-0">
        <div className="text-sm font-medium text-stone-100">{label}</div>
        <div className="text-xs text-stone-500">{desc}</div>
      </div>
      <Play className="h-4 w-4 shrink-0 text-stone-600" />
    </button>
  );
}

/* ─────────────── Utilities ─────────────── */

function buildPortfolioSummary(holdings: Holding[]) {
  let cost = 0;
  let value = 0;
  let topValue = 0;
  let topSymbol = "";
  for (const item of holdings) {
    const rowCost = item.quantity * item.avg_cost;
    const rowValue = item.quantity * (item.current_price ?? item.avg_cost);
    cost += rowCost;
    value += rowValue;
    if (rowValue > topValue) {
      topValue = rowValue;
      topSymbol = item.symbol;
    }
  }
  const pnl = value - cost;
  const pnlPct = cost ? (pnl / cost) * 100 : 0;
  const concentration = value ? (topValue / value) * 100 : 0;
  return { count: holdings.length, cost, value, pnl, pnlPct, concentration, topSymbol };
}

function holdingMarketValue(holding: Holding) {
  return holding.quantity * (holding.current_price ?? holding.avg_cost);
}

function holdingPnl(holding: Holding) {
  return ((holding.current_price ?? holding.avg_cost) - holding.avg_cost) * holding.quantity;
}

function formatMoney(value: number) {
  if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(2)}万`;
  return value.toFixed(0);
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 2,
  }).format(value);
}
