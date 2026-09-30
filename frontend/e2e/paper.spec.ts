import { expect, test } from "@playwright/test";
import { mockAgentTasks } from "./agentMock";

test("composite paper compares account, two sleeves and SSE while exposing ledger details", async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const session = { session_id: "paper:comparison", mode: "paper", strategy: "allocator",
      config_name: "demo", initial_cash: 100000, last_date: "2026-09-28", params: { kind: "composite" } };
    let body: unknown = {};
    if (path === "/api/v1/paper/sessions") body = [session];
    else if (path.endsWith("/status")) body = {
      session, kind: "composite", trades_count: 2,
      snapshot: { as_of_date: "2026-09-28", equity: 110000, cash: 5000,
        positions: { "600519.SH": { name: "贵州茅台", shares: 50, avg_cost: 1000,
          last_price: 1200, value: 60000, day_pnl: 1000 } } },
      sleeve_curves: {
        wfo_max_cagr: [{ date: "2026-09-25", equity: 100000 }, { date: "2026-09-28", equity: 105000 }],
        csi800_breakout: [{ date: "2026-09-25", equity: 100000 }, { date: "2026-09-28", equity: 98000 }],
      },
    };
    else if (path.endsWith("/equity")) body = {
      daily_records: [{ date: "2026-09-25T00:00:00", equity: 100000, cash: 100000 },
        { date: "2026-09-28T00:00:00", equity: 110000, cash: 5000 }],
      benchmark_curve: [{ date: "2026-09-25", equity: 3000 }, { date: "2026-09-28", equity: 3060 }],
    };
    else if (path.endsWith("/trades")) body = [
      { trade_date: "2026-09-28", code: "600519.SH", name: "贵州茅台", side: "BUY",
        shares: 50, price: 1200, amount: 60000, fee: 18, note: "策略入场" },
      { trade_date: "2026-09-25", code: "000001.SZ", name: "平安银行", side: "SELL",
        shares: 100, price: 10, amount: 1000, fee: 5 },
    ];
    else if (path.endsWith("/next-plan")) body = { signal_date: "2026-09-28", equity: 110000,
      items: [{ code: "600519.SH", name: "贵州茅台", action: "HOLD", diff_value: 0 },
        { code: "000001.SZ", name: "平安银行", action: "SKIP", diff_value: 0, reason: "交易约束" }] };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await mockAgentTasks(page, "已核对模拟盘账本。", [
    "Authoritative unified executable ledger. Sleeve curves are read-only allocator signals, not account equity.",
  ]);

  await page.goto("/paper?session=paper%3Acomparison");
  const legend = page.getByLabel("收益曲线图例");
  await expect(legend.getByRole("button", { name: /模拟盘账户/ })).toContainText("+10.00%");
  await expect(legend.getByRole("button", { name: /wfo_max_cagr/ })).toContainText("+5.00%");
  await expect(legend.getByRole("button", { name: /csi800_breakout/ })).toContainText("-2.00%");
  await expect(legend.getByRole("button", { name: /上证指数/ })).toContainText("+2.00%");
  await page.getByRole("button", { name: "选择今天推进" }).click();
  await expect(page.getByLabel("推进至交易日")).toHaveValue(/^\d{4}-\d{2}-\d{2}$/);
  await page.getByRole("button", { name: "总结当前权益、持仓和近期成交" }).click();
  await expect(page.getByText("已核对模拟盘账本。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "展开任务档案" }).click();
  const inspector = page.getByRole("complementary", { name: "任务证据与方案" });
  await expect(inspector).toBeVisible();
  const drawerLayout = await inspector.evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    const content = element.children[1] as HTMLElement;
    const topmost = document.elementFromPoint(bounds.right - 12, bounds.top + 120);
    return { portalled: element.parentElement === document.body, abovePage: element.contains(topmost),
      withinViewport: bounds.bottom <= window.innerHeight, noHorizontalOverflow: content.scrollWidth <= content.clientWidth + 1 };
  });
  expect(drawerLayout).toEqual({ portalled: true, abovePage: true, withinViewport: true, noHorizontalOverflow: true });
  if (process.env.CAPTURE_INSPECTOR_QA) await page.screenshot({ path: "test-results/paper-inspector.png" });
  await page.getByRole("button", { name: "收起任务档案" }).click();
  if (process.env.CAPTURE_PAPER_QA) {
    await page.getByRole("img", { name: /累计收益对比/ }).scrollIntoViewIfNeeded();
    await page.mouse.move(0, 0);
    await page.screenshot({ path: "test-results/paper-comparison.png" });
  }
  await legend.getByRole("button", { name: /wfo_max_cagr/ }).click();
  await expect(legend.getByRole("button", { name: /wfo_max_cagr/ })).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByText("虚线为子策略影子信号，非组合账户权益。")).toBeVisible();
  await expect(page.getByRole("region", { name: "模拟盘账本" })).toContainText("+¥10,000");
  const trades = page.getByRole("heading", { name: "成交与操作流水" }).locator("xpath=ancestor::section[1]");
  await expect(trades).toContainText("策略入场");
  await trades.getByRole("button", { name: "卖出" }).click();
  await expect(trades).toContainText("平安银行");
  await expect(trades).not.toContainText("策略入场");
  const plan = page.getByRole("heading", { name: "下一交易日计划" }).locator("xpath=ancestor::section[1]");
  await expect(plan).toContainText("跳过 1");
  await plan.getByRole("button", { name: "全部" }).click();
  await expect(plan).toContainText("持有 1");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: "累计收益对比" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391);
});

test("unknown paper deep link does not switch to another account", async ({ page }) => {
  const ledgerRequests: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/v1/paper/sessions") body = [{
      session_id: "paper:existing", mode: "paper", strategy: "demo", config_name: "",
      initial_cash: 100000, last_date: "2026-01-02", params: {},
    }];
    else if (path.startsWith("/api/v1/paper/sessions/paper")) {
      ledgerRequests.push(path);
      if (path.includes("missing") && path.endsWith("/status")) {
        await route.fulfill({ status: 404, contentType: "application/json",
          body: JSON.stringify({ detail: "Session not found" }) });
        return;
      }
      if (path.endsWith("/status")) body = { session: { initial_cash: 100000 },
        snapshot: { as_of_date: "2026-01-02", equity: 100000, cash: 100000, positions: {} },
        trades_count: 0 };
      else if (path.endsWith("/equity")) body = { daily_records: [], benchmark_curve: [] };
      else if (path.endsWith("/trades")) body = [];
      else if (path.endsWith("/next-plan")) body = null;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await mockAgentTasks(page);

  await page.goto("/paper?session=paper%3Amissing");
  await expect(page.getByRole("alert")).toContainText("未找到模拟盘会话 paper:missing");
  await expect(page.getByRole("region", { name: "交易 Agent 对话" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "推进模拟盘" })).toHaveCount(0);
  expect(ledgerRequests).toHaveLength(1);
  expect(ledgerRequests[0]).toContain("missing");
  await page.getByRole("combobox", { name: "当前模拟盘会话" }).selectOption("paper:existing");
  await expect(page).toHaveURL(/\/paper\?session=paper%3Aexisting$/);
  await expect(page.getByText("¥100,000").first()).toBeVisible();
  await expect.poll(() => ledgerRequests.some((path) => path.includes("existing") &&
    path.endsWith("/status"))).toBe(true);
});

test("composite child deep link opens its ledger without direct advance", async ({ page }) => {
  const parent = "paper:parent";
  const child = "paper:child";
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/v1/paper/sessions") body = [{
      session_id: parent, mode: "paper", strategy: "allocator", config_name: "demo",
      initial_cash: 100000, last_date: "2026-09-25", params: { kind: "composite" },
    }];
    else if (path.endsWith("/status")) {
      const isChild = path.includes("child");
      body = { session: { session_id: isChild ? child : parent, mode: "paper",
        strategy: isChild ? "sleeve" : "allocator", config_name: "demo",
        initial_cash: 100000, last_date: "2026-09-25",
        params: isChild ? { composite_child: true, parent_composite_id: parent } : { kind: "composite" } },
      snapshot: { as_of_date: "2026-09-25", equity: isChild ? 105000 : 110000,
        cash: 100000, positions: {} }, trades_count: 0 };
    } else if (path.endsWith("/equity")) body = { daily_records: [], benchmark_curve: [] };
    else if (path.endsWith("/trades")) body = [];
    else if (path.endsWith("/next-plan")) body = null;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await mockAgentTasks(page);

  await page.goto(`/paper?session=${encodeURIComponent(child)}`);
  await expect(page.getByRole("combobox", { name: "当前模拟盘会话" })).toHaveValue(child);
  await expect(page.getByText("¥105,000").first()).toBeVisible();
  await expect(page.getByRole("region", { name: "交易 Agent 对话" })).toContainText("策略模拟盘 · 已绑定模拟盘");
  await page.getByRole("button", { name: "展开任务档案" }).click();
  await expect(page.getByRole("complementary", { name: "任务证据与方案" }).getByText(child)).toBeVisible();
  await page.getByRole("button", { name: "收起任务档案" }).click();
  await expect(page.getByRole("status")).toContainText("组合子策略账本");
  await expect(page.getByRole("button", { name: "推进模拟盘" })).toHaveCount(0);
  await page.getByRole("link", { name: "查看组合账户" }).click();
  await expect(page.getByRole("combobox", { name: "当前模拟盘会话" })).toHaveValue(parent);
  await expect(page.getByText("¥110,000").first()).toBeVisible();
});

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
      body = { session: { initial_cash: 100000 }, state_fingerprint: "a".repeat(64), snapshot: { as_of_date: "2026-01-02", equity: 102000, cash: 40000, positions: { "600519.SH": { name: "贵州茅台", shares: 40, avg_cost: 1000, last_price: 1550, value: 62000 } } }, trades_count: 1 };
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
  expect(advanceBody).toMatchObject({ target_date: "2026-01-05", expected_state_fingerprint: "a".repeat(64) });
  expect((advanceBody as { client_request_id: string }).client_request_id).toMatch(/^[0-9a-f-]{36}$/);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("link", { name: "在工作台继续" })).toBeVisible();
  const agentHeader = page.getByRole("region", { name: "交易 Agent 对话" }).locator("header");
  expect(await agentHeader.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
});

test("composite decision is readable and Agent chat stays bound to its account", async ({ page }) => {
  const sessionId = "paper:allocator:demo:follow";
  const childId = "paper:sleeve:wfo";
  const otherSessionId = "paper:other";
  await page.route("**/api/v1/**", async (route) => {
    const { pathname } = new URL(route.request().url());
    let body: unknown = {};
    if (pathname === "/api/v1/paper/sessions") body = [
      { session_id: sessionId, mode: "paper", strategy: "allocator", config_name: "demo", initial_cash: 100000, last_date: "2026-09-24", params: { kind: "composite", allocator_config_path: "config/allocators/demo.json", child_session_ids: [childId] } },
      { session_id: otherSessionId, mode: "paper", strategy: "other", config_name: "", initial_cash: 100000, last_date: "2026-09-24", params: {} },
    ];
    else if (pathname === "/api/v1/paper/strategies") body = [{ name: "demo" }];
    else if (pathname === "/api/v1/paper/configs") body = [];
    else if (pathname === "/api/v1/paper/allocator-configs") body = [{ name: "demo", path: "config/allocators/demo.json", description: "测试组合配置", status: "ready", initial_cash: 100000, start_date: "2026-07-01", sleeves: ["wfo", "csi"] }];
    else if (pathname.endsWith("/status") && pathname.includes("sleeve")) body = {
      session: { session_id: childId, initial_cash: 100000, last_date: "2026-09-24",
        params: { composite_child: true, parent_composite_id: sessionId } },
      snapshot: { as_of_date: "2026-09-24", equity: 105000, cash: 10000, positions: {} }, trades_count: 1,
    };
    else if (pathname.endsWith("/status")) body = {
      kind: "composite", session: { session_id: sessionId, initial_cash: 100000,
        params: { kind: "composite", child_session_ids: [childId] } }, snapshot: { as_of_date: "2026-09-24", equity: 108000, cash: 20000, positions: {} }, trades_count: 3,
      decision: { date: "2026-09-24", active_sleeve: "wfo", switched: false, switch_count: 1, fast_relative_return: 0.02 },
      readiness: { status: "ready_to_observe", can_reference_plan: true, reasons: [] },
      freshness: { is_shadow_aligned: true, active_plan_lag_days: 0 },
      summary: { total_return: 0.08, max_drawdown: -0.1, sharpe: 1.2 },
      sleeves: { wfo: { strategy: "smallcap", session_id: childId, equity: 105000, last_date: "2026-09-24" }, csi: { strategy: "breakout", session_id: "paper:foreign", equity: 95000, last_date: "2026-09-24" } },
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
  await expect(page.getByRole("region", { name: "交易 Agent 对话" })).toContainText("已绑定模拟盘");
  await expect(page.getByText("当前由 wfo 子策略运行。")).toBeVisible();
  await page.getByRole("button", { name: "展开任务档案" }).click();
  await expect(page.getByRole("complementary", { name: "任务证据与方案" })).toBeVisible();
  await page.getByRole("button", { name: "收起任务档案" }).click();
  await page.getByRole("link", { name: "在工作台继续" }).click();
  await expect(page).toHaveURL(/\/chat\?paper_session=paper%3Aallocator%3Ademo%3Afollow&conversation=conversation-1$/);
  await expect(page.getByText("当前由 wfo 子策略运行。")).toBeVisible();
  await page.getByRole("button", { name: "展开任务档案" }).click();
  await page.getByRole("link", { name: "查看完整账本" }).click();
  await expect(page).toHaveURL(/\/paper\?session=paper%3Aallocator%3Ademo%3Afollow$/);
  await expect(page.getByText("当前由 wfo 子策略运行。")).toBeVisible();
  await page.getByRole("combobox", { name: "当前模拟盘会话" }).selectOption(otherSessionId);
  await expect(page.getByText("当前由 wfo 子策略运行。")).toHaveCount(0);
  await page.getByRole("button", { name: "总结当前权益、持仓和近期成交" }).click();
  await expect.poll(() => submissions[1]?.paperSessionId).toBe(otherSessionId);
  await expect(page.getByText("第二个会话已单独核对。")).toBeVisible();
  await page.getByRole("combobox", { name: "当前模拟盘会话" }).selectOption(sessionId);
  await expect(page.getByText("当前由 wfo 子策略运行。")).toBeVisible();
  await page.reload();
  await expect(page.getByText("当前由 wfo 子策略运行。")).toBeVisible();
  await expect(page.getByRole("link", { name: "查看csi账本" })).toHaveCount(0);
  await expect(page.getByText("账本归属待核对")).toBeVisible();
  await page.getByRole("link", { name: "查看wfo账本" }).click();
  await expect(page.getByRole("combobox", { name: "当前模拟盘会话" })).toHaveValue(childId);
  await expect(page.getByRole("status")).toContainText("组合子策略账本");
  await expect(page.getByRole("button", { name: "推进模拟盘" })).toHaveCount(0);
  await page.getByRole("link", { name: "查看组合账户" }).click();
  await expect(page.getByRole("combobox", { name: "当前模拟盘会话" })).toHaveValue(sessionId);
  expect(submissions).toHaveLength(2);
});

test("an advancing paper job can be observed again after page reload", async ({ page }) => {
  let polls = 0;
  let jobLost = false;
  let advanceFails = false;
  let reviewed = false;
  let reviewAcks = 0;
  let lastRequestId = "";
  await page.route("**/api/v1/**", async (route) => {
    const { pathname } = new URL(route.request().url());
    let body: unknown = {};
    if (pathname === "/api/v1/paper/sessions") body = [{ session_id: "paper:demo", mode: "paper", strategy: "demo", config_name: "", initial_cash: 100000, last_date: "2026-01-02", params: {} }];
    else if (pathname.endsWith("/status")) body = { session: { initial_cash: 100000 }, state_fingerprint: "b".repeat(64), snapshot: null, trades_count: 0,
      advance_operation: jobLost ? { job_id: "job-running", target_date: "2026-01-05",
        state: reviewed ? "reviewed" : "needs_review", updated_at: "2026-01-05T00:00:00Z" } : null };
    else if (pathname.endsWith("/equity")) body = { daily_records: [], benchmark_curve: [] };
    else if (pathname.endsWith("/trades")) body = [];
    else if (pathname.endsWith("/next-plan")) body = null;
    else if (pathname.endsWith("/advance")) {
      lastRequestId = route.request().postDataJSON().client_request_id;
      if (advanceFails) {
        await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Connection lost" }) });
        return;
      }
      body = { job_id: "job-running" };
    } else if (pathname.includes("/advance-requests/")) {
      if (advanceFails) {
        await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Receipt not found" }) });
        return;
      }
      expect(pathname.endsWith(lastRequestId)).toBe(true);
      body = { client_request_id: lastRequestId, session_id: "paper:demo",
        job_id: "job-running", target_date: "2026-01-05", state: "running", result: null };
    } else if (pathname.endsWith("/advance-review")) {
      expect(route.request().postDataJSON()).toMatchObject({ job_id: "job-running",
        observed_state_fingerprint: "b".repeat(64), confirmed: true });
      reviewed = true;
      reviewAcks += 1;
      body = { ok: true, state: "reviewed" };
    }
    else if (pathname.endsWith("/jobs/job-running")) {
      polls += 1;
      if (jobLost) {
        await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Job not found" }) });
        return;
      }
      body = { job_id: "job-running", state: "running", progress: 25, message: "计算中", result: null };
    } else if (pathname.endsWith("/jobs/submission-unknown")) {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Job not found" }) });
      return;
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
  await expect(page.getByRole("button", { name: "推进模拟盘" })).toBeDisabled();
  await page.reload();
  await expect(page.getByRole("button", { name: "推进模拟盘" })).toBeDisabled();
  await page.getByRole("button", { name: "核对账本后解除锁定" }).click();
  await expect.poll(() => reviewAcks).toBe(1);
  await page.getByLabel("推进至交易日").fill("2026-01-05");
  await expect(page.getByRole("button", { name: "推进模拟盘" })).toBeEnabled();
  advanceFails = true;
  await page.getByRole("button", { name: "推进模拟盘" }).click();
  await expect(page.getByText("提交结果待核对")).toBeVisible();
  await expect(page.getByRole("button", { name: "推进模拟盘" })).toBeDisabled();
  await page.reload();
  await expect(page.getByRole("button", { name: "推进模拟盘" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "核对账本后解除锁定" })).toBeVisible();
});

test("lost paper submission response recovers the original job by receipt", async ({ page }) => {
  let submissions = 0;
  let receiptReads = 0;
  let jobReads = 0;
  let requestId = "";
  page.on("dialog", (dialog) => void dialog.accept());
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/v1/paper/sessions") body = [{ session_id: "paper:recover", mode: "paper",
      strategy: "demo", config_name: "", initial_cash: 100000, last_date: "2026-01-02", params: {} }];
    else if (path.endsWith("/status")) body = { session: { initial_cash: 100000 },
      state_fingerprint: "d".repeat(64), snapshot: { as_of_date: "2026-01-02", equity: 100000,
        cash: 100000, positions: {} }, trades_count: 0 };
    else if (path.endsWith("/equity")) body = { daily_records: [], benchmark_curve: [] };
    else if (path.endsWith("/trades")) body = [];
    else if (path.endsWith("/next-plan")) body = null;
    else if (path.endsWith("/advance")) {
      submissions += 1;
      requestId = route.request().postDataJSON().client_request_id;
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Response lost" }) });
      return;
    } else if (path.includes("/advance-requests/")) {
      receiptReads += 1;
      expect(path.endsWith(requestId)).toBe(true);
      body = { client_request_id: requestId, session_id: "paper:recover", job_id: "job-recovered",
        target_date: "2026-01-05", state: "running", result: null };
    } else if (path.endsWith("/jobs/job-recovered")) {
      jobReads += 1;
      body = { job_id: "job-recovered", state: "running", progress: 25, message: "计算中", result: null };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await mockAgentTasks(page);
  await page.goto("/paper");
  await page.getByLabel("推进至交易日").fill("2026-01-05");
  await page.getByRole("button", { name: "推进模拟盘" }).click();
  await expect.poll(() => jobReads).toBeGreaterThan(0);
  await page.reload();
  await expect.poll(() => receiptReads).toBeGreaterThan(1);
  await expect.poll(() => jobReads).toBeGreaterThan(1);
  expect(submissions).toBe(1);
  await expect(page.getByRole("button", { name: "推进模拟盘" })).toBeDisabled();
});

test("server-side paper review lock appears without local browser state", async ({ page }) => {
  let reviewed = false;
  let reviewAcks = 0;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/v1/paper/sessions") body = [{ session_id: "paper:server-lock", mode: "paper",
      strategy: "demo", config_name: "", initial_cash: 100000, last_date: "2026-01-02", params: {} }];
    else if (path.endsWith("/status")) body = { session: { initial_cash: 100000 },
      state_fingerprint: "c".repeat(64), snapshot: null, trades_count: 0,
      advance_operation: { job_id: "job:other-client", target_date: "2026-01-05",
        state: reviewed ? "reviewed" : "needs_review", updated_at: "2026-01-05T00:00:00Z" } };
    else if (path.endsWith("/equity")) body = { daily_records: [], benchmark_curve: [] };
    else if (path.endsWith("/trades")) body = [];
    else if (path.endsWith("/next-plan")) body = null;
    else if (path.endsWith("/advance-review")) {
      expect(route.request().postDataJSON()).toMatchObject({ job_id: "job:other-client",
        observed_state_fingerprint: "c".repeat(64), confirmed: true });
      reviewed = true;
      reviewAcks += 1;
      body = { ok: true, state: "reviewed" };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await mockAgentTasks(page);
  page.on("dialog", (dialog) => void dialog.accept());
  await page.goto("/paper");
  await page.getByLabel("推进至交易日").fill("2026-01-05");
  await expect(page.getByRole("button", { name: "推进模拟盘" })).toBeDisabled();
  await page.getByRole("button", { name: "核对账本后解除锁定" }).click();
  await expect.poll(() => reviewAcks).toBe(1);
  await expect(page.getByRole("button", { name: "推进模拟盘" })).toBeEnabled();
});
