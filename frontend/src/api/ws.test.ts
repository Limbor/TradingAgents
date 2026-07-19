import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { chatWsManager, wsManager } from "@/api/ws";

/**
 * Controllable fake WebSocket so we can drive the reconnect state machine
 * deterministically (open/message/close/error) under fake timers, without a
 * real socket. Mirrors the browser API surface ws.ts relies on.
 */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  url: string;
  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { wasClean: boolean; code?: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];
  closeCalls: Array<{ code?: number; reason?: string }> = [];

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(code?: number, reason?: string): void {
    this.closeCalls.push({ code, reason });
    this.readyState = FakeWebSocket.CLOSED;
  }

  // ---- test drivers ----
  triggerOpen(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  triggerMessage(data: unknown): void {
    this.onmessage?.({ data: typeof data === "string" ? data : JSON.stringify(data) });
  }

  triggerClose(wasClean = false, code = 1006): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ wasClean, code });
  }

  triggerError(): void {
    this.onerror?.();
  }
}

function latest(): FakeWebSocket {
  const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  if (!ws) throw new Error("no FakeWebSocket instance created");
  return ws;
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
});

afterEach(() => {
  // Reset singleton state so reconnect timers/handlers don't leak between tests.
  wsManager.disconnect();
  chatWsManager.disconnect();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("wsManager (run stream) reconnect", () => {
  it("reconnects after an unclean close using backoff", () => {
    wsManager.connect("run-1");
    expect(FakeWebSocket.instances).toHaveLength(1);
    latest().triggerOpen();
    latest().triggerClose(false, 1006);

    // First backoff = min(1000 * 2^1, 30000) = 2000ms.
    vi.advanceTimersByTime(1999);
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("does not reconnect after a clean close", () => {
    wsManager.connect("run-2");
    latest().triggerOpen();
    latest().triggerClose(true, 1000);

    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("gives up after maxReconnectAttempts (5)", () => {
    wsManager.connect("run-3");
    // Drive 6 unclean closes; only the first 5 should trigger a reconnect.
    for (let i = 0; i < 6; i++) {
      latest().triggerClose(false, 1006);
      vi.advanceTimersByTime(60_000);
    }
    // 1 initial + 5 reconnects = 6 sockets, then it stops.
    expect(FakeWebSocket.instances).toHaveLength(6);
  });
});

describe("chatWsManager reconnect + lifecycle", () => {
  it("schedules a reconnect on a non-manual close", () => {
    chatWsManager.connect();
    latest().triggerOpen();
    latest().triggerClose();

    vi.advanceTimersByTime(2000);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("does not reconnect after an explicit disconnect", () => {
    chatWsManager.connect();
    latest().triggerOpen();
    chatWsManager.disconnect();
    latest().triggerClose();

    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("is a no-op when already open (dedup connect)", () => {
    chatWsManager.connect();
    latest().triggerOpen();
    chatWsManager.connect();
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("closes the socket on error so onclose can drive reconnect", () => {
    chatWsManager.connect();
    latest().triggerOpen();
    latest().triggerError();
    expect(latest().closeCalls.length).toBeGreaterThan(0);
  });

  it("send throws when not open and serializes payload when open", () => {
    expect(() => chatWsManager.send("hi")).toThrow();

    chatWsManager.connect();
    latest().triggerOpen();
    chatWsManager.send("hi", { symbol: "600519.SH" });
    expect(JSON.parse(latest().sent[0] ?? "")).toEqual({
      message: "hi",
      context: { symbol: "600519.SH" },
    });
  });

  it("dispatches parsed messages and ignores malformed frames", () => {
    const received: unknown[] = [];
    const off = chatWsManager.onMessage((msg) => received.push(msg));
    chatWsManager.connect();
    latest().triggerOpen();

    latest().triggerMessage({ type: "chat_answer", run_id: "", timestamp: "", payload: {} });
    latest().triggerMessage("}{ not json");

    expect(received).toHaveLength(1);
    expect((received[0] as { type: string }).type).toBe("chat_answer");
    off();
  });
});
