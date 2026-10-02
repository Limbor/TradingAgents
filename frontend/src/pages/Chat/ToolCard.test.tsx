import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ToolCard } from "@/pages/Chat/ToolCard";
import type { ChatMessage } from "@/stores/useChatStore";

function toolMessage(overrides: Partial<NonNullable<ChatMessage["toolCall"]>> = {}): ChatMessage {
  return {
    id: "tool-1",
    role: "assistant",
    kind: "tool",
    content: "持仓摘要",
    timestamp: "2026-07-11T00:00:00Z",
    toolCall: {
      tool: "get_portfolio_summary",
      args: { limit: 10 },
      display: "table",
      result: {
        holdings: [{ symbol: "600519.SH", pnl: 200 }],
        warnings: ["价格截至上一交易日"],
      },
      ...overrides,
    },
  };
}

afterEach(() => {
  // vitest globals are off, so RTL auto-cleanup never registers.
  cleanup();
});

describe("ToolCard", () => {
  it("renders table results with a Chinese tool label and provenance warnings", () => {
    const message: ChatMessage = {
      ...toolMessage(),
      citations: [
        {
          tool: "get_portfolio_summary",
          args: {},
          summary: "SQLite holdings",
          as_of_date: "2026-07-10",
          source: "local_db",
        },
      ],
    };

    render(<ToolCard message={message} />);

    // Header shows the Chinese label instead of the raw uppercase tool name.
    expect(screen.getByText("持仓概览")).toBeInTheDocument();
    expect(screen.queryByText("get_portfolio_summary")).not.toBeInTheDocument();
    expect(screen.getByText("600519.SH")).toBeInTheDocument();
    expect(screen.getByText(/价格截至上一交易日/)).toBeInTheDocument();
    expect(screen.getByText(/2026-07-10/)).toBeInTheDocument();
  });

  it("renders string results in text mode as markdown", () => {
    const message = toolMessage({
      tool: "get_strategy_lessons",
      display: "text",
      result: "## 策略经验\n\n**追高** 需谨慎",
    });

    render(<ToolCard message={message} />);

    expect(screen.getByRole("heading", { name: "策略经验" })).toBeInTheDocument();
    expect(screen.getByText("追高").tagName).toBe("STRONG");
    // No raw <pre> dump for markdown-capable strings.
    expect(document.querySelector("pre")).toBeNull();
  });

  it("renders object results in text mode as a key-value grid instead of raw JSON", () => {
    const message = toolMessage({
      tool: "search_artifacts",
      display: "text",
      result: { message: "找到 2 条", count: 2 },
    });

    render(<ToolCard message={message} />);

    expect(screen.getByText("message:")).toBeInTheDocument();
    expect(screen.getByText("找到 2 条")).toBeInTheDocument();
    expect(document.querySelector("pre")).toBeNull();
  });

  it("collapses call details (args + citations) by default", () => {
    render(<ToolCard message={toolMessage()} />);

    expect(screen.getByText("调用详情")).toBeInTheDocument();
    const details = document.querySelector("details");
    expect(details).not.toBeNull();
    expect(details!.open).toBe(false);
  });
});
