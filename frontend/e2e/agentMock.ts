import type { Page } from "@playwright/test";

const timestamp = "2026-09-25T08:00:00Z";

export async function mockAgentTasks(page: Page, answer: string | ((paperId: string | null) => string) = "已核对模拟盘账本。", evidenceWarnings: string[] = []) {
  const conversations: Array<{ id: string; title: string; paper_session_id: string | null; created_at: string; updated_at: string; latest_status: string | null }> = [];
  const messages = new Map<string, Array<Record<string, unknown>>>();
  const tasks = new Map<string, Array<Record<string, unknown>>>();
  const submissions: Array<{ conversationId: string; paperSessionId: string | null; message: string }> = [];

  await page.route("**/api/v1/agent/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    let body: unknown = {};
    let status = 200;
    if (path === "/api/v1/agent/conversations" && method === "GET") body = conversations;
    else if (path === "/api/v1/agent/conversations" && method === "POST") {
      const request = route.request().postDataJSON() as { title: string; paper_session_id: string | null };
      const conversation = { id: `conversation-${conversations.length + 1}`, title: request.title, paper_session_id: request.paper_session_id,
        created_at: timestamp, updated_at: timestamp, latest_status: null };
      conversations.unshift(conversation);
      messages.set(conversation.id, []);
      tasks.set(conversation.id, []);
      body = conversation;
      status = 201;
    } else {
      const match = path.match(/^\/api\/v1\/agent\/conversations\/([^/]+)(?:\/tasks)?$/);
      if (match) {
        const id = match[1];
        const conversation = conversations.find((item) => item.id === id);
        if (conversation && method === "GET") body = { ...conversation, messages: messages.get(id) ?? [], tasks: tasks.get(id) ?? [] };
        else if (conversation && method === "POST") {
          const request = route.request().postDataJSON() as { message: string };
          const reply = typeof answer === "function" ? answer(conversation.paper_session_id) : answer;
          submissions.push({ conversationId: id, paperSessionId: conversation.paper_session_id, message: request.message });
          const taskId = `task-${submissions.length}`;
          const evidence = { id: `evidence-${submissions.length}`, task_id: taskId, tool_name: conversation.paper_session_id ? "get_paper_session" : "get_portfolio_summary",
            source: conversation.paper_session_id ? "StockManager ledger" : "local_db", as_of_date: "2026-09-25", retrieved_at: timestamp,
            summary: conversation.paper_session_id ? "模拟盘账本" : "手工持仓 1 只", warnings: evidenceWarnings, result: {} };
          const task = { id: taskId, conversation_id: id, goal: request.message, status: "completed", result: { content: reply, citations: [evidence], read_only: true },
            error: null, created_at: timestamp, updated_at: timestamp,
            events: [{ task_id: taskId, seq: 1, event_type: "plan_created", payload: { steps: [{ id: "paper", label: "读取账户数据" }] }, created_at: timestamp },
              { task_id: taskId, seq: 2, event_type: "step_completed", payload: { id: "paper", status: "completed" }, created_at: timestamp }],
            evidence: [evidence] };
          tasks.get(id)!.push(task);
          messages.get(id)!.push({ id: `user-${taskId}`, conversation_id: id, task_id: taskId, role: "user", content: request.message, created_at: timestamp },
            { id: `assistant-${taskId}`, conversation_id: id, task_id: taskId, role: "assistant", content: reply, created_at: timestamp });
          conversation.latest_status = "completed";
          body = task;
          status = 202;
        }
      }
    }
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  });
  return submissions;
}
