import { expect, test } from "@playwright/test";

test("memory progress updates, explanation persists and narrow inspector stays contained", async ({ page }) => {
  const time = "2026-10-02T00:00:00Z";
  const conversation = { id: "memory-c", title: "医药板块分析", paper_session_id: null,
    created_at: time, updated_at: time, latest_status: "reviewing" };
  const snapshots = [{ id: "lesson", scope: "industry", target: "医药生物", finding: "核对量价持续性与风险标签",
    suggested_adjustment: "有缺失数据时说明判断前提", evidence_count: 5, version_id: 12,
    examples: [{ id: "case", symbol: "600000.SH", signal_date: "2026-08-01", horizon_days: 5,
      outcome: "incorrect", actual_return: -0.032, excess_return: -0.012 }] }];
  const trace = { snapshots, retrieved_ids: ["lesson"], injected_ids: ["lesson"], as_of_date: "2026-09-30",
    status: "model_reported", usage: [{ lesson_id: "lesson", status: "referenced", reason: "同一行业且资金数据覆盖条件相近" }] };
  const event = (seq: number, event_type: string, payload: Record<string, unknown>) =>
    ({ task_id: "memory-t", seq, event_type, payload, created_at: time });
  let completed = false;
  let releaseStream = () => {};
  const gate = new Promise<void>(resolve => { releaseStream = resolve; });
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/stream")) {
      await gate;
      completed = true;
      await route.fulfill({ contentType: "text/event-stream", body:
        `event: agent_event\ndata: ${JSON.stringify(event(3, "memory_reviewed", trace))}\n\n` + "event: done\ndata: {}\n\n" });
      return;
    }
    const task = { id: "memory-t", conversation_id: conversation.id, goal: "医药板块值得关注吗",
      status: completed ? "completed" : "reviewing", result: completed ? { content: "关注量价延续情况。", memory_trace: trace } : {},
      error: null, created_at: time, updated_at: time, proposal: null,
      events: [event(1, "memory_retrieved", { lesson_ids: ["lesson"] }),
        event(2, "memory_comparing", { lesson_ids: ["lesson"] })],
      evidence: [{ id: "memory-e", task_id: "memory-t", tool_name: "get_strategy_lessons", source: "历史经验库",
        as_of_date: null, retrieved_at: time, summary: "找到 1 条适用经验", warnings: [],
        result: { lessons: snapshots, memory_cutoff: "2026-09-30" } }] };
    const body = path === "/api/v1/agent/conversations" ? [conversation]
      : path === "/api/v1/agent/conversations/memory-c" ? { ...conversation, tasks: [task], messages: [
        { id: "user", conversation_id: conversation.id, task_id: task.id, role: "user", content: task.goal, created_at: time },
        ...(completed ? [{ id: "answer", conversation_id: conversation.id, task_id: task.id,
          role: "assistant", content: "关注量价延续情况。", created_at: time }] : []) ] } : {};
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.setViewportSize({ width: 1100, height: 800 });
  await page.goto("/chat");
  await expect(page.getByLabel("历史经验进度")).toContainText("正在结合当前证据核对 1 条历史经验");
  releaseStream();
  await expect(page.getByLabel("历史经验进度")).toContainText("模型报告参考 1 条");
  await page.reload();
  await expect(page.getByLabel("历史经验进度")).toContainText("模型报告参考 1 条");
  await page.getByRole("button", { name: "展开任务档案" }).click();
  const inspector = page.getByRole("complementary", { name: "任务证据与方案" });
  const memory = inspector.getByLabel("历史经验详情");
  await expect(memory.getByText("截止 2026-09-30")).toBeVisible();
  await memory.getByText("核对量价持续性与风险标签", { exact: true }).click();
  await expect(memory.getByText("模型报告参考：同一行业且资金数据覆盖条件相近")).toBeVisible();
  await expect(memory.getByText(/收益 -3.20% · 超额 -1.20%/)).toBeVisible();
  expect(await inspector.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  if (process.env.CAPTURE_AGENT_QA) await page.screenshot({ path: "test-results/agent-memory.png", fullPage: true });
});
