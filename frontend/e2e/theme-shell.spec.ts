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
  await expect(page.getByRole("navigation", { name: "主导航" }).locator("..")).toHaveCSS("width", "56px");
  await expect(page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "交易 Agent" })).toHaveAttribute("aria-current", "page");
  await expect(page.locator("main")).toHaveCSS("background-color", "rgb(244, 245, 242)");
  await expect(page.locator("main")).toHaveCSS("padding-top", "0px");
  await expect(page.locator(".agent-workspace")).toHaveCSS("background-color", "rgb(244, 245, 242)");
  await expect(page.locator("main").getByRole("button", { name: "主题：浅色，点击切换" })).toBeVisible();
  if (process.env.CAPTURE_THEME_QA) await page.screenshot({ path: "test-results/theme-agent-light.png", fullPage: true });

  await page.goto("/research");
  await expect(page.getByRole("navigation", { name: "主导航" }).locator("..")).toHaveCSS("width", "56px");
  await expect(page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "策略研究" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { name: "策略研究" }).first()).toBeVisible();
  await expect(page.locator("main section").first()).toHaveCSS("background-color", "rgba(255, 255, 255, 0.7)");
  if (process.env.CAPTURE_THEME_QA) await page.screenshot({ path: "test-results/theme-research-light.png", fullPage: true });
  await page.goto("/paper");
  await expect(page.getByRole("navigation", { name: "主导航" }).locator("..")).toHaveCSS("width", "56px");
  await expect(page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "模拟盘" })).toHaveAttribute("aria-current", "page");
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

test("reflection badges stay readable in both themes and mobile navigation marks the page", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => window.localStorage.setItem("tradingagents.theme", "light"));
  const lesson = {
    id: "lesson-1", lesson_type: "directional", scope: "global", target: "",
    finding: "样本内判断需复核", suggested_adjustment: "继续观察", evidence_count: 4,
    confidence: "high", active: true, governance_status: "approved", expires_at: null,
    payload: {}, created_at: "2026-09-25", updated_at: "2026-09-25",
  };
  const reflectionCase = {
    id: "case-1", source_type: "run", reflection_scope: "stock", eligible_for_strategy_learning: true,
    status: "completed", symbol: "600519.SH", name: "贵州茅台", signal_date: "2026-09-01",
    horizon_days: 10, due_date: "2026-09-15", source_run_id: "run-1", source_artifact_id: "",
    snapshot_payload: { final_decision: "HOLD_REVIEW" }, outcome_payload: {},
    attribution_payload: { attribution: "missed_upside", excess_return: 0.05 }, lesson_payload: {},
    created_at: "2026-09-25", updated_at: "2026-09-25",
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path.endsWith("/reflections/summary")
      ? { total: 5, correct: 3, incorrect: 2, accuracy: 0.6, lookback_days: 30 }
      : path.endsWith("/prediction-scorecard")
        ? { available: false, reason: "样本不足", n_evaluated: 0, min_samples: 10,
          lookback_days: 30, as_of: "2026-09-25" }
        : path.endsWith("/strategy-lessons") ? [lesson]
          : path.endsWith("/reflection-cases") ? [reflectionCase] : {};
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/reflection");
  await expect(page.getByRole("heading", { name: "反思闭环评测" })).toBeVisible();
  const badge = page.getByText("机会错失");
  await expect(badge).toHaveCSS("color", "rgb(154, 91, 0)");
  const mobileLink = page.getByRole("navigation", { name: "移动端主导航" }).getByRole("link", { name: "反思记录" });
  await expect(mobileLink).toHaveAttribute("aria-current", "page");
  await expect(mobileLink).toHaveClass(/bg-teal-500\/15/);
  await badge.scrollIntoViewIfNeeded();
  await expect(badge).toBeVisible();
  if (process.env.CAPTURE_THEME_QA) await page.screenshot({ path: "test-results/theme-reflection-light.png", fullPage: true });

  await page.getByRole("button", { name: "主题：浅色，点击切换" }).click();
  await expect(badge).toHaveCSS("color", "rgb(246, 195, 110)");
  if (process.env.CAPTURE_THEME_QA) await page.screenshot({ path: "test-results/theme-reflection-dark.png", fullPage: true });
});
