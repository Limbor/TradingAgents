import { Fragment, useState } from "react";
import { AlertCircle, ArrowRight, ChevronDown, ChevronRight, Info, Plus, TrendingUp } from "lucide-react";
import { saveCandidateAction } from "@/api/client";

export interface CandidateRow {
  rank: number;
  symbol: string;
  name?: string;
  industry?: string;
  board?: string;
  decision?: string;
  quantDecision?: string;
  llmView?: string;
  catalystStrength?: string;
  riskAssessment?: string;
  score?: number;
  quantScore?: number;
  latestPrice?: number;
  priceTradeDate?: string;
  entryZone?: number[];
  stopLoss?: number;
  targets?: number[];
  actionPlan?: Record<string, unknown>;
  keyMetrics?: Record<string, unknown>;
  gate_reasons?: string[];
  quantGateReasons?: QuantGateReason[];
  dataCoverageWarnings?: string[];
  keyCatalysts?: string[];
  keyRisks?: string[];
  reasoning?: string;
  strategyLessonHits?: Array<Record<string, unknown>>;
  lessonAdjustmentReason?: string;
  raw?: Record<string, unknown>;
}

interface QuantGateReason {
  gate: string;
  passed?: boolean;
  detail?: string;
}

interface CandidateTableProps {
  candidates: CandidateRow[];
  warnings?: string[];
  asOfDate?: string;
  dataWindowNote?: string;
  sessionState?: string;
  onAnalyze?: (symbol: string, context?: Record<string, unknown>) => void;
  onAddWatchlist?: (symbols: string[]) => void;
  actionContext?: {
    runId?: string;
    artifactId?: string;
    tradeDate?: string;
  };
}

export function CandidateTable({ candidates, warnings, asOfDate, dataWindowNote, sessionState, onAnalyze, onAddWatchlist, actionContext }: CandidateTableProps) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [actionFeedback, setActionFeedback] = useState<Record<string, string>>({});
  if (candidates.length === 0) {
    return (
      <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-4 text-sm text-amber-200 space-y-2">
        <div className="flex items-center gap-2">
          <AlertCircle className="h-4 w-4 shrink-0 text-amber-300" />
          <span className="font-medium">没有找到候选标的</span>
        </div>
        {warnings && warnings.length > 0 && (
          <ul className="ml-6 list-disc space-y-1 text-xs text-amber-300/80">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        )}
        <p className="text-xs text-stone-500 mt-1">
          可能原因：非交易日、MCP 数据未更新、板块过滤过严或指数成分股缺失
        </p>
      </div>
    );
  }

  const decisionColor = (decision?: string) => {
    if (!decision) return "text-stone-400";
    const d = decision.toLowerCase();
    if (d.includes("buy") || d.includes("strong")) return "text-emerald-300";
    if (d.includes("sell") || d.includes("avoid")) return "text-red-300";
    if (d.includes("hold") || d.includes("neutral")) return "text-amber-300";
    return "text-stone-300";
  };

  return (
    <div className="space-y-3">
      {dataWindowNote && (
        <div className="rounded-lg border border-sky-500/20 bg-sky-500/5 p-2.5 text-xs text-sky-200">
          <div className="flex items-start gap-2">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sky-300" />
            <div className="min-w-0 flex-1">
              <span className="font-medium">数据窗口</span>
              {sessionState && (
                <span className="ml-2 rounded bg-sky-500/20 px-1.5 py-0.5 font-mono text-[10px] text-sky-100">
                  {sessionLabel(sessionState)}
                </span>
              )}
              <span className="ml-2 text-sky-100/90">{dataWindowNote}</span>
              {asOfDate && <span className="ml-2 font-mono text-sky-300/70">as-of {asOfDate}</span>}
            </div>
          </div>
        </div>
      )}
      {warnings && warnings.length > 0 && candidates.length > 0 && (
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-2.5 text-xs text-amber-200">
          <div className="flex items-center gap-2 font-medium">
            <AlertCircle className="h-3.5 w-3.5 text-amber-300" />
            提示
          </div>
          <ul className="ml-5 mt-1 list-disc space-y-0.5 text-amber-300/80">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="overflow-hidden rounded-lg border border-stone-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-stone-900 text-xs uppercase text-stone-500">
            <tr>
              <th className="px-3 py-2 w-8">#</th>
              <th className="px-3 py-2">标的</th>
              <th className="px-3 py-2 text-center">最终</th>
              <th className="px-3 py-2 text-center">量化</th>
              <th className="px-3 py-2 text-center">LLM</th>
              <th className="px-3 py-2 text-center">风险</th>
              <th className="px-3 py-2 text-right">价格/计划</th>
              <th className="px-3 py-2 text-right">分数</th>
              <th className="px-3 py-2 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((row) => (
              <Fragment key={row.symbol}>
              <tr className="border-t border-stone-800 hover:bg-stone-900/50">
                <td className="px-3 py-2 text-stone-500">
                  <button
                    onClick={() => setExpanded(expanded === row.symbol ? null : row.symbol)}
                    className="inline-flex items-center gap-1 text-stone-500 hover:text-stone-200"
                    title="展开候选解释"
                  >
                    {expanded === row.symbol ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                    {row.rank}
                  </button>
                </td>
                <td className="px-3 py-2">
                  <span className="font-mono text-stone-100">{row.symbol}</span>
                  {row.name && <span className="ml-2 text-stone-400">{row.name}</span>}
                  <div className="mt-1 flex flex-wrap gap-1 text-xs text-stone-500">
                    {row.board && <span>{row.board}</span>}
                    {row.industry && <span>{row.industry}</span>}
                    {row.gate_reasons?.slice(0, 2).map((reason) => (
                      <span key={reason} className="rounded bg-stone-800 px-1.5 py-0.5" title={explainGateReason(reason)}>{reason}</span>
                    ))}
                    {row.dataCoverageWarnings?.map((warning) => (
                      <span key={warning} className="rounded bg-amber-500/10 px-1.5 py-0.5 text-amber-300">{warning}</span>
                    ))}
                  </div>
                </td>
                <td className={`px-3 py-2 text-center font-medium ${decisionColor(row.decision)}`}>
                  <span title={explainFinalDecision(row.decision)}>{row.decision ?? "-"}</span>
                </td>
                <td className="px-3 py-2 text-center text-stone-300">
                  <span title="量化层门控结论，只代表候选资格，不等同最终买入">{row.quantDecision ?? "-"}</span>
                </td>
                <td className="px-3 py-2 text-center text-stone-300">
                  <div title="LLM 对量化信号是否仍成立的离散判断">{row.llmView ?? "-"}</div>
                  {row.catalystStrength && (
                    <div className="text-xs text-stone-500" title="催化剂强度：confirmed/likely/speculative/none">{row.catalystStrength}</div>
                  )}
                </td>
                <td className="px-3 py-2 text-center text-stone-300">
                  <span title="LLM 风险评估：low/moderate/high/critical">{row.riskAssessment ?? "-"}</span>
                </td>
                <td className="px-3 py-2 text-right font-mono text-stone-300">
                  {row.latestPrice !== undefined ? (
                    <>
                      <div>{formatPrice(row.latestPrice)}</div>
                      {row.targets?.length ? <div className="text-xs text-emerald-300">T {row.targets.map(formatPrice).join("/")}</div> : null}
                      {row.stopLoss !== undefined ? <div className="text-xs text-red-300">S {formatPrice(row.stopLoss)}</div> : null}
                    </>
                  ) : (
                    <span className="text-xs text-stone-600" title="MCP 未返回收盘价，且日线补价不可用">缺收盘价</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-stone-200">
                  {row.score !== undefined ? row.score : "-"}
                  {row.quantScore !== undefined && (
                    <div className="text-xs text-stone-500">Q {row.quantScore}</div>
                  )}
                </td>
                <td className="px-3 py-2 text-right">
                  <div className="flex justify-end gap-1">
                  {onAnalyze && (
                    <button
                      onClick={() => onAnalyze(row.symbol, { selection_context: rowSelectionContext(row) })}
                      className="inline-flex items-center gap-1 rounded border border-teal-500/30 px-2 py-1 text-xs text-teal-300 transition hover:bg-teal-500/10"
                    >
                      <TrendingUp className="h-3 w-3" />
                      分析
                    </button>
                  )}
                  </div>
                </td>
              </tr>
              {expanded === row.symbol && (
                <tr className="border-t border-stone-800 bg-stone-950/80">
                  <td />
                  <td colSpan={8} className="px-3 py-3">
                    <CandidateExpanded
                      row={row}
                      feedback={actionFeedback[row.symbol]}
                      onAction={async (action) => {
                        try {
                          const result = await saveCandidateAction({
                            action,
                            symbol: row.symbol,
                            name: row.name,
                            run_id: actionContext?.runId,
                            artifact_id: actionContext?.artifactId,
                            trade_date: actionContext?.tradeDate,
                            payload: row.raw,
                          });
                          setActionFeedback((prev) => ({ ...prev, [row.symbol]: result.message }));
                        } catch (exc) {
                          setActionFeedback((prev) => ({ ...prev, [row.symbol]: exc instanceof Error ? exc.message : "操作失败" }));
                        }
                      }}
                    />
                  </td>
                </tr>
              )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {onAddWatchlist && candidates.length > 0 && (
        <div className="flex gap-2">
          <button
            onClick={() => onAnalyze?.(candidates[0]!.symbol, { selection_context: rowSelectionContext(candidates[0]!) })}
            className="inline-flex items-center gap-1.5 rounded-lg border border-teal-500/30 bg-teal-500/10 px-3 py-1.5 text-xs font-medium text-teal-300 transition hover:bg-teal-500/20"
          >
            <ArrowRight className="h-3.5 w-3.5" />
            深度分析第1名
          </button>
          <button
            onClick={() => onAddWatchlist(candidates.map((c) => c.symbol))}
            className="inline-flex items-center gap-1.5 rounded-lg border border-stone-700 px-3 py-1.5 text-xs font-medium text-stone-300 transition hover:bg-stone-800"
          >
            <Plus className="h-3.5 w-3.5" />
            全部加入关注
          </button>
        </div>
      )}
    </div>
  );
}

function CandidateExpanded({
  row,
  feedback,
  onAction,
}: {
  row: CandidateRow;
  feedback?: string;
  onAction: (action: "adopt" | "watch" | "private" | "ignore") => void;
}) {
  const lessonHits = row.strategyLessonHits ?? [];
  return (
    <div className="grid gap-3 text-xs text-stone-400 lg:grid-cols-2">
      <div className="space-y-2">
        <InfoBlock title="LLM 分析结论" empty="暂无 LLM reasoning">
          {row.reasoning}
        </InfoBlock>
        <ListBlock title="关键催化剂" items={row.keyCatalysts} empty="暂无明确催化剂" tone="emerald" />
        <ListBlock title="关键风险" items={row.keyRisks} empty="暂无显式风险" tone="amber" />
        <TradePlanBlock row={row} />
        <QuantGateBlock row={row} />
        <DataCoverageBlock row={row} />
      </div>
      <div className="space-y-2">
        <ListBlock
          title="反思经验命中"
          items={lessonHits.map((item) => String(item.finding || item.suggested_adjustment || item.id || ""))}
          empty="本次未命中历史策略经验"
          tone="purple"
        />
        {row.lessonAdjustmentReason && (
          <div className="rounded border border-purple-500/20 bg-purple-500/5 p-2 text-purple-200">
            {row.lessonAdjustmentReason}
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <button onClick={() => onAction("adopt")} className="rounded border border-emerald-500/30 px-2 py-1 text-emerald-300 hover:bg-emerald-500/10">
            加入次日计划
          </button>
          <button onClick={() => onAction("watch")} className="rounded border border-teal-500/30 px-2 py-1 text-teal-300 hover:bg-teal-500/10">
            仅观察
          </button>
          <button onClick={() => onAction("private")} className="rounded border border-stone-600 px-2 py-1 text-stone-300 hover:bg-stone-800">
            私人复盘
          </button>
          <button onClick={() => onAction("ignore")} className="rounded border border-stone-700 px-2 py-1 text-stone-500 hover:bg-stone-800">
            忽略
          </button>
        </div>
        {feedback && <div className="rounded border border-stone-700 bg-stone-900 p-2 text-stone-300">{feedback}</div>}
      </div>
    </div>
  );
}

function QuantGateBlock({ row }: { row: CandidateRow }) {
  const gates = row.quantGateReasons ?? [];
  return (
    <div className="rounded border border-stone-800 bg-stone-900 p-2">
      <div className="mb-1 font-medium text-stone-200">量化门控</div>
      {gates.length === 0 ? (
        <div className="text-stone-500">暂无 MCP 量化门控明细</div>
      ) : (
        <div className="space-y-1">
          {gates.map((item, index) => (
            <div key={`${item.gate}-${index}`} className="flex items-start gap-2 rounded bg-stone-950 px-2 py-1">
              <span className={item.passed === false ? "text-red-300" : "text-emerald-300"}>
                {item.passed === false ? "未过" : "通过"}
              </span>
              <div className="min-w-0">
                <div className="font-mono text-stone-200">{item.gate}</div>
                {item.detail && <div className="text-stone-500">{item.detail}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DataCoverageBlock({ row }: { row: CandidateRow }) {
  const warnings = row.dataCoverageWarnings ?? [];
  const metrics = row.keyMetrics ?? {};
  return (
    <div className="rounded border border-stone-800 bg-stone-900 p-2">
      <div className="mb-1 font-medium text-stone-200">数据覆盖</div>
      {warnings.length === 0 ? (
        <div className="text-emerald-300">MCP 未标记关键数据缺失。</div>
      ) : (
        <div className="space-y-1">
          {warnings.map((warning) => (
            <div key={warning} className="text-amber-300">• {warning}</div>
          ))}
          <div className="text-stone-500">
            这些字段来自 MCP 的 data_coverage。若显示 missing，说明当前数据源没有返回该类因子，前端没有进行默认补值。
          </div>
        </div>
      )}
      {Object.keys(metrics).length > 0 && (
        <div className="mt-2 grid gap-1 sm:grid-cols-2">
          {Object.entries(metrics).slice(0, 8).map(([key, value]) => (
            <div key={key} className="rounded bg-stone-950 px-2 py-1 font-mono text-stone-400">
              {key}: {String(value)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TradePlanBlock({ row }: { row: CandidateRow }) {
  const entryCondition = row.actionPlan?.entry_condition;
  return (
    <div className="rounded border border-stone-800 bg-stone-900 p-2">
      <div className="mb-1 font-medium text-stone-200">价格与交易计划</div>
      <div className="grid gap-2 sm:grid-cols-2">
        <MiniMetric label="收盘价" value={row.latestPrice !== undefined ? `${formatPrice(row.latestPrice)}${row.priceTradeDate ? ` · ${row.priceTradeDate}` : ""}` : "数据源缺失"} />
        <MiniMetric label="入场区间" value={row.entryZone?.length ? row.entryZone.map(formatPrice).join(" - ") : "未生成"} />
        <MiniMetric label="止损" value={row.stopLoss !== undefined ? formatPrice(row.stopLoss) : "未生成"} />
        <MiniMetric label="目标价" value={row.targets?.length ? row.targets.map(formatPrice).join(" / ") : "未生成"} />
      </div>
      {Boolean(entryCondition) && (
        <div className="mt-2 text-stone-400">{String(entryCondition)}</div>
      )}
      <div className="mt-2 text-stone-600">
        目标价来自融合层：优先按 ATR 倍数生成；无 ATR 时使用收盘价的固定比例作为展示参考。
      </div>
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-stone-800 bg-stone-950 px-2 py-1.5">
      <div className="text-stone-500">{label}</div>
      <div className="mt-0.5 font-mono text-stone-200">{value}</div>
    </div>
  );
}

function InfoBlock({ title, children, empty }: { title: string; children?: string; empty: string }) {
  return (
    <div className="rounded border border-stone-800 bg-stone-900 p-2">
      <div className="mb-1 font-medium text-stone-200">{title}</div>
      <div className="leading-5 text-stone-400">{children || empty}</div>
    </div>
  );
}

function ListBlock({ title, items, empty, tone }: { title: string; items?: string[]; empty: string; tone: "emerald" | "amber" | "purple" }) {
  const color = tone === "emerald" ? "text-emerald-300" : tone === "amber" ? "text-amber-300" : "text-purple-300";
  const values = (items ?? []).filter(Boolean);
  return (
    <div className="rounded border border-stone-800 bg-stone-900 p-2">
      <div className="mb-1 font-medium text-stone-200">{title}</div>
      {values.length === 0 ? (
        <div className="text-stone-500">{empty}</div>
      ) : (
        <ul className="space-y-1">
          {values.map((item, index) => (
            <li key={`${item}-${index}`} className={color}>• {item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function explainFinalDecision(decision?: string) {
  const value = String(decision || "").toUpperCase();
  if (value === "BUY") return "最终状态机结论：量化通过，LLM 支持，催化剂较明确。";
  if (value === "WATCHLIST") return "最终状态机结论：值得观察，但缺少足够买入确认。";
  if (value === "MONITOR") return "最终状态机结论：保留监控，不进入强候选。";
  if (value === "HOLD_REVIEW") return "最终状态机结论：量化与 LLM 有分歧，需要人工复核。";
  if (value === "SKIP") return "最终状态机结论：交易性、风险或证据不满足要求。";
  return "最终状态机结论。";
}

function explainGateReason(reason: string) {
  if (reason.includes("llm_neutral")) return "LLM 对量化信号保持中性，因此降级观察。";
  if (reason.includes("quant_buy")) return "量化层认为该标的达到候选买入门槛。";
  if (reason.includes("default_gate")) return "未触发更强规则，使用默认门控结果。";
  if (reason.includes("missing")) return "关键数据缺失，需要谨慎解读。";
  return "状态机门控原因。";
}

function rowSelectionContext(row: CandidateRow): Record<string, unknown> {
  const ctx: Record<string, unknown> = {
    symbol: row.symbol,
    final_decision: row.decision,
  };
  if (row.score !== undefined) ctx.display_score = row.score;
  if (row.entryZone) ctx.entry_zone = row.entryZone;
  if (row.stopLoss !== undefined) ctx.stop_loss = row.stopLoss;
  if (row.targets) ctx.targets = row.targets;
  if (row.actionPlan) ctx.action_plan = row.actionPlan;
  if (row.reasoning) ctx.reasoning = row.reasoning;
  if (row.priceTradeDate) ctx.price_trade_date = row.priceTradeDate;
  return ctx;
}

function sessionLabel(state: string): string {
  if (state === "before_close_data") return "盘前";
  if (state === "after_close_data") return "盘后";
  if (state === "non_trading") return "非交易日";
  return state;
}

function formatPrice(value: number) {
  return Number.isFinite(value) ? value.toFixed(2) : "-";
}

/**
 * Parse raw candidate data from WebSocket into typed rows.
 */
export function parseCandidates(raw: unknown): CandidateRow[] {
  if (!Array.isArray(raw) || raw.length === 0) return [];
  return raw.slice(0, 10).map((item, index) => {
    const row = item as Record<string, unknown>;
    const symbol = String(row.symbol ?? row.ticker ?? row.ts_code ?? `#${index + 1}`);
    const name = row.name ? String(row.name) : undefined;
    const industry = row.industry ? String(row.industry) : undefined;
    const board = row.board ? String(row.board) : undefined;
    const decision = row.final_decision ?? row.signal ?? row.quant_decision;
    const score = row.display_score ?? row.final_score ?? row.score ?? row.total_score;
    const quantScore = row.quant_score;
    const keyMetrics = row.key_metrics as Record<string, unknown> | undefined;
    const factorSnapshot = row.factor_snapshot as Record<string, unknown> | undefined;
    const latestPrice = firstNumber(row.latest_price, row.current_price, row.close, keyMetrics?.latest_price, keyMetrics?.close, factorSnapshot?.latest_price, factorSnapshot?.close);
    const entryZone = numberArray(row.entry_zone);
    const targets = numberArray(row.targets ?? (row.action_plan as Record<string, unknown> | undefined)?.take_profit);
    const stopLoss = firstNumber(row.stop_loss, (row.action_plan as Record<string, unknown> | undefined)?.stop_loss);
    const dataCoverage = row.data_coverage as Record<string, unknown> | undefined;
    const dataCoverageWarnings = dataCoverage
      ? Object.entries(dataCoverage)
          .filter(([, value]) => String(value).toLowerCase() === "missing")
          .map(([key]) => `${key} missing`)
      : undefined;
    return {
      rank: index + 1,
      symbol,
      name,
      industry,
      board,
      decision: decision ? String(decision) : undefined,
      quantDecision: row.quant_decision ? String(row.quant_decision) : undefined,
      llmView: row.llm_view ? String(row.llm_view) : undefined,
      catalystStrength: row.catalyst_strength ? String(row.catalyst_strength) : undefined,
      riskAssessment: row.risk_assessment ? String(row.risk_assessment) : undefined,
      score: score !== undefined ? Number(score) : undefined,
      quantScore: quantScore !== undefined ? Number(quantScore) : undefined,
      latestPrice,
      priceTradeDate: row.price_trade_date ? String(row.price_trade_date) : undefined,
      entryZone,
      stopLoss,
      targets,
      actionPlan: row.action_plan as Record<string, unknown> | undefined,
      keyMetrics,
      gate_reasons: Array.isArray(row.gate_reasons) ? row.gate_reasons.map(String) : undefined,
      quantGateReasons: parseQuantGateReasons(row.quant_gate_reasons ?? row.mcp_gate_reasons),
      dataCoverageWarnings,
      keyCatalysts: Array.isArray(row.key_catalysts) ? row.key_catalysts.map(String) : undefined,
      keyRisks: Array.isArray(row.key_risks) ? row.key_risks.map(String) : undefined,
      reasoning: typeof row.reasoning === "string"
        ? row.reasoning
        : typeof (row.llm_review as Record<string, unknown> | undefined)?.reasoning === "string"
          ? String((row.llm_review as Record<string, unknown>).reasoning)
          : undefined,
      strategyLessonHits: Array.isArray(row.strategy_lesson_hits)
        ? row.strategy_lesson_hits as Array<Record<string, unknown>>
        : undefined,
      lessonAdjustmentReason: row.lesson_adjustment_reason ? String(row.lesson_adjustment_reason) : undefined,
      raw: row,
    };
  });
}

function parseQuantGateReasons(raw: unknown): QuantGateReason[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const gates = raw.flatMap((item) => {
    if (typeof item === "string") {
      return [{ gate: item }];
    }
    if (!item || typeof item !== "object") return [];
    const value = item as Record<string, unknown>;
    const gate = value.gate ?? value.name ?? value.type;
    if (!gate) return [];
    return [{
      gate: String(gate),
      passed: typeof value.passed === "boolean" ? value.passed : undefined,
      detail: value.detail ? String(value.detail) : undefined,
    }];
  });
  return gates.length ? gates : undefined;
}

function firstNumber(...values: unknown[]): number | undefined {
  for (const value of values) {
    if (value === undefined || value === null || value === "") continue;
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return numeric;
  }
  return undefined;
}

function numberArray(value: unknown): number[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const values = value.map(Number).filter(Number.isFinite);
  return values.length ? values : undefined;
}
