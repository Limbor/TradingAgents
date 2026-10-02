import { expect, test } from "@playwright/test";

test("Agent updates a running task when its event stream advances", async ({ page }) => {
  const time = "2026-09-27T00:00:00Z";
  const conversation = { id: "stream-conversation", title: "交易任务", paper_session_id: null,
    created_at: time, updated_at: time, latest_status: "running" };
  const event = (seq: number, event_type: string, payload: Record<string, unknown>) =>
    ({ task_id: "stream-task", seq, event_type, payload, created_at: time });
  let streamDelivered = false;
  await page.route("**/api/v1/agent/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/stream")) {
      streamDelivered = true;
      const completed = event(2, "task_completed", { content: "事件流已更新" });
      await route.fulfill({ status: 200, contentType: "text/event-stream",
        body: `id: 2\nevent: agent_event\ndata: ${JSON.stringify(completed)}\n\n` +
          "event: done\ndata: {}\n\n" });
      return;
    }
    if (path === "/api/v1/agent/conversations") {
      await route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify([{ ...conversation, latest_status: streamDelivered ? "completed" : "running" }]) });
      return;
    }
    const detail = { ...conversation,
      messages: [
        { id: "m1", conversation_id: conversation.id, task_id: "stream-task",
          role: "user", content: "评估当前风险", created_at: time },
        ...(streamDelivered ? [{ id: "m2", conversation_id: conversation.id, task_id: "stream-task",
          role: "assistant", content: "事件流已更新", created_at: time }] : []),
      ],
      tasks: [{ id: "stream-task", conversation_id: conversation.id, goal: "评估当前风险",
        status: streamDelivered ? "completed" : "running", result: streamDelivered
          ? { content: "事件流已更新" } : {}, error: null, created_at: time, updated_at: time,
        proposal: null, evidence: [], events: [event(1, "task_created", { goal: "评估当前风险" })] }],
    };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(detail) });
  });

  await page.goto("/chat");
  await expect(page.getByText("事件流已更新")).toBeVisible({ timeout: 3000 });
  expect(streamDelivered).toBe(true);
  await expect(page.getByText("已完成", { exact: true }).first()).toBeVisible();
});
