import { expect, test, type Page } from "@playwright/test";

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

test("position advice reaches a prefilled confirmation dialog", async ({ page }) => {
  page.on("pageerror", (error) => console.error("pageerror", error.message));
  await mockApi(page);
  await page.routeWebSocket(/\/ws\/chat/, (ws) => {
    ws.onMessage(() => {
      const send = (type: string, payload: Record<string, unknown>) => ws.send(JSON.stringify({
        type,
        run_id: "run-advisor",
        timestamp: "2026-07-11T00:00:00Z",
        payload,
      }));
      send("chat_reply", { content: "开始持仓建议", skill_triggered: "position_advisor", run_id: "run-advisor" });
      send("position_advice", {
        symbol: holding.symbol,
        action: "REDUCE",
        confidence: 0.8,
        metrics: { current_price: 110, pnl_pct: 10, position_pct: 52.38 },
        risk: { level: "orange" },
        execution: { quantity_change: -700, target_quantity: 300, target_position_pct: 24.79, requires_user_confirmation: true },
        reasons: ["仓位超过集中度阈值"],
        warnings: [],
      });
      send("skill_complete", { status: "success" });
      send("run_complete", { status: "completed" });
    });
  });

  await page.goto("/chat");
  await expect(page.getByText("已连接")).toBeVisible();
  await page.getByPlaceholder(/例如/).fill("600519.SH 要不要卖");
  await page.locator("form button").click();
  await expect(page.getByText("REDUCE", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /前往 Portfolio 确认减仓/ }).click();

  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByLabel("卖出数量")).toHaveValue("700");
  await expect(page.getByLabel("卖出价")).toHaveValue("110");
  await expect(page.getByRole("button", { name: "确认减仓" })).toBeEnabled();
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
  await expect(page.getByText("Market", { exact: true })).toBeVisible();
});

test("chat renders a lightweight tool answer with provenance", async ({ page }) => {
  await mockApi(page);
  await page.routeWebSocket(/\/ws\/chat/, (ws) => {
    ws.onMessage(() => ws.send(JSON.stringify({
      type: "tool_answer",
      run_id: "",
      timestamp: "2026-07-11T00:00:00Z",
      payload: {
        content: "持仓查询完成",
        tool: "get_portfolio_summary",
        args: {},
        result: { holdings: [holding], warnings: ["价格截至上一交易日"] },
        display: "table",
        citations: [{ tool: "get_portfolio_summary", args: {}, summary: "SQLite holdings", as_of_date: "2026-07-10", source: "local_db" }],
      },
    })));
  });
  await page.goto("/chat");
  await page.getByPlaceholder(/例如/).fill("我的持仓怎么样");
  await page.locator("form button").click();
  await expect(page.getByText("get_portfolio_summary")).toBeVisible();
  await expect(page.getByText("600519.SH")).toBeVisible();
  await expect(page.getByText(/2026-07-10/)).toBeVisible();
});

test("audit separates decisions, executions, outcomes, and sample gate", async ({ page }) => {
  await mockApi(page);
  await page.goto("/audit");
  await expect(page.getByText("决策—执行—收益—反思")).toBeVisible();
  await expect(page.getByText("样本不足", { exact: true })).toBeVisible();
  await expect(page.getByText("600519.SH")).toBeVisible();
  await expect(page.getByText("候选量化规则回测")).toBeVisible();
});
