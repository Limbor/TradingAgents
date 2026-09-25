import { expect, test, type Page } from "@playwright/test";
import { mockAgentTasks } from "./agentMock";

const holding = {
  symbol: "600519.SH",
  name: "贵州茅台",
  quantity: 1000,
  avg_cost: 100,
  current_price: 110,
  notes: null,
  updated_at: "2026-07-11T00:00:00Z",
};

async function mockApi(page: Page, options: { artifacts?: unknown[] } = {}) {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body: unknown = {};
    if (path.endsWith("/holdings")) body = [holding];
    else if (path.endsWith("/runs")) body = [];
    else if (path.includes("/artifacts")) body = options.artifacts ?? [];
    else if (path.endsWith("/strategy-lessons")) body = [];
    else if (path.endsWith("/reflection-cases")) body = [];
    else if (path.endsWith("/plans")) body = [];
    else if (path.endsWith("/profile")) body = { investment_style: "long_term", risk_tolerance: "moderate", sector_prefs: [], updated_at: null };
    else if (path.endsWith("/risk-events")) body = [{
      id: "risk:1",
      symbol: holding.symbol,
      name: holding.name,
      level: "red",
      event_type: "announcement",
      title: "公司被立案调查",
      source: "exchange",
      event_date: "2026-07-10",
      status: "open",
      first_seen_at: "2026-07-10T00:00:00Z",
      last_seen_at: "2026-07-11T00:00:00Z",
      payload: {},
    }];
    else if (path.endsWith("/health")) body = { stockmanager_mcp: { connected: true } };
    else if (path.endsWith("/config")) body = {};
    else if (path.endsWith("/reflections/summary")) body = { total: 0, correct: 0, incorrect: 0, accuracy: 0 };
    else if (path.endsWith("/decision-audit/summary")) body = {
      decision_count: 2, open_count: 1, realized_count: 1, execution_count: 1,
      linked_execution_count: 1, execution_link_rate: 1,
      execution_validation: { sample_count: 1, win_rate: 1, average_directional_return: 0.04, statistically_usable: false },
      validation: { overall: { sample_count: 1, win_rate: 1, average_return: 0.05, statistically_usable: false }, by_decision: {}, strategy_claims_allowed: false, effectiveness_claim_allowed: false, warnings: ["Only 1 realized sample"] },
    };
    else if (path.endsWith("/decision-audit/decisions")) body = [{
      id: "decision:1", source_type: "daily_pipeline", symbol: "600519.SH", name: "贵州茅台",
      decision_date: "2026-07-01", decision: "BUY", horizon_days: 5, reference_price: 100,
      status: "realized", reflection_case_id: "audit:1", execution_count: 1, outcome_count: 1, payload: {},
    }];
    else if (path.endsWith("/backtests")) body = [];
    else if (path.endsWith("/backtests/catalog")) body = { strategies: [{ name: "ff_residual_csi800_main", sha1: "s1" }], configs: [{ name: "prod_ff_residual_csi800_tv15", sha1: "c1" }] };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

test("dashboard renders structured risk events", async ({ page }) => {
  page.on("pageerror", (error) => console.error("pageerror", error.message));
  await mockApi(page);
  await page.goto("/");

  await expect(page.getByText("结构化风险事件")).toBeVisible();
  await expect(page.getByText(/公司被立案调查/)).toBeVisible();
  await expect(page.getByText("高", { exact: true })).toBeVisible();
});

test("decision desk shows sourced candidates and their selected evidence", async ({ page }) => {
  await mockApi(page, { artifacts: [{
    id: "decision-pack-1", run_id: "run-1", artifact_type: "decision_pack", title: "每日选股 2026-09-25 决策包",
    summary: "输出 2 个候选", created_at: "2026-09-25T08:00:00Z",
    payload: { market_asof_date: "2026-09-25", decision_pack: [
      { symbol: "600519.SH", name: "贵州茅台", quant_decision: "BUY", final_decision: "WATCHLIST", display_score: 78, gate_reasons: ["公告待核验"] },
      { symbol: "000001.SZ", name: "平安银行", quant_decision: "BUY", final_decision: "SKIP", display_score: 61, gate_reasons: ["流动性门槛未通过"] },
    ] },
  }] });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "最新候选与证据" })).toBeVisible();
  await page.getByRole("row", { name: /平安银行/ }).click();
  await expect(page.getByText("流动性门槛未通过").last()).toBeVisible();
  await expect(page.getByRole("button", { name: "问 Agent" })).toBeVisible();
});

test("decision desk opens strategy research and submits the selected backtest", async ({ page }) => {
  await mockApi(page);
  let submitted: unknown;
  await page.route("**/api/v1/backtests", async (route) => {
    if (route.request().method() === "POST") {
      submitted = route.request().postDataJSON();
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ id: "backtest-1", job_id: "job-1", status: "queued" }) });
    } else {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    }
  });
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "交易决策流程" })).toBeVisible();
  await page.getByRole("link", { name: "策略研究" }).click();
  await expect(page).toHaveURL(/\/research$/);
  await expect(page.getByRole("heading", { name: "策略研究" })).toBeVisible();
  await page.getByRole("button", { name: "提交回测" }).click();
  await expect(page.getByRole("status").getByText(/回测已提交/)).toBeVisible();
  expect(submitted).toMatchObject({ strategy_name: "ff_residual_csi800_main", config_name: "prod_ff_residual_csi800_tv15" });
});

test("agent presents a read-only task and evidence without changing holdings", async ({ page }) => {
  await mockApi(page);
  const submissions = await mockAgentTasks(page, "仓位需要复核；这只是分析建议。");

  await page.goto("/chat");
  await page.getByRole("textbox", { name: "交易问题" }).fill("600519.SH 要不要卖");
  await page.getByRole("button", { name: "发送" }).click();
  await expect.poll(() => submissions[0]?.message).toBe("600519.SH 要不要卖");
  await expect(page.getByText("仓位需要复核；这只是分析建议。")).toBeVisible();
  await expect(page.getByText("读取账户数据")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("library renders a persisted decision artifact", async ({ page }) => {
  await mockApi(page, { artifacts: [{
    id: "artifact-1",
    run_id: "run-1",
    skill_id: "daily_pipeline",
    artifact_type: "screening_report",
    title: "2026-07-11 每日选股",
    subtitle: "Top 1",
    subject_type: "market",
    subject_id: "cn_a",
    subject_name: "A股",
    status: "success",
    summary: "候选 1 只",
    content_markdown: "## 每日选股结果",
    payload: { candidates: [{ symbol: "600519.SH", name: "贵州茅台", final_decision: "WATCHLIST" }] },
    tags: ["daily_pipeline"],
    created_at: "2026-07-11T00:00:00Z",
    updated_at: "2026-07-11T00:00:00Z",
  }] });
  await page.goto("/library");
  await expect(page.getByText("2026-07-11 每日选股")).toBeVisible();
  await expect(page.getByText("候选 1 只")).toBeVisible();
});

test("analysis renders replayed WebSocket report content", async ({ page }) => {
  await mockApi(page);
  await page.routeWebSocket(/\/ws\/run\/run-1/, async (ws) => {
    const send = (type: string, payload: Record<string, unknown>) => ws.send(JSON.stringify({
      type, run_id: "run-1", timestamp: "2026-07-11T00:00:00Z", payload,
    }));
    // Keep the route handler alive until the page has installed its native
    // WebSocket callbacks, then emulate the server's immediate event replay.
    await new Promise((resolve) => setTimeout(resolve, 100));
    send("agent_status", { agent: "Market Analyst", status: "completed" });
    send("report_chunk", { section: "market_report", content: "趋势保持强势", is_final: true });
    send("run_complete", { status: "completed" });
  });
  await page.goto("/analysis/run-1");
  await expect(page.getByText("趋势保持强势")).toBeVisible();
  await expect(page.getByRole("button", { name: "Market", exact: true })).toBeVisible();
});

test("chat renders a lightweight tool answer with provenance", async ({ page }) => {
  await mockApi(page);
  await mockAgentTasks(page, "持仓查询完成：600519.SH。");
  await page.goto("/chat");
  await page.getByRole("textbox", { name: "交易问题" }).fill("我的持仓怎么样");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("持仓查询完成：600519.SH。")).toBeVisible();
  await expect(page.getByRole("complementary", { name: "任务证据与方案" }).getByText(/2026-09-25/)).toBeVisible();
});

test("agent mobile workspace keeps the composer usable and opens evidence on demand", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 });
  await mockApi(page);
  await mockAgentTasks(page, "已核对账户。");
  await page.goto("/chat");
  await expect(page.getByRole("textbox", { name: "交易问题" })).toBeVisible();
  await page.getByRole("textbox", { name: "交易问题" }).fill("查看持仓");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("已核对账户。")).toBeVisible();
  await page.getByRole("button", { name: "展开任务档案" }).click();
  await expect(page.getByRole("complementary", { name: "任务证据与方案" })).toBeVisible();
  await page.getByRole("button", { name: "收起任务档案" }).click();
  await expect(page.getByRole("textbox", { name: "交易问题" })).toBeVisible();
});

test("audit separates decisions, executions, outcomes, and sample gate", async ({ page }) => {
  await mockApi(page);
  await page.goto("/audit");
  await expect(page.getByText("决策—执行—收益—反思")).toBeVisible();
  await expect(page.getByText("样本不足", { exact: true })).toBeVisible();
  await expect(page.getByText("600519.SH")).toBeVisible();
  await expect(page.getByRole("link", { name: "前往策略研究" })).toHaveAttribute("href", "/research");
});
