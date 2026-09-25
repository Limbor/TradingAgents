import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Bell, Shield, Trash2, X } from "lucide-react";
import { deletePlan, listPlans, updatePlan, type Plan } from "@/api/client";
import { displayNameOf } from "@/components/common/StockName";
import { queryKeys } from "@/api/queryKeys";

function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return Number.isFinite(value) ? value.toFixed(2) : "-";
}

type PlanDirection = "long" | "exit";

function planDirection(plan: Plan): PlanDirection {
  if (plan.plan_action === "REDUCE" || plan.plan_action === "EXIT") return "exit";
  if (plan.plan_action === "ENTER" || plan.plan_action === "ADD") return "long";
  const rating = (plan.rating ?? "").toLowerCase();
  if (rating.includes("sell") || rating.includes("underweight") || rating.includes("reduce") || rating.includes("减持") || rating.includes("减仓") || rating.includes("卖出") || rating.includes("偏空")) return "exit";
  if (plan.entry_zone?.length && plan.targets?.length && Math.max(...plan.targets) < Math.min(...plan.entry_zone)) return "exit";
  return "long";
}

function conditionLabel(kind: string | undefined, direction: PlanDirection, triggerAction?: string): string {
  const action = String(triggerAction || "").toUpperCase();
  if (action === "ENTER") return "建仓";
  if (action === "ADD") return "加仓";
  if (action === "REDUCE") return "减仓";
  if (action === "EXIT") return "清仓";
  if (action === "HOLD") return "观望";
  const k = String(kind || "").toLowerCase();
  if (direction === "exit") {
    if (k === "entry") return "减仓";
    if (k === "full") return "清仓";
    if (k === "stop") return "看空失效";
    if (k === "take_profit") return "下行止盈";
  }
  if (k === "entry") return "建仓";
  if (k === "full") return "满仓";
  if (k === "stop") return "止损";
  if (k === "take_profit") return "止盈";
  return kind || "条件";
}

function lastCheckLabel(plan: Plan): string {
  // Prefer the trade date the check was based on (what the user asked for);
  // fall back to the run timestamp when no quote was fetched, else mark unrated.
  if (plan.last_checked_trade_date) return plan.last_checked_trade_date;
  if (plan.last_checked_at) return `${plan.last_checked_at.slice(0, 10)}（未取到价）`;
  return "未检查";
}

function triggeredTimeLabel(plan: Plan): string {
  if (!plan.triggered_at) return "";
  // 2026-07-10T16:45:00 -> 2026-07-10 16:45
  return plan.triggered_at.slice(0, 16).replace("T", " ");
}

type PriceTone = "blue" | "red" | "green" | "slate";

const PRICE_TONES: Record<PriceTone, string> = {
  blue: "border-blue-500/30 bg-blue-500/5 text-blue-200",
  red: "border-red-500/30 bg-red-500/5 text-red-200",
  green: "border-emerald-500/30 bg-emerald-500/5 text-emerald-200",
  slate: "border-stone-600/40 bg-stone-800/30 text-stone-200",
};

function PriceBlock({ label, value, tone }: { label: string; value: string; tone: PriceTone }) {
  return (
    <div className={"rounded border px-2 py-1 " + PRICE_TONES[tone]}>
      <div className="text-[10px] text-stone-500">{label}</div>
      <div className="font-mono text-xs">{value}</div>
    </div>
  );
}

export function PlanListCard() {
  const qc = useQueryClient();
  const { data, refetch } = useQuery({
    queryKey: queryKeys.plans(),
    queryFn: () => listPlans({ limit: 30 }),
  });
  const plans = data ?? [];
  const active = plans.filter((p) => p.status === "active");
  const triggered = plans.filter((p) => p.status === "triggered");

  const close = async (id: string) => {
    await updatePlan(id, { status: "closed" });
    qc.invalidateQueries({ queryKey: queryKeys.plans() });
    refetch();
  };
  const remove = async (id: string) => {
    await deletePlan(id);
    qc.invalidateQueries({ queryKey: queryKeys.plans() });
    refetch();
  };

  // Triggered plans stay in the list (highlighted, with full detail) instead of
  // being demoted to a bare reminder card. Closing / deleting is always an
  // explicit user action -- a trigger never removes a plan on its own.
  const visible = [...triggered, ...active];

  return (
    <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-stone-100">
          <Shield className="h-4 w-4 text-emerald-300" />
          <h3 className="text-sm font-semibold">交易计划</h3>
        </div>
        <span className="text-xs text-stone-500">
          {active.length} 个监控中 · {triggered.length} 个已触发
        </span>
      </div>

      {visible.length === 0 ? (
        <p className="text-xs text-stone-500">
          暂无监控中的计划。在单股分析卡片点"纳入计划"即可加入监控--收盘后会自动评估价格触及/金叉死叉/现金流等条件并在此提醒。
        </p>
      ) : (
        <div className="space-y-2">
          {visible.map((p) => (
            <PlanRow key={p.id} plan={p} onClose={() => close(p.id)} onRemove={() => remove(p.id)} />
          ))}
        </div>
      )}
    </section>
  );
}

function PlanRow({ plan, onClose, onRemove }: { plan: Plan; onClose: () => void; onRemove: () => void }) {
  const isTriggered = plan.status === "triggered";
  const displayName = displayNameOf(plan.name, plan.symbol);
  const direction = planDirection(plan);
  const isExit = direction === "exit";
  const lifecycleLabel = plan.lifecycle_state === "waiting_trigger" ? "等待触发"
    : plan.lifecycle_state === "executable" ? "可执行"
      : plan.lifecycle_state === "cancelled" ? "已取消"
        : plan.lifecycle_state === "expired" ? "已过期" : "";

  return (
    <div
      className={
        "rounded-lg border p-3 text-xs transition-colors " +
        (isTriggered ? "border-amber-500/50 bg-amber-500/5" : "border-stone-700 bg-stone-950")
      }
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {isTriggered ? (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-300">
              <Bell className="h-3 w-3" /> 已触发
            </span>
          ) : (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
              <Activity className="h-3 w-3" /> 监控中
            </span>
          )}
          <span className="truncate font-medium text-stone-100">{displayName}</span>
          {plan.name && plan.name !== plan.symbol && (
            <span className="shrink-0 font-mono text-[11px] text-stone-500">{plan.symbol}</span>
          )}
          {lifecycleLabel && (
            <span className="shrink-0 rounded bg-teal-500/10 px-1.5 py-0.5 text-[10px] text-teal-300">{lifecycleLabel}</span>
          )}
        </div>
        {plan.rating && (
          <span className="shrink-0 rounded bg-stone-800 px-1.5 py-0.5 font-mono text-[10px] text-stone-300">
            {plan.rating}
          </span>
        )}
      </div>

      {isTriggered && plan.trigger_reason && (
        <div className="mt-2 rounded bg-amber-500/10 px-2 py-1 text-amber-200">
          <span className="font-medium">触发</span>: {plan.trigger_reason}
          {triggeredTimeLabel(plan) && <span className="ml-2 text-amber-300/60">· {triggeredTimeLabel(plan)}</span>}
        </div>
      )}

      <div className="mt-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        <PriceBlock label={isExit ? "减仓/卖出" : "建仓"} value={plan.entry_zone?.length ? plan.entry_zone.map(formatPrice).join("-") : "-"} tone="blue" />
        <PriceBlock label={isExit ? "风控位" : "止损"} value={formatPrice(plan.stop_loss)} tone="red" />
        <PriceBlock label={isExit ? "下方目标" : "目标"} value={plan.targets?.length ? plan.targets.map(formatPrice).join("/") : "-"} tone="green" />
        <PriceBlock
          label={isExit ? "仓位上限" : "仓位"}
          value={plan.position_pct !== null && plan.position_pct !== undefined ? `${plan.position_pct}%` : "-"}
          tone="slate"
        />
      </div>

      {(plan.reliability_score !== null || plan.expires_at) && (
        <div className="mt-2 flex flex-wrap gap-3 text-[10px] text-stone-500">
          {plan.reliability_score !== null && <span>推荐可靠性 {Math.round(plan.reliability_score)}/100</span>}
          {plan.expires_at && <span>有效至 {plan.expires_at}</span>}
        </div>
      )}

      {plan.conditions?.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {plan.conditions.map((c, i) => (
            <span
              key={i}
              className="rounded border border-stone-700 bg-stone-800/50 px-1.5 py-0.5 text-[10px] text-stone-300"
              title={c.description}
            >
              {conditionLabel(c.kind, direction, c.trigger_action)} · {c.description}
            </span>
          ))}
        </div>
      )}

      <div className="mt-2.5 flex items-center justify-between border-t border-stone-800 pt-2">
        <span className="text-[10px] text-stone-500" title={plan.last_checked_at ?? undefined}>
          上次检查: {lastCheckLabel(plan)}
        </span>
        <div className="flex gap-1.5">
          <button
            onClick={onClose}
            className="inline-flex items-center gap-1 rounded border border-stone-600 px-2 py-0.5 text-stone-400 hover:bg-stone-800"
          >
            <X className="h-3 w-3" /> 关闭
          </button>
          <button
            onClick={onRemove}
            className="inline-flex items-center gap-1 rounded border border-stone-700 px-2 py-0.5 text-stone-500 hover:border-red-500/40 hover:bg-red-500/10 hover:text-red-300"
          >
            <Trash2 className="h-3 w-3" /> 删除
          </button>
        </div>
      </div>
    </div>
  );
}
