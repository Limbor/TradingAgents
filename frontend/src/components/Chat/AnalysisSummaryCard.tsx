import { MemoryDetails } from "@/pages/AgentWorkspace/MemoryPanel";
import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Bell, ExternalLink, GitBranch, Plus, Shield, Target, TrendingDown, TrendingUp } from "lucide-react";
import { createPlan, saveCandidateAction } from "@/api/client";

export interface TradeConditionShape {
  kind?: string; // entry | full | stop | take_profit
  trigger_action?: "ENTER" | "ADD" | "HOLD" | "REDUCE" | "EXIT";
  description?: string;
  source?: string;
  order_quantity?: number;
  target_quantity?: number;
}

interface ExecutionValidationShape {
  status?: "valid" | "adjusted" | "blocked";
  current_quantity?: number;
  lot_size?: number;
  original_plan_action?: string;
  executable_plan_action?: string;
  warnings?: string[];
}

export interface TradePlanShape {
  plan_action?: "ENTER" | "ADD" | "HOLD" | "REDUCE" | "EXIT";
  action_zone?: number[];
  invalidation_level?: number;
  objective_levels?: number[];
  entry_zone?: number[];
  stop_loss?: number;
  targets?: number[];
  position_pct?: number;
  order_quantity?: number;
  target_quantity?: number;
  conditions?: TradeConditionShape[];
  execution_validation?: ExecutionValidationShape;
}

export interface AnalysisSummary {
  memory_trace?: Record<string, unknown>;
  rating?: string;
  target_price?: string;
  confidence?: number;
  reasons?: string[];
  runId?: string;
  symbol?: string;
  name?: string;
  plan?: TradePlanShape;
  selection_alignment?: {
    status?: string;
    selection_decision?: string;
    analysis_rating?: string;
    requires_review?: boolean;
    explanation?: string;
    explicit_explanation?: boolean;
    plan_consistency?: { status?: string };
  };
}

interface AnalysisSummaryCardProps {
  summary: AnalysisSummary;
  /** Selection plan handed off from the scanner/daily pipeline, rendered alongside
   * the analysis plan so the user can see where they agree or conflict. */
  selectionPlan?: Record<string, unknown>;
  /** Library artifact id of this analysis report (links the adopted plan / reflection case). */
  artifactId?: string;
  onAddHolding?: (symbol: string) => void;
  onAnalyzeMore?: (symbol: string) => void;
}

function ratingColor(rating?: string) {
  if (!rating) return "text-ui-muted";
  const r = rating.toLowerCase();
  if (r.includes("buy") || r.includes("strong buy") || r.includes("overweight")) return "text-ui-success";
  if (r.includes("sell") || r.includes("underweight")) return "text-ui-danger";
  if (r.includes("hold") || r.includes("neutral")) return "text-ui-warning";
  return "text-ui-body";
}

function RatingIcon({ rating }: { rating?: string }) {
  if (!rating) return null;
  const r = rating.toLowerCase();
  if (r.includes("buy") || r.includes("overweight")) return <TrendingUp className="h-5 w-5 text-ui-success" />;
  if (r.includes("sell") || r.includes("underweight")) return <TrendingDown className="h-5 w-5 text-ui-danger" />;
  return <Target className="h-5 w-5 text-ui-warning" />;
}

function formatPriceNum(value: unknown): string {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : "-";
}

function toPlan(raw: unknown): TradePlanShape | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const r = raw as Record<string, unknown>;
  const actionPlan = r.action_plan && typeof r.action_plan === "object" ? (r.action_plan as Record<string, unknown>) : undefined;
  const positionPct =
    typeof r.position_pct === "number" ? r.position_pct :
    typeof actionPlan?.position_pct === "number" ? actionPlan.position_pct : undefined;
  const conditionsRaw = Array.isArray(r.conditions) ? r.conditions : undefined;
  const conditions = conditionsRaw
    ?.map((c): TradeConditionShape | null => {
      if (!c || typeof c !== "object") return null;
      const cond = c as Record<string, unknown>;
      return {
        kind: typeof cond.kind === "string" ? cond.kind : undefined,
        trigger_action: typeof cond.trigger_action === "string"
          ? cond.trigger_action.toUpperCase() as TradeConditionShape["trigger_action"]
          : undefined,
        description: typeof cond.description === "string" ? cond.description : undefined,
        source: typeof cond.source === "string" ? cond.source : undefined,
        order_quantity: typeof cond.order_quantity === "number" ? cond.order_quantity : undefined,
        target_quantity: typeof cond.target_quantity === "number" ? cond.target_quantity : undefined,
      };
    })
    .filter((c): c is TradeConditionShape => c !== null);
  const numericArray = (value: unknown): number[] | undefined =>
    Array.isArray(value)
      ? value.filter((item) => Number.isFinite(Number(item))).map(Number)
      : undefined;
  const actionZone = numericArray(r.action_zone ?? r.entry_zone);
  const objectiveLevels = numericArray(r.objective_levels ?? r.targets);
  const invalidation = r.invalidation_level ?? r.stop_loss;
  return {
    plan_action: typeof r.plan_action === "string" ? r.plan_action.toUpperCase() as TradePlanShape["plan_action"] : undefined,
    action_zone: actionZone,
    invalidation_level: typeof invalidation === "number" ? invalidation : undefined,
    objective_levels: objectiveLevels,
    entry_zone: actionZone,
    stop_loss: typeof invalidation === "number" ? invalidation : undefined,
    targets: objectiveLevels,
    position_pct: positionPct,
    order_quantity: typeof r.order_quantity === "number" ? r.order_quantity : undefined,
    target_quantity: typeof r.target_quantity === "number" ? r.target_quantity : undefined,
    conditions: conditions && conditions.length ? conditions : undefined,
    execution_validation: r.execution_validation && typeof r.execution_validation === "object"
      ? r.execution_validation as ExecutionValidationShape
      : undefined,
  };
}

function conditionStyle(kind: string | undefined, direction: PlanDirection, triggerAction?: string): { label: string; icon: typeof GitBranch; color: string } {
  const action = String(triggerAction || "").toUpperCase();
  if (action === "ENTER") return { label: "建仓", icon: GitBranch, color: "text-ui-accent" };
  if (action === "ADD") return { label: "加仓", icon: TrendingUp, color: "text-ui-success" };
  if (action === "REDUCE") return { label: "减仓", icon: TrendingDown, color: "text-ui-warning" };
  if (action === "EXIT") return { label: "清仓", icon: TrendingDown, color: "text-ui-danger" };
  if (action === "HOLD") return { label: "观望", icon: Shield, color: "text-ui-body" };
  const k = String(kind || "").toLowerCase();
  if (direction === "exit") {
    if (k === "entry") return { label: "减仓", icon: TrendingDown, color: "text-ui-accent" };
    if (k === "full") return { label: "清仓", icon: TrendingDown, color: "text-ui-danger" };
    if (k === "stop") return { label: "看空失效", icon: Shield, color: "text-ui-warning" };
    if (k === "take_profit") return { label: "下行止盈", icon: Target, color: "text-ui-warning" };
  }
  if (k === "entry") return { label: "建仓", icon: GitBranch, color: "text-ui-accent" };
  if (k === "full") return { label: "满仓", icon: TrendingUp, color: "text-ui-success" };
  if (k === "stop") return { label: "止损", icon: Shield, color: "text-ui-danger" };
  if (k === "take_profit") return { label: "止盈", icon: Target, color: "text-ui-warning" };
  return { label: kind || "条件", icon: AlertTriangle, color: "text-ui-body" };
}

type PlanDirection = "long" | "exit";

/** Bearish ratings (or inverted levels: all targets below the zone) mean the
 * "entry zone" is really a reduce/exit zone — relabel instead of showing a
 * long-style card where the entry sits above every target. */
function planDirection(rating: string | undefined, plan: TradePlanShape | undefined): PlanDirection {
  if (plan?.plan_action === "REDUCE" || plan?.plan_action === "EXIT") return "exit";
  if (plan?.plan_action === "ENTER" || plan?.plan_action === "ADD") return "long";
  const r = (rating ?? "").toLowerCase();
  if (r.includes("sell") || r.includes("underweight") || r.includes("reduce") || r.includes("减持") || r.includes("减仓") || r.includes("卖出") || r.includes("偏空")) return "exit";
  if (plan?.entry_zone?.length && plan?.targets?.length && Math.max(...plan.targets) < Math.min(...plan.entry_zone)) {
    return "exit";
  }
  return "long";
}

function PlanCard({ plan, title, tone, direction = "long" }: { plan: TradePlanShape; title: string; tone: "selection" | "analysis"; direction?: PlanDirection }) {
  const actionZone = plan.action_zone ?? plan.entry_zone;
  const invalidationLevel = plan.invalidation_level ?? plan.stop_loss;
  const objectiveLevels = plan.objective_levels ?? plan.targets;
  const hasLevels = actionZone?.length || invalidationLevel !== undefined || objectiveLevels?.length || plan.position_pct !== undefined || plan.order_quantity !== undefined || plan.target_quantity !== undefined;
  const hasConditions = plan.conditions && plan.conditions.length > 0;
  if (!hasLevels && !hasConditions) return null;
  const toneClass = tone === "selection" ? "border-ui-info/20 bg-ui-info/5" : "border-ui-accent/20 bg-ui-accent/5";
  const isExit = direction === "exit";
  return (
    <div className={`rounded-lg border p-3 ${toneClass}`}>
      <div className="mb-2 text-xs font-medium text-ui-body">{title}</div>
      {plan.execution_validation?.status && plan.execution_validation.status !== "valid" && (
        <div className="mb-2 rounded border border-ui-warning/30 bg-ui-warning/10 px-2 py-1.5 text-xs text-ui-warning">
          <div className="font-medium">A 股交易单位校验已拦截不可执行数量</div>
          {(plan.execution_validation.warnings ?? []).map((warning, index) => (
            <div key={index} className="mt-0.5 text-ui-warning/80">{warning}</div>
          ))}
        </div>
      )}
      {hasLevels && (
        <div className="grid gap-2 sm:grid-cols-2 text-xs">
          <PlanMetric label={isExit ? "减仓/卖出区间" : "建仓区间"} value={actionZone?.length ? actionZone.map(formatPriceNum).join(" - ") : undefined} />
          <PlanMetric label={isExit ? "风控位" : "止损"} value={invalidationLevel !== undefined ? formatPriceNum(invalidationLevel) : undefined} />
          <PlanMetric label={isExit ? "下方目标价" : "目标价"} value={objectiveLevels?.length ? objectiveLevels.map(formatPriceNum).join(" / ") : undefined} />
          <PlanMetric label={isExit ? "建议仓位上限" : "仓位"} value={plan.position_pct !== undefined ? `${plan.position_pct}%` : undefined} />
          {plan.order_quantity !== undefined && <PlanMetric label="本次可执行数量" value={`${plan.order_quantity} 股`} />}
          {plan.target_quantity !== undefined && <PlanMetric label="执行后持仓" value={`${plan.target_quantity} 股`} />}
        </div>
      )}
      {isExit && hasLevels && (
        <p className="mt-2 text-xs text-ui-faint">
          评级偏空：上方区间用于减仓/卖出，下方目标价用于继续减持、清仓或重新评估，不代表加仓。
        </p>
      )}
      {hasConditions && (
        <div className="mt-2 space-y-1">
          {plan.conditions!.map((c, i) => {
            const st = conditionStyle(c.kind, direction, c.trigger_action);
            const Icon = st.icon;
            return (
              <div key={i} className="flex items-start gap-1.5 text-xs">
                <Icon className={`mt-0.5 h-3 w-3 shrink-0 ${st.color}`} />
                <span className={`shrink-0 font-mono ${st.color}`}>[{st.label}]</span>
                <span className="text-ui-body">{c.description}</span>
                {c.source && <span className="text-ui-faint">· {c.source}</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function PlanMetric({ label, value }: { label: string; value?: string }) {
  return (
    <div className="rounded border border-ui-line bg-ui-subtle px-2 py-1.5">
      <div className="text-ui-faint">{label}</div>
      <div className="mt-0.5 font-mono text-ui-body">{value ?? "—"}</div>
    </div>
  );
}

export function AnalysisSummaryCard({ summary, selectionPlan, artifactId, onAddHolding, onAnalyzeMore }: AnalysisSummaryCardProps) {
  const { rating, target_price, confidence, reasons, runId, symbol, name, plan, selection_alignment: alignment } = summary;
  const selectionPlanShape = toPlan(selectionPlan);
  const selectionRating = selectionPlan && typeof selectionPlan.rating === "string" ? selectionPlan.rating : undefined;
  const [feedback, setFeedback] = useState<string | null>(null);

  const handleAdoptPlan = async () => {
    if (!symbol || !plan) return;
    setFeedback("正在纳入计划…");
    try {
      await createPlan({
        symbol,
        name,
        plan_action: plan.plan_action,
        action_zone: plan.action_zone ?? plan.entry_zone,
        invalidation_level: plan.invalidation_level ?? plan.stop_loss,
        objective_levels: plan.objective_levels ?? plan.targets,
        entry_zone: plan.entry_zone,
        stop_loss: plan.stop_loss ?? undefined,
        targets: plan.targets,
        position_pct: plan.position_pct ?? undefined,
        conditions: plan.conditions,
        rating: rating ?? undefined,
        status: "active",
        source: "analysis",
        artifact_id: artifactId,
      });
      setFeedback("✓ 已纳入计划（active），收盘后将自动监控条件并提醒");
    } catch (e) {
      setFeedback("纳入计划失败：" + (e instanceof Error ? e.message : String(e)));
    }
  };

  const handleEnrollReflection = async () => {
    if (!symbol) return;
    setFeedback("正在加入反思…");
    try {
      const r = await saveCandidateAction({
        action: "private",
        symbol,
        artifact_id: artifactId,
        payload: { rating, plan, source: "stock_analysis" },
      });
      setFeedback(r.message || "✓ 已加入反思队列");
    } catch (e) {
      setFeedback("加入反思失败：" + (e instanceof Error ? e.message : String(e)));
    }
  };

  const planHasLevels = !!plan && (
    !!plan.action_zone?.length ||
    plan.invalidation_level !== undefined ||
    !!plan.objective_levels?.length ||
    !!plan.entry_zone?.length ||
    plan.stop_loss !== undefined ||
    !!plan.targets?.length ||
    !!plan.conditions?.length
  );
  const direction = planDirection(rating, plan);
  const canAddHolding = direction === "long" && !!rating && /(buy|overweight)/i.test(rating);
  const planExecutionBlocked = !!plan?.execution_validation?.status
    && plan.execution_validation.status !== "valid";

  return (
    <div className="space-y-3 rounded-lg border border-ui-line bg-ui-panel p-4">
      {summary.memory_trace && <MemoryDetails trace={summary.memory_trace} />}
      {/* Header: Rating + Target + Confidence */}
      <div className="flex items-center gap-4">
        <RatingIcon rating={rating} />
        <div className="flex-1">
          {(name || symbol) && (
            <div className="mb-0.5">
              {name && <span className="text-sm font-semibold text-ui-ink">{name}</span>}
              {symbol && (
                <span className={`ml-1 font-mono text-xs ${name ? "text-ui-faint" : "text-ui-ink font-semibold"}`}>
                  {symbol}
                </span>
              )}
            </div>
          )}
          <div className="flex items-baseline gap-3">
            <span className={`text-lg font-bold ${ratingColor(rating)}`}>{rating ?? "N/A"}</span>
            {target_price && (
              <span className="text-sm text-ui-muted">
                目标价: <span className="font-mono text-ui-body">{target_price}</span>
              </span>
            )}
            {confidence !== undefined && (
              <span className="text-sm text-ui-muted">
                信心: <span className="font-mono text-ui-body">{confidence}%</span>
              </span>
            )}
          </div>
        </div>
      </div>

      {alignment?.selection_decision && (
        <div className={`rounded-lg border p-3 text-xs ${alignment.requires_review ? "border-ui-warning/30 bg-ui-warning/10 text-ui-warning" : "border-ui-success/20 bg-ui-success/5 text-ui-success"}`}>
          <div className="flex items-start gap-2">
            {alignment.requires_review ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> : <Shield className="mt-0.5 h-4 w-4 shrink-0" />}
            <div>
              <div className="font-medium">
                选股 {alignment.selection_decision} → 个股分析 {alignment.analysis_rating ?? rating ?? "N/A"}
                {alignment.requires_review ? " · 需要复核" : " · 结论可兼容"}
              </div>
              {alignment.explanation && <p className="mt-1 text-ui-body">{alignment.explanation}</p>}
              {alignment.requires_review && !alignment.explicit_explanation && (
                <p className="mt-1 text-ui-warning">本次反转缺少明确的新证据说明，不能直接转为交易动作。</p>
              )}
              {alignment.plan_consistency?.status === "conflict" && (
                <p className="mt-1 text-ui-danger">评级与交易计划动作冲突，请检查 ENTER/REDUCE/EXIT 方向。</p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Selection plan vs analysis plan comparison */}
      {selectionPlanShape && (selectionPlanShape.entry_zone?.length || selectionPlanShape.stop_loss !== undefined || selectionPlanShape.targets?.length) && (
        <PlanCard plan={selectionPlanShape} title="选股计划（来自选股结论）" tone="selection" direction={planDirection(selectionRating, selectionPlanShape)} />
      )}
      {plan && <PlanCard plan={plan} title="分析计划（本次单股分析）" tone="analysis" direction={planDirection(rating, plan)} />}
      {selectionPlanShape && plan && (
        <p className="text-xs text-ui-faint">
          对比上方两份计划：若操作区间、风控位、目标价或评级不一致，说明选股与深度分析存在分歧，请重点复核。
        </p>
      )}

      {/* Key Reasons */}
      {reasons && reasons.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-ui-faint">关键论据</p>
          <ol className="space-y-1 pl-4">
            {reasons.map((reason, i) => (
              <li key={i} className="text-sm text-ui-body list-decimal">{reason}</li>
            ))}
          </ol>
        </div>
      )}

      {/* Action Buttons */}
      <div className="flex flex-wrap items-center gap-2 border-t border-ui-line pt-3">
        {runId && (
          <Link
            to={`/library?run_id=${runId}`}
            className="inline-flex items-center gap-1.5 rounded-lg border border-ui-accent/30 bg-ui-accent/10 px-3 py-1.5 text-xs font-medium text-ui-accent transition hover:bg-ui-accent/20"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            查看完整报告
          </Link>
        )}
        {symbol && onAddHolding && canAddHolding && (
          <button
            onClick={() => onAddHolding(symbol)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-ui-strong px-3 py-1.5 text-xs font-medium text-ui-body transition hover:bg-ui-hover"
          >
            <Plus className="h-3.5 w-3.5" />
            加入持仓
          </button>
        )}
        {symbol && onAnalyzeMore && (
          <button
            onClick={() => onAnalyzeMore(symbol)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-ui-strong px-3 py-1.5 text-xs font-medium text-ui-body transition hover:bg-ui-hover"
          >
            <TrendingUp className="h-3.5 w-3.5" />
            对比同行业
          </button>
        )}
        {symbol && planHasLevels && !planExecutionBlocked && (
          <button
            onClick={handleAdoptPlan}
            className="inline-flex items-center gap-1.5 rounded-lg border border-ui-success/30 bg-ui-success/10 px-3 py-1.5 text-xs font-medium text-ui-success transition hover:bg-ui-success/20"
          >
            <Shield className="h-3.5 w-3.5" />
            纳入计划
          </button>
        )}
        {symbol && (
          <button
            onClick={handleEnrollReflection}
            className="inline-flex items-center gap-1.5 rounded-lg border border-ui-info/30 bg-ui-info/10 px-3 py-1.5 text-xs font-medium text-ui-info transition hover:bg-ui-info/20"
          >
            <Bell className="h-3.5 w-3.5" />
            加入反思
          </button>
        )}
        {feedback && (
          <span className="text-xs text-ui-muted">{feedback}</span>
        )}
      </div>
    </div>
  );
}

/**
 * Build an AnalysisSummary from the structured_conclusion dict captured on
 * skill_complete. This replaces the old regex scrape of report text — the
 * structured plan (entry/stop/targets/conditions) is only available here.
 */
export function parseAnalysisSummaryFromStructured(data: unknown, runId?: string): AnalysisSummary | null {
  if (!data || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  return {
    rating: typeof d.rating === "string" ? d.rating : undefined,
    target_price: d.target_price !== undefined && d.target_price !== null ? String(d.target_price) : undefined,
    confidence: typeof d.confidence === "number" ? d.confidence : undefined,
    reasons: Array.isArray(d.reasons) ? d.reasons.map(String) : undefined,
    symbol: typeof d.symbol === "string" ? d.symbol : undefined,
    name: typeof d.name === "string" ? d.name : undefined,
    runId,
    plan: toPlan(d.plan),
    memory_trace: d.memory_trace && typeof d.memory_trace === "object" ? d.memory_trace as Record<string, unknown> : undefined,
    selection_alignment: d.selection_alignment && typeof d.selection_alignment === "object"
      ? d.selection_alignment as AnalysisSummary["selection_alignment"]
      : undefined,
  };
}

/**
 * Legacy fallback: try to extract a structured analysis summary from report_chunk
 * content (regex). Kept for older runs that did not capture structured_conclusion.
 */
export function parseAnalysisSummary(content: string, runId?: string): AnalysisSummary | null {
  const ratingMatch = content.match(/(?:rating|评级|recommendation|建议)[：:\s]*([A-Za-z\s]+?)(?:\n|$|[|,;])/i);
  const targetMatch = content.match(/(?:target|目标价)[：:\s]*([¥$]?[\d,.]+)/i);
  const confidenceMatch = content.match(/(?:confidence|信心|把握)[：:\s]*(\d+)/i);

  if (!ratingMatch && !targetMatch) return null;

  const reasonMatches = content.match(/^\d+[.、]\s*(.+)$/gm);
  const reasons = reasonMatches?.slice(0, 5).map((r) => r.replace(/^\d+[.、]\s*/, ""));

  return {
    rating: ratingMatch?.[1]?.trim(),
    target_price: targetMatch?.[1]?.trim(),
    confidence: confidenceMatch ? Number(confidenceMatch[1]) : undefined,
    reasons,
    runId,
  };
}
