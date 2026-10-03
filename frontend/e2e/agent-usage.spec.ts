import { expect, test } from "@playwright/test";

test("usage is aggregated per reply, node details expand and the conversation total stays in the inspector", async ({ page }) => {
  const time = "2026-10-03T00:00:00Z";
  const conversation = { id: "usage-c", title: "亨通光电研究", paper_session_id: null,
    created_at: time, updated_at: time, latest_status: "completed" };
  const group = { role: "Fundamentals Analyst", provider: "qianwen", model: "qwen3.8-max",
    model_calls: 2, reported_calls: 2, missing_calls: 0, pending_calls: 0,
    input_tokens: 10000, output_tokens: 1000, total_tokens: 11000, cache_read_tokens: 8000,
    cache_creation_tokens: 0, reasoning_tokens: 100, cache_unknown_calls: 0,
    reasoning_unknown_calls: 0, unpriced_calls: 0, cost_cny: 0.072 };
  const usage = { ...group, groups: [group], currency: "CNY", price_date: "2026-10-03",
    price_source: "https://www.qianwenai.com/models/", incomplete: false, cost_complete: true, cache_hit_rate: 0.8 };
  const task = { id: "usage-t", conversation_id: conversation.id, goal: "分析亨通光电",
    status: "completed", result: { content: "关注营收增长和毛利率变化。" }, error: null,
    created_at: time, updated_at: time, proposal: null, events: [], evidence: [], usage_stats: usage };
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const body = path === "/api/v1/agent/conversations" ? [conversation]
      : path === "/api/v1/agent/conversations/usage-c" ? { ...conversation, tasks: [task],
        usage_stats: { ...usage, input_tokens: 20000 }, messages: [
          { id: "user", conversation_id: conversation.id, task_id: task.id, role: "user", content: task.goal, created_at: time },
          { id: "reply", conversation_id: conversation.id, task_id: task.id, role: "assistant", content: task.result.content, created_at: time },
        ] } : {};
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.setViewportSize({ width: 1100, height: 800 });
  await page.goto("/chat");
  const turn = page.getByLabel("本轮用量", { exact: true });
  await expect(turn.locator("summary")).toContainText("输入 10,000");
  await expect(turn.getByText("基本面分析", { exact: true })).toBeHidden();
  await turn.locator("summary").click();
  await expect(turn.getByText("基本面分析", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "展开任务档案" }).click();
  const cumulative = page.getByLabel("会话累计用量", { exact: true });
  await expect(cumulative.locator("summary")).toContainText("输入 20,000");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await cumulative.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.getByRole("button", { name: "收起任务档案" }).click();
  await expect(turn).toBeVisible();
  expect(await turn.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
});
