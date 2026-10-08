import { useState } from 'react';
import { Check, ChevronDown, CircleAlert, Clock3, LoaderCircle, Square } from 'lucide-react';
import type { AgentTask } from '@/api/agent';
import { activityDetail, activityMessage, AGENT_ACTIONS, SKILL_ACTIONS, agentRole, toolAction } from '@/utils/activityLabels';

export interface Activity {
  id: string;
  label: string;
  message: string;
  status: string;
  detail?: string;
  agent?: string;
  runId?: string;
  parentId?: string;
  model?: string;
}
const active = new Set(['queued', 'planning', 'running', 'reviewing', 'executing_action']);
const knownStates = new Set(['queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted']);
const reuseLabels: Record<string, string> = { 'Market Analyst': '复用技术面研究', 'Fundamentals Analyst': '复用基本面研究',
  'News Analyst': '复用新闻研究', 'Sentiment Analyst': '复用情绪研究' };
const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown): string | undefined => typeof value === 'string' && value.trim() ? value : undefined;

/** Rebuild durable activity state, including legacy events, after reload. */
export function taskActivities(task: AgentTask): Activity[] {
  const activities = new Map<string, Activity>();
  const runtimeEvents = task.events.filter((event) => event.event_type === 'agent_runtime');
  const roleRuns = new Set(runtimeEvents.filter((event) => event.payload.kind === 'agent').map((event) => String(event.payload.run_id)));
  const runtimeRoles = new Set(runtimeEvents.filter((event) => event.payload.kind === 'agent').map((event) => String(event.payload.role)));
  const normalizedIds = new Set(task.events.filter((e) => e.event_type === 'skill_progress' && e.payload.event_type === 'skill_progress')
    .map((e) => `${String(e.payload.run_id ?? '')}:${String(object(e.payload.payload).activity_id ?? '')}`));
  const normalizedAgents = new Set(task.events.filter((e) => e.event_type === 'skill_progress' && e.payload.event_type === 'skill_progress')
    .map((e) => `${String(e.payload.run_id ?? '')}:${String(object(e.payload.payload).agent ?? '')}`));
  for (const event of task.events) {
    if (['model_waiting', 'model_wait_finished', 'tool_loop_fallback'].includes(event.event_type)) {
      const id = 'model:tool_selection';
      if (event.event_type === 'model_waiting') {
        activities.set(id, { id, label: '等待模型选择分析工具', message: '正在等待模型选择分析工具', status: 'running',
          model: text(event.payload.model), detail: `单次等待上限 ${String(event.payload.timeout_seconds)} 秒` });
      } else if (activities.has(id)) {
        const item = activities.get(id)!;
        item.status = event.event_type === 'model_wait_finished' ? 'completed' : 'failed';
        item.detail = text(event.payload.message) ?? item.detail;
        item.message = activityMessage(item.label, item.status);
      }
      continue;
    }
    if (['skill_loaded', 'task_context_updated', 'task_resumed', 'evidence_read', 'checkpoint_reused'].includes(event.event_type)) {
      const label = event.event_type === 'task_resumed' ? '恢复未完成的研究目标'
        : event.event_type === 'skill_loaded' ? '读取技能流程'
        : event.event_type === 'evidence_read' ? '核对完整证据'
        : event.event_type === 'checkpoint_reused' ? '恢复已完成的查询' : '整理研究对象与条件';
      const id = `${event.event_type}:${String(event.payload.skill_id ?? event.payload.evidence_id ?? '')}`;
      activities.set(id, { id, label, message: label, status: 'completed',
        detail: text(event.payload.goal) ?? text(event.payload.name) ?? text(event.payload.message) });
      continue;
    }
    if (event.event_type === 'agent_runtime') {
      const record = event.payload;
      const id = text(record.run_id);
      const role = text(record.role);
      if (!id || !role || record.kind === 'tool' || (record.kind === 'model' && roleRuns.has(String(record.parent_id)))) continue;
      const modelTimedOut = record.kind === 'model' && record.status === 'interrupted' && task.result.error_code === 'model_timeout';
      const status = modelTimedOut ? 'failed' : text(record.status) ?? 'running';
      const reused = object(record.reused_from);
      const warning = Array.isArray(record.warnings) ? record.warnings.find(value => typeof value === 'string') : undefined;
      const label = text(reused.run_id) ? reuseLabels[role] ?? '复用已有研究' : AGENT_ACTIONS[role] ?? SKILL_ACTIONS[role] ?? agentRole(role) ?? '分析任务';
      activities.set(`runtime:${id}`, { id: `runtime:${id}`, label, status, message: activityMessage(label, status),
        parentId: text(record.parent_id) ? `runtime:${String(record.parent_id)}` : undefined,
        model: text(record.model), agent: agentRole(role), runId: id,
        detail: (modelTimedOut ? '模型响应超时；未返回完整用量统计' : text(warning)) ?? (text(reused.run_id) ? `沿用已核验研究 · 基准日 ${String(reused.as_of_date ?? '未知')} · 保留原始来源`
          : Number(object(record.context_stats).saved_tokens_estimate ?? 0) > 0 ? '已精简研究摘要，完整报告保留' : undefined) });
      continue;
    }
    const runId = text(event.payload.run_id) ?? '';
    if (event.event_type === 'skill_completed') {
      for (const item of activities.values()) {
        if (item.runId === runId && ['running', 'queued'].includes(item.status)) {
          item.status = event.payload.status === 'failed' ? 'failed' : event.payload.status === 'cancelled' ? 'cancelled' : 'ended';
        }
      }
      continue;
    }
    if (event.event_type !== 'skill_progress') continue;
    const payload = object(event.payload.payload);
    const type = event.payload.event_type;
    let id = text(payload.activity_id);
    let label: string | undefined;
    let message: string | undefined;
    let status = text(payload.status) ?? 'running';
    let detail = text(payload.detail);
    const agent = text(payload.agent);
    if (agent && runtimeRoles.has(agent) && type !== 'tool_call' && payload.stage_kind !== 'tool') continue;
    if (type === 'skill_progress' || type === 'progress_update') {
      id ??= text(payload.stage_id) ?? text(payload.step_id);
      label = text(payload.stage_label) ?? text(payload.step_label);
      message = text(payload.step_label);
    } else if (type === 'agent_status') {
      if (!agent || (id && normalizedIds.has(`${runId}:${id}`)) || (!id && normalizedAgents.has(`${runId}:${agent}`))) continue;
      id ??= `agent:${agent}`;
      label = AGENT_ACTIONS[agent] ?? agentRole(agent);
      // Some old skills put descriptive Chinese text in status.
      if (!knownStates.has(status)) {
        detail = status;
        status = 'running';
      }
    } else if (type === 'tool_call') {
      if (id && normalizedIds.has(`${runId}:${id}`)) continue;
      id ??= `tool:${String(payload.tool)}:${event.seq}`;
      label = toolAction(String(payload.tool ?? ''));
      detail = activityDetail(payload.args);
    }
    if (!id || !label) continue;
    status = knownStates.has(status) ? status : 'running';
    const key = `${runId}:${id}`;
    const previous = activities.get(key);
    activities.set(key, { id: key, label, message: message ?? activityMessage(label, status), status,
      parentId: text(payload.parent_activity_id) ? `${runId}:${String(payload.parent_activity_id)}` : undefined,
      detail: detail ?? previous?.detail, agent: agentRole(agent) ?? previous?.agent, runId });
  }
  for (const item of activities.values()) {
    const parent = item.parentId ? activities.get(item.parentId) : undefined;
    if (parent && !['running', 'queued'].includes(parent.status) && ['running', 'queued'].includes(item.status)) {
      item.status = parent.status === 'completed' ? 'ended' : parent.status;
    }
  }
  if (!active.has(task.status)) {
    for (const item of activities.values()) {
      if (['running', 'queued'].includes(item.status)) {
        item.status = task.status === 'failed' ? 'failed' : task.status === 'cancelled' ? 'cancelled' : task.status === 'interrupted' ? 'interrupted' : 'ended';
      }
    }
  }
  return [...activities.values()];
}

const statusText: Record<string, string> = {
  completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断',
  ended: '状态未回传', queued: '等待中', running: '进行中',
};
function ActivityRow({ item, nested = false }: { item: Activity; nested?: boolean }) {
  const running = item.status === 'running';
  return <li className={`flex min-w-0 items-start gap-2.5 py-1.5 ${nested ? "ml-3 border-l border-ui-line pl-3" : ""}`}>
    {running ? <LoaderCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-ui-accent" />
      : item.status === 'completed' ? <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ui-accent" />
      : item.status === 'failed' ? <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ui-danger" />
      : item.status === 'queued' ? <Clock3 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ui-faint" />
      : <Square className="mt-1 h-3 w-3 shrink-0 text-ui-faint" />}
    <div className="min-w-0 flex-1">
      <p className={`break-words leading-5 ${running ? 'font-medium text-ui-body' : 'text-ui-muted'}`}>
        {running ? item.message : `${item.label} · ${statusText[item.status] ?? '已结束'}`}
      </p>
      {(item.agent || item.detail || item.model) && <p className="mt-0.5 break-words text-xs leading-5 text-ui-faint">{[item.agent, item.model, item.detail].filter(Boolean).join(' · ')}</p>}
    </div>
  </li>;
}

export function ActivityTimeline({ activities }: { activities: Activity[] }) {
  const [expanded, setExpanded] = useState(false);
  const current = activities.filter((a) => a.status === 'running' || a.status === 'queued');
  const history = activities.filter((a) => a.status !== 'running' && a.status !== 'queued');
  const visible = expanded ? history : history.filter((a, index) => index >= history.length - 3 || a.status === 'failed');
  if (!activities.length) return null;
  return <div aria-label="分析子任务进度" className="min-w-0 border-l border-ui-line pl-3 text-xs">
    {current.length > 0 && <div role="status" aria-live="polite" aria-atomic="true" className="mb-2 rounded-md bg-ui-accentSoft px-2.5 py-1">
      <ul aria-label="当前动作">{current.map((item) => <ActivityRow key={item.id} item={item} nested={activities.some((parent) => parent.id === item.parentId)} />)}</ul>
    </div>}
    {visible.length > 0 && <ul aria-label="执行记录">{visible.map((item) => <ActivityRow key={item.id} item={item} nested={activities.some((parent) => parent.id === item.parentId)} />)}</ul>}
    {history.length > 3 && <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)} className="mt-1 flex items-center gap-1 py-1 text-ui-accent hover:underline">
      <ChevronDown className={`h-3 w-3 ${expanded ? 'rotate-180' : ''}`} />
      {expanded ? '收起执行记录' : `查看全部 ${history.length} 项执行记录`}
    </button>}
  </div>;
}
