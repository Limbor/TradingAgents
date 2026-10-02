import { beforeEach, describe, expect, it, vi } from "vitest";

import { useChatStore } from "@/stores/useChatStore";

beforeEach(() => {
  vi.stubGlobal("crypto", { randomUUID: vi.fn(() => "test-id") });
  useChatStore.getState().reset();
});

describe("useChatStore", () => {
  it("records tool and clarify responses", () => {
    const store = useChatStore.getState();
    store.addToolMessage({
      content: "持仓摘要",
      tool: "get_portfolio_summary",
      args: {},
      result: { total: 2 },
      display: "table",
    });
    store.addClarifyMessage({ content: "请选择股票", options: ["茅台", "平安"] });

    const [tool, clarify] = useChatStore.getState().messages;
    expect(tool?.kind).toBe("tool");
    expect(tool?.toolCall?.tool).toBe("get_portfolio_summary");
    expect(clarify?.clarifyOptions).toEqual(["茅台", "平安"]);
  });

  it("deduplicates tasks and closes active steps", () => {
    const store = useChatStore.getState();
    store.createTask({ runId: "run-1", skillId: "daily_pipeline", title: "每日选股" });
    store.createTask({ runId: "run-1", skillId: "daily_pipeline", title: "重复任务" });
    store.addTaskStep("run-1", { label: "量化筛选" });
    store.finishTask("run-1", "completed", "完成");

    const tasks = useChatStore.getState().messages.filter((message) => message.kind === "task");
    expect(tasks).toHaveLength(1);
    expect(tasks[0]?.taskStatus).toBe("completed");
    expect(tasks[0]?.steps?.every((step) => step.status === "completed")).toBe(true);
  });
  it("keeps identified parallel activities running independently", () => {
    const store = useChatStore.getState();
    store.createTask({ runId: "parallel", title: "股票分析" });
    store.addTaskStep("parallel", { activityId: "price", label: "正在查询股票价格", status: "running" });
    store.addTaskStep("parallel", { activityId: "news", label: "正在查询相关新闻", status: "running" });
    store.addTaskStep("parallel", { activityId: "price", label: "查询股票价格完成", status: "completed" });
    const steps = useChatStore.getState().messages[0]!.steps!;
    expect(steps.filter(s => s.activityId).map(s => [s.activityId, s.status])).toEqual([
      ["price", "completed"], ["news", "running"],
    ]);
  });
});
