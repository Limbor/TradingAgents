import { expect, test } from "@playwright/test";
import { mockAgentTasks } from "./agentMock";

test("strategy paper workbench creates, reads, and advances a StockManager session", async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem("tradingagents.theme", "light"));
  let created = false;
  let advanced = false;
  let advanceBody: unknown = null;
  page.on("dialog", (dialog) => void dialog.accept());
  await page.route("**/api/v1/**", async (route) => {
    const { pathname } = new URL(route.request().url());
    let body: unknown = {};
    if (pathname === "/api/v1/paper/sessions" && route.request().method() === "POST") {
      const request = route.request().postDataJSON();
      expect(request).toMatchObject({ strategy: "demo", start_date: "2026-01-02", initial_cash: 100000 });
      created = true;
      body = { session_id: "paper:demo" };
    } else if (pathname === "/api/v1/paper/sessions") {
      body = created ? [{ session_id: "paper:demo", mode: "paper", strategy: "demo", config_name: "", initial_cash: 100000, last_date: "2026-01-02", params: {} }] : [];
    } else if (pathname === "/api/v1/paper/strategies") {
      body = [{ name: "demo" }];
    } else if (pathname === "/api/v1/paper/configs") {
      body = [];
    } else if (pathname.endsWith("/status")) {
      body = { session: { initial_cash: 100000 }, snapshot: { as_of_date: "2026-01-02", equity: 102000, cash: 40000, positions: { "600519.SH": { name: "贵州茅台", shares: 40, avg_cost: 1000, last_price: 1550, value: 62000 } } }, trades_count: 1 };
    } else if (pathname.endsWith("/equity")) {
      body = { daily_records: [
        { date: "2026-01-01", equity: 100000, cash: 100000 },
        { date: "2026-01-02", equity: 102000, cash: 40000 },
      ], benchmark_curve: [] };
    } else if (pathname.endsWith("/trades")) {
      body = [{ trade_date: "2026-01-02", code: "600519.SH", name: "贵州茅台", side: "BUY", shares: 40, price: 1550, amount: 62000 }];
    } else if (pathname.endsWith("/next-plan")) {
      body = { signal_date: "2026-01-02", equity: 102000, items: [{ code: "600519.SH", name: "贵州茅台", action: "HOLD", diff_value: 0 }] };
    } else if (pathname.endsWith("/advance")) {
      advanceBody = route.request().postDataJSON();
      body = { job_id: "job-1" };
    } else if (pathname.endsWith("/jobs/job-1")) {
      advanced = true;
      body = { job_id: "job-1", state: "success", progress: 100, message: "完成", result: { ok: true } };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await mockAgentTasks(page);

  await page.goto("/paper");
  await expect(page.getByText("还没有策略模拟会话")).toBeVisible();
  await page.getByRole("button", { name: "新建会话" }).click();
  await page.getByRole("combobox", { name: "策略", exact: true }).selectOption("demo");
  await page.getByLabel("起始日期").fill("2026-01-02");
  await page.getByLabel("初始资金").fill("100000");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByText("¥102,000")).toBeVisible();
  await expect(page.getByText("贵州茅台").first()).toBeVisible();
  const equityLine = page.locator(".recharts-line path").first();
  await expect(equityLine).toHaveCSS("stroke", "rgb(8, 125, 104)");
  await page.locator("main").evaluate((element) => { element.scrollTop = 380; });
  if (process.env.CAPTURE_THEME_QA) await page.screenshot({ path: "test-results/theme-paper-equity-light.png", fullPage: true });
  await page.getByRole("button", { name: "主题：浅色，点击切换" }).click();
  await expect(equityLine).toHaveCSS("stroke", "rgb(45, 212, 191)");
  if (process.env.CAPTURE_THEME_QA) await page.screenshot({ path: "test-results/theme-paper-equity-dark.png", fullPage: true });
  await page.getByLabel("推进至交易日").fill("2026-01-05");
  await page.getByRole("button", { name: "推进模拟盘" }).click();
  await expect.poll(() => advanced).toBe(true);
  expect(advanceBody).toEqual({ target_date: "2026-01-05" });
});

test("composite decision is readable and Agent chat stays bound to its account", async ({ page }) => {
  const sessionId = "paper:allocator:demo:follow";
  const otherSessionId = "paper:other";
  await page.route("**/api/v1/**", async (route) => {
    const { pathname } = new URL(route.request().url());
    let body: unknown = {};
    if (pathname === "/api/v1/paper/sessions") body = [
      { session_id: sessionId, mode: "paper", strategy: "allocator", config_name: "demo", initial_cash: 100000, last_date: "2026-09-24", params: { kind: "composite", allocator_config_path: "config/allocators/demo.json" } },
      { session_id: otherSessionId, mode: "paper", strategy: "other", config_name: "", initial_cash: 100000, last_date: "2026-09-24", params: {} },
    ];
    else if (pathname === "/api/v1/paper/strategies") body = [{ name: "demo" }];
    else if (pathname === "/api/v1/paper/configs") body = [];
    else if (pathname === "/api/v1/paper/allocator-configs") body = [{ name: "demo", path: "config/allocators/demo.json", description: "测试组合配置", status: "ready", initial_cash: 100000, start_date: "2026-07-01", sleeves: ["wfo", "csi"] }];
    else if (pathname.endsWith("/status")) body = {
      kind: "composite", session: { initial_cash: 100000 }, snapshot: { as_of_date: "2026-09-24", equity: 108000, cash: 20000, positions: {} }, trades_count: 3,
      decision: { date: "2026-09-24", active_sleeve: "wfo", switched: false, switch_count: 1, fast_relative_return: 0.02 },
      readiness: { status: "ready_to_observe", can_reference_plan: true, reasons: [] },
      freshness: { is_shadow_aligned: true, active_plan_lag_days: 0 },
      summary: { total_return: 0.08, max_drawdown: -0.1, sharpe: 1.2 },
      sleeves: { wfo: { strategy: "smallcap", equity: 105000, last_date: "2026-09-24" }, csi: { strategy: "breakout", equity: 95000, last_date: "2026-09-24" } },
    };
    else if (pathname.endsWith("/equity")) body = { daily_records: [{ date: "2026-09-24", equity: 108000, cash: 20000 }], benchmark_curve: [] };
    else if (pathname.endsWith("/trades")) body = [];
    else if (pathname.endsWith("/next-plan")) body = { signal_date: "2026-09-24", equity: 108000, items: [] };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  const submissions = await mockAgentTasks(page, (paperId) => paperId === sessionId ? "当前由 wfo 子策略运行。" : "第二个会话已单独核对。");

  await page.goto("/paper");
  await expect(page.getByText("账户权益以组合可执行账本为准")).toBeVisible();
  await expect(page.getByText("wfo", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("计划可参考")).toBeVisible();
  await page.getByRole("button", { name: "新建会话" }).click();
  await page.getByRole("combobox", { name: "类型" }).selectOption("composite");
  await expect(page.getByRole("combobox", { name: "组合配置" })).toHaveValue("config/allocators/demo.json");
  await expect(page.getByText(/测试组合配置/)).toBeVisible();
  await page.getByRole("button", { name: "问 Agent 原因" }).click();
  await expect(page).toHaveURL(/\/paper$/);
  await expect.poll(() => submissions[0]?.paperSessionId).toBe(sessionId);
  await expect(page.getByText("已绑定 StockManager 模拟盘")).toBeVisible();
  await expect(page.getByText("当前由 wfo 子策略运行。")).toBeVisible();
  await page.getByRole("button", { name: "展开任务档案" }).click();
  await expect(page.getByRole("complementary", { name: "任务证据与方案" })).toBeVisible();
  await page.getByRole("button", { name: "收起任务档案" }).click();
  await page.getByRole("combobox", { name: "当前模拟盘会话" }).selectOption(otherSessionId);
  await expect(page.getByText("当前由 wfo 子策略运行。")).toHaveCount(0);
  await page.getByRole("button", { name: "总结当前权益、持仓和近期成交" }).click();
  await expect.poll(() => submissions[1]?.paperSessionId).toBe(otherSessionId);
  await expect(page.getByText("第二个会话已单独核对。")).toBeVisible();
  await page.getByRole("combobox", { name: "当前模拟盘会话" }).selectOption(sessionId);
  await expect(page.getByText("当前由 wfo 子策略运行。")).toBeVisible();
  await page.reload();
  await expect(page.getByText("当前由 wfo 子策略运行。")).toBeVisible();
  expect(submissions).toHaveLength(2);
});

test("an advancing paper job can be observed again after page reload", async ({ page }) => {
  let polls = 0;
  let jobLost = false;
  await page.route("**/api/v1/**", async (route) => {
    const { pathname } = new URL(route.request().url());
    let body: unknown = {};
    if (pathname === "/api/v1/paper/sessions") body = [{ session_id: "paper:demo", mode: "paper", strategy: "demo", config_name: "", initial_cash: 100000, last_date: "2026-01-02", params: {} }];
    else if (pathname.endsWith("/status")) body = { session: { initial_cash: 100000 }, snapshot: null, trades_count: 0 };
    else if (pathname.endsWith("/equity")) body = { daily_records: [], benchmark_curve: [] };
    else if (pathname.endsWith("/trades")) body = [];
    else if (pathname.endsWith("/next-plan")) body = null;
    else if (pathname.endsWith("/advance")) body = { job_id: "job-running" };
    else if (pathname.endsWith("/jobs/job-running")) {
      polls += 1;
      if (jobLost) {
        await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Job not found" }) });
        return;
      }
      body = { job_id: "job-running", state: "running", progress: 25, message: "计算中", result: null };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await mockAgentTasks(page);
  page.on("dialog", (dialog) => void dialog.accept());
  await page.goto("/paper");
  await page.getByLabel("推进至交易日").fill("2026-01-05");
  await page.getByRole("button", { name: "推进模拟盘" }).click();
  await expect.poll(() => polls).toBeGreaterThan(0);
  await page.reload();
  await page.getByLabel("推进至交易日").fill("2026-01-05");
  await expect.poll(() => polls).toBeGreaterThan(1);
  await expect(page.getByRole("button", { name: "推进模拟盘" })).toBeDisabled();
  jobLost = true;
  await expect(page.getByText(/任务记录已失效。StockManager/)).toBeVisible();
  await expect(page.getByRole("button", { name: "推进模拟盘" })).toBeEnabled();
});
