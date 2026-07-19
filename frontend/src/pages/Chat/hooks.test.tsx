import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WSMessage } from "@/api/ws";
import { useChatStore } from "@/stores/useChatStore";
import { useChatWebSocket } from "@/pages/Chat/hooks";

/**
 * Capture the chat-WS handlers the hook registers so tests can push messages
 * and drive the reconnect-recovery onOpen path deterministically.
 */
let messageHandler: ((msg: WSMessage) => void) | null = null;
let openHandler: (() => void | Promise<void>) | null = null;
const getRunMock = vi.fn();

vi.mock("@/api/ws", () => ({
  chatWsManager: {
    onOpen: (h: () => void) => {
      openHandler = h;
      return () => {
        openHandler = null;
      };
    },
    onClose: () => () => {},
    onMessage: (h: (msg: WSMessage) => void) => {
      messageHandler = h;
      return () => {
        messageHandler = null;
      };
    },
    connect: vi.fn(),
    isOpen: () => false,
  },
}));

vi.mock("@/api/client", () => ({
  getRun: (...args: unknown[]) => getRunMock(...args),
}));

function emit(message: Partial<WSMessage> & { type: string }): void {
  act(() => {
    messageHandler?.({
      run_id: "r1",
      timestamp: "2026-01-01T00:00:00Z",
      payload: {},
      ...message,
    } as WSMessage);
  });
}

function renderChatHook() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  const view = renderHook(() => useChatWebSocket(), { wrapper });
  return { invalidateSpy, ...view };
}

beforeEach(() => {
  messageHandler = null;
  openHandler = null;
  getRunMock.mockReset();
  useChatStore.getState().reset();
});

afterEach(() => {
  useChatStore.getState().reset();
});

describe("useChatWebSocket query invalidation", () => {
  it("invalidates runs, dashboard artifacts and holdings on run_complete", () => {
    const { invalidateSpy, unmount } = renderChatHook();
    emit({ type: "run_complete", run_id: "r1" });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["runs"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["dashboard-artifacts"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["holdings"] });
    unmount();
  });

  it("invalidates runs on a terminal error event", () => {
    const { invalidateSpy, unmount } = renderChatHook();
    emit({ type: "error", run_id: "r1", payload: { message: "boom" } });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["runs"] });
    unmount();
  });

  it("invalidates runs on run_cancelled", () => {
    const { invalidateSpy, unmount } = renderChatHook();
    emit({ type: "run_cancelled", run_id: "r1" });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["runs"] });
    unmount();
  });

  it("recovers a run that finished during a socket drop (reconnect onOpen)", async () => {
    getRunMock.mockResolvedValue({ status: "completed" });
    useChatStore.getState().setCurrentRunId("r1");
    const { invalidateSpy, unmount } = renderChatHook();

    await act(async () => {
      await openHandler?.();
    });

    expect(getRunMock).toHaveBeenCalledWith("r1");
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["runs"] });
    expect(useChatStore.getState().currentRunId).toBeNull();
    unmount();
  });

  it("skips recovery when there is no in-flight run", async () => {
    const { invalidateSpy, unmount } = renderChatHook();

    await act(async () => {
      await openHandler?.();
    });

    expect(getRunMock).not.toHaveBeenCalled();
    expect(invalidateSpy).not.toHaveBeenCalled();
    unmount();
  });
});
