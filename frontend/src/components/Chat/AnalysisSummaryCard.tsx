import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Bell, ExternalLink, GitBranch, Plus, Shield, Target, TrendingDown, TrendingUp } from "lucide-react";
import { createPlan, saveCandidateAction } from "@/api/client";

export interface TradeConditionShape {
  kind?: string; // entry | full | stop | take_profit
  description?: string;
  source?: string;
}

export interface TradePlanShape {
  entry_zone?: number[];
  stop_loss?: number;
  targets?: number[];
  position_pct?: number;
  conditions?: TradeConditionShape[];
}

export interface AnalysisSummary {
  rating?: string;
  target_price?: string;
  confidence?: number;
  reasons?: string[];
  runId?: string;
  symbol?: string;
  plan?: TradePlanShape;
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
  if (!rating) return "text-stone-400";
  const r = rating.toLowerCase();
  if (r.includes("buy") || r.includes("strong buy") || r.includes("overweight")) return "text-emerald-300";
  if (r.includes("sell") || r.includes("underweight")) return "text-red-300";
  if (r.includes("hold") || r.includes("neutral")) return "text-amber-300";
  return "text-stone-300";
}

function RatingIcon({ rating }: { rating?: string }) {
  if (!rating) return null;
  const r = rating.toLowerCase();
  if (r.includes("buy") || r.includes("overweight")) return <TrendingUp className="h-5 w-5 text-emerald-300" />;
  if (r.includes("sell") || r.includes("underweight")) return <TrendingDown className="h-5 w-5 text-red-300" />;
  return <Target className="h-5 w-5 text-amber-300" />;
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
        description: typeof cond.description === "string" ? cond.description : undefined,
        source: typeof cond.source === "string" ? cond.source : undefined,
      };
    })
    .filter((c): c is TradeConditionShape => c !== null);
  return {
    entry_zone: Array.isArray(r.entry_zone) ? r.entry_zone.filter((v) => Number.isFinite(Number(v))).map(Number) : undefined,
    stop_loss: typeof r.stop_loss === "number" ? r.stop_loss : undefined,
    targets: Array.isArray(r.targets) ? r.targets.filter((v) => Number.isFinite(Number(v))).map(Number) : undefined,
    position_pct: positionPct,
    conditions: conditions && conditions.length ? conditions : undefined,
  };
}

function conditionStyle(kind?: string): { label: string; icon: typeof GitBranch; color: string } {
  const k = String(kind || "").toLowerCase();
  if (k === "entry") return { label: "建仓", icon: GitBranch, color: "text-teal-300" };
  if (k === "full") return { label: "满仓", icon: TrendingUp, color: "text-emerald-300" };
  if (k === "stop") return { label: "止损", icon: Shield, color: "text-red-300" };
  if (k === "take_profit") return { label: "止盈", icon: Target, color: "text-amber-300" };
  return { label: kind || "条件", icon: AlertTriangle, color: "text-stone-300" };
}

function PlanCard({ plan, title, tone }: { plan: TradePlanShape; title: string; tone: "selection" | "analysis" }) {
  const hasLevels = plan.entry_zone?.length || plan.stop_loss !== undefined || plan.targets?.length || plan.position_pct !== undefined;
  const hasConditions = plan.conditions && plan.conditions.length > 0;
  if (!hasLevels && !hasConditions) return null;
  const toneClass = tone === "selection" ? "border-sky-500/20 bg-sky-500/5" : "border-teal-500/20 bg-teal-500/5";
  return (
    <div className={`rounded-lg border p-3 ${toneClass}`}>
      <div className="mb-2 text-xs font-medium text-stone-300">{title}</div>
      {hasLevels && (
        <div className="grid gap-2 sm:grid-cols-2 text-xs">
          <PlanMetric label="建仓区间" value={plan.entry_zone?.length ? plan.entry_zone.map(formatPriceNum).join(" - ") : undefined} />
          <PlanMetric label="止损" value={plan.stop_loss !== undefined ? formatPriceNum(plan.stop_loss) : undefined} />
          <PlanMetric label="目标价" value={plan.targets?.length ? plan.targets.map(formatPriceNum).join(" / ") : undefined} />
          <PlanMetric label="仓位" value={plan.position_pct !== undefined ? `${plan.position_pct}%` : undefined} />
        </div>
      )}
      {hasConditions && (
        <div className="mt-2 space-y-1">
          {plan.conditions!.map((c, i) => {
            const st = conditionStyle(c.kind);
            const Icon = st.icon;
            return (
              <div key={i} className="flex items-start gap-1.5 text-xs">
                <Icon className={`mt-0.5 h-3 w-3 shrink-0 ${st.color}`} />
                <span className={`shrink-0 font-mono ${st.color}`}>[{st.label}]</span>
                <span className="text-stone-300">{c.description}</span>
                {c.source && <span className="text-stone-500">· {c.source}</span>}
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
    <div className="rounded border border-stone-800 bg-stone-950 px-2 py-1.5">
      <div className="text-stone-500">{label}</div>
      <div className="mt-0.5 font-mono text-stone-200">{value ?? "—"}</div>
    </div>
  );
}

export function AnalysisSummaryCard({ summary, selectionPlan, artifactId, onAddHolding, onAnalyzeMore }: AnalysisSummaryCardProps) {
  const { rating, target_price, confidence, reasons, runId, symbol, plan } = summary;
  const selectionPlanShape = toPlan(selectionPlan);
  const [feedback, setFeedback] = useState<string | null>(null);

  const handleAdoptPlan = async () => {
    if (!symbol || !plan) return;
    setFeedback("正在纳入计划…");
    try {
      await createPlan({
        symbol,
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

  const planHasLevels = !!plan && (!!plan.entry_zone?.length || plan.stop_loss !== undefined || !!plan.targets?.length || !!plan.conditions?.length);

  return (
    <div className="space-y-3 rounded-lg border border-stone-800 bg-stone-900 p-4">
      {/* Header: Rating + Target + Confidence */}
      <div className="flex items-center gap-4">
        <RatingIcon rating={rating} />
        <div className="flex-1">
          <div className="flex items-baseline gap-3">
            <span className={`text-lg font-bold ${ratingColor(rating)}`}>{rating ?? "N/A"}</span>
            {target_price && (
              <span className="text-sm text-stone-400">
                目标价: <span className="font-mono text-stone-200">{target_price}</span>
              </span>
            )}
            {confidence !== undefined && (
              <span className="text-sm text-stone-400">
                信心: <span className="font-mono text-stone-200">{confidence}%</span>
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Selection plan vs analysis plan comparison */}
      {selectionPlanShape && (selectionPlanShape.entry_zone?.length || selectionPlanShape.stop_loss !== undefined || selectionPlanShape.targets?.length) && (
        <PlanCard plan={selectionPlanShape} title="选股计划（来自选股结论）" tone="selection" />
      )}
      {plan && <PlanCard plan={plan} title="分析计划（本次单股分析）" tone="analysis" />}
      {selectionPlanShape && plan && (
        <p className="text-xs text-stone-500">
          对比上方两份计划：若建仓/止损/目标价或评级不一致，说明选股与深度分析存在分歧，请重点复核。
        </p>
      )}

      {/* Key Reasons */}
      {reasons && reasons.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-stone-500">关键论据</p>
          <ol className="space-y-1 pl-4">
            {reasons.map((reason, i) => (
              <li key={i} className="text-sm text-stone-300 list-decimal">{reason}</li>
            ))}
          </ol>
        </div>
      )}

      {/* Action Buttons */}
      <div className="flex flex-wrap items-center gap-2 border-t border-stone-800 pt-3">
        {runId && (
          <Link
            to={`/library?run_id=${runId}`}
            className="inline-flex items-center gap-1.5 rounded-lg border border-teal-500/30 bg-teal-500/10 px-3 py-1.5 text-xs font-medium text-teal-300 transition hover:bg-teal-500/20"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            查看完整报告
          </Link>
        )}
        {symbol && onAddHolding && (
          <button
            onClick={() => onAddHolding(symbol)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-stone-700 px-3 py-1.5 text-xs font-medium text-stone-300 transition hover:bg-stone-800"
          >
            <Plus className="h-3.5 w-3.5" />
            加入持仓
          </button>
        )}
        {symbol && onAnalyzeMore && (
          <button
            onClick={() => onAnalyzeMore(symbol)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-stone-700 px-3 py-1.5 text-xs font-medium text-stone-300 transition hover:bg-stone-800"
          >
            <TrendingUp className="h-3.5 w-3.5" />
            对比同行业
          </button>
        )}
        {symbol && planHasLevels && (
          <button
            onClick={handleAdoptPlan}
            className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 transition hover:bg-emerald-500/20"
          >
            <Shield className="h-3.5 w-3.5" />
            纳入计划
          </button>
        )}
        {symbol && (
          <button
            onClick={handleEnrollReflection}
            className="inline-flex items-center gap-1.5 rounded-lg border border-purple-500/30 bg-purple-500/10 px-3 py-1.5 text-xs font-medium text-purple-300 transition hover:bg-purple-500/20"
          >
            <Bell className="h-3.5 w-3.5" />
            加入反思
          </button>
        )}
        {feedback && (
          <span className="text-xs text-stone-400">{feedback}</span>
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
    runId,
    plan: toPlan(d.plan),
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
