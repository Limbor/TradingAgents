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

test("uncertain paper action can be reconciled without another approval", async ({ page }) => {
  const time = "2026-09-25T08:00:00Z";
  let checks = 0;
  const conversation = { id: "conversation-2", title: "模拟盘 · paper:one", paper_session_id: "paper:one",
    created_at: time, updated_at: time, latest_status: "needs_review" };
  const proposal = () => ({ id: "proposal-2", task_id: "task-2", action_type: "advance_paper_day",
    session_id: "paper:one", args: { target_date: "2026-09-28" },
    baseline: { as_of_date: "2026-09-25", equity: 100000 }, status: "unknown",
    result: checks ? { job_id: "job:lost", observed_date: "2026-09-28", job_error: "Job not found",
      child_ledgers: [
        { session_id: "paper:child-one", as_of_date: "2026-09-28", equity: 110000, error: null },
        { session_id: "paper:child-two", as_of_date: "2026-09-25", equity: 90000, error: "读取超时" },
      ] } : { job_id: "job:lost" },
    expires_at: "2026-09-25T08:15:00Z" });
  const detail = () => ({ ...conversation,
    messages: [{ id: "m1", conversation_id: conversation.id, task_id: "task-2", role: "user",
      content: "推进模拟盘到 2026-09-28", created_at: time }],
    tasks: [{ id: "task-2", conversation_id: conversation.id, goal: "推进模拟盘到 2026-09-28",
      status: "needs_review", result: {}, error: "执行状态待核对", created_at: time, updated_at: time,
      events: [], evidence: [], proposal: proposal() }],
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/v1/agent/conversations") body = [conversation];
    else if (path === "/api/v1/agent/conversations/conversation-2") body = detail();
    else if (path === "/api/v1/agent/proposals/proposal-2/reconcile") {
      checks += 1;
      body = proposal();
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/chat?paper_session=paper%3Aone");
  await expect(page.getByText("执行结果待核对", { exact: false }).first()).toBeVisible();
  await page.getByRole("button", { name: "核对执行结果" }).click();
  await expect.poll(() => checks).toBe(1);
  await expect(page.getByText("最近核对的账本日期：2026-09-28")).toBeVisible();
  await expect(page.getByText("子策略账本核对")).toBeVisible();
  await expect(page.getByText("基准日 2026-09-28")).toBeVisible();
  await expect(page.getByText("读取超时")).toBeVisible();
  await expect(page.getByRole("link", { name: "查看账本" }).first()).toHaveAttribute("href", "/paper?session=paper%3Achild-one");
  await expect(page.getByRole("button", { name: "确认推进" })).toHaveCount(0);
});

test("completed paper job without ledger advance is shown as no change", async ({ page }) => {
  const time = "2026-09-26T08:00:00Z";
  const conversation = { id: "conversation-no-day", title: "模拟盘 · paper:one", paper_session_id: "paper:one",
    created_at: time, updated_at: time, latest_status: "completed" };
  const detail = { ...conversation,
    messages: [
      { id: "m1", conversation_id: conversation.id, task_id: "task-no-day", role: "user",
        content: "推进模拟盘到 2026-09-26", created_at: time },
      { id: "m2", conversation_id: conversation.id, task_id: "task-no-day", role: "assistant",
        content: "StockManager 作业已结束，但模拟盘账本未推进。", created_at: time },
    ],
    tasks: [{ id: "task-no-day", conversation_id: conversation.id, goal: "推进模拟盘到 2026-09-26",
      status: "completed", result: { content: "模拟盘账本未推进" }, error: null,
      created_at: time, updated_at: time, events: [], evidence: [],
      proposal: { id: "proposal-no-day", task_id: "task-no-day", action_type: "advance_paper_day",
        session_id: "paper:one", args: { target_date: "2026-09-26" },
        baseline: { as_of_date: "2026-09-25", equity: 100000 }, status: "no_change",
        result: { job_id: "job:no-day", as_of_date: "2026-09-25", advanced_days: 0 },
        expires_at: time } }],
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path === "/api/v1/agent/conversations" ? [conversation] : detail;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/chat?paper_session=paper%3Aone");
  await expect(page.getByText("账本未推进", { exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("账本日期仍为 2026-09-25");
  await expect(page.getByRole("status")).toContainText("目标日期 2026-09-26 尚未达到");
  await expect(page.getByRole("button", { name: "确认推进" })).toHaveCount(0);
});

test("same-day account change invalidates the paper approval card", async ({ page }) => {
  const time = "2026-09-25T08:00:00Z";
  const conversation = { id: "conversation-stale", title: "模拟盘 · paper:one",
    paper_session_id: "paper:one", created_at: time, updated_at: time, latest_status: "completed" };
  const detail = { ...conversation,
    messages: [
      { id: "m1", conversation_id: conversation.id, task_id: "task-stale", role: "user",
        content: "推进模拟盘到 2026-09-29", created_at: time },
      { id: "m2", conversation_id: conversation.id, task_id: "task-stale", role: "assistant",
        content: "确认前账户资金发生变化，提案已失效。", created_at: time },
    ],
    tasks: [{ id: "task-stale", conversation_id: conversation.id, goal: "推进模拟盘到 2026-09-29",
      status: "completed", result: { content: "提案未执行" }, error: null,
      created_at: time, updated_at: time, events: [], evidence: [],
      proposal: { id: "proposal-stale", task_id: "task-stale", action_type: "advance_paper_day",
        session_id: "paper:one", args: { target_date: "2026-09-29" },
        baseline: { as_of_date: "2026-09-25", equity: 100000 }, status: "stale",
        result: { current_date: "2026-09-25", reason: "account_state_changed" },
        expires_at: time } }],
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify(path === "/api/v1/agent/conversations" ? [conversation] : detail) });
  });
  await page.goto("/chat?paper_session=paper%3Aone");
  await expect(page.getByText("提案已失效", { exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("原提案失效且未执行");
  await expect(page.getByRole("button", { name: "确认推进" })).toHaveCount(0);
});
