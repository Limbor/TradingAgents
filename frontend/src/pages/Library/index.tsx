import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Boxes, FileText, Search } from "lucide-react";
import { getArtifact, listArtifacts, type ArtifactInfo } from "@/api/client";
import { formatRelativeTime } from "@/lib/utils";
import {
  CandidateTable,
  parseCandidates,
  RiskAlertList,
  parseRisks,
} from "@/components/Chat";
import { formatMoney, formatNumber } from "@/utils/portfolio";

const FILTERS = [
  { label: "全部", value: "" },
  { label: "个股分析", value: "stock_report" },
  { label: "每日选股", value: "screening_report" },
  { label: "市场扫描", value: "scanner_report" },
  { label: "风险报告", value: "risk_report" },
  { label: "组合报告", value: "portfolio_report" },
  { label: "反思记录", value: "reflection_report" },
  { label: "收盘复盘", value: "daily_review_report" },
  { label: "次日计划", value: "next_day_plan" },
];

const PRIMARY_ARTIFACT_TYPES = new Set([
  "stock_report",
  "screening_report",
  "scanner_report",
  "risk_report",
  "portfolio_report",
  "reflection_report",
  "daily_review_report",
]);

const TYPE_LABELS: Record<string, string> = {
  stock_report: "个股分析",
  screening_report: "每日选股",
  scanner_report: "市场扫描",
  risk_report: "风险报告",
  portfolio_report: "组合报告",
  signal_pack: "信号包",
  decision_pack: "决策包",
  reflection_report: "反思记录",
  daily_review_report: "收盘复盘",
  next_day_plan: "次日计划",
};

export default function Library() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [artifactType, setArtifactType] = useState("");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const runId = searchParams.get("run_id") || undefined;

  const artifactsQuery = useQuery({
    queryKey: ["artifacts", artifactType, query, runId],
    queryFn: () => listArtifacts({ limit: 50, artifact_type: artifactType || undefined, q: query || undefined, run_id: runId }),
    refetchInterval: runId ? 5000 : 30000,
  });

  const visibleArtifacts = useMemo(() => {
    const rows = artifactsQuery.data ?? [];
    if (artifactType) return rows;
    const dailyReviewRunIds = new Set(
      rows
        .filter((item) => item.artifact_type === "daily_review_report")
        .map((item) => item.run_id)
        .filter(Boolean)
    );
    return rows.filter((item) => {
      if (!PRIMARY_ARTIFACT_TYPES.has(item.artifact_type)) return false;
      if (dailyReviewRunIds.has(item.run_id)) return item.artifact_type === "daily_review_report";
      return true;
    });
  }, [artifactType, artifactsQuery.data]);

  const selected = selectedId ?? visibleArtifacts[0]?.id ?? null;
  const detailQuery = useQuery({
    queryKey: ["artifact", selected],
    queryFn: () => getArtifact(selected!),
    enabled: Boolean(selected),
  });

  return (
    <div className="mx-auto flex h-full max-w-7xl gap-4">
      <aside className="flex w-96 min-w-80 flex-col rounded-lg border border-stone-800 bg-stone-900">
        <div className="border-b border-stone-800 p-4">
          <div className="mb-3 flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-stone-100">Library</h3>
              <p className="text-xs text-stone-500">跨 Skill 任务产物库</p>
              {runId && <p className="mt-1 font-mono text-xs text-teal-300">run {runId.slice(0, 8)}</p>}
            </div>
            <Boxes className="h-4 w-4 text-stone-500" />
          </div>
          <div className="flex items-center gap-2 rounded-lg border border-stone-800 bg-stone-950 px-3 py-2">
            <Search className="h-4 w-4 text-stone-500" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索标题、摘要或标的"
              className="min-w-0 flex-1 bg-transparent text-sm text-stone-200 outline-none placeholder:text-stone-600"
            />
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {FILTERS.map((item) => (
              <button
                key={item.value || "all"}
                onClick={() => {
                  setArtifactType(item.value);
                  setSelectedId(null);
                }}
                className={`rounded border px-2 py-1 text-xs transition ${
                  artifactType === item.value
                    ? "border-teal-500/50 bg-teal-500/10 text-teal-200"
                    : "border-stone-700 text-stone-400 hover:border-stone-600 hover:text-stone-200"
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {artifactsQuery.isLoading && <p className="p-3 text-sm text-stone-400">Loading...</p>}
          {!artifactsQuery.isLoading && visibleArtifacts.length === 0 && (
            <div className="rounded-lg border border-dashed border-stone-700 bg-stone-950 p-6 text-center text-sm text-stone-500">
              暂无任务产物
            </div>
          )}
          <div className="space-y-2">
            {visibleArtifacts.map((item) => (
              <ArtifactListItem
                key={item.id}
                artifact={item}
                selected={item.id === selected}
                onClick={() => setSelectedId(item.id)}
              />
            ))}
          </div>
        </div>
      </aside>

      <section className="min-w-0 flex-1 overflow-y-auto rounded-lg border border-stone-800 bg-stone-900 p-5">
        {detailQuery.isLoading && <p className="text-stone-400">Loading artifact...</p>}
        {detailQuery.data ? (
          <ArtifactDetail
            artifact={detailQuery.data}
            onAnalyze={(symbol) => navigate("/chat", { state: { prompt: `帮我分析 ${symbol}`, autoSend: true } })}
          />
        ) : (
          !detailQuery.isLoading && (
            <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-stone-700 bg-stone-950 text-sm text-stone-500">
              选择一个任务产物查看详情
            </div>
          )
        )}
      </section>
    </div>
  );
}

function ArtifactListItem({
  artifact,
  selected,
  onClick,
}: {
  artifact: ArtifactInfo;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full rounded-lg border p-3 text-left transition ${
        selected ? "border-teal-500/40 bg-teal-500/10" : "border-stone-800 bg-stone-950 hover:bg-stone-800/70"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-stone-100">{artifact.title}</div>
          <div className="mt-1 truncate text-xs text-stone-500">
            {TYPE_LABELS[artifact.artifact_type] ?? artifact.artifact_type}
            {artifact.subtitle ? ` · ${artifact.subtitle}` : ""}
          </div>
        </div>
        <span className="shrink-0 rounded border border-stone-700 px-1.5 py-0.5 text-xs text-stone-400">
          {artifact.skill_id}
        </span>
      </div>
      {artifact.summary && <p className="mt-2 line-clamp-2 text-xs text-stone-400">{artifact.summary}</p>}
      <p className="mt-2 text-xs text-stone-600">{formatRelativeTime(artifact.created_at)}</p>
    </button>
  );
}

function ArtifactDetail({ artifact, onAnalyze }: { artifact: ArtifactInfo; onAnalyze: (symbol: string) => void }) {
  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-4 border-b border-stone-800 pb-4">
        <div>
          <p className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-stone-500">
            <FileText className="h-3.5 w-3.5" />
            {TYPE_LABELS[artifact.artifact_type] ?? artifact.artifact_type}
          </p>
          <h2 className="text-xl font-semibold text-stone-50">{artifact.title}</h2>
          {artifact.subtitle && <p className="mt-1 text-sm text-stone-500">{artifact.subtitle}</p>}
        </div>
        <span className="rounded border border-stone-700 px-2 py-1 text-xs text-stone-400">
          {artifact.status}
        </span>
      </div>

      {artifact.summary && (
        <div className="mb-4 rounded-lg border border-stone-800 bg-stone-950 p-3 text-sm text-stone-300">
          {artifact.summary}
        </div>
      )}

      <StructuredArtifact artifact={artifact} onAnalyze={onAnalyze} />

      {artifact.content_markdown && (
        <div className="prose prose-invert mt-5 max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{artifact.content_markdown}</ReactMarkdown>
        </div>
      )}
    </div>
  );
}

function StructuredArtifact({ artifact, onAnalyze }: { artifact: ArtifactInfo; onAnalyze: (symbol: string) => void }) {
  const payload = artifact.payload ?? {};
  const candidates = useMemo(() => {
    const rows = payload.decision_pack ?? payload.reviewed_candidates ?? payload.quant_candidates ?? payload.candidates;
    return parseCandidates(rows);
  }, [payload]);

  if (["signal_pack", "decision_pack", "screening_report", "scanner_report"].includes(artifact.artifact_type) && candidates.length > 0) {
    const warnings = Array.isArray(payload.warnings) ? payload.warnings.map(String) : undefined;
    return (
      <CandidateTable
        candidates={candidates}
        warnings={warnings}
        onAnalyze={onAnalyze}
        actionContext={{
          runId: artifact.run_id,
          artifactId: artifact.id,
          tradeDate: typeof payload.trade_date === "string" ? payload.trade_date : undefined,
        }}
      />
    );
  }

  if (artifact.artifact_type === "risk_report") {
    return <RiskAlertList risks={parseRisks(payload.risks)} onAnalyze={onAnalyze} />;
  }

  if (artifact.artifact_type === "portfolio_report") {
    return <PortfolioArtifact payload={payload} />;
  }

  if (["daily_review_report", "next_day_plan"].includes(artifact.artifact_type)) {
    return <DailyReviewArtifact payload={payload} artifact={artifact} onAnalyze={onAnalyze} />;
  }

  if (artifact.artifact_type === "reflection_report") {
    return <ReflectionAndPlanArtifact payload={payload} />;
  }

  return null;
}

function DailyReviewArtifact({
  payload,
  artifact,
  onAnalyze,
}: {
  payload: Record<string, unknown>;
  artifact: ArtifactInfo;
  onAnalyze: (symbol: string) => void;
}) {
  const reflection = (payload.reflection ?? {}) as Record<string, unknown>;
  const lessons = Array.isArray(payload.active_strategy_lessons)
    ? payload.active_strategy_lessons as Record<string, unknown>[]
    : [];
  const actions = Array.isArray(payload.next_actions)
    ? payload.next_actions as Record<string, unknown>[]
    : [];
  const risks = parseRisks(payload.risk_items);
  const highRisks = parseRisks(payload.high_risk_items);
  const candidates = parseCandidates(payload.candidates);
  const counts = (payload.candidate_counts ?? {}) as Record<string, unknown>;
  const refreshed = (payload.refreshed_prices ?? {}) as Record<string, unknown>;
  return (
    <div className="mb-4 space-y-5">
      <div className="grid gap-3 sm:grid-cols-4">
        <Metric label="刷新持仓" value={String(refreshed.updated ?? 0)} />
        <Metric label="高风险" value={String(highRisks.length)} />
        <Metric label="反思处理" value={String(reflection.processed ?? 0)} />
        <Metric label="BUY / WATCH" value={`${String(counts.BUY ?? 0)} / ${String(counts.WATCHLIST ?? 0)}`} />
      </div>

      {actions.length > 0 && (
        <section className="rounded-lg border border-teal-500/20 bg-teal-500/5 p-3">
          <div className="mb-2 text-sm font-semibold text-teal-100">次日动作清单</div>
          <div className="grid gap-2 sm:grid-cols-2">
            {actions.map((item, index) => (
              <button
                key={index}
                onClick={() => item.symbol && onAnalyze(String(item.symbol))}
                className="rounded border border-teal-500/20 bg-stone-950 p-2 text-left text-sm text-stone-200 transition hover:border-teal-500/50"
              >
                {String(item.label ?? item.type ?? "")}
              </button>
            ))}
          </div>
        </section>
      )}

      {candidates.length > 0 && (
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-stone-100">次日选股候选</h3>
            <span className="text-xs text-stone-500">收盘价、目标价和止损仅在数据源可用时展示</span>
          </div>
          <CandidateTable
            candidates={candidates}
            warnings={Array.isArray(payload.warnings) ? payload.warnings.map(String) : undefined}
            onAnalyze={onAnalyze}
            actionContext={{
              runId: artifact.run_id,
              artifactId: artifact.id,
              tradeDate: typeof payload.trade_date === "string" ? payload.trade_date : undefined,
            }}
          />
        </section>
      )}

      {risks.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-semibold text-stone-100">持仓风险</h3>
          <RiskAlertList risks={risks} onAnalyze={onAnalyze} />
        </section>
      )}

      {lessons.length > 0 && (
        <section className="rounded-lg border border-purple-500/20 bg-purple-500/5 p-3">
          <div className="mb-2 text-sm font-semibold text-purple-100">本次使用的策略经验</div>
          <div className="space-y-2">
            {lessons.map((lesson, index) => (
              <div key={String(lesson.id ?? index)} className="rounded border border-purple-500/20 bg-stone-950 p-2 text-sm text-purple-100">
                <span className="mr-2 rounded border border-purple-500/30 px-1.5 py-0.5 text-xs">
                  {String(lesson.confidence ?? "low")}
                </span>
                {String(lesson.finding ?? "")}
                {Boolean(lesson.suggested_adjustment) && (
                  <div className="mt-1 text-xs text-purple-200/80">建议：{String(lesson.suggested_adjustment)}</div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function ReflectionAndPlanArtifact({ payload }: { payload: Record<string, unknown> }) {
  const reflection = (payload.reflection ?? payload) as Record<string, unknown>;
  const lessons = Array.isArray(payload.active_strategy_lessons)
    ? payload.active_strategy_lessons as Record<string, unknown>[]
    : [];
  const actions = Array.isArray(payload.next_actions)
    ? payload.next_actions as Record<string, unknown>[]
    : [];
  const counts = (payload.candidate_counts ?? {}) as Record<string, unknown>;
  return (
    <div className="mb-4 space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <Metric label="反思处理" value={String(reflection.processed ?? 0)} />
        <Metric label="生成经验" value={String(reflection.lessons_created ?? lessons.length ?? 0)} />
        <Metric label="候选 BUY" value={String(counts.BUY ?? 0)} />
      </div>
      {actions.length > 0 && (
        <div className="rounded-lg border border-stone-800 bg-stone-950 p-3">
          <div className="mb-2 text-sm font-medium text-stone-100">次日动作</div>
          <div className="space-y-1 text-sm text-stone-300">
            {actions.map((item, index) => (
              <div key={index}>• {String(item.label ?? item.type ?? "")}</div>
            ))}
          </div>
        </div>
      )}
      {lessons.length > 0 && (
        <div className="rounded-lg border border-purple-500/20 bg-purple-500/5 p-3">
          <div className="mb-2 text-sm font-medium text-purple-100">活跃策略经验</div>
          <div className="space-y-2">
            {lessons.map((lesson, index) => (
              <div key={String(lesson.id ?? index)} className="text-sm text-purple-100">
                <span className="mr-2 rounded border border-purple-500/30 px-1.5 py-0.5 text-xs text-purple-200">
                  {String(lesson.confidence ?? "low")}
                </span>
                {String(lesson.finding ?? "")}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function PortfolioArtifact({ payload }: { payload: Record<string, unknown> }) {
  const summary = (payload.summary ?? {}) as Record<string, unknown>;
  const holdings = Array.isArray(payload.holdings) ? payload.holdings as Record<string, unknown>[] : [];
  return (
    <div className="mb-4 space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <Metric label="市值" value={formatMoney(Number(summary.market_value ?? 0))} />
        <Metric label="未实现盈亏" value={formatMoney(Number(summary.unrealized_pnl ?? 0))} />
        <Metric label="风险等级" value={String(summary.risk_level ?? "-")} />
      </div>
      {holdings.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-stone-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-stone-950 text-xs uppercase text-stone-500">
              <tr>
                <th className="px-3 py-2">标的</th>
                <th className="px-3 py-2 text-right">数量</th>
                <th className="px-3 py-2 text-right">成本</th>
                <th className="px-3 py-2 text-right">现价</th>
              </tr>
            </thead>
            <tbody>
              {holdings.map((row, index) => (
                <tr key={`${row.symbol}-${index}`} className="border-t border-stone-800">
                  <td className="px-3 py-2 font-mono text-stone-100">{String(row.symbol ?? "-")}</td>
                  <td className="px-3 py-2 text-right font-mono text-stone-300">{formatNumber(Number(row.quantity ?? 0))}</td>
                  <td className="px-3 py-2 text-right font-mono text-stone-300">{formatNumber(Number(row.avg_cost ?? 0))}</td>
                  <td className="px-3 py-2 text-right font-mono text-stone-300">{formatNumber(Number(row.current_price ?? row.avg_cost ?? 0))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-stone-800 bg-stone-950 p-3">
      <div className="text-xs text-stone-500">{label}</div>
      <div className="mt-1 font-mono text-lg font-semibold text-stone-100">{value}</div>
    </div>
  );
}
