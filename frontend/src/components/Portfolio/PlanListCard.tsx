import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Shield } from "lucide-react";
import { deletePlan, listPlans, updatePlan, type Plan } from "@/api/client";

function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return Number.isFinite(value) ? value.toFixed(2) : "—";
}

function conditionLabel(kind?: string): string {
  const k = String(kind || "").toLowerCase();
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

export function PlanListCard() {
  const qc = useQueryClient();
  const { data, refetch } = useQuery({
    queryKey: ["plans"],
    queryFn: () => listPlans({ limit: 30 }),
  });
  const plans = data ?? [];
  const active = plans.filter((p) => p.status === "active");
  const triggered = plans.filter((p) => p.status === "triggered");

  const close = async (id: string) => {
    await updatePlan(id, { status: "closed" });
    qc.invalidateQueries({ queryKey: ["plans"] });
    refetch();
  };
  const remove = async (id: string) => {
    await deletePlan(id);
    qc.invalidateQueries({ queryKey: ["plans"] });
    refetch();
  };

  return (
    <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-stone-100">
          <Shield className="h-4 w-4 text-emerald-300" />
          <h3 className="text-sm font-semibold">交易计划</h3>
        </div>
        <span className="text-xs text-stone-500">{active.length} 个监控中 · {triggered.length} 个已触发</span>
      </div>

      {triggered.length > 0 && (
        <div className="mb-3 space-y-2">
          {triggered.map((p) => (
            <div key={p.id} className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-2.5 text-xs">
              <div className="flex items-center gap-2 font-medium text-amber-200">
                <Bell className="h-3.5 w-3.5" />
                {p.name && p.name !== p.symbol ? `${p.name} (${p.symbol})` : p.symbol} · 计划触发
              </div>
              <div className="mt-1 text-amber-100/80">{p.trigger_reason}</div>
              <div className="mt-1 flex gap-2">
                <button onClick={() => close(p.id)} className="rounded border border-stone-600 px-2 py-0.5 text-stone-300 hover:bg-stone-800">关闭</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {active.length === 0 ? (
        <p className="text-xs text-stone-500">
          暂无监控中的计划。在单股分析卡片点"纳入计划"即可加入监控——收盘后会自动评估价格触及/金叉死叉/现金流等条件并在此提醒。
        </p>
      ) : (
        <div className="space-y-2">
          {active.map((p) => (
            <PlanRow key={p.id} plan={p} onClose={() => close(p.id)} onRemove={() => remove(p.id)} />
          ))}
        </div>
      )}
    </section>
  );
}

function PlanRow({ plan, onClose, onRemove }: { plan: Plan; onClose: () => void; onRemove: () => void }) {
  return (
    <div className="rounded-lg border border-stone-700 bg-stone-950 p-2.5 text-xs">
      <div className="flex items-center justify-between">
        <div className="min-w-0">
          <span className="text-stone-100">{plan.name && plan.name !== plan.symbol ? plan.name : plan.symbol}</span>
          {plan.name && plan.name !== plan.symbol && (
            <span className="ml-1 font-mono text-xs text-stone-500">{plan.symbol}</span>
          )}
        </div>
        <span className="text-stone-500">{plan.rating ?? ""}</span>
      </div>
      <div className="mt-1 grid grid-cols-2 gap-1 text-stone-400 sm:grid-cols-4">
        <div>建仓: {plan.entry_zone?.length ? plan.entry_zone.map(formatPrice).join("-") : "—"}</div>
        <div>止损: {formatPrice(plan.stop_loss)}</div>
        <div>目标: {plan.targets?.length ? plan.targets.map(formatPrice).join("/") : "—"}</div>
        <div>仓位: {plan.position_pct !== null && plan.position_pct !== undefined ? `${plan.position_pct}%` : "—"}</div>
      </div>
      {plan.conditions?.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {plan.conditions.map((c, i) => (
            <span key={i} className="rounded bg-stone-800 px-1.5 py-0.5 text-[10px] text-stone-300" title={c.description}>
              [{conditionLabel(c.kind)}] {c.description}
            </span>
          ))}
        </div>
      )}
      <div className="mt-1.5 flex items-center justify-between">
        <span className="text-[10px] text-stone-500" title={plan.last_checked_at ?? undefined}>
          上次检查: {lastCheckLabel(plan)}
        </span>
        <div className="flex gap-2">
          <button onClick={onClose} className="rounded border border-stone-600 px-2 py-0.5 text-stone-400 hover:bg-stone-800">关闭</button>
          <button onClick={onRemove} className="rounded border border-stone-700 px-2 py-0.5 text-stone-500 hover:bg-stone-800">删除</button>
        </div>
      </div>
    </div>
  );
}
