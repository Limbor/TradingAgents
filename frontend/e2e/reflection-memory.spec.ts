import { expect, test } from "@playwright/test";

test("memory inventory, version history and paired report are accessible", async ({ page }) => {
  const lesson = { id: "l", lesson_type: "risk_avoidance", scope: "industry", target: "医药生物",
    finding: "风险数据缺失时，应说明判断前提", suggested_adjustment: "核对当前数据覆盖", evidence_count: 5,
    confidence: "medium", active: true, governance_status: "approved", payload: {},
    created_at: "2026-08-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z" };
  let active = true;
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path.endsWith("/deactivate")) { active = false; body = { status: "ok" }; }
    else if (path.endsWith("/strategy-memory/overview")) body = {
      inventory: { available: active ? 1 : 0, pending: 3, retired: active ? 0 : 1, expired: 0 },
      usage: { sampled_tasks: 10, injected_tasks: 4, reported_tasks: 3 },
      evaluation: { id: "report", model: "测试模型", memory_pairs: 2, total_pairs: 3,
        changed_directions: 1, sufficient_samples: false, min_samples: 20,
        arms: { with_memory: { coverage: 1, hit_rate: 0.5 }, without_memory: { coverage: 0.5, hit_rate: 0 } } },
    };
    else if (path.endsWith("/strategy-lessons")) body = active ? [lesson] : [];
    else if (path.endsWith("/versions")) body = [{ ...lesson, version_id: 2 },
      { ...lesson, version_id: 1, governance_status: "candidate", active: false, updated_at: "2026-08-01T00:00:00Z" }];
    else if (path.endsWith("/cases")) body = [];
    else if (path.endsWith("/reflection-cases")) body = [];
    else if (path.endsWith("/prediction-scorecard")) body = { available: false, reason: "样本不足" };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/reflection");
  const overview = page.getByLabel("记忆使用概览");
  await expect(overview.getByText(/4 个提供了经验，3 个返回了具体参考理由/)).toBeVisible();
  await overview.getByText(/有记忆 \/ 无记忆对照评测/).click();
  await expect(overview.getByText("适用样本不足 20 对，暂不判断记忆是否有效。")).toBeVisible();
  await expect(overview.getByRole("link", { name: "查看评测报告" })).toHaveAttribute("href", "/library?artifact_type=memory_evaluation");
  await page.getByRole("button", { name: "展开详情" }).click();
  await page.getByText("版本与批准记录", { exact: true }).click();
  await expect(page.getByText(/版本 2 · .*已批准/)).toBeVisible();
  await expect(page.getByText(/版本 1 · .*等待批准/)).toBeVisible();
  if (process.env.CAPTURE_AGENT_QA) await page.screenshot({ path: "test-results/reflection-memory.png", fullPage: true });
  await page.getByRole("button", { name: "停用", exact: true }).click();
  await expect(page.getByText("经验已停用，后续复核不再注入。")).toBeVisible();
  await expect(overview.getByText("4 个提供了经验", { exact: false })).toBeVisible();
  const availableCount = overview.locator("dl > div").filter({ hasText: "可用经验" });
  await expect(availableCount.locator("dd")).toHaveText("0");
});
