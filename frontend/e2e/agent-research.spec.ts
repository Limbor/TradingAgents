import { expect, test } from "@playwright/test";

test("supplemental research shows reused scope, current retrieval and compacted handoff after reload", async ({ page }) => {
  const time = "2026-10-03T00:00:00Z";
  const conversation = { id: "research-c", title: "补充基本面", paper_session_id: null,
    created_at: time, updated_at: time, latest_status: "running" };
  const event = (seq: number, event_type: string, payload: Record<string, unknown>) =>
    ({ task_id: "research-t", seq, event_type, payload, created_at: time });
  let completed = false;
  let release = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/stream")) {
      await gate;
      completed = true;
      await route.fulfill({ contentType: "text/event-stream", body:
        `event: agent_event\ndata: ${JSON.stringify(event(4, "skill_progress", { run_id: "workflow", event_type: "skill_progress",
          payload: { stage_id: "handoff", stage_label: "整理研究摘要", status: "completed",
            detail: "保留事实、判断、风险、数据缺口和来源，完整报告仍可查看" } }))}\n\n` + "event: done\ndata: {}\n\n" });
      return;
    }
    const events = [event(1, "agent_runtime", { run_id: "market", role: "Market Analyst", kind: "agent", status: "completed",
      model: "qwen3.8-max", reused_from: { run_id: "original", as_of_date: "2026-09-30" } }),
    event(2, "skill_progress", { run_id: "workflow", event_type: "skill_progress", payload: { stage_id: "research_scope",
      stage_label: "核对已有研究", status: "completed", detail: "复用有效的技术面研究；本轮获取基本面数据，仅执行所选研究维度" } }),
    event(3, "skill_progress", { run_id: "workflow", event_type: "tool_call", payload: { tool: "get_income_statement",
      activity_id: "income", status: completed ? "completed" : "running" } }),
    ...(completed ? [event(4, "skill_progress", { run_id: "workflow", event_type: "skill_progress", payload: {
      stage_id: "handoff", stage_label: "整理研究摘要", status: "completed",
      detail: "保留事实、判断、风险、数据缺口和来源，完整报告仍可查看" } })] : [])];
    const task = { id: "research-t", conversation_id: conversation.id, goal: "补充行业、营收和现金流",
      status: completed ? "completed" : "running", result: completed ? { content: "本轮补充财务分析，技术面沿用已核验研究。" } : {},
      error: null, created_at: time, updated_at: time, proposal: null, events, evidence: [] };
    const body = path === "/api/v1/agent/conversations" ? [conversation]
      : path === "/api/v1/agent/conversations/research-c" ? { ...conversation, tasks: [task], messages: [
        { id: "user", conversation_id: conversation.id, task_id: task.id, role: "user", content: task.goal, created_at: time },
        ...(completed ? [{ id: "reply", conversation_id: conversation.id, task_id: task.id,
          role: "assistant", content: task.result.content, created_at: time }] : []) ] } : {};
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.setViewportSize({ width: 1100, height: 800 });
  await page.goto("/chat");
  const timeline = page.getByLabel("分析子任务进度");
  await expect(timeline).toContainText("复用技术面研究 · 已完成");
  await expect(timeline).toContainText("本轮获取基本面数据");
  await expect(timeline.getByRole("status")).toContainText("利润表");
  release();
  await expect(timeline).toContainText("整理研究摘要 · 已完成");
  await page.reload();
  await timeline.getByRole("button", { name: /查看全部/ }).click();
  await expect(timeline).toContainText("保留原始来源");
  await expect(timeline).toContainText("完整报告仍可查看");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await timeline.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
});
