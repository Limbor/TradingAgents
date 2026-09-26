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
        event(3, "plan_revised", { steps: [{ id: "artifacts", label: "查找关联分析产物" }] }),
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
  await expect(page.getByText("失败", { exact: true })).toBeVisible();
});
