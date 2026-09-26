import { expect, test } from "@playwright/test";

test("old browser chat becomes a scoped, inert Agent archive", async ({ page }) => {
  const time = "2026-09-20T08:00:00Z";
  await page.addInitScript((timestamp) => {
    window.localStorage.setItem("tradingagents-chat", JSON.stringify({ state: { messages: [
      { id: "old-user", role: "user", content: "解释旧模拟盘", scope: "paper:paper:one", timestamp },
      { id: "old-answer", role: "assistant", content: "旧版回答仅供回看", scope: "paper:paper:one", timestamp },
    ] } }));
  }, time);
  let imports = 0;
  const conversation = { id: "imported-1", title: "旧版模拟盘记录 · 解释旧模拟盘",
    paper_session_id: "paper:one", legacy_archive: true, created_at: time, updated_at: time, latest_status: null };
  const detail = { ...conversation, tasks: [], messages: [
    { id: "m1", conversation_id: conversation.id, task_id: null, role: "user", content: "解释旧模拟盘", created_at: time },
    { id: "m2", conversation_id: conversation.id, task_id: null, role: "assistant", content: "旧版回答仅供回看", created_at: time },
  ] };
  await page.route("**/api/v1/agent/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/v1/agent/conversations") body = imports ? [conversation] : [];
    else if (path === "/api/v1/agent/legacy-import") {
      const request = route.request().postDataJSON() as { paper_session_id: string; messages: unknown[] };
      expect(request.paper_session_id).toBe("paper:one");
      expect(request.messages).toHaveLength(2);
      imports += 1;
      body = { ...conversation, imported_count: 2 };
    } else if (path === "/api/v1/agent/conversations/imported-1") body = detail;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/chat?paper_session=paper%3Aone");
  await expect(page.getByText("旧版回答仅供回看")).toBeVisible();
  await expect(page.getByText("历史聊天存档 · 数据未重新核对")).toBeVisible();
  await expect(page.getByRole("button", { name: "新建对话继续" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "交易问题" })).toHaveCount(0);
  expect(imports).toBe(1);
  await page.reload();
  await expect(page.getByText("旧版回答仅供回看")).toBeVisible();
  expect(imports).toBe(1);
});

test("a page prompt starts a fresh task when the selected conversation is an archive", async ({ page }) => {
  await page.addInitScript(() => {
    window.history.replaceState({ idx: 0, key: "archive-prompt", usr: {
      prompt: "重新检查当前持仓", autoSend: true, nonce: "prompt-1",
    } }, "");
  });
  const now = "2026-09-20T08:00:00Z";
  const archive = { id: "archive", title: "旧版聊天记录", paper_session_id: null,
    legacy_archive: true, created_at: now, updated_at: now, latest_status: null };
  const fresh = { ...archive, id: "fresh", title: "新对话", legacy_archive: false };
  let created = false;
  let submittedTo = "";
  await page.route("**/api/v1/agent/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/v1/agent/conversations") {
      if (route.request().method() === "POST") { created = true; body = fresh; }
      else body = created ? [fresh, archive] : [archive];
    } else if (path === "/api/v1/agent/conversations/archive") {
      body = { ...archive, tasks: [], messages: [{ id: "old", conversation_id: "archive",
        task_id: null, role: "assistant", content: "旧回答", created_at: now }] };
    } else if (path === "/api/v1/agent/conversations/fresh") {
      body = { ...fresh, tasks: [], messages: [] };
    } else if (path.endsWith("/tasks") && route.request().method() === "POST") {
      submittedTo = path;
      expect(route.request().postDataJSON()).toMatchObject({ message: "重新检查当前持仓" });
      body = { id: "new-task" };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/chat");
  await expect.poll(() => submittedTo).toBe("/api/v1/agent/conversations/fresh/tasks");
  expect(created).toBe(true);
});
