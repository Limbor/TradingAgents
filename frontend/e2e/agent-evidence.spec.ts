import { expect, test } from "@playwright/test";

test("each Agent answer opens its own evidence and shows trading facts", async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem("tradingagents.theme", "light"));
  const now = "2026-09-25T08:00:00Z";
  const conversation = { id: "history-1", title: "交易分析", paper_session_id: null,
    created_at: now, updated_at: now, latest_status: "completed" };
  const oldEvidence = { id: "e-old", task_id: "t-old", tool_name: "get_mcp_factor_snapshot",
    source: "StockManager MCP", as_of_date: "2026-09-25", retrieved_at: now,
    summary: "600519.SH 因子快照", warnings: [], result: { ts_code: "600519.SH",
      snapshot: { rows: [{ ts_code: "600519.SH", latest_price: 1500,
        raw_factors: { pe_ttm: 12.3, pb: 4.2 } }] } } };
  const newEvidence = { id: "e-new", task_id: "t-new", tool_name: "get_portfolio_summary",
    source: "TradingAgents local holdings", as_of_date: null, retrieved_at: now,
    summary: "手工持仓 1 只", warnings: ["价格为本地保存值"], result: {
      total_symbols: 1, holdings: [{ symbol: "000001.SZ", pnl: 250 }],
    } };
  const tasks = [
    { id: "t-old", conversation_id: conversation.id, goal: "旧任务：分析 600519.SH",
      status: "completed", result: { content: "旧回答", citations: [{ id: oldEvidence.id }] },
      error: null, created_at: now, updated_at: now, events: [], evidence: [oldEvidence], proposal: null },
    { id: "t-new", conversation_id: conversation.id, goal: "新任务：检查组合",
      status: "completed", result: { content: "新回答", citations: [{ id: newEvidence.id }] },
      error: null, created_at: now, updated_at: now, events: [], evidence: [newEvidence], proposal: null },
  ];
  const messages = [
    { id: "m1", conversation_id: conversation.id, task_id: "t-old", role: "user", content: tasks[0].goal, created_at: now },
    { id: "m2", conversation_id: conversation.id, task_id: "t-old", role: "assistant", content: "旧回答", created_at: now },
    { id: "m3", conversation_id: conversation.id, task_id: "t-new", role: "user", content: tasks[1].goal, created_at: now },
    { id: "m4", conversation_id: conversation.id, task_id: "t-new", role: "assistant", content: "新回答", created_at: now },
  ];

  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path === "/api/v1/agent/conversations" ? [conversation]
      : path === "/api/v1/agent/conversations/history-1"
        ? { ...conversation, tasks, messages }
        : path.endsWith("/health") ? { stockmanager_mcp: { connected: true } } : {};
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/chat");
  const inspector = page.getByRole("complementary", { name: "任务证据与方案" });
  await expect(inspector.getByText("新任务：检查组合")).toBeVisible();
  await page.getByRole("button", { name: "已关联 1 项证据 · 查看任务档案" }).first().click();
  await expect(inspector.getByText("旧任务：分析 600519.SH")).toBeVisible();
  await expect(inspector.getByText("市盈率")).toBeVisible();
  await expect(inspector.getByText("12.3")).toBeVisible();
  if (process.env.CAPTURE_AGENT_QA) await page.screenshot({ path: "test-results/agent-evidence.png", fullPage: true });
  await page.getByRole("button", { name: "已关联 1 项证据 · 查看任务档案" }).last().click();
  await expect(inspector.getByText("新任务：检查组合")).toBeVisible();
  await expect(inspector.getByText("估算盈亏")).toBeVisible();
  await expect(inspector.getByText("250")).toBeVisible();
});
