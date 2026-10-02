import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { AgentEvent, AgentTask } from '@/api/agent';
import { ActivityTimeline, taskActivities } from './ActivityTimeline';

afterEach(cleanup);
const event = (seq: number, type: string, payload: Record<string, unknown>, runId = 'r'): AgentEvent => ({
  task_id: 't', seq, event_type: 'skill_progress', payload: { run_id: runId, event_type: type, payload }, created_at: '',
});
const task = (events: AgentEvent[], status = 'running'): AgentTask => ({
  id: 't', conversation_id: 'c', goal: '', status, result: {}, error: null, created_at: '', updated_at: '', evidence: [], events,
});
it('keeps simultaneous price queries distinct and merges completion by invocation', () => {
  const events = [
    event(1, 'tool_call', { tool: 'get_stock_data', activity_id: 'a', status: 'running', args: { symbol: '600000.SH', api_key: 'secret' } }),
    event(2, 'tool_call', { tool: 'get_stock_data', activity_id: 'b', status: 'running', args: { symbol: '600001.SH' } }),
    event(3, 'tool_call', { tool: 'get_stock_data', activity_id: 'b', status: 'completed' }),
  ];
  const activities = taskActivities(task(events));
  expect(activities.map(a => a.status)).toEqual(['running', 'completed']);
  render(<ActivityTimeline activities={activities} />);
  expect(screen.getByRole('status')).toHaveTextContent('正在查询股票价格');
  expect(screen.getByRole('status')).toHaveTextContent('600000.SH');
  expect(screen.getByLabelText('执行记录')).toHaveTextContent('600001.SH');
  expect(document.body.textContent).not.toContain('secret');
  expect(document.body.textContent).not.toContain('get_stock_data');
});
it('does not duplicate raw agent and tool events when normalized events exist', () => {
  const events = [event(1, 'skill_progress', { stage_id: 'tool', stage_label: '查询股票价格', activity_id: 'a', status: 'running' }),
    event(2, 'tool_call', { tool: 'get_stock_data', activity_id: 'a' })];
  expect(taskActivities(task(events))).toHaveLength(1);
});
it('retains old agent messages and closes unresolved activity without claiming success', () => {
  const events = [event(1, 'agent_status', { agent: 'Market Analyst', status: 'running' })];
  expect(taskActivities(task(events, 'completed'))[0]?.status).toBe('ended');
  expect(taskActivities(task(events, 'cancelled'))[0]?.status).toBe('cancelled');
  expect(taskActivities(task(events, 'interrupted'))[0]?.status).toBe('interrupted');
  render(<ActivityTimeline activities={taskActivities(task(events, 'completed'))} />);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  expect(screen.getByText(/状态未回传/)).toBeInTheDocument();
});
it('always shows active work, reveals older completed records and preserves failures', () => {
  const activities = Array.from({ length: 6 }, (_, i) => ({ id: `${i}`, label: `动作${i}`, message: `动作${i}`, status: 'completed' }));
  render(<ActivityTimeline activities={[...activities, { id: 'active', label: '查公告', message: '正在查公告', status: 'running' }]} />);
  expect(screen.getByRole('status')).toHaveTextContent('正在查公告');
  expect(screen.queryByText('动作0 · 已完成')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看全部 6 项执行记录' }));
  expect(screen.getByText('动作0 · 已完成')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '收起执行记录' }));
  expect(screen.queryByText('动作0 · 已完成')).not.toBeInTheDocument();
});
it('settles only the ended sub-run while other parallel work stays active', () => {
  const events = [event(1, 'tool_call', { tool: 'get_stock_data', activity_id: 'a' }),
    event(2, 'tool_call', { tool: 'get_news', activity_id: 'a' }, 'other'),
    { task_id: 't', seq: 3, created_at: '', event_type: 'skill_completed', payload: { run_id: 'r', status: 'failed' } }];
  expect(taskActivities(task(events)).map(a => a.status)).toEqual(['failed', 'running']);
});
it('settles unfinished nested analysis when its parent fails even if the task continues', () => {
  const events = [event(1, 'skill_progress', { stage_id: 'deep:a', stage_label: '深入分析', activity_id: 'deep:a', status: 'running' }),
    event(2, 'skill_progress', { stage_id: 'query', stage_label: '查价格', activity_id: 'query', parent_activity_id: 'deep:a', status: 'running' }),
    event(3, 'skill_progress', { stage_id: 'deep:a', stage_label: '深入分析', activity_id: 'deep:a', status: 'failed' })];
  expect(taskActivities(task(events)).map(a => a.status)).toEqual(['failed', 'failed']);
});
