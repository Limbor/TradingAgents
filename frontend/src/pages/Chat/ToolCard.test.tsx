import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolCard } from "@/pages/Chat/ToolCard";
import type { ChatMessage } from "@/stores/useChatStore";

describe("ToolCard", () => {
  it("renders table results and provenance warnings", () => {
    const message: ChatMessage = {
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
      },
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

    expect(screen.getByText("get_portfolio_summary")).toBeInTheDocument();
    expect(screen.getByText("600519.SH")).toBeInTheDocument();
    expect(screen.getByText(/价格截至上一交易日/)).toBeInTheDocument();
    expect(screen.getByText(/2026-07-10/)).toBeInTheDocument();
  });
});
