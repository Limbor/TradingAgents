import { Link } from "react-router-dom";
import { ArrowRight, Target } from "lucide-react";
import { MemoryDetails } from "@/pages/AgentWorkspace/MemoryPanel";

export interface DecisionCondition {
  description: string; source?: string; metric?: string; operator?: string;
  threshold?: number | null; upper?: number | null; confirmation?: string;
}
export interface DecisionBrief {
  version?: number; symbol?: string; run_id?: string; action_state: string; summary: string;
  as_of_date?: string; horizon?: string; horizon_days?: number; time_horizon?: string | null;
  entry_conditions?: DecisionCondition[]; exit_conditions?: DecisionCondition[];
  invalidation_conditions?: DecisionCondition[]; recheck_conditions?: string[];
  evidence_gaps?: string[]; memory_trace?: Record<string, unknown>;
}
const states: Record<string, string> = { consider_entry: "可考虑进入", wait_trigger: "等待条件满足", avoid: "暂不考虑进入", insufficient_evidence: "尚不足以形成决策" };
const horizons: Record<string, string> = { short_term: "短线", medium_term: "中线", long_term: "长线" };

function Conditions({ title, rows }: { title: string; rows?: DecisionCondition[] }) {
  return <section className="min-w-0"><h4 className="text-xs font-medium text-ui-muted">{title}</h4>
    {rows?.length ? <ul className="mt-2 space-y-3">{rows.map((row, i) => <li key={i} className="text-sm leading-6 text-ui-body">
      <p className="break-words">{row.description}</p>
      {row.source && <p className="mt-0.5 break-words text-xs leading-5 text-ui-faint">依据：{row.source} · {row.confirmation || "需核对"}</p>}
    </li>)}</ul> : <p className="mt-2 text-xs leading-5 text-ui-faint">尚无已确认条件</p>}
  </section>;
}

export function DecisionBriefCard({ brief, onAsk, disabled }: { brief: DecisionBrief; onAsk?: (text: string) => void; disabled?: boolean }) {
  const symbol = brief.symbol || "该股";
  return <article aria-label="个股条件决策" className="not-prose min-w-0 rounded-lg border border-ui-line bg-ui-panel p-4 sm:p-5">
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-ui-muted">
      <span className="flex items-center gap-2"><Target className="h-4 w-4 text-ui-accent" />{symbol} · {horizons[brief.horizon || ""] || "投资研究"}</span>
      <span>数据截至 {brief.as_of_date || "未确认"}</span>
    </div>
    <h3 className="mt-4 text-lg font-medium leading-7 text-ui-ink">{states[brief.action_state] || "条件研究"}</h3>
    <p className="mt-2 break-words text-sm leading-7 text-ui-body">{brief.summary}</p>
    <div className="mt-5 grid gap-5 border-t border-ui-line pt-4 sm:grid-cols-2">
      <Conditions title="什么条件下考虑进入" rows={brief.entry_conditions} />
      <Conditions title="什么条件下考虑退出" rows={brief.exit_conditions} />
    </div>
    <details className="mt-4 border-t border-ui-line pt-3 text-xs text-ui-muted">
      <summary className="cursor-pointer leading-6">判断失效与复核 · {brief.horizon_days ? `${brief.horizon_days} 个交易日评价窗口` : "期限待确认"}</summary>
      <div className="mt-3 space-y-4"><Conditions title="判断失效条件" rows={brief.invalidation_conditions} />
        {!!brief.recheck_conditions?.length && <div><h4 className="font-medium">何时重新评估</h4><ul className="mt-2 space-y-2 leading-6">{brief.recheck_conditions.map((row, i) => <li key={i}>{row}</li>)}</ul></div>}
        {brief.memory_trace && <MemoryDetails trace={brief.memory_trace} />}
        <p className="leading-5 text-ui-faint">这些是标的研究条件，尚未核对实时触发或实际成交，不涉及账户持仓操作。</p>
      </div>
    </details>
    {!!brief.evidence_gaps?.length && <div className="mt-4 rounded-md bg-ui-warning/5 px-3 py-2 text-xs leading-6 text-ui-warning"><strong className="font-medium">需要补充的依据</strong><ul className="mt-1 space-y-1">{brief.evidence_gaps.map((gap, i) => <li key={i}>{gap}</li>)}</ul></div>}
    <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-xs text-ui-accent">
      {brief.run_id && <Link to={`/analysis/${encodeURIComponent(brief.run_id)}`} className="inline-flex items-center gap-1">查看完整研究 <ArrowRight className="h-3 w-3" /></Link>}
      {onAsk && ["把进入和退出条件说具体", "补充基本面与估值依据", "中线是否值得投资"].map(text => <button key={text} disabled={disabled} onClick={() => onAsk(`${symbol}：${text}`)} className="text-left disabled:opacity-50">{text}</button>)}
    </div>
  </article>;
}
