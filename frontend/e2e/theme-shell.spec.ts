import { expect, test } from "@playwright/test";

test("workspace theme applies to Agent, research, and paper pages", async ({ page }) => {
  await page.addInitScript(() => {
    if (!window.sessionStorage.getItem("theme-test-started")) {
      window.localStorage.setItem("tradingagents.theme", "light");
      window.sessionStorage.setItem("theme-test-started", "1");
    }
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path === "/api/v1/backtests/catalog"
      ? { strategies: [], configs: [] }
      : [];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/chat");
  await expect(page.locator("main")).toHaveCSS("background-color", "rgb(244, 245, 242)");
  await expect(page.locator(".agent-workspace")).toHaveCSS("background-color", "rgb(244, 245, 242)");
  if (process.env.CAPTURE_THEME_QA) await page.screenshot({ path: "test-results/theme-agent-light.png", fullPage: true });

  await page.goto("/research");
  await expect(page.getByRole("heading", { name: "策略研究" }).first()).toBeVisible();
  await expect(page.locator("main section").first()).toHaveCSS("background-color", "rgba(255, 255, 255, 0.7)");
  if (process.env.CAPTURE_THEME_QA) await page.screenshot({ path: "test-results/theme-research-light.png", fullPage: true });
  await page.goto("/paper");
  await expect(page.getByRole("heading", { name: "模拟盘工作台" })).toBeVisible();
  await expect(page.locator("main")).toHaveCSS("background-color", "rgb(244, 245, 242)");
  if (process.env.CAPTURE_THEME_QA) await page.screenshot({ path: "test-results/theme-paper-light.png", fullPage: true });

  await page.goto("/chat");
  await page.getByRole("button", { name: "主题：浅色，点击切换" }).click();
  await expect(page.locator("main")).toHaveCSS("background-color", "rgb(21, 26, 24)");
  await expect(page.locator(".agent-workspace")).toHaveCSS("background-color", "rgb(21, 26, 24)");
  if (process.env.CAPTURE_THEME_QA) await page.screenshot({ path: "test-results/theme-agent-dark.png", fullPage: true });

  await page.goto("/research");
  await expect(page.getByRole("heading", { name: "策略研究" }).first()).toBeVisible();
  await expect(page.locator("main")).toHaveCSS("background-color", "rgb(21, 26, 24)");
  await expect(page.locator("main section").first()).toHaveCSS("background-color", "rgba(32, 38, 34, 0.7)");
  if (process.env.CAPTURE_THEME_QA) await page.screenshot({ path: "test-results/theme-research-dark.png", fullPage: true });

  await page.goto("/paper");
  await expect(page.getByRole("heading", { name: "模拟盘工作台" })).toBeVisible();
  await expect(page.locator("main")).toHaveCSS("background-color", "rgb(21, 26, 24)");
  if (process.env.CAPTURE_THEME_QA) await page.screenshot({ path: "test-results/theme-paper-dark.png", fullPage: true });

  await page.getByRole("button", { name: "主题：深色，点击切换" }).click();
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem("tradingagents.theme"))).toBeNull();
});
