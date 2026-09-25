import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { analyzeStockHint, type ChatNavState } from "@/lib/chatNav";
import { useChatStore } from "@/stores/useChatStore";
import Chat from ".";

/**
 * Chat page send pipeline: nonce-deduplicated autoSend from navigation state,
 * pending context/hint carried into the next manual submit, and the
 * "never drop silently" prefill feedback path.
 */
const sendMock = vi.fn();

vi.mock("@/api/ws", () => ({
  chatWsManager: {
    send: (...args: unknown[]) => sendMock(...args),
    connect: vi.fn(),
    isOpen: () => false,
    onOpen: () => () => {},
    onClose: () => () => {},
    onMessage: () => () => {},
  },
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getRun: vi.fn().mockResolvedValue({ status: "completed" }),
}));

/** Imperative second navigation into /chat (same component instance). */
const navRef: { current: (state: Omit<ChatNavState, "nonce"> & { nonce: string }) => void } = {
  current: () => {},
};
function NavCapture() {
  const navigate = useNavigate();
  useEffect(() => {
    navRef.current = (state) => navigate("/chat", { state });
  }, [navigate]);
  return null;
}

function renderChat(state?: Omit<ChatNavState, "nonce"> & { nonce: string }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[{ pathname: "/chat", state }]}>
        <NavCapture />
        <Routes>
          <Route path="/chat" element={<Chat />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const CONTEXT = { holding_context: { symbol: "600519.SH", quantity: 100 } };
const HINT = analyzeStockHint("600519.SH");
const PROMPT = "帮我分析 600519.SH";

function chatInput(): HTMLInputElement {
  return screen.getByPlaceholderText(/例如/) as HTMLInputElement;
}

function systemMessages(): string[] {
  return useChatStore
    .getState()
    .messages.filter((m) => m.role === "system")
    .map((m) => m.content);
}

beforeAll(() => {
  // jsdom does not implement scrollIntoView (used by MessageList autoscroll).
  Element.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  sendMock.mockReset();
  useChatStore.getState().reset();
});

afterEach(() => {
  // vitest globals are off, so RTL auto-cleanup never registers.
  cleanup();
});

describe("Chat autoSend from navigation state", () => {
  it("sends immediately with context + intent hint when connected and idle", () => {
    useChatStore.getState().setConnected(true);
    renderChat({ prompt: PROMPT, autoSend: true, context: CONTEXT, intentHint: HINT, nonce: "n1" });

    expect(sendMock).toHaveBeenCalledTimes(1);
    expect(sendMock).toHaveBeenCalledWith(PROMPT, { context: CONTEXT, intentHint: HINT });
    const state = useChatStore.getState();
    expect(state.running).toBe(true);
    expect(state.messages.some((m) => m.role === "user" && m.content === PROMPT)).toBe(true);
  });

  it("fires again for the same prompt on a second navigation (different nonce)", () => {
    useChatStore.getState().setConnected(true);
    renderChat({ prompt: PROMPT, autoSend: true, intentHint: HINT, nonce: "n1" });
    expect(sendMock).toHaveBeenCalledTimes(1);

    // First task finished, then the user jumps in from the same page button.
    act(() => useChatStore.getState().setRunning(false));
    act(() => navRef.current({ prompt: PROMPT, autoSend: true, intentHint: HINT, nonce: "n2" }));
    expect(sendMock).toHaveBeenCalledTimes(2);
  });

  it("prefills instead of sending while running, then submit carries the pending context", () => {
    const store = useChatStore.getState();
    store.setConnected(true);
    store.setRunning(true);
    renderChat({ prompt: PROMPT, autoSend: true, context: CONTEXT, intentHint: HINT, nonce: "n1" });

    // Not silently dropped: input prefilled + system feedback, nothing sent.
    expect(sendMock).not.toHaveBeenCalled();
    expect(chatInput().value).toBe(PROMPT);
    expect(systemMessages().some((c) => c.includes("当前有任务运行中"))).toBe(true);

    act(() => useChatStore.getState().setRunning(false));
    fireEvent.submit(chatInput().closest("form")!);
    expect(sendMock).toHaveBeenCalledWith(PROMPT, { context: CONTEXT, intentHint: HINT });
  });

  it("drops the pending context/hint once the user edits the input", () => {
    const store = useChatStore.getState();
    store.setConnected(true);
    store.setRunning(true);
    renderChat({ prompt: PROMPT, autoSend: true, context: CONTEXT, intentHint: HINT, nonce: "n1" });
    expect(chatInput().value).toBe(PROMPT);

    // Manual rewrite to another symbol: stale 600519 context must not ride along.
    fireEvent.change(chatInput(), { target: { value: "分析 000001.SZ" } });
    act(() => useChatStore.getState().setRunning(false));
    fireEvent.submit(chatInput().closest("form")!);
    expect(sendMock).toHaveBeenCalledWith("分析 000001.SZ", undefined);
  });
});

describe("Chat clarify options while busy", () => {
  it("prefills the input with feedback instead of silently dropping the click", () => {
    const store = useChatStore.getState();
    store.setConnected(true);
    store.setRunning(true);
    store.addClarifyMessage({ content: "请确认要分析的标的", options: ["分析 600519.SH"] });
    renderChat();

    fireEvent.click(screen.getByRole("button", { name: "分析 600519.SH" }));
    expect(sendMock).not.toHaveBeenCalled();
    expect(chatInput().value).toBe("分析 600519.SH");
    expect(systemMessages().some((c) => c.includes("当前有任务运行中"))).toBe(true);
  });
});
