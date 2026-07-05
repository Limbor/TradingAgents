import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  getConfig,
  createRun,
  healthCheck,
  listArtifacts,
  listHoldings,
  listRuns,
  listStrategyLessons,
  advanceTradingDay,

  updateConfig,
  type DailyPipelineFilters,
} from "../../api/client";
import { FilterPanel } from "../../components/FilterPanel";
import { KPICard, TimelineItem, HoldingsTable } from "../../components/Dashboard";
import { ReflectionQueueCard } from "../../components/Dashboard/ReflectionQueueCard";
import { buildPortfolioSummary, formatMoney } from "../../utils/portfolio";
import {
  Activity,
  Brain,
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
import type { ArtifactInfo } from "../../api/client";

export default function Dashboard() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [refreshingPrices, setRefreshingPrices] = useState(false);
  const [startingDailyReview, setStartingDailyReview] = useState(false);
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

  // Polling intervals are relaxed because Chat WS terminal events now
  // invalidate ["runs"]/["dashboard-artifacts"]/["holdings"] on demand.
  // staleTime prevents refetch storms when tabs re-mount or the window
  // regains focus while data is still fresh.
  const runsQuery = useQuery({
    queryKey: ["runs"],
    queryFn: () => listRuns(20),
    refetchInterval: 30000,
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });
  const holdingsQuery = useQuery({
    queryKey: ["holdings"],
    queryFn: listHoldings,
    refetchInterval: 60000,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: healthCheck,
    refetchInterval: 60000,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  const configQuery = useQuery({ queryKey: ["config"], queryFn: getConfig, staleTime: 60_000 });
  const reflectionQuery = useQuery({
    queryKey: ["reflections-summary"],
    queryFn: async () => {
      const res = await fetch("/api/v1/reflections/summary?lookback_days=30");
      if (!res.ok) return null;
      return res.json();
    },
    refetchInterval: 120000,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const artifactQuery = useQuery({
    queryKey: ["dashboard-artifacts"],
    queryFn: () => listArtifacts({ limit: 20 }),
    refetchInterval: 60000,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  const lessonsQuery = useQuery({
    queryKey: ["strategy-lessons"],
    queryFn: () => listStrategyLessons({ limit: 5 }),
    refetchInterval: 120000,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
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
  const signalArtifacts = (artifactQuery.data ?? []).filter((item) =>
    ["decision_pack", "signal_pack", "screening_report", "scanner_report"].includes(item.artifact_type)
  ).slice(0, 3);
  const riskArtifacts = (artifactQuery.data ?? []).filter((item) => item.artifact_type === "risk_report").slice(0, 3);
  const riskKpi = buildRiskKpi(riskArtifacts, portfolio.count);
  const dailyReviewDone = todayRuns.some((run) => run.skill_id === "daily_review" && run.status === "completed");
  const dailyReviewRunning = todayRuns.some((run) => run.skill_id === "daily_review" && ["pending", "running"].includes(run.status));

  const goChat = (prompt: string) => {
    navigate("/chat", { state: { prompt, autoSend: true } });
  };

  const refreshPrices = async () => {
    setRefreshingPrices(true);
    setRefreshFeedback(null);
    try {
      const result = await advanceTradingDay();
      await holdingsQuery.refetch();
      await queryClient.invalidateQueries({ queryKey: ["plans"] });
      await queryClient.invalidateQueries({ queryKey: ["reflection-cases", "pending"] });
      const failedText = result.refreshed_prices.failed.length
        ? `，${result.refreshed_prices.failed.length} 个失败`
        : "";
      const alertText = result.plan_alerts.length
        ? `；${result.plan_alerts.length} 个计划触发提醒`
        : "";
      const asof = result.temporal_context?.market_asof_date ?? "";
      setRefreshFeedback(`已进入交易日 ${asof}：更新 ${result.refreshed_prices.updated} 个收盘价${failedText}${alertText}`);
    } catch (exc) {
      setRefreshFeedback(exc instanceof Error ? exc.message : "刷新失败");
    } finally {
      setRefreshingPrices(false);
    }
  };

  const startDailyReview = async () => {
    setStartingDailyReview(true);
    try {
      const run = await createRun("daily_review", { daily_limit: 5, candidate_limit: 80 });
      await runsQuery.refetch();
      navigate(`/library?run_id=${run.id}`);
    } finally {
      setStartingDailyReview(false);
    }
  };

  const openRunDetail = (run: typeof runs[number]) => {
    if (run.status === "completed") {
      navigate(`/library?run_id=${run.id}`);
      return;
    }
    navigate(`/analysis/${run.id}`);
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
        <KPICard icon={WalletCards} label="持仓市值" value={formatMoney(portfolio.value)} sub={`${portfolio.count} 个持仓`} tone="teal" />
        <KPICard icon={Activity} label="浮动盈亏" value={formatMoney(portfolio.pnl)} sub={`${portfolio.pnl >= 0 ? "+" : ""}${portfolio.pnlPct.toFixed(2)}%`} tone={portfolio.pnl >= 0 ? "emerald" : "red"} />
        <KPICard icon={PieChart} label="集中度" value={`${portfolio.concentration.toFixed(1)}%`} sub={portfolio.topSymbol ? `最大 ${portfolio.topSymbol}` : "暂无持仓"} tone={portfolio.concentration > 45 ? "amber" : "teal"} />
        <KPICard icon={Sparkles} label="今日任务" value={String(todayRuns.length)} sub={`${todayRuns.filter((r) => r.status === "completed").length} 已完成`} tone="teal" />
        <KPICard icon={ShieldAlert} label="公告风险" value={riskKpi.value} sub={riskKpi.sub} tone={riskKpi.tone} />
      </section>

      <section className="rounded-lg border border-teal-500/20 bg-teal-500/5 p-4">
        <div className="flex flex-col justify-between gap-3 lg:flex-row lg:items-center">
          <div>
            <div className="flex items-center gap-2">
              <Brain className="h-4 w-4 text-teal-300" />
              <h3 className="text-sm font-semibold text-stone-100">收盘复盘工作流</h3>
              <span className={`rounded border px-2 py-0.5 text-xs ${dailyReviewDone ? "border-emerald-500/30 text-emerald-300" : dailyReviewRunning ? "border-amber-500/30 text-amber-300" : "border-stone-700 text-stone-400"}`}>
                {dailyReviewDone ? "今日已完成" : dailyReviewRunning ? "运行中" : "待执行"}
              </span>
            </div>
            <p className="mt-1 text-xs text-stone-400">
              刷新持仓收盘价 → 扫描持仓风险 → 因果反思 → 每日选股 → 生成次日计划
            </p>
          </div>
          <button
            onClick={startDailyReview}
            disabled={startingDailyReview || dailyReviewRunning}
            className="inline-flex items-center justify-center gap-2 rounded-lg border border-teal-500/40 bg-teal-500/10 px-4 py-2 text-sm font-semibold text-teal-100 transition hover:bg-teal-500/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Play className="h-4 w-4" />
            {startingDailyReview || dailyReviewRunning ? "复盘运行中" : "开始收盘复盘"}
          </button>
        </div>
      </section>

      {/* Holdings */}
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
              {refreshingPrices ? "刷新中" : "进入下一交易日"}
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
            <button onClick={() => navigate("/portfolio")} className="mt-2 text-sm text-teal-300 hover:text-teal-200">
              添加第一笔持仓
            </button>
          </div>
        ) : (
          <HoldingsTable holdings={holdings} totalValue={portfolio.value} onAnalyze={(symbol) => goChat(`帮我分析 ${symbol}`)} onManage={() => navigate("/portfolio")} />
        )}
      </section>

      {/* Lower grid: Timeline + Quick Actions + Reflection */}
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
                <TimelineItem key={run.id} run={run} onClick={() => openRunDetail(run)} />
              ))}
            </div>
          )}
        </section>

        <div className="flex flex-col gap-4">
          {/* Quick Actions */}
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
              <QuickActionBtn icon={ShieldAlert} label="风险扫描" desc="检查当前持仓的公告和风险事件" onClick={() => goChat("分析当前持仓风险")} />
              <QuickActionBtn icon={TrendingUp} label="分析个股" desc="深度 13-Agent 分析管道" onClick={() => navigate("/chat")} />
            </div>
          </section>

          <LatestSignalCard artifacts={signalArtifacts} onOpen={(item) => navigate(`/library?run_id=${item.run_id}`)} />
          <RiskTodoCard artifacts={riskArtifacts} onOpen={(item) => navigate(`/library?run_id=${item.run_id}`)} />
          <StrategyLessonsCard lessons={lessonsQuery.data ?? []} />

          {/* Reflection Summary Card */}
          <ReflectionSummaryCard data={reflectionQuery.data} />
          <ReflectionQueueCard />
        </div>
      </div>

      {/* Filter Modal */}
      {showFilters && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowFilters(false)}>
          <div className="relative mx-4 w-full max-w-3xl rounded-xl border border-stone-700 bg-stone-900 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-stone-800 px-6 py-4">
              <div>
                <h3 className="text-lg font-semibold text-stone-50">选股过滤器</h3>
                <p className="mt-0.5 text-xs text-stone-500">选择预设方案或自定义过滤条件</p>
              </div>
              <button onClick={() => setShowFilters(false)} className="rounded-lg p-1.5 text-stone-500 transition hover:bg-stone-800 hover:text-stone-300">
                <X className="h-5 w-5" />
              </button>
            </div>
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

/* ─── Sub-components (Dashboard-specific) ─── */

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

type KpiTone = "teal" | "emerald" | "amber" | "red";

function buildRiskKpi(artifacts: ArtifactInfo[], holdingCount: number): { value: string; sub: string; tone: KpiTone } {
  if (holdingCount === 0) {
    return { value: "-", sub: "暂无持仓", tone: "teal" };
  }

  const latest = artifacts[0];
  if (!latest) {
    return { value: "未扫描", sub: "运行持仓风险扫描", tone: "amber" };
  }

  const risks = parseRiskRows(latest.payload?.risks);
  const levels = risks.map((item) => normalizeRiskLevel(item.level));
  const highCount = levels.filter((level) => ["critical", "high", "red"].includes(level)).length;
  const actionableCount = levels.filter((level) => !["none", "green", "low", "unknown"].includes(level)).length;
  const scanDate = formatArtifactDate(latest.created_at);

  if (highCount > 0) {
    return { value: "高", sub: `高风险 ${highCount} 条 · ${scanDate}`, tone: "red" };
  }
  if (actionableCount > 0) {
    return { value: "中", sub: `风险待办 ${actionableCount} 条 · ${scanDate}`, tone: "amber" };
  }
  if (levels.length > 0 && levels.every((level) => level === "unknown")) {
    return { value: "未知", sub: `扫描结果不可判定 · ${scanDate}`, tone: "amber" };
  }
  return { value: "低", sub: `最近扫描 ${scanDate}`, tone: "emerald" };
}

function parseRiskRows(value: unknown): Array<{ level?: unknown }> {
  return Array.isArray(value) ? value.filter((item): item is { level?: unknown } => typeof item === "object" && item !== null) : [];
}

function normalizeRiskLevel(value: unknown): string {
  const text = String(value ?? "unknown").trim().toLowerCase();
  if (["critical", "high", "red"].includes(text)) return text;
  if (["orange", "medium", "moderate", "yellow", "amber"].includes(text)) return "medium";
  if (["green", "low", "none", "ok"].includes(text)) return text === "ok" ? "green" : text;
  return "unknown";
}

function formatArtifactDate(value: string | null | undefined): string {
  return value ? value.slice(0, 10) : "未知日期";
}

function LatestSignalCard({ artifacts, onOpen }: { artifacts: ArtifactInfo[]; onOpen: (item: ArtifactInfo) => void }) {
  return (
    <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
      <div className="mb-3 flex items-center gap-2">
        <TrendingUp className="h-4 w-4 text-teal-300" />
        <h3 className="text-sm font-semibold text-stone-100">最新信号</h3>
      </div>
      {artifacts.length === 0 ? (
        <p className="text-xs text-stone-500">暂无选股或扫描产物。</p>
      ) : (
        <div className="space-y-2">
          {artifacts.map((item) => (
            <button
              key={item.id}
              onClick={() => onOpen(item)}
              className="w-full rounded border border-stone-800 bg-stone-950 p-2 text-left transition hover:border-teal-500/40"
            >
              <div className="truncate text-xs font-medium text-stone-200">{item.title}</div>
              <div className="mt-1 line-clamp-2 text-xs text-stone-500">{item.summary || item.subtitle || item.artifact_type}</div>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function RiskTodoCard({ artifacts, onOpen }: { artifacts: ArtifactInfo[]; onOpen: (item: ArtifactInfo) => void }) {
  return (
    <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
      <div className="mb-3 flex items-center gap-2">
        <ShieldAlert className="h-4 w-4 text-amber-300" />
        <h3 className="text-sm font-semibold text-stone-100">风险待办</h3>
      </div>
      {artifacts.length === 0 ? (
        <p className="text-xs text-stone-500">暂无持仓风险报告。</p>
      ) : (
        <div className="space-y-2">
          {artifacts.map((item) => (
            <button
              key={item.id}
              onClick={() => onOpen(item)}
              className="w-full rounded border border-stone-800 bg-stone-950 p-2 text-left transition hover:border-amber-500/40"
            >
              <div className="truncate text-xs font-medium text-stone-200">{item.title}</div>
              <div className="mt-1 line-clamp-2 text-xs text-stone-500">{item.summary || "查看风险报告"}</div>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function StrategyLessonsCard({ lessons }: { lessons: Array<{ id: string; finding: string; confidence: string; suggested_adjustment?: string }> }) {
  return (
    <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
      <div className="mb-3 flex items-center gap-2">
        <Brain className="h-4 w-4 text-purple-300" />
        <h3 className="text-sm font-semibold text-stone-100">策略经验</h3>
      </div>
      {lessons.length === 0 ? (
        <p className="text-xs text-stone-500">暂无可回流的策略经验。系统只会沉淀可归因的信号时点误判。</p>
      ) : (
        <div className="space-y-2">
          {lessons.map((lesson) => (
            <div key={lesson.id} className="rounded border border-purple-500/20 bg-purple-500/5 p-2">
              <div className="mb-1 text-xs text-purple-300">{lesson.confidence}</div>
              <div className="line-clamp-3 text-xs text-stone-300">{lesson.finding}</div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function ReflectionSummaryCard({ data }: { data: { total: number; correct: number; accuracy: number } | null | undefined }) {
  const triggerReflection = async () => {
    try {
      await fetch("/api/v1/reflections/trigger", { method: "POST" });
    } catch {
      // silently ignore
    }
  };

  return (
    <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
      <div className="mb-3 flex items-center gap-2">
        <Brain className="h-4 w-4 text-purple-300" />
        <h3 className="text-sm font-semibold text-stone-100">反思摘要</h3>
      </div>
      {data && data.total > 0 ? (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs text-stone-400">30天正确率</span>
            <span className={`font-mono text-sm font-semibold ${data.accuracy >= 0.6 ? "text-emerald-300" : data.accuracy >= 0.4 ? "text-amber-300" : "text-red-300"}`}>
              {(data.accuracy * 100).toFixed(1)}%
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-xs text-stone-400">已反思决策</span>
            <span className="font-mono text-sm text-stone-200">{data.total}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-xs text-stone-400">正确/错误</span>
            <span className="font-mono text-xs text-stone-300">
              <span className="text-emerald-300">{data.correct}</span> / <span className="text-red-300">{data.total - data.correct}</span>
            </span>
          </div>
        </div>
      ) : (
        <p className="text-xs text-stone-500">暂无反思数据。完成选股后系统将自动回顾决策准确性。</p>
      )}
      <button
        onClick={triggerReflection}
        className="mt-3 w-full rounded border border-purple-500/30 bg-purple-500/10 px-3 py-1.5 text-xs font-medium text-purple-200 transition hover:border-purple-400/60"
      >
        手动触发反思
      </button>
    </section>
  );
}
