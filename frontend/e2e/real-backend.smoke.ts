import { expect, test } from "@playwright/test";

/**
 * Pre-release smoke against a REAL backend (no request mocking).
 *
 * The stable e2e contract lives in workbench.spec.ts, which intercepts
 * /api/v1/** with static fixtures so the frontend main flows stay verifiable
 * regardless of backend state. This smoke is the complement: it boots the
 * actual FastAPI server (throwaway SQLite, MCP degraded) via the Playwright
 * webServer and asserts the app shell talks to it end-to-end.
 *
 * Only runs when E2E_REAL_BACKEND=1 (see playwright.config.ts testMatch gate);
 * the default `npm run test:e2e` run ignores *.smoke.ts entirely.
 */

test("real backend answers the health probe", async ({ request }) => {
  // baseURL is the vite dev server; /api is proxied to the FastAPI backend.
  const res = await request.get("/api/v1/health");
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  expect(body.status).toBe("ok");
  expect(body.service).toBe("tradingagents-api");
  // MCP may be connected or degraded; the field must simply be present.
  expect(body).toHaveProperty("stockmanager_mcp");
});

test("real backend persists an Agent conversation", async ({ request }) => {
  const created = await request.post("/api/v1/agent/conversations", { data: { title: "冒烟测试" } });
  expect(created.status()).toBe(201);
  const conversation = await created.json();
  const detail = await request.get(`/api/v1/agent/conversations/${conversation.id}`);
  expect(detail.ok()).toBeTruthy();
  expect((await detail.json()).title).toBe("冒烟测试");
});

test("app shell loads against the real backend", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto("/");

  // The sidebar shell is backend-independent and always present once the SPA
  // mounts, proving the bundle loaded and rendered.
  await expect(page.getByText("TradingAgents", { exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主导航" }).getByRole("link", { name: "决策工作台" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "更多工具" }).getByRole("link", { name: "持仓管理" })).toBeVisible();

  // The dashboard header renders after its real /api/v1 queries resolve
  // (empty in-memory DB → empty states, no crash).
  await expect(page.getByRole("heading", { name: "决策工作台" })).toBeVisible();

  expect(errors).toEqual([]);
});

test("portfolio route renders its empty state from the real backend", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto("/portfolio");

  // Navigating to another route that fetches real /api/v1/holdings must not
  // throw; the sidebar stays mounted regardless of returned data.
  await expect(page.getByRole("navigation", { name: "更多工具" }).getByRole("link", { name: "持仓管理" })).toBeVisible();
  expect(errors).toEqual([]);
});
