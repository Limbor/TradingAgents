import { useState } from "react";
import type { PaperPlan, PaperStatus } from "@/api/paper";

const currency = (value: unknown) => typeof value === "number" && Number.isFinite(value)
  ? `¥${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}` : "—";
const signedCurrency = (value: number) => value === 0 ? currency(0)
  : `${value > 0 ? "+" : "-"}${currency(Math.abs(value))}`;

const actionLabels: Record<string, string> = { BUY: "买入", SELL: "卖出", HOLD: "持有", SKIP: "跳过", ADD: "加仓", REDUCE: "减仓" };
const actionTones: Record<string, string> = {
  BUY: "bg-ui-danger/10 text-ui-danger", ADD: "bg-ui-danger/10 text-ui-danger",
  SELL: "bg-ui-success/10 text-ui-success", REDUCE: "bg-ui-success/10 text-ui-success",
  SKIP: "bg-ui-warning/10 text-ui-warning", HOLD: "bg-ui-hover text-ui-muted",
};

export function PlanCard({ plan, status, loading, onAsk }: {
  plan?: PaperPlan | null;
  status?: PaperStatus;
  loading: boolean;
  onAsk: () => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const items = plan?.items ?? [];
  const actionable = items.filter((item) => item.action.toUpperCase() !== "HOLD");
  const shown = showAll ? items : actionable;
  const counts = items.reduce<Record<string, number>>((acc, item) => {
    const action = item.action.toUpperCase();
    acc[action] = (acc[action] ?? 0) + 1;
    return acc;
  }, {});
  return <section className="rounded-xl border border-ui-line bg-ui-panel p-4">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div><h2 className="font-medium">下一交易日计划</h2><p className="mt-1 text-xs text-ui-faint">信号日 {plan?.signal_date ?? "—"}{plan?.active_sleeve ? ` · 当前子策略 ${plan.active_sleeve}` : ""}</p></div>
      <button className="text-xs text-ui-accent" onClick={onAsk}>问 Agent</button>
    </div>
    {status?.readiness?.can_reference_plan === false && <p className="mt-3 rounded-md bg-ui-warning/10 px-3 py-2 text-xs text-ui-warning">计划尚不可引用，请先核对组合状态和数据时点。</p>}
    {plan?.reason && <p className="mt-3 text-sm leading-6 text-ui-body">{plan.reason}</p>}
    {items.length > 0 && <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap gap-1.5">{["BUY", "ADD", "SELL", "REDUCE", "SKIP", "HOLD"].filter((action) => counts[action]).map((action) =>
        <span key={action} className={`rounded-md px-2 py-1 text-xs ${actionTones[action]}`}>{actionLabels[action]} {counts[action]}</span>)}</div>
      <div className="flex rounded-md border border-ui-line p-0.5 text-xs" aria-label="计划筛选">
        <button type="button" aria-pressed={!showAll} onClick={() => setShowAll(false)} className={`rounded px-2.5 py-1 ${!showAll ? "bg-ui-hover font-medium text-ui-ink" : "text-ui-muted"}`}>非持有</button>
        <button type="button" aria-pressed={showAll} onClick={() => setShowAll(true)} className={`rounded px-2.5 py-1 ${showAll ? "bg-ui-hover font-medium text-ui-ink" : "text-ui-muted"}`}>全部</button>
      </div>
    </div>}
    {shown.length > 0 ? <div className="mt-3 grid gap-2 sm:grid-cols-2">{shown.map((item, index) => {
      const action = item.action.toUpperCase();
      return <div key={`${item.code}-${action}-${index}`} className="min-w-0 rounded-lg border border-ui-line bg-ui-subtle/50 p-3 text-sm">
        <div className="flex items-start justify-between gap-2"><div className="min-w-0"><strong className="block truncate font-medium text-ui-ink" title={item.name || item.code}>{item.name || item.code}</strong><span className="text-xs text-ui-faint">{item.code}</span></div><span className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${actionTones[action] ?? "bg-ui-hover text-ui-muted"}`}>{actionLabels[action] ?? action}</span></div>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ui-muted">{typeof item.est_shares === "number" && <span>预计股数 {item.est_shares.toLocaleString("zh-CN")}</span>}{typeof item.diff_value === "number" && <span>预计金额变化 {signedCurrency(item.diff_value)}</span>}</div>
        {item.reason && <p className="mt-2 text-xs leading-5 text-ui-body">{item.reason}</p>}
      </div>;
    })}</div> : <p className="mt-4 text-sm text-ui-faint">{loading ? "正在读取计划…" : !plan ? "尚无计划" : items.length ? "当前筛选下没有计划项，可切换“全部”查看。" : "该信号日未列出调仓条目；不能据此推断后续没有交易。"}</p>}
  </section>;
}
