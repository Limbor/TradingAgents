import { expect, test } from '@playwright/test';

test('condition decision remains readable, retains symbol in followup and has no account action', async ({ page }) => {
  const time = '2026-10-04T00:00:00Z';
  const conversation = { id: 'decision-c', title: '浦发银行中线判断', paper_session_id: null,
    created_at: time, updated_at: time, latest_status: 'completed' };
  const goal = '600000.SH 中线还能投资吗';
  const brief = { symbol: '600000.SH', action_state: 'wait_trigger', summary: '等待盈利与现金流改善得到确认',
    as_of_date: '2026-09-30', horizon: 'medium_term', horizon_days: 60,
    entry_conditions: [{ description: '营收与现金流同步改善', source: '已披露半年报', confirmation: '下一财报复核' }],
    exit_conditions: [{ description: '盈利改善逻辑失效' }], invalidation_conditions: [{ description: '新的重大风险公告' }],
    recheck_conditions: ['下一份财报披露后'], evidence_gaps: ['尚缺同业估值对比'] };
  const task = { id: 'decision-t', conversation_id: conversation.id, goal, status: 'completed',
    result: { content: '先观察盈利改善情况，再核对进入条件。', decision_briefs: [brief] }, error: null,
    created_at: time, updated_at: time, evidence: [], events: [] };
  let followup = '';
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'POST' && path.endsWith('/tasks')) {
      followup = route.request().postDataJSON().message;
      await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify(task) });
      return;
    }
    const body = path === '/api/v1/agent/conversations' ? [conversation]
      : path === '/api/v1/agent/conversations/decision-c' ? { ...conversation, tasks: [task], messages: [
        { id: 'u', conversation_id: conversation.id, task_id: task.id, role: 'user', content: goal, created_at: time },
        { id: 'a', conversation_id: conversation.id, task_id: task.id, role: 'assistant', content: task.result.content, created_at: time },
      ] } : {};
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.setViewportSize({ width: 1100, height: 850 });
  await page.goto('/chat?conversation=decision-c');
  const card = page.getByLabel('个股条件决策');
  await expect(card.getByText('等待条件满足')).toBeVisible();
  await expect(card.getByText('什么条件下考虑退出')).toBeVisible();
  await card.getByText(/判断失效与复核/).click();
  await expect(card.getByText('新的重大风险公告')).toBeVisible();
  await expect(page.getByRole('button', { name: '加入持仓' })).toHaveCount(0);
  expect(await card.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  if (process.env.CAPTURE_AGENT_QA) await page.screenshot({ path: 'test-results/condition-decision.png', fullPage: true });
  await card.getByRole('button', { name: '把进入和退出条件说具体' }).click();
  await expect.poll(() => followup).toBe('600000.SH：把进入和退出条件说具体');
});

test('memory replay requires sufficient samples and an explicit click', async ({ page }) => {
  let posts = 0;
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path.endsWith('/evaluation-preview')) body = { available_pairs: 3, minimum_pairs: 20, can_run: false, active_job_id: null };
    else if (path.endsWith('/evaluations') && route.request().method() === 'POST') posts++;
    else if (path.endsWith('/strategy-memory/overview')) body = { inventory: { available: 1, pending: 3, retired: 0, expired: 0 },
      usage: { sampled_tasks: 2, injected_tasks: 1, reported_tasks: 1 }, evaluation: null };
    else if (path.endsWith('/strategy-lessons') || path.endsWith('/reflection-cases')) body = [];
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('/reflection');
  const overview = page.getByLabel('记忆使用概览');
  await overview.getByText(/有记忆 \/ 无记忆对照评测/).click();
  await expect(overview.getByText(/可用历史样本 3 对/)).toBeVisible();
  await expect(overview.getByRole('button', { name: /运行 20 对评测/ })).toBeDisabled();
  expect(posts).toBe(0);
});
