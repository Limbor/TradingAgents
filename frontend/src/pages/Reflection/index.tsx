import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, ChevronDown, Lightbulb, RefreshCw, Sparkles, Target, BarChart3 } from "lucide-react";
import {
  approveLesson,
  deactivateLesson,
  getConfig,
  getPredictionScorecard,
  getReflectionSummary,
  listLessonCases,
  listReflectionCases,
  listStrategyLessons,
  minePatterns,
  triggerReflection,
  type AlphaSuggestion,
  type PredictionScorecard,
  type ReflectionCase,
  type ScorecardBucket,
  type StrategyLesson,
} from "@/api/client";
import { queryKeys } from "@/api/queryKeys";
import { displayNameOf } from "@/components/common/StockName";
import {
  alphaDeltaTone,
  attributionMeta,
  caseAttribution,
  caseExcess,
  caseOriginalDecision,
  confidenceCls,
  formatAlpha,
  formatIC,
  formatPct,
  fusionDelta,
  lessonMetrics,
  lessonTrend,
  orderedBuckets,
  sparklinePoints,
} from "./helpers";

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
  const scorecard = useQuery({
    queryKey: queryKeys.predictionScorecard(lookback),
    queryFn: () => getPredictionScorecard(lookback),
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
    qc.invalidateQueries({ queryKey: ["prediction-scorecard"] });
  };

  const handleDeactivate = async (id: string) => {
    setError(null); setNotice(null);
    try {
      const res = await deactivateLesson(id);
      if (res.status === "ok") {
        setNotice("经验已停用，后续复核不再注入。");
        qc.invalidateQueries({ queryKey: ["reflection-lessons"] });
      } else {
        setError("未找到该经验，可能已被移除。");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "停用经验失败");
    }
  };

  const handleApprove = async (id: string) => {
    setError(null); setNotice(null);
    try {
      const res = await approveLesson(id);
      if (res.status === "ok") {
        setNotice("经验已人工批准，后续分析可使用该规则。");
        qc.invalidateQueries({ queryKey: ["reflection-lessons"] });
      } else {
        setError("该经验不是可批准候选，或已被处理。");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "批准经验失败");
    }
  };

  const s = summary.data;
  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5">
      <header className="flex items-start justify-between gap-4 border-b border-ui-line pb-5">
        <div>
          <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-ui-accent">
            <Brain className="h-3.5 w-3.5" /> Reflection Loop
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-ui-ink">反思闭环评测</h2>
          <p className="mt-1 text-sm text-ui-faint">
            决策到期后按真实收益复盘；挖掘规律先进入候选区，只有人工批准后才会注入后续复核。
          </p>
        </div>
        <button
          onClick={refreshAll}
          className="flex shrink-0 items-center gap-2 rounded border border-ui-strong px-3 py-2 text-xs text-ui-body transition hover:border-ui-strong hover:text-ui-ink"
        >
          <RefreshCw className="h-4 w-4" /> 刷新
        </button>
      </header>

      {error && <div className="rounded border border-ui-danger/30 bg-ui-danger/10 p-3 text-sm text-ui-danger">{error}</div>}
      {notice && <div className="rounded border border-ui-accent/30 bg-ui-accent/10 p-3 text-sm text-ui-accent">{notice}</div>}

      {/* Accuracy KPIs */}
      <section className="rounded-lg border border-ui-line bg-ui-panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-ui-ink">方向性准确率</h3>
          <div className="flex gap-1.5">
            {LOOKBACKS.map((d) => (
              <button
                key={d}
                onClick={() => setLookback(d)}
                className={`rounded border px-2 py-1 text-xs transition ${
                  lookback === d
                    ? "border-ui-accent/50 bg-ui-accent/10 text-ui-accent"
                    : "border-ui-strong text-ui-muted hover:border-ui-strong hover:text-ui-body"
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
        <p className="mt-2 text-xs text-ui-faint">
          准确率仅统计有明确对错的方向性决策（BUY/SELL 等）；中性观望的功过以“超额收益归因”体现在下方反思案例中。
        </p>
      </section>

      {/* Prediction-quality scorecard (read-only measurement) */}
      <ScorecardSection query={scorecard} lookback={lookback} />

      {/* Strategy lessons */}
      <section className="rounded-lg border border-ui-line bg-ui-panel p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-ui-ink">
            <Lightbulb className="h-4 w-4 text-purple-300" /> 策略经验库
            <span className="text-xs font-normal text-ui-faint">
              {lessons.data ? `${lessons.data.length} 条` : ""}
            </span>
          </h3>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setActiveOnly((v) => !v)}
              className={`rounded border px-2 py-1 text-xs transition ${
                activeOnly
                  ? "border-ui-accent/50 bg-ui-accent/10 text-ui-accent"
                  : "border-ui-strong text-ui-muted hover:border-ui-strong"
              }`}
            >
              {activeOnly ? "仅已批准" : "含候选/退役"}
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
        {lessons.isLoading && <p className="text-sm text-ui-faint">加载中...</p>}
        {!lessons.isLoading && (lessons.data?.length ?? 0) === 0 && (
          <p className="rounded border border-dashed border-ui-strong bg-ui-subtle p-6 text-center text-sm text-ui-faint">
            暂无{activeOnly ? "已批准" : ""}策略经验。挖掘结果先进入候选区，经统计检验和人工批准后才会影响后续分析。
          </p>
        )}
        <div className="grid gap-2 md:grid-cols-2">
          {(lessons.data ?? []).map((lesson) => (
            <LessonCard key={lesson.id} lesson={lesson} onDeactivate={handleDeactivate} onApprove={handleApprove} />
          ))}
        </div>
      </section>

      {/* Reflection cases */}
      <section className="rounded-lg border border-ui-line bg-ui-panel p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-ui-ink">
            <Target className="h-4 w-4 text-ui-accent" /> 反思案例
            <span className="text-xs font-normal text-ui-faint">
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
                      ? "border-ui-accent/50 bg-ui-accent/10 text-ui-accent"
                      : "border-ui-strong text-ui-muted hover:border-ui-strong"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <button
              onClick={runReflection}
              disabled={busy !== null}
              className="flex items-center gap-1.5 rounded border border-ui-accent/40 px-2.5 py-1 text-xs text-ui-accent transition hover:bg-ui-accent/10 disabled:opacity-40"
            >
              <RefreshCw className="h-3.5 w-3.5" /> {busy === "reflect" ? "运行中..." : "运行反思"}
            </button>
          </div>
        </div>
        {cases.isLoading && <p className="text-sm text-ui-faint">加载中...</p>}
        {!cases.isLoading && (cases.data?.length ?? 0) === 0 && (
          <p className="rounded border border-dashed border-ui-strong bg-ui-subtle p-6 text-center text-sm text-ui-faint">
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

function LessonCard({
  lesson,
  onDeactivate,
  onApprove,
}: {
  lesson: StrategyLesson;
  onDeactivate: (id: string) => void | Promise<void>;
  onApprove: (id: string) => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [retiring, setRetiring] = useState(false);
  const [approving, setApproving] = useState(false);
  const m = lessonMetrics(lesson);
  const trend = lessonTrend(lesson);
  const trendPath = sparklinePoints(trend.values);
  const lessonCases = useQuery({
    queryKey: ["lesson-cases", lesson.id],
    queryFn: () => listLessonCases(lesson.id),
    enabled: open,
  });

  const retire = async () => {
    setRetiring(true);
    try {
      await onDeactivate(lesson.id);
    } finally {
      setRetiring(false);
    }
  };

  const approve = async () => {
    setApproving(true);
    try {
      await onApprove(lesson.id);
    } finally {
      setApproving(false);
    }
  };

  return (
    <div className={`rounded-lg border p-3 ${lesson.active ? "border-ui-line bg-ui-subtle" : "border-ui-line/60 bg-ui-subtle/40 opacity-70"}`}>
      <div className="mb-1.5 flex items-center gap-2">
        <ConfidenceBadge confidence={lesson.confidence} />
        <span className={`rounded px-1.5 py-0.5 text-[10px] ${m.isNeutral ? "border border-ui-info/30 text-ui-info" : "border border-ui-warning/30 text-ui-warning"}`}>
          {m.isNeutral ? "中性通道" : "方向通道"}
        </span>
        {lesson.scope !== "global" && (
          <span className="rounded border border-ui-strong px-1.5 py-0.5 text-[10px] text-ui-muted">
            {lesson.scope}={lesson.target}
          </span>
        )}
        <span className="text-[10px] text-ui-faint">
          {lesson.governance_status === "approved" ? "已批准" : lesson.governance_status === "validated" ? "统计已验证·待批准" : lesson.governance_status === "candidate" ? "候选·待验证/批准" : "已退役"}
        </span>
        <span className="ml-auto text-[10px] text-ui-faint">证据 {lesson.evidence_count}</span>
      </div>
      <p className="text-sm text-ui-body">{lesson.finding}</p>
      {lesson.suggested_adjustment && (
        <p className="mt-1 text-xs text-ui-muted">建议：{lesson.suggested_adjustment}</p>
      )}
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-ui-faint">
        {m.isNeutral && m.distinctPeriods > 0 && <span>跨 {m.distinctPeriods} 个 ISO 周</span>}
        {m.avgExcess !== null && <span>平均超额 {formatPct(m.avgExcess, 2, true)}</span>}
        {m.winRate !== null && <span>胜率 {formatPct(m.winRate, 1)}</span>}
      </div>

      <div className="mt-2 flex items-center gap-3 border-t border-ui-line/70 pt-2">
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1 text-[10px] text-ui-faint transition hover:text-ui-body"
        >
          <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
          {open ? "收起详情" : "展开详情"}
        </button>
        {lesson.active && (
          <button
            onClick={retire}
            disabled={retiring}
            className="ml-auto rounded border border-ui-strong px-2 py-0.5 text-[10px] text-ui-muted transition hover:border-ui-danger/40 hover:text-ui-danger disabled:opacity-40"
          >
            {retiring ? "停用中..." : "停用"}
          </button>
        )}
        {!lesson.active && ["candidate", "validated"].includes(lesson.governance_status) && (
          <button
            onClick={approve}
            disabled={approving}
            className="ml-auto rounded border border-ui-accent px-2 py-0.5 text-[10px] text-ui-accent transition hover:border-ui-accent disabled:opacity-40"
          >
            {approving ? "批准中..." : "人工批准"}
          </button>
        )}
      </div>

      {open && (
        <div className="mt-2 space-y-3">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px] text-ui-faint">
            <Detail label="类型" value={lesson.lesson_type} />
            <Detail label="治理状态" value={lesson.governance_status} />
            <Detail label="样本量" value={m.sampleSize !== null ? String(m.sampleSize) : "—"} />
            <Detail label="一致率" value={formatPct(m.consistency, 1)} />
            <Detail label="平均超额" value={formatPct(m.avgExcess, 2, true)} />
            <Detail label="胜率" value={formatPct(m.winRate, 1)} />
            <Detail label="跨周数" value={m.distinctPeriods > 0 ? String(m.distinctPeriods) : "—"} />
            {lesson.expires_at && <Detail label="失效" value={lesson.expires_at.slice(0, 10)} />}
            <Detail label="更新" value={lesson.updated_at.slice(0, 10)} />
          </dl>

          {/* Metric trend across mining days (miner persists a history series). */}
          <div>
            <p className="mb-1 text-[10px] text-ui-faint">{trend.label}趋势（按挖掘日）</p>
            {trendPath ? (
              <div className="flex items-center gap-2">
                <svg viewBox="0 0 96 24" className="h-6 w-24 overflow-visible" preserveAspectRatio="none">
                  <polyline
                    points={trendPath}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1.5}
                    className={m.isNeutral ? "text-ui-info" : "text-ui-warning"}
                  />
                </svg>
                <span className="font-mono text-[10px] text-ui-faint">
                  {trend.values
                    .filter((v): v is number => v !== null)
                    .map((v) => formatPct(v, m.isNeutral ? 1 : 0, trend.signed))
                    .join(" → ")}
                </span>
              </div>
            ) : (
              <p className="text-[10px] text-ui-faint">样本仅一期，暂无趋势。</p>
            )}
          </div>

          {/* Supporting evidence: the reflection cases behind this lesson. */}
          <div>
            <p className="mb-1 text-[10px] text-ui-faint">支撑证据</p>
            {lessonCases.isLoading && <p className="text-[10px] text-ui-faint">加载中...</p>}
            {!lessonCases.isLoading && (lessonCases.data?.length ?? 0) === 0 && (
              <p className="text-[10px] text-ui-faint">暂无关联案例（该经验早于证据追踪，或案例已清理）。</p>
            )}
            <div className="space-y-1.5">
              {(lessonCases.data ?? []).map((c) => (
                <CaseRow key={c.id} item={c} />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="text-ui-faint">{label}</dt>
      <dd className="font-mono text-ui-muted">{value}</dd>
    </div>
  );
}

function CaseRow({ item }: { item: ReflectionCase }) {
  const attribution = caseAttribution(item);
  const excess = caseExcess(item);
  const original = caseOriginalDecision(item);
  return (
    <div className="flex items-center justify-between gap-3 rounded border border-ui-line bg-ui-subtle px-3 py-2">
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-sm text-ui-body">
          {displayNameOf(item.name, item.symbol)}
          {item.name && item.name !== item.symbol && (
            <span className="font-mono text-[10px] text-ui-faint">{item.symbol}</span>
          )}
          <AttributionBadge attribution={attribution} status={item.status} />
        </div>
        <div className="mt-0.5 text-[10px] text-ui-faint">
          {item.signal_date} · {original} · {item.horizon_days} 日
          {item.status === "pending" && <span className="ml-1 text-ui-warning/80">待反思</span>}
        </div>
      </div>
      {excess !== null && (
        <span className={`shrink-0 font-mono text-xs ${excess >= 0 ? "text-ui-success" : "text-ui-danger"}`}>
          超额 {formatPct(excess, 2, true)}
        </span>
      )}
    </div>
  );
}

function AttributionBadge({ attribution, status }: { attribution: string; status: string }) {
  if (!attribution) {
    if (status === "pending") return null;
    return <span className="rounded border border-ui-strong px-1.5 py-0.5 text-[10px] text-ui-muted">已复盘</span>;
  }
  const meta = attributionMeta(attribution);
  return <span className={`rounded border px-1.5 py-0.5 text-[10px] ${meta.cls}`}>{meta.label}</span>;
}

function ConfidenceBadge({ confidence }: { confidence: string }) {
  return <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase ${confidenceCls(confidence)}`}>{confidence}</span>;
}

function ScorecardSection({
  query,
  lookback,
}: {
  query: { data?: PredictionScorecard; isLoading: boolean };
  lookback: number;
}) {
  const data = query.data;
  return (
    <section className="rounded-lg border border-ui-line bg-ui-panel p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-ui-ink">
          <BarChart3 className="h-4 w-4 text-ui-info" /> 预测质量看板
          <span className="text-xs font-normal text-ui-faint">只读度量 · 近 {lookback} 天</span>
        </h3>
        {data?.available && (
          <span className="text-[10px] text-ui-faint">
            {data.n_evaluated} 条已评估 · as of {data.as_of}
          </span>
        )}
      </div>

      {query.isLoading && <p className="text-sm text-ui-faint">加载中...</p>}

      {!query.isLoading && data && !data.available && (
        <p className="rounded border border-dashed border-ui-strong bg-ui-subtle p-6 text-center text-sm text-ui-faint">
          {data.reason ?? `评估样本不足（现有 ${data.n_evaluated} 条，需 ≥${data.min_samples} 条）。`}
        </p>
      )}

      {!query.isLoading && data?.available && <ScorecardBody data={data} />}
    </section>
  );
}

function ScorecardBody({ data }: { data: PredictionScorecard }) {
  const overall = data.overall;
  const quantIc = data.rank_ic?.quant_score;
  const llmIc = data.rank_ic?.llm_confidence;
  const delta = fusionDelta(data);
  const quantOnly = data.fusion_comparison?.quant_only ?? null;
  const fused = data.fusion_comparison?.quant_llm_fused ?? null;
  return (
    <div className="space-y-4">
      {/* Headline KPIs */}
      <div className="grid gap-3 sm:grid-cols-4">
        <Kpi label="命中率" value={formatPct(overall?.hit_rate ?? null, 1)} tone="teal" />
        <Kpi label="平均超额" value={formatPct(overall?.avg_excess ?? null, 2, true)} tone="emerald" />
        <Kpi
          label={`量化 RankIC (n=${quantIc?.n ?? 0})`}
          value={formatIC(quantIc?.value ?? null)}
        />
        <Kpi
          label={`LLM RankIC (n=${llmIc?.n ?? 0})`}
          value={formatIC(llmIc?.value ?? null)}
        />
      </div>

      {/* Adaptive-alpha suggestion (advisory only, never changes live decisions) */}
      {data.alpha_suggestion && <AlphaSuggestionCard suggestion={data.alpha_suggestion} />}

      {/* Quant-only vs fused comparison */}
      <div className="rounded border border-ui-line bg-ui-subtle p-3">
        <p className="mb-2 text-xs font-medium text-ui-body">纯量化 vs 融合（LLM 复核是否加分）</p>
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-ui-faint">
              <th className="text-left font-normal">模式</th>
              <th className="text-right font-normal">样本</th>
              <th className="text-right font-normal">命中率</th>
              <th className="text-right font-normal">平均收益</th>
            </tr>
          </thead>
          <tbody className="font-mono text-ui-body">
            <ScorecardComparisonRow label="纯量化" row={quantOnly} />
            <ScorecardComparisonRow label="量化+LLM" row={fused} />
          </tbody>
        </table>
        {delta.hasBoth && (
          <p className="mt-2 text-[10px] text-ui-faint">
            融合相对纯量化：命中率{" "}
            <DeltaText value={delta.hitRate} digits={1} />，平均收益{" "}
            <DeltaText value={delta.avgReturn} digits={2} />
          </p>
        )}
      </div>

      {/* Bucketed breakdowns */}
      <div className="grid gap-3 md:grid-cols-3">
        <ScorecardBucketTable title="按量化分" rows={orderedBuckets("quant_score", data.buckets?.quant_score ?? [])} />
        <ScorecardBucketTable title="按 LLM 置信度" rows={orderedBuckets("llm_confidence", data.buckets?.llm_confidence ?? [])} />
        <ScorecardBucketTable title="按决策" rows={orderedBuckets("decision", data.buckets?.decision ?? [])} />
      </div>
    </div>
  );
}

function AlphaSuggestionCard({ suggestion }: { suggestion: AlphaSuggestion }) {
  const { applicable, static_alpha, suggested_alpha, delta } = suggestion;
  // The badge reflects the real production switch (adaptive_alpha_enabled):
  // when ON, daily_pipeline uses the suggested alpha as the fusion weight.
  const configQuery = useQuery({ queryKey: queryKeys.config(), queryFn: getConfig, staleTime: 60_000 });
  const alphaLive = configQuery.data?.adaptive_alpha_enabled ?? false;
  return (
    <div className="rounded border border-ui-line bg-ui-subtle p-3">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-medium text-ui-body">融合权重 α 建议（quant 权重）</p>
        <span
          className={`rounded border px-1.5 py-0.5 text-[10px] ${
            alphaLive
              ? "border-ui-accent/40 bg-ui-accent/10 text-ui-accent"
              : "border-ui-strong text-ui-faint"
          }`}
          title={
            alphaLive
              ? "设置中已开启自适应α：每日选股按此建议动态调整量化权重（样本不足时自动回退静态α）"
              : "设置中未开启自适应α：实盘仍使用静态 STYLE_ALPHA，可在 设置 → 高级 中开启"
          }
        >
          {alphaLive ? "已实装 · 实盘生效" : "仅建议 · 未改实盘"}
        </span>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Kpi label="当前静态 α" value={formatAlpha(static_alpha)} />
        <Kpi
          label="建议 α"
          value={applicable ? formatAlpha(suggested_alpha) : formatAlpha(static_alpha)}
          tone={applicable ? "teal" : undefined}
        />
        <div className="rounded border border-ui-line bg-ui-panel/40 p-2">
          <p className="text-[10px] text-ui-faint">差值</p>
          <p className={`mt-0.5 font-mono text-lg ${alphaDeltaTone(applicable ? delta : 0)}`}>
            {applicable ? `${delta >= 0 ? "+" : ""}${formatAlpha(delta)}` : "—"}
          </p>
        </div>
      </div>
      <p className="mt-2 text-[10px] text-ui-faint">
        {applicable ? suggestion.reason : "样本不足，维持静态权重。"}
      </p>
    </div>
  );
}

function ScorecardComparisonRow({ label, row }: { label: string; row: ScorecardBucket | null }) {
  if (!row) {
    return (
      <tr>
        <td className="text-left text-ui-muted">{label}</td>
        <td className="text-right text-ui-faint" colSpan={3}>无样本</td>
      </tr>
    );
  }
  return (
    <tr>
      <td className="text-left text-ui-muted">{label}</td>
      <td className="text-right">{row.count}</td>
      <td className="text-right">{formatPct(row.hit_rate, 1)}</td>
      <td className="text-right">{formatPct(row.avg_return, 2, true)}</td>
    </tr>
  );
}

function ScorecardBucketTable({ title, rows }: { title: string; rows: ScorecardBucket[] }) {
  return (
    <div className="rounded border border-ui-line bg-ui-subtle p-3">
      <p className="mb-2 text-xs font-medium text-ui-body">{title}</p>
      {rows.length === 0 ? (
        <p className="text-[10px] text-ui-faint">无数据</p>
      ) : (
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-ui-faint">
              <th className="text-left font-normal">桶</th>
              <th className="text-right font-normal">样本</th>
              <th className="text-right font-normal">命中率</th>
              <th className="text-right font-normal">平均收益</th>
            </tr>
          </thead>
          <tbody className="font-mono text-ui-body">
            {rows.map((r) => (
              <tr key={r.bucket || "—"}>
                <td className="text-left text-ui-muted">{r.bucket || "—"}</td>
                <td className="text-right">{r.count}</td>
                <td className="text-right">{formatPct(r.hit_rate, 1)}</td>
                <td className="text-right">{formatPct(r.avg_return, 2, true)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function DeltaText({ value, digits }: { value: number | null; digits: number }) {
  if (value === null) return <span className="text-ui-faint">—</span>;
  const cls = value >= 0 ? "text-ui-success" : "text-ui-danger";
  return <span className={`font-mono ${cls}`}>{formatPct(value, digits, true)}</span>;
}

function Kpi({ label, value, tone = "stone" }: { label: string; value: string; tone?: "stone" | "teal" | "emerald" | "red" }) {
  const valueCls = {
    stone: "text-ui-ink",
    teal: "text-ui-accent",
    emerald: "text-ui-success",
    red: "text-ui-danger",
  }[tone];
  return (
    <div className="rounded-lg border border-ui-line bg-ui-subtle p-3">
      <div className="text-xs text-ui-faint">{label}</div>
      <div className={`mt-1 font-mono text-lg font-semibold ${valueCls}`}>{value}</div>
    </div>
  );
}
