import { expect, test } from "@playwright/test";

/**
 * Pre-release smoke against a REAL backend (no request mocking).
 *
 * The stable e2e contract lives in workbench.spec.ts, which intercepts
 * /api/v1/** with static fixtures so the frontend main flows stay verifiable
 * regardless of backend state. This smoke is the complement: it boots the
 * actual FastAPI server (throwaway SQLite, MCP degraded) via the Playwright
 * webServer and asserts the app shell talks to it end-to-end.
 *
 * Only runs when E2E_REAL_BACKEND=1 (see playwright.config.ts testMatch gate);
 * the default `npm run test:e2e` run ignores *.smoke.ts entirely.
 */

test("real backend answers the health probe", async ({ request }) => {
  // baseURL is the vite dev server; /api is proxied to the FastAPI backend.
  const res = await request.get("/api/v1/health");
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  expect(body.status).toBe("ok");
  expect(body.service).toBe("tradingagents-api");
  // MCP may be connected or degraded; the field must simply be present.
  expect(body).toHaveProperty("stockmanager_mcp");
});

test("real backend persists an Agent conversation", async ({ request }) => {
  const created = await request.post("/api/v1/agent/conversations", { data: { title: "冒烟测试" } });
  expect(created.status()).toBe(201);
  const conversation = await created.json();
  const detail = await request.get(`/api/v1/agent/conversations/${conversation.id}`);
  expect(detail.ok()).toBeTruthy();
  expect((await detail.json()).title).toBe("冒烟测试");
});

test("app shell loads against the real backend", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto("/");

  // The sidebar shell is backend-independent and always present once the SPA
  // mounts, proving the bundle loaded and rendered.
  await expect(page.getByRole("link", { name: "TradingAgents 首页" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "决策工作台" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "更多工具" }).getByRole("link", { name: "持仓管理" })).toBeVisible();

  // The dashboard header renders after its real /api/v1 queries resolve
  // (empty in-memory DB → empty states, no crash).
  await expect(page.getByRole("heading", { name: "决策工作台" })).toBeVisible();

  expect(errors).toEqual([]);
});

test("portfolio route renders its empty state from the real backend", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto("/portfolio");

  // Navigating to another route that fetches real /api/v1/holdings must not
  // throw; the sidebar stays mounted regardless of returned data.
  await expect(page.getByRole("navigation", { name: "更多工具" }).getByRole("link", { name: "持仓管理" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("Agent keeps a trade question when the user supplies a missing symbol", async ({ page, request }) => {
  await page.goto("/chat");
  const createdResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/agent/conversations") &&
    response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "新建对话" }).click();
  const conversation = await (await createdResponse).json() as { id: string };

  const input = page.getByRole("textbox", { name: "交易问题" });
  await input.fill("现在要不要买入茅台？");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(/请直接回复要评估的 A 股代码/)).toBeVisible();
  await expect(page.getByText("需要补充信息").first()).toBeVisible();

  await input.fill("600519.SH");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("600519.SH", { exact: true })).toBeVisible();
  await expect.poll(async () => {
    const detail = await (await request.get(`/api/v1/agent/conversations/${conversation.id}`)).json();
    const task = detail.tasks[1];
    return {
      count: detail.tasks.length,
      goal: task?.goal,
      scope: task?.events.find((event: { event_type: string }) => event.event_type === "scope_resolved")?.payload.ts_code,
      input: detail.messages.find((message: { task_id: string; role: string }) =>
        message.task_id === task?.id && message.role === "user")?.content,
    };
  }).toMatchObject({
    count: 2,
    goal: expect.stringContaining("现在要不要买入茅台"),
    scope: "600519.SH",
    input: "600519.SH",
  });
  await expect.poll(async () => {
    const detail = await (await request.get(`/api/v1/agent/conversations/${conversation.id}`)).json();
    return detail.tasks[1]?.status;
  }).toBe("completed");
  const detail = await (await request.get(`/api/v1/agent/conversations/${conversation.id}`)).json();
  expect(detail.tasks[1].evidence.map((item: { tool_name: string }) => item.tool_name))
    .toContain("get_mcp_factor_snapshot");
  expect(detail.tasks[1].evidence[0].result.error).toContain("MCP");
  expect(detail.tasks[1].result.content).toContain("没有生成交易判断");
  expect(detail.tasks[1].agent_runs).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: "tool", role: "tool:get_mcp_factor_snapshot", status: "failed" }),
  ]));
  await expect(page.getByText(/本轮没有生成交易判断/)).toBeVisible();
  await page.getByRole("button", { name: "展开任务档案" }).click();
  const inspector = page.getByRole("complementary", { name: "任务证据与方案" });
  await expect(inspector.getByText("读取失败")).toBeVisible();
  await expect(inspector.getByText("StockManager MCP is not connected")).toBeVisible();
});

test("real backend exposes a migrated shared model policy", async ({ request }) => {
  const response = await request.get("/api/v1/config");
  expect(response.ok()).toBeTruthy();
  const config = await response.json();
  expect(config.model_policy.default_model).toEqual(expect.any(String));
  expect(config.model_policy.default_model.length).toBeGreaterThan(0);
  expect(config).not.toHaveProperty("api_key");
});
