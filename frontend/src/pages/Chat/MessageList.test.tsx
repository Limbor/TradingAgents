import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { groupMessages, MessageList } from "@/pages/Chat/MessageList";
import type { ChatMessage } from "@/stores/useChatStore";

function toolMessage(id: string, tool: string): ChatMessage {
  return {
    id,
    role: "assistant",
    kind: "tool",
    content: "",
    timestamp: "2026-07-11T00:00:00Z",
    toolCall: { tool, args: {}, display: "text", result: "结果 " + id },
  };
}

function textMessage(id: string, content: string): ChatMessage {
  return { id, role: "assistant", kind: "text", content, timestamp: "2026-07-11T00:00:00Z" };
}

function renderList(messages: ChatMessage[]) {
  return render(
    <MemoryRouter>
      <MessageList
        messages={messages}
        sendReady
        onAnalyze={vi.fn()}
        onSendPrompt={vi.fn()}
        onPrefillInput={vi.fn()}
      />
    </MemoryRouter>,
  );
}

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    get: () => 640,
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get: () => 200,
  });
  HTMLElement.prototype.scrollTo = vi.fn();
});

afterEach(() => {
  // vitest globals are off, so RTL auto-cleanup never registers.
  cleanup();
  vi.clearAllMocks();
});

describe("groupMessages", () => {
  it("groups consecutive tool messages and keeps singles untouched", () => {
    const groups = groupMessages([
      textMessage("m1", "你好"),
      toolMessage("t1", "get_portfolio_summary"),
      toolMessage("t2", "get_recent_runs"),
      textMessage("m2", "好的"),
      toolMessage("t3", "get_strategy_lessons"),
    ]);

    expect(groups.map((g) => g.type)).toEqual(["single", "tools", "single", "single"]);
    const tools = groups[1];
    expect(tools?.type === "tools" && tools.messages.map((m) => m.id)).toEqual(["t1", "t2"]);
  });
});

describe("MessageList tool grouping", () => {
  it("positions the transcript at the bottom immediately on entry", () => {
    renderList([textMessage("m1", "已有历史消息")]);

    const container = screen.getByTestId("message-scroll-container");
    expect(container.scrollTop).toBe(640);
    expect(container.scrollTo).not.toHaveBeenCalled();
  });

  it("pins rapid message updates without starting competing smooth scrolls", () => {
    const view = renderList([textMessage("m1", "你好")]);
    const container = screen.getByTestId("message-scroll-container");

    view.rerender(
      <MemoryRouter>
        <MessageList
          messages={[
            textMessage("m1", "你好"),
            { ...textMessage("m2", "开始选股"), role: "user" },
          ]}
          sendReady
          onAnalyze={vi.fn()}
          onSendPrompt={vi.fn()}
          onPrefillInput={vi.fn()}
        />
      </MemoryRouter>,
    );
    view.rerender(
      <MemoryRouter>
        <MessageList
          messages={[
            textMessage("m1", "你好"),
            { ...textMessage("m2", "开始选股"), role: "user" },
            textMessage("m3", "任务已创建"),
          ]}
          sendReady
          onAnalyze={vi.fn()}
          onSendPrompt={vi.fn()}
          onPrefillInput={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(container.scrollTop).toBe(640);
    expect(container.scrollTo).not.toHaveBeenCalled();
  });

  it("does not pull the transcript down while the user is reading history", () => {
    const view = renderList([textMessage("m1", "旧消息")]);
    const container = screen.getByTestId("message-scroll-container");
    container.scrollTop = 100;
    fireEvent.scroll(container);

    view.rerender(
      <MemoryRouter>
        <MessageList
          messages={[textMessage("m1", "旧消息"), textMessage("m2", "后台进度更新")]}
          sendReady
          onAnalyze={vi.fn()}
          onSendPrompt={vi.fn()}
          onPrefillInput={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(container.scrollTop).toBe(100);
  });

  it("renders two consecutive tool messages as one 工具调用 ×2 group", () => {
    renderList([
      toolMessage("t1", "get_portfolio_summary"),
      toolMessage("t2", "get_recent_runs"),
    ]);

    expect(screen.getByText("工具调用 ×2")).toBeInTheDocument();
    expect(screen.getByText("持仓概览")).toBeInTheDocument();
    expect(screen.getByText("近期任务")).toBeInTheDocument();
  });

  it("renders a lone tool message without a group header", () => {
    renderList([toolMessage("t1", "get_portfolio_summary")]);

    expect(screen.queryByText(/工具调用 ×/)).not.toBeInTheDocument();
    expect(screen.getByText("持仓概览")).toBeInTheDocument();
  });
});
