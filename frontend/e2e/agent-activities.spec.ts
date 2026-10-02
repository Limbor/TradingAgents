import { expect, test } from '@playwright/test';

test('real-time subtask states, expand history, reload and cancellation', async ({ page }) => {
  const time = '2026-10-02T00:00:00Z';
  const conversation = { id: 'activity-c', title: '医药股票分析', paper_session_id: null, created_at: time, updated_at: time };
  const event = (seq: number, type: string, payload: Record<string, unknown>) => ({
    task_id: 'activity-t', seq, event_type: type, payload, created_at: time,
  });
  const progress = (seq: number, id: string, label: string, status: string, agent: string, detail: string) => event(seq, 'skill_progress', {
    run_id: 'r', event_type: 'skill_progress', payload: { stage_id: id, activity_id: id, stage_label: label,
      step_label: status === 'running' ? `正在${label}` : undefined, status, agent, detail },
  });
  const initial = [event(1, 'plan_created', { steps: [{ id: 'analysis', label: '分析股票走势、基本面与风险' }] }),
    event(2, 'step_started', { id: 'analysis' }),
    progress(3, 'prepare', '准备分析', 'completed', 'Market Analyst', '基准日 2026-09-30'),
    progress(4, 'memory', '核对历史经验', 'completed', 'Market Analyst', '找到适用经验'),
    progress(5, 'news', '查询相关新闻', 'completed', 'News Analyst', '标的 600000.SH'),
    progress(6, 'financial', '查询资产负债表', 'completed', 'Fundamentals Analyst', '标的 600000.SH'),
    progress(7, 'price-a', '查询股票价格', 'running', 'Market Analyst', '标的 600000.SH'),
    progress(8, 'price-b', '查询股票价格', 'running', 'Market Analyst', '标的 600001.SH')];
  let cancelled = false;
  let updated = false;
  let release = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/stream')) {
      if (updated) return route.fulfill({ contentType: 'text/event-stream', body: 'event: done\ndata: {}\n\n' });
      await gate;
      updated = true;
      return route.fulfill({ contentType: 'text/event-stream', body: `event: agent_event\ndata: ${JSON.stringify(progress(9, 'price-a', '查询股票价格', 'completed', 'Market Analyst', '标的 600000.SH'))}\n\nevent: done\ndata: {}\n\n` });
    }
    const task = { id: 'activity-t', conversation_id: conversation.id, goal: '帮我分析医药股票',
      status: cancelled ? 'cancelled' : 'running', result: {}, error: null, created_at: time, updated_at: time,
      evidence: [], proposal: null, events: [...initial, ...(updated ? [progress(9, 'price-a', '查询股票价格', 'completed', 'Market Analyst', '标的 600000.SH')] : [])] };
    const body = path === '/api/v1/agent/conversations' ? [conversation]
      : path === '/api/v1/agent/conversations/activity-c' ? { ...conversation, tasks: [task], messages: [
        { id: 'u', conversation_id: conversation.id, task_id: task.id, role: 'user', content: task.goal, created_at: time }] } : {};
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.setViewportSize({ width: 1050, height: 800 });
  await page.goto('/chat');
  const timeline = page.getByLabel('分析子任务进度');
  await expect(timeline.getByText('正在查询股票价格', { exact: true })).toHaveCount(2);
  await expect(timeline.getByLabel('当前动作')).toContainText('行情分析师');
  await timeline.getByRole('button', { name: '查看全部 4 项执行记录' }).click();
  await expect(timeline.getByText('准备分析 · 已完成')).toBeVisible();
  release();
  await expect(timeline.getByText('正在查询股票价格', { exact: true })).toHaveCount(1);
  await expect(timeline.getByLabel('当前动作')).toContainText('600001.SH');
  await page.reload();
  await expect(timeline.getByText('正在查询股票价格', { exact: true })).toHaveCount(1);
  expect(await timeline.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  if (process.env.CAPTURE_AGENT_QA) await page.screenshot({ path: 'test-results/agent-activities.png', fullPage: true });
  cancelled = true;
  await page.reload();
  await expect(timeline.getByLabel('当前动作')).toHaveCount(0);
  await expect(timeline.getByText('查询股票价格 · 已取消')).toBeVisible();
});

test('runtime role hierarchy and actual model survive reload', async ({ page }) => {
  const time = '2026-10-02T00:00:00Z';
  const conversation = { id: 'unified-c', title: '统一研究任务', paper_session_id: null, created_at: time, updated_at: time };
  const runtime = (seq: number, run_id: string, parent_id: string, role: string, kind: string, status: string, model: string) => ({
    task_id: 'unified-t', seq, event_type: 'agent_runtime', created_at: time,
    payload: { run_id, parent_id, role, kind, status, model },
  });
  const events = [runtime(1, 'workflow', 'unified-t', 'stock_analysis', 'workflow', 'running', 'flash'),
    runtime(2, 'analyst', 'workflow', 'Market Analyst', 'agent', 'running', 'flash'),
    runtime(3, 'round', 'analyst', 'Market Analyst', 'model', 'completed', 'flash'),
    runtime(4, 'analyst', 'workflow', 'Market Analyst', 'agent', 'completed', 'flash'),
    runtime(5, 'manager', 'workflow', 'Research Manager', 'agent', 'running', 'pro')];
  const task = { id: 'unified-t', conversation_id: 'unified-c', goal: '分析股票', status: 'running',
    result: {}, error: null, created_at: time, updated_at: time, evidence: [], events, proposal: null };
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/stream')) return route.fulfill({ contentType: 'text/event-stream', body: 'event: done\ndata: {}\n\n' });
    const body = path === '/api/v1/agent/conversations' ? [conversation]
      : path === '/api/v1/agent/conversations/unified-c' ? { ...conversation, tasks: [task], messages: [{ id: "u", conversation_id: conversation.id, task_id: task.id, role: "user", content: task.goal, created_at: time }] } : {};
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('/chat');
  const timeline = page.getByLabel('分析子任务进度');
  await expect(timeline.getByLabel('当前动作')).toContainText('研究经理 · pro');
  await expect(timeline.getByLabel('执行记录')).toContainText('行情分析师 · flash');
  await expect(timeline.getByText('分析价格走势与技术指标 · 已完成', { exact: true })).toHaveCount(1);
  await page.reload();
  await expect(timeline.getByLabel('当前动作')).toContainText('研究经理 · pro');
});
