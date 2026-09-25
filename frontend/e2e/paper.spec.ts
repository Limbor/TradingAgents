import { expect, test } from "@playwright/test";

test("strategy paper workbench creates, reads, and advances a StockManager session", async ({ page }) => {
  let created = false;
  let advanced = false;
  let advanceBody: unknown = null;
  page.on("dialog", (dialog) => void dialog.accept());
  await page.route("**/api/v1/**", async (route) => {
    const { pathname } = new URL(route.request().url());
    let body: unknown = {};
    if (pathname === "/api/v1/paper/sessions" && route.request().method() === "POST") {
      const request = route.request().postDataJSON();
      expect(request).toMatchObject({ strategy: "demo", start_date: "2026-01-02", initial_cash: 100000 });
      created = true;
      body = { session_id: "paper:demo" };
    } else if (pathname === "/api/v1/paper/sessions") {
      body = created ? [{ session_id: "paper:demo", mode: "paper", strategy: "demo", config_name: "", initial_cash: 100000, last_date: "2026-01-02", params: {} }] : [];
    } else if (pathname === "/api/v1/paper/strategies") {
      body = [{ name: "demo" }];
    } else if (pathname === "/api/v1/paper/configs") {
      body = [];
    } else if (pathname.endsWith("/status")) {
      body = { session: { initial_cash: 100000 }, snapshot: { as_of_date: "2026-01-02", equity: 102000, cash: 40000, positions: { "600519.SH": { name: "贵州茅台", shares: 40, avg_cost: 1000, last_price: 1550, value: 62000 } } }, trades_count: 1 };
    } else if (pathname.endsWith("/equity")) {
      body = { daily_records: [{ date: "2026-01-02", equity: 102000, cash: 40000 }], benchmark_curve: [] };
    } else if (pathname.endsWith("/trades")) {
      body = [{ trade_date: "2026-01-02", code: "600519.SH", name: "贵州茅台", side: "BUY", shares: 40, price: 1550, amount: 62000 }];
    } else if (pathname.endsWith("/next-plan")) {
      body = { signal_date: "2026-01-02", equity: 102000, items: [{ code: "600519.SH", name: "贵州茅台", action: "HOLD", diff_value: 0 }] };
    } else if (pathname.endsWith("/advance")) {
      advanceBody = route.request().postDataJSON();
      body = { job_id: "job-1" };
    } else if (pathname.endsWith("/jobs/job-1")) {
      advanced = true;
      body = { job_id: "job-1", state: "success", progress: 100, message: "完成", result: { ok: true } };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/paper");
  await expect(page.getByText("还没有策略模拟会话")).toBeVisible();
  await page.getByRole("button", { name: "新建会话" }).click();
  await page.getByRole("combobox", { name: "策略", exact: true }).selectOption("demo");
  await page.getByLabel("起始日期").fill("2026-01-02");
  await page.getByLabel("初始资金").fill("100000");
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByText("¥102,000")).toBeVisible();
  await expect(page.getByText("贵州茅台").first()).toBeVisible();
  await page.getByLabel("推进至交易日").fill("2026-01-05");
  await page.getByRole("button", { name: "推进模拟盘" }).click();
  await expect.poll(() => advanced).toBe(true);
  expect(advanceBody).toEqual({ target_date: "2026-01-05" });
});
