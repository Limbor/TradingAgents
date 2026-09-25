import type { PaperStatus } from "@/api/paper";

const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toFixed(2)}%`;
const money = (value: number | null | undefined) =>
  value == null ? "—" : `¥${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;

export function CompositeDecision({ status, onAsk }: {
  status: PaperStatus;
  onAsk: () => void;
}) {
  const decision = status.decision;
  const summary = status.summary;
  const freshness = status.freshness;
  const sleeves = Object.entries(status.sleeves ?? {});
  const ready = status.readiness?.can_reference_plan;
  return (
    <section className="rounded-xl border border-stone-800 bg-stone-900 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="font-medium">组合决策</h2>
          <p className="mt-1 text-xs text-stone-500">决策基准日 {decision?.date ?? "—"} · 账户权益以组合可执行账本为准</p>
        </div>
        <button className="text-xs text-teal-300" onClick={onAsk}>问 Agent 原因</button>
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-lg border border-stone-800 bg-stone-950 p-3">
          <p className="text-xs text-stone-500">当前子策略</p>
          <p className="mt-1 break-all text-sm font-medium text-teal-200">{decision?.active_sleeve ?? "—"}</p>
          <p className="mt-1 text-xs text-stone-400">{decision?.switched ? "本次发生切换" : "本次未切换"} · 累计 {decision?.switch_count ?? 0} 次</p>
        </div>
        <div className="rounded-lg border border-stone-800 bg-stone-950 p-3">
          <p className="text-xs text-stone-500">相对收益</p>
          <p className="mt-1 text-sm font-medium">{percent(decision?.relative_return)}</p>
          <p className="mt-1 text-xs text-stone-400">快速窗口 {percent(decision?.fast_relative_return)}</p>
        </div>
        <div className="rounded-lg border border-stone-800 bg-stone-950 p-3">
          <p className="text-xs text-stone-500">账户表现</p>
          <p className="mt-1 text-sm font-medium">累计 {percent(summary?.total_return)}</p>
          <p className="mt-1 text-xs text-stone-400">最大回撤 {percent(summary?.max_drawdown)} · 夏普 {summary?.sharpe?.toFixed(2) ?? "—"}</p>
        </div>
        <div className="rounded-lg border border-stone-800 bg-stone-950 p-3">
          <p className="text-xs text-stone-500">信号状态</p>
          <p className={`mt-1 text-sm font-medium ${ready ? "text-teal-300" : "text-amber-300"}`}>{ready ? "计划可参考" : "计划待核对"}</p>
          <p className="mt-1 text-xs text-stone-400">子策略同步 {freshness?.is_shadow_aligned ? "正常" : "待核对"} · 计划滞后 {freshness?.active_plan_lag_days ?? "—"} 天</p>
        </div>
      </div>
      {(decision?.fast_gate_triggered || decision?.blocked_until_rebalance || decision?.exposure_fallback_triggered) && (
        <p className="mt-3 text-xs text-amber-200">
          {[
            decision.fast_gate_triggered && "快速切换门槛触发",
            decision.blocked_until_rebalance && "切换受再平衡窗口限制",
            decision.exposure_fallback_triggered && "启用风险暴露回退",
          ].filter(Boolean).join(" · ")}
        </p>
      )}
      {!!status.readiness?.reasons?.length && <p className="mt-3 text-xs text-amber-200">{status.readiness.reasons.join("；")}</p>}
      {sleeves.length > 0 && <div className="mt-4">
        <h3 className="mb-2 text-xs font-medium text-stone-400">子策略参考信号</h3>
        <div className="grid gap-2 sm:grid-cols-2">{sleeves.map(([name, sleeve]) => (
          <div key={name} className="rounded-lg border border-stone-800 p-3 text-sm">
            <div className="flex items-center justify-between gap-2"><span className={name === decision?.active_sleeve ? "font-medium text-teal-200" : "text-stone-200"}>{name}</span><span>{money(sleeve.equity)}</span></div>
            <p className="mt-1 break-all text-xs text-stone-500">{sleeve.strategy} · {sleeve.last_date ?? "—"}</p>
          </div>
        ))}</div>
        <p className="mt-2 text-xs text-stone-500">子策略权益仅作切换信号参考，不等于组合账户资金。</p>
      </div>}
    </section>
  );
}
