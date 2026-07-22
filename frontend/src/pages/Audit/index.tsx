import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, CheckCircle2, FlaskConical, Link2, RefreshCw } from "lucide-react";
import {
  createBacktest, evaluateDecisionAudit, getDecisionAuditSummary,
  getBacktestCatalog, listBacktests, listDecisionRecords, recordDecisionExecution,
} from "@/api/client";

export default function Audit() {
  const qc = useQueryClient();
  const summary = useQuery({ queryKey: ["decision-audit-summary"], queryFn: getDecisionAuditSummary });
  const decisions = useQuery({ queryKey: ["decision-audit-decisions"], queryFn: listDecisionRecords });
  const backtests = useQuery({ queryKey: ["backtests"], queryFn: listBacktests, refetchInterval: 10_000 });
  const catalog = useQuery({ queryKey: ["backtest-catalog"], queryFn: getBacktestCatalog, retry: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [auditNotice, setAuditNotice] = useState<string | null>(null);
  const [form, setForm] = useState({ start_date: "2024-01-01", end_date: "2026-01-01", strategy_name: "ff_residual_csi800_main", config_name: "prod_ff_residual_csi800_tv15" });
  const [execution, setExecution] = useState<{ decision_id: string; symbol: string; action: string; quantity: string; price: string } | null>(null);
  const data = summary.data;

  const refreshAudit = async () => {
    setBusy(true); setError(null);
    try { const result = await evaluateDecisionAudit(); const warnings = Array.isArray(result.warnings) ? result.warnings : []; setAuditNotice(`新增收益快照 ${Number(result.evaluated_outcomes ?? 0)} 条${warnings.length ? `；${warnings.length} 条基准/数据警告` : ""}`); await qc.invalidateQueries({ queryKey: ["decision-audit-summary"] }); await qc.invalidateQueries({ queryKey: ["decision-audit-decisions"] }); }
    catch (e) { setError(e instanceof Error ? e.message : "评估失败"); }
    finally { setBusy(false); }
  };
  const submitBacktest = async () => {
    setBusy(true); setError(null);
    try { await createBacktest(form); await backtests.refetch(); }
    catch (e) { setError(e instanceof Error ? e.message : "回测提交失败"); }
    finally { setBusy(false); }
  };
  const submitExecution = async () => {
    if (!execution) return;
    setBusy(true); setError(null);
    try {
      await recordDecisionExecution({ ...execution, quantity: Number(execution.quantity), price: Number(execution.price) });
      setExecution(null); await summary.refetch(); await decisions.refetch();
    } catch (e) { setError(e instanceof Error ? e.message : "执行记录失败"); }
    finally { setBusy(false); }
  };

  return <div className="mx-auto flex max-w-7xl flex-col gap-5">
    <header className="border-b border-stone-800 pb-5">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-teal-300">Decision Audit</p>
      <h2 className="mt-2 text-2xl font-semibold text-stone-50">决策—执行—收益—反思</h2>
      <p className="mt-1 text-sm text-stone-500">区分系统建议与用户成交，用真实未来收益验证，而不是事后重写结论。</p>
    </header>
    {error && <div className="rounded border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">{error}</div>}
    {auditNotice && <div className="rounded border border-teal-500/30 bg-teal-500/10 p-3 text-sm text-teal-100">{auditNotice}</div>}
    <section className="grid gap-3 sm:grid-cols-6">
      <Kpi icon={Activity} label="决策" value={data?.decision_count ?? 0} />
      <Kpi icon={CheckCircle2} label="已实现结果" value={data?.realized_count ?? 0} />
      <Kpi icon={Link2} label="已关联执行" value={data?.linked_execution_count ?? 0} />
      <Kpi icon={FlaskConical} label="方向胜率" value={data ? `${(data.validation.overall.win_rate * 100).toFixed(1)}%` : "-"} />
      <Kpi icon={CheckCircle2} label="实际执行胜率" value={data?.execution_validation?.sample_count ? `${(data.execution_validation.win_rate * 100).toFixed(1)}%` : "无到期样本"} />
      <Kpi icon={FlaskConical} label="有效性门禁" value={data?.validation.effectiveness_claim_allowed ? "显著有效" : data?.validation.strategy_claims_allowed ? "可评估，未证实有效" : "样本不足"} />
    </section>
    {!data?.validation.effectiveness_claim_allowed && <div className="rounded border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-100">{data?.validation.strategy_claims_allowed ? "样本已达到评估门槛，但方向收益尚未达到 95% 置信区间显著为正，禁止宣称策略有效。" : "至少需要 20 个已实现样本。目前禁止宣称策略有效。"}</div>}
    {data && Object.keys(data.validation.by_decision ?? {}).length > 0 && <section className="flex flex-wrap gap-2">{Object.entries(data.validation.by_decision).map(([decision, metrics]) => <div key={decision} className="rounded border border-stone-800 bg-stone-900 px-3 py-2 text-xs text-stone-300"><strong>{decision}</strong> · n={metrics.sample_count} · 方向胜率 {(metrics.win_rate * 100).toFixed(1)}% · 方向收益 {pct(metrics.average_directional_return)}</div>)}</section>}
    <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
      <div className="flex items-center justify-between"><h3 className="font-semibold text-stone-100">决策账本</h3><button onClick={refreshAudit} disabled={busy} className="flex items-center gap-2 rounded border border-teal-500/30 px-3 py-2 text-xs text-teal-200"><RefreshCw className="h-4 w-4" />评估到期收益</button></div>
      <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-xs"><thead className="text-stone-500"><tr><th>日期</th><th>标的</th><th>来源</th><th>决策</th><th>周期</th><th>原始收益</th><th>超额收益</th><th>执行</th><th>状态</th><th></th></tr></thead><tbody>{(decisions.data ?? []).map(d => <tr key={d.id} className="border-t border-stone-800 text-stone-300"><td>{d.decision_date}</td><td className="py-2 font-mono">{d.symbol}</td><td>{d.source_type}</td><td>{d.decision}</td><td>{d.horizon_days}日</td><td>{pct(d.final_return)}</td><td>{pct(d.final_excess_return)}</td><td>{d.execution_count}</td><td>{d.status}</td><td><button onClick={() => setExecution({ decision_id: d.id, symbol: d.symbol, action: d.decision === "BUY" || d.decision === "ADD" ? "buy" : "sell", quantity: "", price: String(d.reference_price ?? "") })} className="text-teal-300">记录执行</button></td></tr>)}</tbody></table></div>
      {execution && <div className="mt-3 flex flex-wrap items-end gap-2 rounded border border-stone-700 p-3"><label className="text-xs text-stone-400">数量<input aria-label="执行数量" value={execution.quantity} onChange={e => setExecution({...execution, quantity: e.target.value})} className="ml-2 w-28 rounded bg-stone-950 p-2 text-stone-100" /></label><label className="text-xs text-stone-400">成交价<input aria-label="执行价格" value={execution.price} onChange={e => setExecution({...execution, price: e.target.value})} className="ml-2 w-28 rounded bg-stone-950 p-2 text-stone-100" /></label><button disabled={busy || !(Number(execution.quantity) > 0) || !(Number(execution.price) > 0)} onClick={submitExecution} className="rounded bg-teal-500 px-3 py-2 text-xs font-semibold text-stone-950 disabled:opacity-40">确认记录</button><button onClick={() => setExecution(null)} className="px-2 py-2 text-xs text-stone-400">取消</button></div>}
    </section>
    <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
      <h3 className="font-semibold text-stone-100">候选量化规则回测</h3>
      <p className="mt-1 text-xs text-stone-500">从 StockManager 的版本化目录选择策略和配置。该回测为 DailyPipeline 提供量化证据，但不声称复现包含 LLM 复核的完整流程。</p>
      <div className="mt-3 flex flex-wrap gap-2"><select aria-label="strategy_name" value={form.strategy_name} onChange={e => setForm({...form, strategy_name: e.target.value})} className="rounded border border-stone-700 bg-stone-950 px-3 py-2 text-sm text-stone-200">{(catalog.data?.strategies ?? [{name: form.strategy_name, sha1: ""}]).map(item => <option key={item.name} value={item.name}>{item.name}</option>)}</select><select aria-label="config_name" value={form.config_name} onChange={e => setForm({...form, config_name: e.target.value})} className="max-w-sm rounded border border-stone-700 bg-stone-950 px-3 py-2 text-sm text-stone-200">{(catalog.data?.configs ?? [{name: form.config_name, sha1: ""}]).map(item => <option key={item.name} value={item.name}>{item.name}</option>)}</select>{(["start_date", "end_date"] as const).map(k => <input key={k} aria-label={k} type="date" value={form[k]} onChange={e => setForm({...form, [k]: e.target.value})} className="rounded border border-stone-700 bg-stone-950 px-3 py-2 text-sm text-stone-200" />)}<button onClick={submitBacktest} disabled={busy || catalog.isError} className="rounded bg-teal-500 px-4 py-2 text-sm font-semibold text-stone-950 disabled:opacity-40">提交回测</button></div>
      <div className="mt-3 space-y-2">{(backtests.data ?? []).map(b => {
        const validation = (b.result.validation ?? {}) as Record<string, unknown>;
        const checks = (validation.threshold_checks ?? {}) as Record<string, boolean>;
        const failedChecks = Object.entries(checks).filter(([, passed]) => !passed).map(([key]) => key);
        return <div key={b.id} className="grid gap-2 rounded border border-stone-800 p-3 text-xs text-stone-300 sm:grid-cols-7"><span>{b.start_date} → {b.end_date}</span><span>收益 {pct(Number(b.result.total_return))}</span><span>Alpha {pct(Number(b.result.alpha))}</span><span>回撤 {pct(Number(b.result.max_drawdown))}</span><span>Sharpe {numberText(b.result.sharpe)}</span><span title={failedChecks.length ? `未通过：${failedChecks.join(", ")}` : undefined}>{validation.production_gate_passed === true ? "门禁通过" : "门禁未通过"}</span><span>{b.status}</span></div>;
      })}</div>
    </section>
  </div>;
}

function pct(value: number | null | undefined) { return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : "-"; }
function numberText(value: unknown) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed.toFixed(2) : "-"; }

function Kpi({ icon: Icon, label, value }: { icon: typeof Activity; label: string; value: number | string }) {
  return <div className="rounded-lg border border-stone-800 bg-stone-900 p-4"><Icon className="h-4 w-4 text-teal-300"/><div className="mt-2 text-xs text-stone-500">{label}</div><div className="mt-1 text-lg font-semibold text-stone-100">{value}</div></div>;
}
