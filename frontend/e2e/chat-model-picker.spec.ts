import { expect, test } from "@playwright/test";
import { mockAgentTasks } from "./agentMock";

test("composer and settings share server model policy across reloads", async ({ page }) => {
  const config = {
    llm_provider: "deepseek", model_policy: { default_model: "deepseek-flash", deep_model: null },
    api_keys: { deepseek: true, qianwen: true },
  };
  let configWrites = 0;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/v1/config") {
      if (route.request().method() === "PUT") { configWrites++; Object.assign(config, route.request().postDataJSON()); }
      body = config;
    } else if (path === "/api/v1/profile") body = null;
    else if (path === "/api/v1/config/providers") body = [
      { id: "deepseek", name: "DeepSeek 官方", quick_models: [
        { value: "deepseek-flash", label: "DeepSeek Flash · 快速" }], deep_models: [] },
      { id: "qianwen", name: "千问AI平台", quick_models: [
        { value: "qwen3.8-flash", label: "Qwen3.8 Flash · 快速" },
        { value: "qwen3.7-plus", label: "Qwen3.7 Plus · 均衡" }], deep_models: [
        { value: "qwen3.8-max", label: "Qwen3.8 Max · 深度" }] },
    ];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  const submissions = await mockAgentTasks(page, "已核对股票数据，分析结果使用本次选择的模型。");
  await page.goto("/chat");
  const picker = page.getByRole("button", { name: "切换聊天模型" });
  await expect(picker).toContainText("DeepSeek Flash");
  await picker.click();
  await expect(page.getByRole("menuitem", { name: "Qwen3.8 Flash · 快速" })).toBeVisible();
  await page.screenshot({ path: "test-results/chat-model-picker.png" });
  await page.getByRole("menuitem", { name: "Qwen3.8 Flash · 快速" }).click();
  await page.getByRole("textbox", { name: "交易问题" }).fill("如何看待太极实业的后续走向");
  const selectedRequest = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/tasks"));
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const request = await selectedRequest;
  expect(request.postDataJSON()).not.toHaveProperty("model_selection");
  expect(config.llm_provider).toBe("qianwen");
  expect(config.model_policy).toEqual({ default_model: "qwen3.8-flash", deep_model: null });
  await expect.poll(() => submissions.length).toBe(1);
  await expect(page.getByText("已核对股票数据，分析结果使用本次选择的模型。", { exact: true })).toBeVisible();
  await page.reload();
  await expect(picker).toContainText("Qwen3.8 Flash");
  await page.goto("/settings");
  await expect(picker).toContainText("Qwen3.8 Flash");
  await picker.click();
  await page.getByRole("menuitem", { name: "DeepSeek Flash · 快速" }).click();
  await expect(picker).toContainText("DeepSeek Flash");
  await page.goto("/chat");
  await expect(picker).toContainText("DeepSeek Flash");
  await page.getByRole("textbox", { name: "交易问题" }).fill("再核对一次");
  const defaultRequest = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/tasks"));
  await page.getByRole("button", { name: "发送", exact: true }).click();
  expect((await defaultRequest).postDataJSON()).not.toHaveProperty("model_selection");
  expect(configWrites).toBe(2);
  await expect(page.getByRole("link", { name: "配置默认模型" })).toHaveCount(0);
});

test("failed model save retains the original policy and ignores old browser preferences", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("tradingagents.chat-model.v1",
    JSON.stringify({ provider: "qianwen", model: "qwen3.8-max" })));
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/config" && route.request().method() === "PUT") {
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "配置服务暂不可用" }) });
      return;
    }
    let body: unknown = {};
    if (path === "/api/v1/config") body = { llm_provider: "deepseek", model_policy: { default_model: "deepseek-flash", deep_model: null }, api_keys: { deepseek: true, qianwen: true } };
    else if (path === "/api/v1/config/providers") body = [{ id: "qianwen", name: "千问AI平台", quick_models: [{ value: "qwen3.8-max", label: "Qwen3.8 Max" }], deep_models: [] }];
    else if (path === "/api/v1/agent/conversations") body = [];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/chat");
  const picker = page.getByRole("button", { name: "切换聊天模型" });
  await expect(picker).toContainText("deepseek-flash");
  await picker.click();
  await page.getByRole("menuitem", { name: "Qwen3.8 Max" }).click();
  await expect(page.getByRole("alert")).toContainText("仍使用原模型");
  await expect(picker).toContainText("deepseek-flash");
});

test("unconfigured Qianwen is disabled and explains how to configure the key", async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/v1/config") body = { llm_provider: "deepseek", quick_think_llm: "deepseek-flash", api_keys: { deepseek: true, qianwen: false } };
    else if (path === "/api/v1/config/providers") body = [{ id: "qianwen", name: "千问AI平台", quick_models: [{ value: "qwen3.8-flash", label: "Qwen3.8 Flash · 快速" }], deep_models: [] }];
    else if (path === "/api/v1/agent/conversations") body = [];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/chat");
  await page.getByRole("button", { name: "切换聊天模型" }).click();
  await expect(page.getByRole("menuitem", { name: "Qwen3.8 Flash · 快速" })).toHaveAttribute("aria-disabled", "true");
  await expect(page.getByText("需配置 QIANWEN_API_KEY")).toBeVisible();
  await page.getByRole("menuitem", { name: "管理模型与渠道" }).click();
  await expect(page).toHaveURL(/\/settings$/);
});
