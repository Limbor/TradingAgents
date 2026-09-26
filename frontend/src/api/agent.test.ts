import { afterEach, expect, it, vi } from "vitest";

import { readAgentTaskStream } from "@/api/agent";

afterEach(() => {
  localStorage.removeItem("tradingagents_api_auth_token");
  vi.unstubAllGlobals();
});

it("resumes authenticated task events across split stream chunks", async () => {
  localStorage.setItem("tradingagents_api_auth_token", "test-token");
  const event = { task_id: "task-1", seq: 2, event_type: "step_completed",
    payload: { label: "读取账本" }, created_at: "2026-09-27T00:00:00Z" };
  const frame = `id: 2\nevent: agent_event\ndata: ${JSON.stringify(event)}\n\n`;
  const bytes = new TextEncoder().encode(frame + frame + "event: done\ndata: {}\n\n");
  const split = bytes.indexOf(0xe8) + 1; // Break inside a Chinese UTF-8 character.
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(bytes.slice(0, split));
      controller.enqueue(bytes.slice(split, split + 11));
      controller.enqueue(bytes.slice(split + 11));
      controller.close();
    },
  });
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, body: stream });
  vi.stubGlobal("fetch", fetchMock);
  const seen: number[] = [];

  const result = await readAgentTaskStream("task-1", 1, new AbortController().signal,
    (item) => seen.push(item.seq));

  expect(result).toEqual({ lastSeq: 2, done: true });
  expect(seen).toEqual([2]);
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/tasks/task-1/stream?after_seq=1"),
    expect.objectContaining({ headers: {
      Authorization: "Bearer test-token", Accept: "text/event-stream",
    } }),
  );
});
