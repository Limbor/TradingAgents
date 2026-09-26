import { describe, expect, it } from "vitest";
import { readLegacyChatBatches } from "./legacyChatImport";

describe("legacy chat archive conversion", () => {
  it("keeps account scopes separate and drops system/runtime messages", () => {
    const storage = { getItem: () => JSON.stringify({ state: { messages: [
      { id: "welcome", role: "assistant", content: "欢迎", timestamp: "2026-09-20T08:00:00Z" },
      { id: "1", role: "user", content: "看看组合", scope: "general", timestamp: "2026-09-20T08:00:01Z" },
      { id: "2", role: "assistant", kind: "task", content: "分析完成", result: "报告摘要", scope: "general", timestamp: "2026-09-20T08:00:02Z" },
      { id: "3", role: "user", content: "解释模拟盘", scope: "paper:paper:one", timestamp: "2026-09-20T08:00:03Z" },
      { id: "4", role: "system", content: "连接断开", scope: "paper:paper:one", timestamp: "2026-09-20T08:00:04Z" },
      { id: "5", role: "user", content: "越界账户", scope: "paper:../other", timestamp: "2026-09-20T08:00:05Z" },
    ] } }) };
    const batches = readLegacyChatBatches(storage);
    expect(batches).toEqual([
      { paperSessionId: null, messages: [
        { role: "user", content: "看看组合", created_at: "2026-09-20T08:00:01.000Z" },
        { role: "assistant", content: "分析完成\n\n报告摘要", created_at: "2026-09-20T08:00:02.000Z" },
      ] },
      { paperSessionId: "paper:one", messages: [
        { role: "user", content: "解释模拟盘", created_at: "2026-09-20T08:00:03.000Z" },
      ] },
    ]);
  });
});
