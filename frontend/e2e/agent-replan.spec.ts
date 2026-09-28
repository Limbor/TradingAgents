import { expect, test } from "@playwright/test";

test("Agent timeline shows steps added after a tool failure", async ({ page }) => {
  const time = "2026-09-25T08:00:00Z";
  const conversation = { id: "conversation-replan", title: "持仓风险", paper_session_id: null,
    created_at: time, updated_at: time, latest_status: "completed" };
  const taskId = "task-replan";
  const event = (seq: number, event_type: string, payload: Record<string, unknown>) =>
    ({ task_id: taskId, seq, event_type, payload, created_at: time });
  const detail = { ...conversation,
    messages: [{ id: "message-replan", conversation_id: conversation.id, task_id: taskId,
      role: "user", content: "评估当前持仓风险", created_at: time }],
    tasks: [{ id: taskId, conversation_id: conversation.id, goal: "评估当前持仓风险",
      status: "completed", result: { content: "持仓库不可用，尚未形成交易判断。" },
      error: null, created_at: time, updated_at: time, proposal: null, evidence: [],
      events: [
        event(1, "plan_created", { steps: [{ id: "portfolio", label: "读取当前手工持仓" }] }),
        event(2, "step_completed", { id: "portfolio", status: "failed" }),
        event(3, "plan_revised", { steps: [{ id: "artifacts", label: "查找关联分析产物" }],
          reason: "持仓数据源不可用，改查历史分析产物" }),
        event(4, "step_completed", { id: "artifacts", status: "completed" }),
      ] }],
  };
  await page.route("**/api/v1/agent/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path === "/api/v1/agent/conversations" ? [conversation] : detail;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/chat");
  await expect(page.getByText("执行过程 · 已调整计划")).toBeVisible();
  await expect(page.getByText("读取当前手工持仓")).toBeVisible();
  await expect(page.getByText("查找关联分析产物")).toBeVisible();
  await expect(page.getByText("调整原因：持仓数据源不可用，改查历史分析产物")).toBeVisible();
  await expect(page.getByText("失败", { exact: true })).toBeVisible();
});

test("interrupted read task requires an explicit retry", async ({ page }) => {
  const time = "2026-09-25T08:00:00Z";
  const conversation = { id: "conversation-interrupted", title: "模拟盘风险", paper_session_id: "paper:one",
    created_at: time, updated_at: time, latest_status: "interrupted" };
  const goal = "评估当前模拟盘风险";
  let submissions = 0;
  const detail = { ...conversation,
    messages: [{ id: "m1", conversation_id: conversation.id, task_id: "task-old",
      role: "user", content: goal, created_at: time }],
    tasks: [{ id: "task-old", conversation_id: conversation.id, goal, status: "interrupted",
      result: {}, error: null, created_at: time, updated_at: time, evidence: [], proposal: null,
      events: [
        { task_id: "task-old", seq: 1, event_type: "plan_created",
          payload: { steps: [{ id: "ledger", label: "读取当前模拟盘账本" }] }, created_at: time },
        { task_id: "task-old", seq: 2, event_type: "step_started",
          payload: { id: "ledger" }, created_at: time },
        { task_id: "task-old", seq: 3, event_type: "step_completed",
          payload: { id: "ledger", status: "failed", reason: "interrupted" }, created_at: time },
        { task_id: "task-old", seq: 4, event_type: "task_interrupted",
          payload: { reason: "process_restart" }, created_at: time },
      ] }],
  };
  await page.route("**/api/v1/agent/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = detail;
    if (path === "/api/v1/agent/conversations") body = [conversation];
    else if (path.endsWith("/tasks") && route.request().method() === "POST") {
      const request = route.request().postDataJSON() as { message: string };
      expect(request.message).toBe(goal);
      submissions += 1;
      body = { id: "task-new" };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/chat?paper_session=paper%3Aone");
  await expect(page.getByText("读取当前模拟盘账本").locator("..").getByText("已中断")).toBeVisible();
  await expect(page.getByRole("button", { name: "重新运行任务" })).toBeVisible();
  expect(submissions).toBe(0);
  await page.getByRole("button", { name: "重新运行任务" }).click();
  await expect.poll(() => submissions).toBe(1);
});
