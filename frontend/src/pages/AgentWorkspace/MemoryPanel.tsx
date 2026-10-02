import { Brain, LoaderCircle } from "lucide-react";
import { agentRole } from "@/utils/activityLabels";
import type { AgentTask } from "@/api/agent";

type Row = Record<string, unknown>;
const object = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.map(object).filter(row => Object.keys(row).length) : [];
const ids = (value: unknown): string[] => Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : [];
const active = new Set(["queued", "planning", "running", "reviewing"]);
const scopeNames: Record<string, string> = { symbol: "同一标的", industry: "同行业", factor: "相同因子", board: "同一板块", global: "通用经验" };

export function taskMemory(task: AgentTask): Row | null {
  const trace = object(task.result.memory_trace);
  if (Object.keys(trace).length) return trace;
  const evidence = [...task.evidence].reverse().find(item => item.tool_name === "get_strategy_lessons");
  const events = task.events.filter(event => event.event_type.startsWith("memory_"));
  if (!evidence && !events.length) return null;
  const received = [...events].reverse().find(event => event.event_type === "memory_injected");
  const latest = events[events.length - 1];
  const retrieved = [...events].reverse().find(event => event.event_type === "memory_retrieved");
  return { snapshots: received?.payload.snapshots ?? evidence?.result.lessons ?? [],
    injected_ids: received?.payload.lesson_ids ?? [], retrieved_ids: retrieved?.payload.lesson_ids ?? [],
    as_of_date: received?.payload.as_of_date ?? evidence?.result.memory_cutoff,
    comparing_ids: latest?.event_type === "memory_comparing" ? latest.payload.lesson_ids : [],
    status: latest?.event_type ?? (evidence?.result.error ? "memory_unavailable" : "memory_retrieved"),
    message: latest?.payload.message, usage: [] };
}

export function memoryMessage(trace: Row, running: boolean): string {
  const snapshots = rows(trace.snapshots);
  const injected = ids(trace.injected_ids);
  const usage = rows(trace.usage);
  if (trace.status === "memory_aligned" && running && typeof trace.message === "string") return trace.message;
  if (trace.status === "memory_retrieving" && running) return "正在检索与当前标的、行业和策略条件相关的已批准经验";
  if (typeof trace.message === "string" && trace.status === "memory_unavailable") return trace.message;
  if (usage.length) {
    const used = usage.filter(row => row.status === "referenced").length;
    const skipped = usage.filter(row => row.status === "not_applicable").length;
    return `模型报告参考 ${used} 条${skipped ? `，${skipped} 条不适用` : ""}`;
  }
  if (trace.status === "memory_comparing" && running) return `正在结合当前证据核对 ${ids(trace.comparing_ids).length || snapshots.length || ids(trace.retrieved_ids).length} 条历史经验`;
  if (injected.length) return `已提供 ${injected.length} 条经验${running ? "，正在核对适用条件" : "；模型未报告具体参考情况"}`;
  if (snapshots.length) return `找到 ${snapshots.length} 条适用经验${running ? "，准备核对" : "；尚未确认模型参考情况"}`;
  return "未找到适用的已批准经验，本轮依据当前数据分析";
}

export function MemoryProgress({ task }: { task: AgentTask }) {
  const trace = taskMemory(task);
  if (!trace) return null;
  const running = active.has(task.status);
  return <div role="status" aria-label="历史经验进度" className="flex items-start gap-2 rounded-md bg-ui-subtle px-3 py-2 text-xs leading-5 text-ui-muted">
    {running && trace.status === "memory_comparing" ? <LoaderCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-ui-accent" /> : <Brain className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ui-accent" />}
    <span className="min-w-0 break-words">{memoryMessage(trace, running)}</span>
  </div>;
}

export function RoleMemory({ task }: { task: AgentTask }) {
  const records = new Map<string, Row>();
  for (const run of task.agent_runs ?? []) records.set(run.run_id, run as unknown as Row);
  for (const event of task.events) if (event.event_type === 'agent_runtime') records.set(String(event.payload.run_id), event.payload);
  const roles = new Map<string, { provided: Set<string>; usage: Map<string, Row> }>();
  for (const record of records.values()) {
    if (record.kind !== 'model' || !ids(record.memory_refs).length) continue;
    const name = String(record.role);
    const role = roles.get(name) ?? { provided: new Set<string>(), usage: new Map<string, Row>() };
    for (const id of ids(record.memory_refs)) role.provided.add(id);
    for (const usage of rows(object(record.output).memory_usage)) {
      if (role.provided.has(String(usage.lesson_id))) role.usage.set(String(usage.lesson_id), usage);
    }
    roles.set(name, role);
  }
  if (!roles.size) return null;
  return <section aria-label="各角色经验参考情况" className="min-w-0 space-y-2 text-xs">
    <h4 className="font-medium text-ui-body">各角色参考情况</h4>
    {[...roles].map(([name, role]) => <details key={name} className="rounded-md border border-ui-line px-3 py-2">
      <summary className="cursor-pointer leading-5">{agentRole(name)} · 提供 {role.provided.size} 条 · 报告参考 {[...role.usage.values()].filter(row => row.status === 'referenced').length} 条</summary>
      <div className="mt-2 space-y-1 leading-5 text-ui-muted">
        {[...role.usage].map(([id, row]) => <p key={id}>{row.status === 'referenced' ? '报告参考' : '不适用'}：{String(row.reason)}</p>)}
        {role.provided.size > role.usage.size && <p>{role.provided.size - role.usage.size} 条尚未报告具体参考情况</p>}
      </div>
    </details>)}
  </section>;
}

export function MemoryDetails({ trace: raw }: { trace: unknown }) {
  const trace = object(raw);
  if (!Object.keys(trace).length) return null;
  const snapshots = rows(trace.snapshots);
  const usage = rows(trace.usage);
  const injected = ids(trace.injected_ids);
  return <section aria-label="历史经验详情" className="min-w-0 space-y-3 text-xs">
    <div className="flex items-center gap-2"><Brain className="h-4 w-4 shrink-0 text-ui-accent" /><h3 className="font-medium text-ui-ink">历史经验</h3></div>
    <p className="leading-5 text-ui-body">{memoryMessage(trace, false)}</p>
    <div className="flex flex-wrap gap-x-4 gap-y-1 tabular-nums text-ui-muted"><span>检索 {ids(trace.retrieved_ids).length || snapshots.length} 条</span><span>提供 {injected.length} 条</span>{typeof trace.as_of_date === "string" && <span>截止 {trace.as_of_date}</span>}</div>
    {snapshots.map((row, index) => {
      const entry = usage.find(item => item.lesson_id === row.id);
      const examples = rows(row.examples);
      return <details key={String(row.id ?? index)} className="min-w-0 rounded-md border border-ui-line bg-ui-subtle px-3 py-2">
        <summary className="cursor-pointer break-words leading-5 text-ui-body">{String(row.finding ?? "历史经验")}</summary>
        <div className="mt-3 space-y-2 leading-5 text-ui-muted">
          <p>{scopeNames[String(row.scope)] ?? "相关经验"}{row.target ? ` · ${String(row.target)}` : ""} · 样本 {String(row.evidence_count ?? "未知")}{row.version_id ? ` · 版本 ${String(row.version_id)}` : ""}</p>
          {row.suggested_adjustment ? <p>核对要点：{String(row.suggested_adjustment)}</p> : null}
          <p className="text-ui-accent">{entry ? `${entry.status === "referenced" ? "模型报告参考" : "模型报告不适用"}：${String(entry.reason)}` : "模型未报告该条经验的具体参考情况"}</p>
          {ids(row.conflicting_ids).length > 0 && <p className="text-ui-warning">同一场景有不同方向的经验，应结合周期和当前证据核对。</p>}
          {examples.map((example, i) => <div key={String(example.id ?? i)} className="border-t border-ui-line pt-2">
            <p>{String(example.symbol ?? "历史案例")} · {String(example.signal_date ?? "日期未知")} · {String(example.horizon_days ?? "—")} 天</p>
            <p>{example.outcome === "correct" ? "方向正确" : example.outcome === "incorrect" ? "方向错误" : "中性观察"}{typeof example.actual_return === "number" ? ` · 收益 ${(example.actual_return * 100).toFixed(2)}%` : ""}{typeof example.excess_return === "number" ? ` · 超额 ${(example.excess_return * 100).toFixed(2)}%` : ""}</p>
            {example.lesson ? <p className="break-words">{String(example.lesson)}</p> : null}
          </div>)}
        </div>
      </details>;
    })}
    <p className="leading-5 text-ui-faint">历史案例用于核对适用条件。参考记录不代表已验证的判断或收益改善。</p>
  </section>;
}
