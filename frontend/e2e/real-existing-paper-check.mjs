/** Browser leg of the local, read-only existing-account verifier. */
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
  const cases = JSON.parse(process.env.EXISTING_PAPER_CASES);
  for (const paper of cases) {
    await page.goto(`${process.env.EXISTING_PAPER_URL}/paper?session=${encodeURIComponent(paper.id)}`);
    await expect(page.getByRole("combobox", { name: "当前模拟盘会话" })).toHaveValue(paper.id);
    await expect(page.getByRole("region", { name: "交易 Agent 对话" })).toContainText(paper.id);
    await expect(page.getByText(`¥${Number(paper.equity).toLocaleString("zh-CN", {
      maximumFractionDigits: 2,
    })}`, { exact: true }).first()).toBeVisible();
    await page.getByRole("link", { name: "在工作台继续" }).click();
    await expect(page).toHaveURL(new RegExp(`conversation=${encodeURIComponent(paper.conversation)}`));
    await expect(page.getByText("总结这个模拟盘当前账本的已核对事实。").first()).toBeVisible();
  }
  expect(errors).toEqual([]);
  process.stdout.write(JSON.stringify({ existing_browser_accounts: cases.length,
    account_scope: "matched", screenshots: 0 }) + "\n");
} finally {
  await browser.close();
}
