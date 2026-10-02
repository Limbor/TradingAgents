import { expect, test } from "@playwright/test";
import { mockAgentTasks } from "./agentMock";

test("text-only completion states clearly that no analysis tool ran", async ({ page }) => {
  const now = "2026-09-29T08:00:00Z";
  const conversation = { id: "text-only", title: "每日选股", paper_session_id: null,
    created_at: now, updated_at: now, latest_status: "completed" };
  const task = { id: "plain-task", conversation_id: conversation.id, goal: "按默认跑",
    status: "completed", result: { content: "本轮没有开放工具调用。" }, error: null,
    created_at: now, updated_at: now, evidence: [], proposal: null,
    events: [
      { task_id: "plain-task", seq: 1, event_type: "plan_created",
        payload: { steps: [{ id: "chat", label: "解析问题并选择现有分析能力", tool: "chat_agent" }] }, created_at: now },
      { task_id: "plain-task", seq: 2, event_type: "step_completed",
        payload: { id: "chat", status: "completed" }, created_at: now },
    ],
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path === "/api/v1/agent/conversations" ? [conversation]
      : path === "/api/v1/agent/conversations/text-only" ? {
        ...conversation, tasks: [task], messages: [
          { id: "user-1", conversation_id: conversation.id, task_id: task.id,
            role: "user", content: task.goal, created_at: now },
          { id: "answer-1", conversation_id: conversation.id, task_id: task.id,
            role: "assistant", content: task.result.content, created_at: now },
        ],
      } : {};
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/chat");
  await expect(page.getByText("仅文字回答")).toBeVisible();
  await expect(page.getByText("本轮未运行分析工具，也没有可引用的结果数据。")).toBeVisible();
});

test("mobile Agent keeps the conversation usable with the evidence drawer", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => window.localStorage.setItem("tradingagents.theme", "light"));
  await mockAgentTasks(page, "账户数据已核对。");

  await page.goto("/chat");
  await page.getByRole("textbox", { name: "交易问题" }).fill("看看当前持仓风险");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("账户数据已核对。")).toBeVisible();
  await page.getByRole("button", { name: "展开任务档案" }).click();
  await expect(page.getByRole("complementary", { name: "任务证据与方案" })).toBeVisible();
  await expect(page.getByRole("button", { name: "关闭任务档案遮罩" })).toBeVisible();
  if (process.env.CAPTURE_AGENT_QA) await page.screenshot({ path: "test-results/agent-mobile.png", fullPage: true });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("complementary", { name: "任务证据与方案" })).toHaveCount(0);
  await page.getByRole("button", { name: "展开任务档案" }).click();
  await page.getByRole("button", { name: "关闭任务档案遮罩" }).click({ position: { x: 10, y: 400 } });
  await expect(page.getByRole("complementary", { name: "任务证据与方案" })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "交易问题" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("paper Agent deep link opens the requested conversation", async ({ page }) => {
  const now = "2026-09-25T08:00:00Z";
  const conversations = [
    { id: "new-paper-chat", title: "新模拟盘对话", paper_session_id: "paper:mine",
      created_at: now, updated_at: now, latest_status: "completed" },
    { id: "old-paper-chat", title: "旧模拟盘对话", paper_session_id: "paper:mine",
      created_at: now, updated_at: now, latest_status: "completed" },
  ];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const conversation = conversations.find((item) => path.endsWith(`/conversations/${item.id}`));
    const body = path === "/api/v1/agent/conversations" ? conversations
      : conversation ? { ...conversation, tasks: [], messages: [{
        id: `message-${conversation.id}`, conversation_id: conversation.id,
        task_id: null, role: "assistant", content: `回答：${conversation.title}`, created_at: now,
      }] } : {};
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/chat?paper_session=paper%3Amine&conversation=old-paper-chat");
  await expect(page.getByText("回答：旧模拟盘对话")).toBeVisible();
  await expect(page.getByText("回答：新模拟盘对话")).toHaveCount(0);
  await page.getByRole("button", { name: /新模拟盘对话/ }).click();
  await expect(page.getByText("回答：新模拟盘对话")).toBeVisible();
  await expect(page).toHaveURL(/conversation=new-paper-chat$/);
  await page.reload();
  await expect(page.getByText("回答：新模拟盘对话")).toBeVisible();
});

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
  const events = (taskId: string, stepId: string, label: string) => [
    { task_id: taskId, seq: 1, event_type: "plan_created",
      payload: { steps: [{ id: stepId, label }] }, created_at: now },
    { task_id: taskId, seq: 2, event_type: "step_completed",
      payload: { id: stepId, status: "completed" }, created_at: now },
  ];
  const tasks = [
    { id: "t-old", conversation_id: conversation.id, goal: "旧任务：分析 600519.SH",
      status: "completed", result: { content: "旧回答", citations: [{ id: oldEvidence.id }] },
      error: null, created_at: now, updated_at: now,
      events: events("t-old", "factor", "读取 600519.SH 因子快照"), evidence: [oldEvidence], proposal: null },
    { id: "t-new", conversation_id: conversation.id, goal: "新任务：检查组合",
      status: "completed", result: { content: "新回答", citations: [{ id: newEvidence.id }] },
      error: null, created_at: now, updated_at: now,
      events: events("t-new", "portfolio", "读取当前手工持仓"), evidence: [newEvidence], proposal: null },
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
  await page.getByRole("button", { name: "展开任务档案" }).click();
  const inspector = page.getByRole("complementary", { name: "任务证据与方案" });
  await expect(inspector.getByText("新任务：检查组合")).toBeVisible();
  await page.getByRole("button", { name: "已关联 1 项证据 · 查看任务档案" }).first().click();
  await expect(inspector.getByText("旧任务：分析 600519.SH")).toBeVisible();
  await expect(inspector.getByText("市盈率")).toBeVisible();
  await expect(inspector.getByText("12.3")).toBeVisible();
  await expect(inspector.getByText("来源")).toBeVisible();
  await expect(inspector.getByText("基准日")).toBeVisible();
  if (process.env.CAPTURE_AGENT_QA) await page.screenshot({ path: "test-results/agent-evidence.png", fullPage: true });
  await page.getByRole("button", { name: "已关联 1 项证据 · 查看任务档案" }).last().click();
  await expect(inspector.getByText("新任务：检查组合")).toBeVisible();
  await expect(inspector.getByText("估算盈亏")).toBeVisible();
  await expect(inspector.getByText("250")).toBeVisible();
});

test("announcement evidence shows the query window and its limits", async ({ page }) => {
  const now = "2026-09-25T08:00:00Z";
  const conversation = { id: "risk-1", title: "风险公告", paper_session_id: null,
    created_at: now, updated_at: now, latest_status: "completed" };
  const evidence = { id: "e-risk", task_id: "t-risk", tool_name: "get_mcp_risk_announcements",
    source: "StockManager MCP", as_of_date: "2026-09-25", retrieved_at: now,
    summary: "600519.SH 风险关键词扫描", warnings: ["仅返回关键词命中日期，不含公告标题或原文"],
    result: { ts_code: "600519.SH", start_date: "2026-06-27", end_date: "2026-09-25",
      count: 1, rows: [{ ann_date: "2026-09-20", keyword: "matched" }] } };
  const task = { id: "t-risk", conversation_id: conversation.id, goal: "查看 600519.SH 风险公告",
    status: "completed", result: { content: "查到一个关键词命中日期。", citations: [{ id: evidence.id }] },
    error: null, created_at: now, updated_at: now, events: [], evidence: [evidence], proposal: null };
  const messages = [
    { id: "m-risk-user", conversation_id: conversation.id, task_id: task.id, role: "user",
      content: task.goal, created_at: now },
    { id: "m-risk-answer", conversation_id: conversation.id, task_id: task.id, role: "assistant",
      content: task.result.content, created_at: now },
  ];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path === "/api/v1/agent/conversations" ? [conversation]
      : path === "/api/v1/agent/conversations/risk-1"
        ? { ...conversation, tasks: [task], messages }
        : path.endsWith("/health") ? { stockmanager_mcp: { connected: true } } : {};
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/chat");
  await page.getByRole("button", { name: "展开任务档案" }).click();
  const inspector = page.getByRole("complementary", { name: "任务证据与方案" });
  await expect(inspector.getByText("2026-06-27 至 2026-09-25")).toBeVisible();
  await expect(inspector.getByText("1 个日期")).toBeVisible();
  await expect(inspector.getByText("2026-09-20")).toBeVisible();
  await expect(inspector.getByText("仅返回关键词命中日期，不含公告标题或原文")).toBeVisible();
});

test("Agent Skill progress and result links survive conversation reload", async ({ page }) => {
  const now = "2026-09-25T08:00:00Z";
  const conversation = { id: "research-1", title: "策略研究", paper_session_id: null,
    created_at: now, updated_at: now, latest_status: "completed" };
  const taskId = "task-research";
  const event = (seq: number, event_type: string, payload: Record<string, unknown>) =>
    ({ task_id: taskId, seq, event_type, payload, created_at: now });
  const task = { id: taskId, conversation_id: conversation.id, goal: "分析策略表现",
    status: "completed", result: { content: "研究完成。" }, error: null,
    created_at: now, updated_at: now, proposal: null,
    events: [
      event(1, "plan_created", { steps: [{ id: "research", label: "运行策略研究" }] }),
      event(2, "skill_started", { run_id: "run-123", skill_id: "stock_analysis" }),
      event(3, "skill_progress", { run_id: "run-123", event_type: "skill_progress",
        payload: { stage_id: "report", stage_label: "整理研究报告", status: "completed" } }),
      event(4, "step_completed", { id: "research", status: "completed" }),
    ],
    evidence: [{ id: "e-research", task_id: taskId, tool_name: "skill",
      source: "TradingAgents Skill: stock_analysis", as_of_date: now.slice(0, 10),
      retrieved_at: now, summary: "股票分析完成", warnings: [], result: { run_id: "run-123" } }],
  };
  const detail = { ...conversation, tasks: [task], messages: [
    { id: "m-research-user", conversation_id: conversation.id, task_id: taskId,
      role: "user", content: task.goal, created_at: now },
    { id: "m-research-answer", conversation_id: conversation.id, task_id: taskId,
      role: "assistant", content: task.result.content, created_at: now },
  ] };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path === "/api/v1/agent/conversations" ? [conversation]
      : path === "/api/v1/agent/conversations/research-1" ? detail : {};
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/chat");
  await expect(page.getByLabel("分析子任务进度").getByText("整理研究报告 · 已完成")).toBeVisible();
  await page.getByRole("button", { name: "展开任务档案" }).click();
  const inspector = page.getByRole("complementary", { name: "任务证据与方案" });
  await expect(inspector.getByRole("link", { name: "查看分析过程" })).toHaveAttribute("href", "/analysis/run-123");
  await expect(inspector.getByRole("link", { name: "查看研究产物" })).toHaveAttribute("href", "/library?run_id=run-123");
  await page.reload();
  await expect(page.getByLabel("分析子任务进度").getByText("整理研究报告 · 已完成")).toBeVisible();
  await page.getByRole("button", { name: "展开任务档案" }).click();
  await inspector.getByRole("link", { name: "查看研究产物" }).click();
  await expect(page).toHaveURL(/\/library\?run_id=run-123$/);
});
