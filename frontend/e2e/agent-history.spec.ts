import { expect, test } from "@playwright/test";

test("an older Agent deep link remains selectable beyond the first history page", async ({ page }) => {
  const time = "2026-09-27T00:00:00Z";
  const conversations = Array.from({ length: 51 }, (_, index) => ({
    id: index === 50 ? "older" : `recent-${index}`,
    title: index === 50 ? "历史对话" : `近期对话 ${index}`,
    paper_session_id: null, created_at: time, updated_at: time, latest_status: "completed",
  }));
  const offsets: number[] = [];
  let releaseOlder = () => {};
  const olderGate = new Promise<void>((resolve) => { releaseOlder = () => resolve(); });
  await page.route("**/api/v1/agent/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/v1/agent/conversations") {
      expect(url.searchParams.get("paper_session_id")).toBe("");
      const offset = Number(url.searchParams.get("offset"));
      const limit = Number(url.searchParams.get("limit"));
      offsets.push(offset);
      await route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify(conversations.slice(offset, offset + limit)) });
      return;
    }
    const id = url.pathname.split("/").at(-1);
    const conversation = conversations.find((item) => item.id === id);
    if (!conversation) {
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
      return;
    }
    if (id === "older") await olderGate;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      ...conversation,
      messages: [{ id: `message-${id}`, conversation_id: id, task_id: null,
        role: "user", content: id === "older" ? "旧问题原文" : "近期问题原文", created_at: time }],
      tasks: [],
    }) });
  });

  await page.goto("/chat?conversation=older");
  await expect(page.getByRole("status").getByText("正在打开历史对话…")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "交易问题" })).toBeDisabled();
  releaseOlder();
  await expect(page.getByText("旧问题原文")).toBeVisible();
  await expect(page.getByText("近期问题原文")).toHaveCount(0);
  await page.getByRole("button", { name: "加载更多对话" }).click();
  await expect.poll(() => offsets).toContain(50);
  await expect(page.getByRole("button", { name: "历史对话" })).toHaveCount(1);
});
