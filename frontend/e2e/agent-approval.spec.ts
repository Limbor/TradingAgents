import { expect, test } from "@playwright/test";

test("paper advance proposal requires an explicit confirmation", async ({ page }) => {
  const time = "2026-09-25T08:00:00Z";
  let approvals = 0;
  let status = "awaiting_approval";
  let proposalStatus = "pending";
  const proposal = () => ({
    id: "proposal-1", task_id: "task-1", action_type: "advance_paper_day",
    session_id: "paper:one", args: { target_date: "2026-09-28" },
    baseline: { as_of_date: "2026-09-25", equity: 100000 },
    status: proposalStatus, result: {}, expires_at: "2026-09-25T08:15:00Z",
  });
  const conversation = { id: "conversation-1", title: "模拟盘 · paper:one", paper_session_id: "paper:one",
    created_at: time, updated_at: time, latest_status: status };
  const detail = () => ({ ...conversation,
    messages: [
      { id: "m1", conversation_id: conversation.id, task_id: "task-1", role: "user", content: "推进模拟盘到 2026-09-28", created_at: time },
      { id: "m2", conversation_id: conversation.id, task_id: "task-1", role: "assistant", content: "请核对动作预览。", created_at: time },
    ],
    tasks: [{ id: "task-1", conversation_id: conversation.id, goal: "推进模拟盘到 2026-09-28", status,
      result: { content: "请核对动作预览。" }, error: null, created_at: time, updated_at: time,
      events: [{ task_id: "task-1", seq: 1, event_type: "plan_created", payload: { steps: [{ id: "paper", label: "读取模拟盘账本" }] }, created_at: time }],
      evidence: [{ id: "e1", task_id: "task-1", tool_name: "get_paper_session", source: "StockManager ledger",
        as_of_date: "2026-09-25", retrieved_at: time, summary: "模拟盘账本", warnings: [], result: {} }],
      proposal: proposal() }],
  });

  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/v1/agent/conversations") body = [conversation];
    else if (path === "/api/v1/agent/conversations/conversation-1") body = detail();
    else if (path === "/api/v1/agent/proposals/proposal-1/approve") {
      approvals += 1;
      status = "executing_action";
      proposalStatus = "executing";
      body = proposal();
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/chat?paper_session=paper%3Aone");
  await expect(page.getByText("模拟盘动作预览")).toBeVisible();
  await expect(page.getByText("2026-09-28").first()).toBeVisible();
  expect(approvals).toBe(0);
  await page.getByRole("button", { name: "确认推进" }).click();
  await expect.poll(() => approvals).toBe(1);
  await expect(page.getByRole("button", { name: "确认推进" })).toHaveCount(0);
});
