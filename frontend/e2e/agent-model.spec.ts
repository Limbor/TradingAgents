import { expect, test } from "@playwright/test";

test("DeepSeek Agent model can switch between Flash and Pro and persists in settings", async ({ page }) => {
  const config = {
    llm_provider: "deepseek", quick_think_llm: "deepseek-flash",
    deep_think_llm: "deepseek-v4-pro", agent_model: null as string | null,
    output_language: "Chinese", max_debate_rounds: 1, max_risk_discuss_rounds: 1,
    checkpoint_enabled: false, backend_url: null, stockmanager_mcp_url: null,
    stockmanager_mcp_enabled: false, stockmanager_mcp_timeout: 30,
    daily_pipeline_filters: {}, adaptive_alpha_enabled: false,
    api_keys: { deepseek: true },
  };
  const updates: Array<Record<string, unknown>> = [];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/v1/config/providers") body = [{
      id: "deepseek", name: "DeepSeek",
      quick_models: [
        { label: "DeepSeek Flash · 快速", value: "deepseek-flash" },
        { label: "DeepSeek Pro · 深度", value: "deepseek-v4-pro" },
        { label: "Custom model ID", value: "custom" },
      ],
      deep_models: [
        { label: "DeepSeek Pro · 深度", value: "deepseek-v4-pro" },
        { label: "DeepSeek Flash · 快速", value: "deepseek-flash" },
      ],
    }];
    else if (path === "/api/v1/config") {
      if (route.request().method() === "PUT") {
        const update = route.request().postDataJSON() as Record<string, unknown>;
        updates.push(update);
        Object.assign(config, update);
      }
      body = config;
    } else if (path === "/api/v1/profile") body = {
      investment_style: "medium_term", risk_tolerance: "moderate",
      sector_prefs: [], updated_at: null,
    };
    else if (path === "/api/v1/agent/conversations") body = [];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/settings");
  const agentModel = page.getByRole("combobox", { name: "交易 Agent 模型", exact: true });
  await expect(agentModel).toHaveValue("");
  await agentModel.selectOption("deepseek-v4-pro");
  await expect.poll(() => updates.at(-1)?.agent_model).toBe("deepseek-v4-pro");
  if (process.env.CAPTURE_MODEL_QA) await page.screenshot({ path: "test-results/agent-model-settings.png", fullPage: true });
  await page.goto("/chat");
  await expect(page.getByRole("link", { name: "配置交易 Agent 模型" })).toContainText("deepseek-v4-pro");
  await page.getByRole("link", { name: "配置交易 Agent 模型" }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(agentModel).toHaveValue("deepseek-v4-pro");
  await agentModel.selectOption("deepseek-flash");
  await expect.poll(() => updates.at(-1)?.agent_model).toBe("deepseek-flash");
  await page.reload();
  await expect(page.getByRole("combobox", { name: "交易 Agent 模型", exact: true })).toHaveValue("deepseek-flash");
  await page.getByRole("combobox", { name: "交易 Agent 模型", exact: true }).selectOption("custom");
  await page.getByRole("textbox", { name: "自定义 Agent 模型 ID" }).fill("deepseek-next");
  await page.getByRole("button", { name: "保存模型" }).click();
  await expect.poll(() => updates.at(-1)?.agent_model).toBe("deepseek-next");
  await page.reload();
  await expect(page.getByRole("textbox", { name: "自定义 Agent 模型 ID" })).toHaveValue("deepseek-next");
});
