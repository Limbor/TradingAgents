import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TaskCard } from "@/pages/Chat/TaskCard";
import type { ChatMessage } from "@/stores/useChatStore";

afterEach(() => {
  cleanup();
});

describe("TaskCard", () => {
  it("renders report markdown headings, emphasis, and GFM tables", () => {
    const message: ChatMessage = {
      id: "task-market",
      role: "assistant",
      kind: "task",
      content: "市场/板块分析",
      skillId: "market_overview",
      taskStatus: "completed",
      timestamp: "2026-08-05T10:00:00Z",
      result: [
        "## 钨板块驱动与持续性",
        "",
        "**方向**：偏多",
        "",
        "| 证据 | 涨跌 |",
        "|---|---:|",
        "| 有色金属（代理） | +5.55% |",
      ].join("\n"),
    };

    render(
      <MemoryRouter>
        <TaskCard
          message={message}
          onAnalyze={vi.fn()}
          onSendPrompt={vi.fn()}
          onPrefillInput={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "钨板块驱动与持续性" })).toBeInTheDocument();
    expect(screen.getByText("方向").tagName).toBe("STRONG");
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.queryByText(/## 钨板块/)).not.toBeInTheDocument();
  });

  it("renders both risk and candidate results from a daily review workflow", () => {
    const message: ChatMessage = {
      id: "task-review",
      role: "assistant",
      kind: "task",
      content: "收盘复盘",
      skillId: "daily_review",
      taskStatus: "completed",
      timestamp: "2026-09-04T10:00:00Z",
      result: [
        JSON.stringify({
          __type: "risks",
          data: [{ symbol: "600519.SH", name: "贵州茅台", level: "high", summary: "公告风险" }],
        }),
        "",
        "## 风险扫描报告",
        "",
        JSON.stringify({
          __type: "candidates",
          data: [{ symbol: "600019.SH", name: "宝钢股份", industry: "钢铁", final_decision: "MONITOR" }],
          warnings: [],
        }),
        "",
        "## 收盘复盘与次日计划",
      ].join("\n"),
    };
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <TaskCard
            message={message}
            onAnalyze={vi.fn()}
            onSendPrompt={vi.fn()}
            onPrefillInput={vi.fn()}
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "持仓风险结果" })).toBeInTheDocument();
    expect(screen.getByText("贵州茅台")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "次日选股结果" })).toBeInTheDocument();
    expect(screen.getByText("宝钢股份")).toBeInTheDocument();
    expect(screen.getByText("展开完整复盘报告")).toBeInTheDocument();
  });
});
