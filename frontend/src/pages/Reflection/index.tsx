import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Lightbulb, RefreshCw, Sparkles, Target } from "lucide-react";
import {
  getReflectionSummary,
  listReflectionCases,
  listStrategyLessons,
  minePatterns,
  triggerReflection,
  type ReflectionCase,
  type StrategyLesson,
} from "@/api/client";
import { queryKeys } from "@/api/queryKeys";
import { displayNameOf } from "@/components/common/StockName";

const LOOKBACKS = [7, 30, 90] as const;
const CASE_STATUSES = [
  { label: "待反思", value: "pending" },
  { label: "已完成", value: "completed" },
  { label: "全部", value: "" },
] as const;

export default function Reflection() {
  const qc = useQueryClient();
  const [lookback, setLookback] = useState<number>(30);
  const [activeOnly, setActiveOnly] = useState(true);
  const [caseStatus, setCaseStatus] = useState<string>("");
  const [busy, setBusy] = useState<null | "reflect" | "mine">(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const summary = useQuery({
    queryKey: queryKeys.reflectionSummaryFor(lookback),
    queryFn: () => getReflectionSummary(lookback),
  });
  const lessons = useQuery({
    queryKey: queryKeys.reflectionLessons(activeOnly),
    queryFn: () => listStrategyLessons({ active_only: activeOnly, limit: 50 }),
  });
  const cases = useQuery({
    queryKey: queryKeys.reflectionCases(caseStatus || "all"),
    queryFn: () => listReflectionCases({ status: caseStatus || undefined, limit: 60 }),
  });

  const runReflection = async () => {
    setBusy("reflect"); setError(null); setNotice(null);
    try {
      const res = await triggerReflection();
      setNotice(res.message || "反思批处理已在后台触发，完成后刷新查看。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "触发反思失败");
    } finally {
      setBusy(null);
    }
  };

  const runMining = async () => {
    setBusy("mine"); setError(null); setNotice(null);
    try {
      const res = await minePatterns();
      setNotice(res.message || "规律挖掘已在后台调度，完成后刷新经验列表。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "触发挖掘失败");
    } finally {
      setBusy(null);
    }
  };

  const refreshAll = () => {
    qc.invalidateQueries({ queryKey: ["reflection-lessons"] });
    qc.invalidateQueries({ queryKey: ["reflection-cases"] });
    qc.invalidateQueries({ queryKey: ["reflections-summary"] });
  };

  const s = summary.data;
  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5">
      <header className="flex items-start justify-between gap-4 border-b border-stone-800 pb-5">
        <div>
          <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-teal-300">
            <Brain className="h-3.5 w-3.5" /> Reflection Loop
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-stone-50">反思闭环评测</h2>
          <p className="mt-1 text-sm text-stone-500">
            决策到期后按真实收益复盘，将复现的规律沉淀为策略经验并注入后续复核。此处可查看准确率、挖掘出的经验与反思案例。
          </p>
        </div>
        <button
          onClick={refreshAll}
          className="flex shrink-0 items-center gap-2 rounded border border-stone-700 px-3 py-2 text-xs text-stone-300 transition hover:border-stone-600 hover:text-stone-100"
        >
          <RefreshCw className="h-4 w-4" /> 刷新
        </button>
      </header>

      {error && <div className="rounded border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">{error}</div>}
      {notice && <div className="rounded border border-teal-500/30 bg-teal-500/10 p-3 text-sm text-teal-100">{notice}</div>}

      {/* Accuracy KPIs */}
      <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-stone-100">方向性准确率</h3>
          <div className="flex gap-1.5">
            {LOOKBACKS.map((d) => (
              <button
                key={d}
                onClick={() => setLookback(d)}
                className={`rounded border px-2 py-1 text-xs transition ${
                  lookback === d
                    ? "border-teal-500/50 bg-teal-500/10 text-teal-200"
                    : "border-stone-700 text-stone-400 hover:border-stone-600 hover:text-stone-200"
                }`}
              >
                近 {d} 天
              </button>
            ))}
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-4">
          <Kpi label="反思样本" value={String(s?.total ?? 0)} />
          <Kpi label="判断正确" value={String(s?.correct ?? 0)} tone="emerald" />
          <Kpi label="判断错误" value={String(s?.incorrect ?? 0)} tone="red" />
          <Kpi
            label="准确率"
            value={s && s.total > 0 ? `${(s.accuracy * 100).toFixed(1)}%` : "样本不足"}
            tone="teal"
          />
        </div>
        <p className="mt-2 text-xs text-stone-600">
          准确率仅统计有明确对错的方向性决策（BUY/SELL 等）；中性观望的功过以“超额收益归因”体现在下方反思案例中。
        </p>
      </section>

      {/* Strategy lessons */}
      <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-stone-100">
            <Lightbulb className="h-4 w-4 text-purple-300" /> 策略经验库
            <span className="text-xs font-normal text-stone-500">
              {lessons.data ? `${lessons.data.length} 条` : ""}
            </span>
          </h3>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setActiveOnly((v) => !v)}
              className={`rounded border px-2 py-1 text-xs transition ${
                activeOnly
                  ? "border-teal-500/50 bg-teal-500/10 text-teal-200"
                  : "border-stone-700 text-stone-400 hover:border-stone-600"
              }`}
            >
              {activeOnly ? "仅活跃" : "含已停用"}
            </button>
            <button
              onClick={runMining}
              disabled={busy !== null}
              className="flex items-center gap-1.5 rounded border border-purple-500/40 px-2.5 py-1 text-xs text-purple-200 transition hover:bg-purple-500/10 disabled:opacity-40"
            >
              <Sparkles className="h-3.5 w-3.5" /> {busy === "mine" ? "挖掘中..." : "挖掘规律"}
            </button>
          </div>
        </div>
        {lessons.isLoading && <p className="text-sm text-stone-500">加载中...</p>}
        {!lessons.isLoading && (lessons.data?.length ?? 0) === 0 && (
          <p className="rounded border border-dashed border-stone-700 bg-stone-950 p-6 text-center text-sm text-stone-500">
            暂无{activeOnly ? "活跃" : ""}策略经验。样本积累到阈值后由每日 16:30 反思批处理自动挖掘，或点击“挖掘规律”手动触发。
          </p>
        )}
        <div className="grid gap-2 md:grid-cols-2">
          {(lessons.data ?? []).map((lesson) => (
            <LessonCard key={lesson.id} lesson={lesson} />
          ))}
        </div>
      </section>

      {/* Reflection cases */}
      <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-stone-100">
            <Target className="h-4 w-4 text-teal-300" /> 反思案例
            <span className="text-xs font-normal text-stone-500">
              {cases.data ? `${cases.data.length} 条` : ""}
            </span>
          </h3>
          <div className="flex items-center gap-2">
            <div className="flex gap-1.5">
              {CASE_STATUSES.map((item) => (
                <button
                  key={item.value || "all"}
                  onClick={() => setCaseStatus(item.value)}
                  className={`rounded border px-2 py-1 text-xs transition ${
                    caseStatus === item.value
                      ? "border-teal-500/50 bg-teal-500/10 text-teal-200"
                      : "border-stone-700 text-stone-400 hover:border-stone-600"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <button
              onClick={runReflection}
              disabled={busy !== null}
              className="flex items-center gap-1.5 rounded border border-teal-500/40 px-2.5 py-1 text-xs text-teal-200 transition hover:bg-teal-500/10 disabled:opacity-40"
            >
              <RefreshCw className="h-3.5 w-3.5" /> {busy === "reflect" ? "运行中..." : "运行反思"}
            </button>
          </div>
        </div>
        {cases.isLoading && <p className="text-sm text-stone-500">加载中...</p>}
        {!cases.isLoading && (cases.data?.length ?? 0) === 0 && (
          <p className="rounded border border-dashed border-stone-700 bg-stone-950 p-6 text-center text-sm text-stone-500">
            暂无反思案例。
          </p>
        )}
        <div className="space-y-2">
          {(cases.data ?? []).map((c) => (
            <CaseRow key={c.id} item={c} />
          ))}
        </div>
      </section>
    </div>
  );
}

function LessonCard({ lesson }: { lesson: StrategyLesson }) {
  const dimension = String((lesson.payload?.dimension as string) ?? "");
  const isNeutral = dimension.startsWith("neutral:");
  const distinctPeriods = Number(lesson.payload?.distinct_periods ?? 0);
  const avgExcess = lesson.payload?.avg_excess;
  const winRate = lesson.payload?.win_rate;
  return (
    <div className={`rounded-lg border p-3 ${lesson.active ? "border-stone-800 bg-stone-950" : "border-stone-800/60 bg-stone-950/40 opacity-70"}`}>
      <div className="mb-1.5 flex items-center gap-2">
        <ConfidenceBadge confidence={lesson.confidence} />
        <span className={`rounded px-1.5 py-0.5 text-[10px] ${isNeutral ? "border border-sky-500/30 text-sky-300" : "border border-amber-500/30 text-amber-300"}`}>
          {isNeutral ? "中性通道" : "方向通道"}
        </span>
        {lesson.scope !== "global" && (
          <span className="rounded border border-stone-700 px-1.5 py-0.5 text-[10px] text-stone-400">
            {lesson.scope}={lesson.target}
          </span>
        )}
        {!lesson.active && <span className="text-[10px] text-stone-600">已停用</span>}
        <span className="ml-auto text-[10px] text-stone-600">证据 {lesson.evidence_count}</span>
      </div>
      <p className="text-sm text-stone-200">{lesson.finding}</p>
      {lesson.suggested_adjustment && (
        <p className="mt-1 text-xs text-stone-400">建议：{lesson.suggested_adjustment}</p>
      )}
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-stone-500">
        {isNeutral && distinctPeriods > 0 && <span>跨 {distinctPeriods} 个 ISO 周</span>}
        {typeof avgExcess === "number" && <span>平均超额 {(avgExcess * 100).toFixed(2)}%</span>}
        {typeof winRate === "number" && <span>胜率 {(winRate * 100).toFixed(1)}%</span>}
      </div>
    </div>
  );
}

function CaseRow({ item }: { item: ReflectionCase }) {
  const attribution = String((item.attribution_payload?.attribution as string) ?? "");
  const excess =
    (item.attribution_payload?.excess_return as number | undefined) ??
    (item.outcome_payload?.excess_return as number | undefined);
  const original = String(
    (item.snapshot_payload?.final_decision as string) ??
      (item.snapshot_payload?.quant_decision as string) ??
      item.source_type
  );
  return (
    <div className="flex items-center justify-between gap-3 rounded border border-stone-800 bg-stone-950 px-3 py-2">
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-sm text-stone-200">
          {displayNameOf(item.name, item.symbol)}
          {item.name && item.name !== item.symbol && (
            <span className="font-mono text-[10px] text-stone-500">{item.symbol}</span>
          )}
          <AttributionBadge attribution={attribution} status={item.status} />
        </div>
        <div className="mt-0.5 text-[10px] text-stone-500">
          {item.signal_date} · {original} · {item.horizon_days} 日
          {item.status === "pending" && <span className="ml-1 text-amber-400/80">待反思</span>}
        </div>
      </div>
      {typeof excess === "number" && (
        <span className={`shrink-0 font-mono text-xs ${excess >= 0 ? "text-emerald-300" : "text-red-300"}`}>
          超额 {excess >= 0 ? "+" : ""}{(excess * 100).toFixed(2)}%
        </span>
      )}
    </div>
  );
}

function AttributionBadge({ attribution, status }: { attribution: string; status: string }) {
  if (!attribution) {
    if (status === "pending") return null;
    return <span className="rounded border border-stone-700 px-1.5 py-0.5 text-[10px] text-stone-400">已复盘</span>;
  }
  const map: Record<string, { label: string; cls: string }> = {
    missed_upside: { label: "机会错失", cls: "border-amber-500/30 text-amber-300" },
    validated_avoidance: { label: "规避有效", cls: "border-teal-500/30 text-teal-300" },
    win: { label: "判断正确", cls: "border-emerald-500/30 text-emerald-300" },
    loss: { label: "判断错误", cls: "border-red-500/30 text-red-300" },
  };
  const meta = map[attribution] ?? { label: attribution, cls: "border-stone-700 text-stone-400" };
  return <span className={`rounded border px-1.5 py-0.5 text-[10px] ${meta.cls}`}>{meta.label}</span>;
}

function ConfidenceBadge({ confidence }: { confidence: string }) {
  const cls =
    confidence === "high"
      ? "border-emerald-500/40 text-emerald-300"
      : confidence === "medium"
      ? "border-amber-500/40 text-amber-300"
      : "border-stone-600 text-stone-400";
  return <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase ${cls}`}>{confidence}</span>;
}

function Kpi({ label, value, tone = "stone" }: { label: string; value: string; tone?: "stone" | "teal" | "emerald" | "red" }) {
  const valueCls = {
    stone: "text-stone-100",
    teal: "text-teal-300",
    emerald: "text-emerald-300",
    red: "text-red-300",
  }[tone];
  return (
    <div className="rounded-lg border border-stone-800 bg-stone-950 p-3">
      <div className="text-xs text-stone-500">{label}</div>
      <div className={`mt-1 font-mono text-lg font-semibold ${valueCls}`}>{value}</div>
    </div>
  );
}
