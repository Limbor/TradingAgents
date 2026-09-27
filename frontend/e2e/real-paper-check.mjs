/** Browser leg of scripts/verify_agent_paper_engine.py. Uses only synthetic data. */
import { existsSync } from "node:fs";
import { chromium, expect } from "@playwright/test";

const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const browser = await chromium.launch({
  headless: true,
  ...(existsSync(chrome) ? { executablePath: chrome } : {}),
});
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const session = process.env.REAL_PAPER_SESSION;
  await page.goto(`${process.env.REAL_PAPER_URL}/paper?session=${encodeURIComponent(session)}`);
  await expect(page.getByRole("combobox", { name: "当前模拟盘会话" })).toHaveValue(session);
  await expect(page.getByRole("region", { name: "交易 Agent 对话" })).toContainText(session);
  await expect(page.getByText(`¥${process.env.REAL_PAPER_EQUITY}`, { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "最近成交" })).toBeVisible();
  await expect(page.getByText("模拟盘推进任务已完成")).toBeVisible();
  await expect(page.getByText(`账户权益：¥${process.env.REAL_PAPER_EQUITY}`)).toBeVisible();
  await expect(page.getByText("提案时权益")).toBeVisible();
  await expect(page.getByText("执行后账本").locator("..")).toContainText(`¥${process.env.REAL_PAPER_EQUITY}`);
  await expect(page.locator(".recharts-xAxis .recharts-cartesian-axis-tick-value").first()).toHaveText("06-19");
  if (process.env.REAL_PAPER_SCREENSHOT) {
    await page.screenshot({ path: process.env.REAL_PAPER_SCREENSHOT, fullPage: true });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  if (process.env.REAL_PAPER_SCREENSHOT) {
    await page.screenshot({ path: process.env.REAL_PAPER_SCREENSHOT.replace(/\.png$/i, "-mobile.png"),
      fullPage: true });
  }
  await page.getByRole("button", { name: "查看模拟账本" }).click();
  const ledger = page.getByRole("region", { name: "模拟盘账本" });
  await expect(ledger.getByText("总权益", { exact: true })).toBeInViewport();
  await expect(ledger.getByText(`¥${process.env.REAL_PAPER_EQUITY}`, { exact: true }).first()).toBeInViewport();
  if (process.env.REAL_PAPER_SCREENSHOT) {
    await page.screenshot({ path: process.env.REAL_PAPER_SCREENSHOT.replace(/\.png$/i, "-mobile-ledger.png") });
  }
  await page.getByRole("button", { name: "查看 Agent 对话" }).click();
  await expect(page.getByRole("region", { name: "交易 Agent 对话" })).toBeInViewport();
  await page.getByRole("link", { name: "在工作台继续" }).click();
  await expect(page).toHaveURL(/\/chat\?paper_session=/);
  await expect(page.getByText("模拟盘推进任务已完成")).toBeVisible();
  await expect(page.getByText("执行后账本").locator("..")).toContainText("10 天");
  if (process.env.REAL_PAPER_SCREENSHOT) {
    await page.screenshot({ path: process.env.REAL_PAPER_SCREENSHOT.replace(/\.png$/i, "-mobile-chat.png"),
      fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: process.env.REAL_PAPER_SCREENSHOT.replace(/\.png$/i, "-chat.png"),
      fullPage: true });
  }
  await expect(page.getByRole("link", { name: "查看实际账本" })).toHaveAttribute(
    "href", `/paper?session=${encodeURIComponent(session)}`);
  await page.getByRole("link", { name: "查看实际账本" }).click();
  await expect(page.getByRole("combobox", { name: "当前模拟盘会话" })).toHaveValue(session);
  expect(errors).toEqual([]);
  process.stdout.write(JSON.stringify({ browser: "passed", session,
    equity: process.env.REAL_PAPER_EQUITY, same_conversation: true }) + "\n");
} finally {
  await browser.close();
}
