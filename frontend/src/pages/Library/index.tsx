import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Boxes, FileText, Search } from "lucide-react";
import { getArtifact, listArtifacts, listArtifactVersions, type ArtifactInfo, type ArtifactVersion } from "@/api/client";
import { queryKeys } from "@/api/queryKeys";
import { formatRelativeTime } from "@/lib/utils";
import {
  CandidateTable,
  parseCandidates,
  RiskAlertList,
  parseRisks,
} from "@/components/Chat";
import { formatMoney, formatNumber } from "@/utils/portfolio";
import { analyzeStockHint, useGoChat, type ChatContext } from "@/lib/chatNav";

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
  const goChat = useGoChat();
  const [searchParams] = useSearchParams();
  const [artifactType, setArtifactType] = useState("");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const runId = searchParams.get("run_id") || undefined;

  const artifactsQuery = useQuery({
    queryKey: queryKeys.artifacts(artifactType, query, runId),
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
    queryKey: queryKeys.artifact(selected!),
    queryFn: () => getArtifact(selected!),
    enabled: Boolean(selected),
  });

  return (
    <div className="flex h-full gap-4">
      <aside className="flex w-80 shrink-0 flex-col rounded-lg border border-ui-line bg-ui-panel xl:w-96">
        <div className="border-b border-ui-line p-4">
          <div className="mb-3 flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-ui-ink">Library</h3>
              <p className="text-xs text-ui-faint">跨 Skill 任务产物库</p>
              {runId && <p className="mt-1 font-mono text-xs text-ui-accent">run {runId.slice(0, 8)}</p>}
            </div>
            <Boxes className="h-4 w-4 text-ui-faint" />
          </div>
          <div className="flex items-center gap-2 rounded-lg border border-ui-line bg-ui-subtle px-3 py-2">
            <Search className="h-4 w-4 text-ui-faint" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索标题、摘要或标的"
              className="min-w-0 flex-1 bg-transparent text-sm text-ui-body outline-none placeholder:text-ui-faint"
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
                    ? "border-ui-accent/50 bg-ui-accent/10 text-ui-accent"
                    : "border-ui-strong text-ui-muted hover:border-ui-strong hover:text-ui-body"
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {artifactsQuery.isLoading && <p className="p-3 text-sm text-ui-muted">Loading...</p>}
          {!artifactsQuery.isLoading && visibleArtifacts.length === 0 && (
            <div className="rounded-lg border border-dashed border-ui-strong bg-ui-subtle p-6 text-center text-sm text-ui-faint">
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

      <section className="min-w-0 flex-1 overflow-y-auto rounded-lg border border-ui-line bg-ui-panel p-5">
        {detailQuery.isLoading && <p className="text-ui-muted">Loading artifact...</p>}
        {detailQuery.data ? (
          <ArtifactDetail
            artifact={detailQuery.data}
            onAnalyze={(symbol, context) =>
              goChat({
                prompt: `帮我分析 ${symbol}`,
                autoSend: true,
                context: context as ChatContext | undefined,
                intentHint: analyzeStockHint(symbol),
              })
            }
          />
        ) : (
          !detailQuery.isLoading && (
            <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-ui-strong bg-ui-subtle text-sm text-ui-faint">
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
        selected ? "border-ui-accent/40 bg-ui-accent/10" : "border-ui-line bg-ui-subtle hover:bg-ui-hover/70"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-ui-ink">{artifact.title}</div>
          <div className="mt-1 truncate text-xs text-ui-faint">
            {TYPE_LABELS[artifact.artifact_type] ?? artifact.artifact_type}
            {artifact.subtitle ? ` · ${artifact.subtitle}` : ""}
          </div>
        </div>
        <span className="shrink-0 rounded border border-ui-strong px-1.5 py-0.5 text-xs text-ui-muted">
          {artifact.skill_id}
        </span>
      </div>
      {artifact.summary && <p className="mt-2 line-clamp-2 text-xs text-ui-muted">{artifact.summary}</p>}
      <p className="mt-2 text-xs text-ui-faint">{formatRelativeTime(artifact.created_at)}</p>
    </button>
  );
}

function ArtifactDetail({ artifact, onAnalyze }: { artifact: ArtifactInfo; onAnalyze: (symbol: string, context?: Record<string, unknown>) => void }) {
  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-4 border-b border-ui-line pb-4">
        <div>
          <p className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ui-faint">
            <FileText className="h-3.5 w-3.5" />
            {TYPE_LABELS[artifact.artifact_type] ?? artifact.artifact_type}
          </p>
          <h2 className="text-xl font-semibold text-ui-ink">{artifact.title}</h2>
          {artifact.subtitle && <p className="mt-1 text-sm text-ui-faint">{artifact.subtitle}</p>}
        </div>
        <span className="rounded border border-ui-strong px-2 py-1 text-xs text-ui-muted">
          {artifact.status}
        </span>
      </div>

      {artifact.summary && (
        <div className="mb-4 rounded-lg border border-ui-line bg-ui-subtle p-3 text-sm text-ui-body">
          {artifact.summary}
        </div>
      )}

      <StructuredArtifact artifact={artifact} onAnalyze={onAnalyze} />

      {artifact.content_markdown && (
        <div className="prose prose-invert mt-5 max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{artifact.content_markdown}</ReactMarkdown>
        </div>
      )}

      <ArtifactVersions artifactId={artifact.id} />
    </div>
  );
}

function ArtifactVersions({ artifactId }: { artifactId: string }) {
  const [expanded, setExpanded] = useState(false);
  const versionsQuery = useQuery({
    queryKey: queryKeys.artifactVersions(artifactId),
    queryFn: () => listArtifactVersions(artifactId),
    enabled: expanded,
  });

  return (
    <div className="mt-6 border-t border-ui-line pt-4">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1 text-sm text-ui-muted hover:text-ui-body"
      >
        <FileText className="h-3.5 w-3.5" />
        历史版本
        {versionsQuery.data && versionsQuery.data.length > 0 && (
          <span className="rounded-full bg-ui-hover px-1.5 text-xs text-ui-muted">
            {versionsQuery.data.length}
          </span>
        )}
      </button>
      {expanded && versionsQuery.isLoading && (
        <p className="mt-2 text-xs text-ui-faint">加载中...</p>
      )}
      {expanded && versionsQuery.data && versionsQuery.data.length === 0 && (
        <p className="mt-2 text-xs text-ui-faint">暂无历史版本（当前为首次生成）。</p>
      )}
      {expanded && versionsQuery.data && versionsQuery.data.length > 0 && (
        <div className="mt-3 space-y-2">
          {versionsQuery.data.map((v: ArtifactVersion) => (
            <div key={v.id} className="rounded border border-ui-line bg-ui-subtle p-2.5 text-xs">
              <div className="flex items-center justify-between">
                <span className="font-medium text-ui-body">v{v.version}</span>
                <span className="text-ui-faint">{formatRelativeTime(v.saved_at)}</span>
              </div>
              {v.title && <p className="mt-1 text-ui-muted">{v.title}</p>}
              {v.summary && <p className="mt-1 text-ui-faint">{v.summary}</p>}
              {v.content_markdown && (
                <details className="mt-1">
                  <summary className="cursor-pointer text-ui-faint hover:text-ui-body">查看内容</summary>
                  <div className="prose prose-invert mt-1 max-w-none">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{v.content_markdown}</ReactMarkdown>
                  </div>
                </details>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StructuredArtifact({ artifact, onAnalyze }: { artifact: ArtifactInfo; onAnalyze: (symbol: string, context?: Record<string, unknown>) => void }) {
  const payload = useMemo(() => artifact.payload ?? {}, [artifact.payload]);
  const candidates = useMemo(() => {
    const rows = payload.decision_pack ?? payload.reviewed_candidates ?? payload.quant_candidates ?? payload.candidates;
    return parseCandidates(rows);
  }, [payload]);
  const tradeDate = artifactTradeDate(payload, artifact, candidates);

  if (["signal_pack", "decision_pack", "screening_report", "scanner_report"].includes(artifact.artifact_type) && candidates.length > 0) {
    const warnings = Array.isArray(payload.warnings) ? payload.warnings.map(String) : undefined;
    return (
      <CandidateTable
        candidates={candidates}
        warnings={warnings}
        asOfDate={tradeDate}
        onAnalyze={onAnalyze}
        actionContext={{
          runId: artifact.run_id,
          artifactId: artifact.id,
          tradeDate,
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
  onAnalyze: (symbol: string, context?: Record<string, unknown>) => void;
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
  const tradeDate = artifactTradeDate(payload, artifact, candidates);
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
        <section className="rounded-lg border border-ui-accent/20 bg-ui-accent/5 p-3">
          <div className="mb-2 text-sm font-semibold text-ui-accent">次日动作清单</div>
          <div className="grid gap-2 sm:grid-cols-2">
            {actions.map((item, index) => (
              <button
                key={index}
                onClick={() => item.symbol && onAnalyze(String(item.symbol))}
                className="rounded border border-ui-accent/20 bg-ui-subtle p-2 text-left text-sm text-ui-body transition hover:border-ui-accent/50"
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
            <h3 className="text-sm font-semibold text-ui-ink">次日选股候选</h3>
            <span className="text-xs text-ui-faint">收盘价、目标价和止损仅在数据源可用时展示</span>
          </div>
          <CandidateTable
            candidates={candidates}
            warnings={Array.isArray(payload.warnings) ? payload.warnings.map(String) : undefined}
            asOfDate={tradeDate}
            onAnalyze={onAnalyze}
            actionContext={{
              runId: artifact.run_id,
              artifactId: artifact.id,
              tradeDate,
            }}
          />
        </section>
      )}

      {risks.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-semibold text-ui-ink">持仓风险</h3>
          <RiskAlertList risks={risks} onAnalyze={onAnalyze} />
        </section>
      )}

      {lessons.length > 0 && (
        <section className="rounded-lg border border-ui-info/20 bg-ui-info/5 p-3">
          <div className="mb-2 text-sm font-semibold text-ui-body">本次使用的策略经验</div>
          <div className="space-y-2">
            {lessons.map((lesson, index) => (
              <div key={String(lesson.id ?? index)} className="rounded border border-ui-info/20 bg-ui-subtle p-2 text-sm text-ui-body">
                <span className="mr-2 rounded border border-ui-info/30 px-1.5 py-0.5 text-xs">
                  {String(lesson.confidence ?? "low")}
                </span>
                {String(lesson.finding ?? "")}
                {Boolean(lesson.suggested_adjustment) && (
                  <div className="mt-1 text-xs text-ui-muted">建议：{String(lesson.suggested_adjustment)}</div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

export function artifactTradeDate(
  payload: Record<string, unknown>,
  artifact: ArtifactInfo,
  candidates: ReturnType<typeof parseCandidates>,
): string | undefined {
  const temporal = payload.temporal_context as Record<string, unknown> | undefined;
  const firstRaw = candidates[0]?.raw;
  const values = [
    payload.trade_date,
    payload.market_asof_date,
    payload.as_of_date,
    payload.effective_trade_date,
    temporal?.market_asof_date,
    temporal?.latest_close_date,
    candidates[0]?.priceTradeDate,
    firstRaw?.trade_date,
    firstRaw?.price_trade_date,
    firstRaw?.effective_trade_date,
    artifact.id,
    artifact.created_at,
  ];
  for (const value of values) {
    const text = String(value ?? "");
    const iso = text.match(/(?:19|20)\d{2}-\d{2}-\d{2}/)?.[0];
    if (iso) return iso;
    const compact = text.match(/(?:19|20)\d{6}/)?.[0];
    if (compact) return `${compact.slice(0, 4)}-${compact.slice(4, 6)}-${compact.slice(6, 8)}`;
  }
  return undefined;
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
        <div className="rounded-lg border border-ui-line bg-ui-subtle p-3">
          <div className="mb-2 text-sm font-medium text-ui-ink">次日动作</div>
          <div className="space-y-1 text-sm text-ui-body">
            {actions.map((item, index) => (
              <div key={index}>• {String(item.label ?? item.type ?? "")}</div>
            ))}
          </div>
        </div>
      )}
      {lessons.length > 0 && (
        <div className="rounded-lg border border-ui-info/20 bg-ui-info/5 p-3">
          <div className="mb-2 text-sm font-medium text-ui-body">活跃策略经验</div>
          <div className="space-y-2">
            {lessons.map((lesson, index) => (
              <div key={String(lesson.id ?? index)} className="text-sm text-ui-body">
                <span className="mr-2 rounded border border-ui-info/30 px-1.5 py-0.5 text-xs text-ui-info">
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
        <div className="overflow-hidden rounded-lg border border-ui-line">
          <table className="w-full text-left text-sm">
            <thead className="bg-ui-subtle text-xs uppercase text-ui-faint">
              <tr>
                <th className="px-3 py-2">标的</th>
                <th className="px-3 py-2 text-right">数量</th>
                <th className="px-3 py-2 text-right">成本</th>
                <th className="px-3 py-2 text-right">现价</th>
              </tr>
            </thead>
            <tbody>
              {holdings.map((row, index) => (
                <tr key={`${row.symbol}-${index}`} className="border-t border-ui-line">
                  <td className="px-3 py-2 text-ui-ink">
                    {!!row.name && String(row.name) !== String(row.symbol ?? "")
                      ? String(row.name)
                      : String(row.symbol ?? "-")}
                    {!!row.name && String(row.name) !== String(row.symbol ?? "") && (
                      <span className="ml-1 font-mono text-xs text-ui-faint">{String(row.symbol ?? "")}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-ui-body">{formatNumber(Number(row.quantity ?? 0))}</td>
                  <td className="px-3 py-2 text-right font-mono text-ui-body">{formatNumber(Number(row.avg_cost ?? 0))}</td>
                  <td className="px-3 py-2 text-right font-mono text-ui-body">{formatNumber(Number(row.current_price ?? row.avg_cost ?? 0))}</td>
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
    <div className="rounded-lg border border-ui-line bg-ui-subtle p-3">
      <div className="text-xs text-ui-faint">{label}</div>
      <div className="mt-1 font-mono text-lg font-semibold text-ui-ink">{value}</div>
    </div>
  );
}
