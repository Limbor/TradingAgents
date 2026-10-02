import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, CheckCircle2, FlaskConical, Link2, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import {
  evaluateDecisionAudit, getDecisionAuditSummary,
  listDecisionRecords, recordDecisionExecution,
} from "@/api/client";

export default function Audit() {
  const qc = useQueryClient();
  const summary = useQuery({ queryKey: ["decision-audit-summary"], queryFn: getDecisionAuditSummary });
  const decisions = useQuery({ queryKey: ["decision-audit-decisions"], queryFn: listDecisionRecords });
  const [busy, setBusy] = useState(false);
  const [auditBusy, setAuditBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [auditNotice, setAuditNotice] = useState<string | null>(null);
  const [execution, setExecution] = useState<{ decision_id: string; symbol: string; action: string; quantity: string; price: string } | null>(null);
  const data = summary.data;

  const refreshAudit = async () => {
    setAuditBusy(true); setError(null);
    setAuditNotice("正在检查到期记录并抓取对应行情，请稍候…");
    try {
      const result = await evaluateDecisionAudit();
      const warnings = Array.isArray(result.warnings) ? result.warnings : [];
      if (result.evaluated_outcomes > 0) {
        setAuditNotice(
          "已新增 " + result.evaluated_outcomes + " 条收益快照；检查 "
          + result.selected_records + " 条记录"
          + (result.skipped ? "，" + result.skipped + " 条行情暂不可用" : "")
          + (warnings.length ? "；" + warnings.length + " 条数据警告" : ""),
        );
      } else if (result.attempted_outcomes > 0) {
        setAuditNotice(
          "已检查 " + result.due_records
          + " 条到期记录，但对应行情暂不可用；系统会按退避计划重试。",
        );
      } else {
        setAuditNotice(
          "当前没有新增到期收益；本批检查 " + result.selected_records
          + " 条，" + result.not_due_records + " 条尚未到期或已经评估。",
        );
      }
      await qc.invalidateQueries({ queryKey: ["decision-audit-summary"] });
      await qc.invalidateQueries({ queryKey: ["decision-audit-decisions"] });
    }
    catch (e) { setError(e instanceof Error ? e.message : "评估失败"); }
    finally { setAuditBusy(false); }
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
    <header className="border-b border-ui-line pb-5">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-ui-accent">Decision Audit</p>
      <h2 className="mt-2 text-2xl font-semibold text-ui-ink">决策—执行—收益—反思</h2>
      <p className="mt-1 text-sm text-ui-faint">区分系统建议与用户成交，用真实未来收益验证，而不是事后重写结论。</p>
    </header>
    {error && <div className="rounded border border-ui-danger/30 bg-ui-danger/10 p-3 text-sm text-ui-danger">{error}</div>}
    <section className="grid gap-3 sm:grid-cols-6">
      <Kpi icon={Activity} label="决策" value={data?.decision_count ?? 0} />
      <Kpi icon={CheckCircle2} label="已实现结果" value={data?.realized_count ?? 0} />
      <Kpi icon={Link2} label="已关联执行" value={data?.linked_execution_count ?? 0} />
      <Kpi icon={FlaskConical} label="系统建议方向胜率" value={data ? `${(data.validation.overall.win_rate * 100).toFixed(1)}%` : "-"} />
      <Kpi icon={CheckCircle2} label="实际执行胜率" value={data?.execution_validation?.sample_count ? `${(data.execution_validation.win_rate * 100).toFixed(1)}%` : "无到期样本"} />
      <Kpi icon={FlaskConical} label="有效性门禁" value={data?.validation.effectiveness_claim_allowed ? "显著有效" : data?.validation.strategy_claims_allowed ? "可评估，未证实有效" : "样本不足"} />
    </section>
    {data && <section className="rounded border border-ui-line bg-ui-panel/60 p-3 text-xs text-ui-muted">
      <div className="font-medium text-ui-body">可靠性口径</div>
      <p className="mt-1">
        系统建议方向样本 n={data.validation.overall.sample_count}，来自 {data.validation.unique_signal_date_count ?? "-"} 个信号日、
        {data.validation.unique_symbol_count ?? "-"} 个标的；同源同日同标的重复样本 {data.validation.duplicate_signal_count ?? 0} 条。
        这些是理论信号收益，不是用户实盘成交胜率。
      </p>
      {data.execution_validation.sample_count === 0 && <p className="mt-1 text-ui-warning">目前没有已关联执行，无法评价真实可交易收益、滑点和成交质量。</p>}
    </section>}
    {!data?.validation.effectiveness_claim_allowed && <div className="rounded border border-ui-warning/30 bg-ui-warning/10 p-3 text-sm text-ui-warning">{data?.validation.strategy_claims_allowed ? "样本已达到评估门槛，但方向收益尚未达到 95% 置信区间显著为正，禁止宣称策略有效。" : "至少需要 20 个已实现样本。目前禁止宣称策略有效。"}</div>}
    {data && Object.keys(data.validation.by_source ?? {}).length > 0 && <section>
      <div className="mb-2 text-xs text-ui-faint">按建议来源</div>
      <div className="flex flex-wrap gap-2">
        {Object.entries(data.validation.by_source ?? {}).map(([source, metrics]) => <div key={source} className="rounded border border-ui-line bg-ui-panel px-3 py-2 text-xs text-ui-body"><strong>{source}</strong> · n={metrics.sample_count} · 方向胜率 {(metrics.win_rate * 100).toFixed(1)}% · 方向收益 {pct(metrics.average_directional_return)}</div>)}
      </div>
    </section>}
    {data && Object.keys(data.validation.by_decision ?? {}).length > 0 && <section className="flex flex-wrap gap-2">{Object.entries(data.validation.by_decision).map(([decision, metrics]) => <div key={decision} className="rounded border border-ui-line bg-ui-panel px-3 py-2 text-xs text-ui-body"><strong>{decision}</strong> · n={metrics.sample_count} · 方向胜率 {(metrics.win_rate * 100).toFixed(1)}% · 方向收益 {pct(metrics.average_directional_return)}</div>)}</section>}
    <section className="rounded-lg border border-ui-line bg-ui-panel p-4">
      <div className="flex items-center justify-between"><h3 className="font-semibold text-ui-ink">决策账本</h3><button onClick={refreshAudit} disabled={auditBusy} className="flex items-center gap-2 rounded border border-ui-accent/30 px-3 py-2 text-xs text-ui-accent disabled:cursor-wait disabled:opacity-60"><RefreshCw className={`h-4 w-4 ${auditBusy ? "animate-spin" : ""}`} />{auditBusy ? "评估中…" : "评估到期收益"}</button></div>
      {auditNotice && <div aria-live="polite" className="mt-3 rounded border border-ui-accent/30 bg-ui-accent/10 p-3 text-xs text-ui-accent">{auditNotice}</div>}
      <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-xs"><thead className="text-ui-faint"><tr><th>日期</th><th>标的</th><th>来源</th><th>决策</th><th>周期</th><th>原始收益</th><th>超额收益</th><th>执行</th><th>状态</th><th></th></tr></thead><tbody>{(decisions.data ?? []).map(d => {
        const executionAction = actionForDecision(d.decision);
        return <tr key={d.id} className="border-t border-ui-line text-ui-body"><td>{d.decision_date}</td><td className="py-2 font-mono">{d.symbol}</td><td>{d.source_type}</td><td>{d.decision}</td><td>{d.horizon_days}日</td><td>{pct(d.final_return)}</td><td>{pct(d.final_excess_return)}</td><td>{d.execution_count}</td><td>{d.status}</td><td>{executionAction ? <button onClick={() => setExecution({ decision_id: d.id, symbol: d.symbol, action: executionAction, quantity: "", price: String(d.reference_price ?? "") })} className="text-ui-accent">记录执行</button> : <span className="text-ui-faint">—</span>}</td></tr>;
      })}</tbody></table></div>
      {execution && <div className="mt-3 flex flex-wrap items-end gap-2 rounded border border-ui-strong p-3"><label className="text-xs text-ui-muted">数量<input aria-label="执行数量" value={execution.quantity} onChange={e => setExecution({...execution, quantity: e.target.value})} className="ml-2 w-28 rounded bg-ui-subtle p-2 text-ui-ink" /></label><label className="text-xs text-ui-muted">成交价<input aria-label="执行价格" value={execution.price} onChange={e => setExecution({...execution, price: e.target.value})} className="ml-2 w-28 rounded bg-ui-subtle p-2 text-ui-ink" /></label><button disabled={busy || !(Number(execution.quantity) > 0) || !(Number(execution.price) > 0)} onClick={submitExecution} className="rounded bg-ui-accent px-3 py-2 text-xs font-semibold text-ui-onAccent disabled:opacity-40">确认记录</button><button onClick={() => setExecution(null)} className="px-2 py-2 text-xs text-ui-muted">取消</button></div>}
    </section>
    <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-ui-line bg-ui-panel p-4"><div><h3 className="font-semibold">需要验证策略表现？</h3><p className="mt-1 text-xs text-ui-faint">历史回测已移到独立的策略研究工作台，和决策审计分别保留记录。</p></div><Link to="/research" className="rounded-lg border border-ui-accent/30 px-3 py-2 text-sm text-ui-accent">前往策略研究 →</Link></section>
  </div>;
}

function pct(value: number | null | undefined) { return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : "-"; }
function actionForDecision(decision: string): "buy" | "sell" | null {
  const normalized = decision.toUpperCase();
  if (["BUY", "ADD", "OVERWEIGHT"].includes(normalized)) return "buy";
  if (["SELL", "REDUCE", "EXIT", "UNDERWEIGHT", "AVOID"].includes(normalized)) return "sell";
  return null;
}

function Kpi({ icon: Icon, label, value }: { icon: typeof Activity; label: string; value: number | string }) {
  return <div className="rounded-lg border border-ui-line bg-ui-panel p-4"><Icon className="h-4 w-4 text-ui-accent"/><div className="mt-2 text-xs text-ui-faint">{label}</div><div className="mt-1 text-lg font-semibold text-ui-ink">{value}</div></div>;
}
