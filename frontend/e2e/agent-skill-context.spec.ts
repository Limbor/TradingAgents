import { expect, test } from '@playwright/test';

test('selected skill and inherited research constraints stay readable after reload', async ({ page }) => {
  const time = '2026-10-03T08:00:00Z';
  const conversation = { id: 'skill-context', title: '基本面研究', paper_session_id: null,
    created_at: time, updated_at: time, latest_status: 'completed' };
  const taskId = 'research-followup';
  const detail = { ...conversation, messages: [
    { id: 'u', conversation_id: conversation.id, task_id: taskId, role: 'user', content: '它的营收呢，想做中线', created_at: time },
    { id: 'a', conversation_id: conversation.id, task_id: taskId, role: 'assistant', content: '已完成基本面补充研究。', created_at: time },
  ], tasks: [{ id: taskId, conversation_id: conversation.id, goal: '它的营收呢，想做中线',
    status: 'completed', result: {}, error: null, created_at: time, updated_at: time, evidence: [],
    task_context: { target: 'stock', symbols: ['600487.SH'], industries: ['通信'],
      filters: { board_filter: 'main_board' }, horizon: 'medium_term', dimensions: ['fundamentals'],
      inherited_from: 'previous', last_skill: 'stock_analysis' },
    events: [{ task_id: taskId, seq: 1, event_type: 'skill_loaded', created_at: time,
      payload: { name: '个股研究', skill_id: 'stock_analysis' } }],
  }] };
  await page.route('**/api/v1/agent/**', async route => {
    const url = new URL(route.request().url());
    const body = url.pathname === '/api/v1/agent/conversations' ? [conversation] : detail;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('/chat?conversation=skill-context');
  await expect(page.getByText('读取技能流程 · 已完成')).toBeVisible();
  await page.getByRole('button', { name: '展开任务档案', exact: true }).click();
  const inspector = page.locator('aside').filter({ has: page.getByText('研究条件', { exact: true }) });
  await expect(inspector.getByText('研究条件', { exact: true })).toBeVisible();
  await expect(inspector.getByText('600487.SH', { exact: true })).toBeVisible();
  await expect(inspector.getByText('中线', { exact: true })).toBeVisible();
  await expect(inspector.getByText('排除科创与创业板', { exact: true })).toBeVisible();
  await expect(inspector.getByText('已接续上一轮研究对象与条件；行情依据会重新核验。')).toBeVisible();
  await page.reload();
  await expect(page.getByText('读取技能流程 · 已完成')).toBeVisible();
});
