import { agentApiBase, fetchJson } from "./client";
import { authHeaders } from "./auth";
import type { IntentHint } from "@/lib/chatNav";

export interface AgentConversation {
  id: string;
  title: string;
  paper_session_id: string | null;
  created_at: string;
  updated_at: string;
  latest_status?: string | null;
  legacy_archive?: number | boolean;
}

export interface AgentMessage {
  id: string;
  conversation_id: string;
  task_id: string | null;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface AgentEvent {
  task_id: string;
  seq: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface AgentEvidence {
  id: string;
  task_id: string;
  tool_name: string;
  source: string;
  as_of_date: string | null;
  retrieved_at: string;
  summary: string;
  warnings: string[];
  result: Record<string, unknown>;
}

export interface AgentTask {
  id: string;
  conversation_id: string;
  goal: string;
  status: string;
  result: { content?: string; citations?: unknown[]; read_only?: boolean };
  error: string | null;
  created_at: string;
  updated_at: string;
  events: AgentEvent[];
  evidence: AgentEvidence[];
  proposal?: {
    id: string;
    task_id: string;
    action_type: string;
    session_id: string;
    args: { target_date: string };
    baseline: { as_of_date: string; equity?: number | null };
    status: string;
    result: Record<string, unknown>;
    expires_at: string;
  } | null;
}

export interface AgentConversationDetail extends AgentConversation {
  messages: AgentMessage[];
  tasks: AgentTask[];
}

export interface LegacyArchiveMessage {
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export const listAgentConversations = () =>
  fetchJson<AgentConversation[]>(`${agentApiBase}/conversations`);

export const createAgentConversation = (paperSessionId?: string | null) =>
  fetchJson<AgentConversation>(`${agentApiBase}/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: paperSessionId ? `模拟盘 · ${paperSessionId}` : "新对话", paper_session_id: paperSessionId ?? null }),
  });

export const importLegacyAgentConversation = (paperSessionId: string | null, messages: LegacyArchiveMessage[]) =>
  fetchJson<AgentConversation & { imported_count: number }>(`${agentApiBase}/legacy-import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paper_session_id: paperSessionId, messages }),
  });

export const getAgentConversation = (id: string) =>
  fetchJson<AgentConversationDetail>(`${agentApiBase}/conversations/${encodeURIComponent(id)}`);

/** Follow a task with an authenticated fetch stream, keeping tokens out of URLs. */
export async function readAgentTaskStream(
  taskId: string,
  afterSeq: number,
  signal: AbortSignal,
  onEvent: (event: AgentEvent) => void,
): Promise<{ lastSeq: number; done: boolean }> {
  const response = await fetch(
    `${agentApiBase}/tasks/${encodeURIComponent(taskId)}/stream?after_seq=${afterSeq}`,
    { headers: { ...authHeaders(), Accept: "text/event-stream" }, signal },
  );
  if (!response.ok || !response.body) throw new Error(`任务事件连接失败 (${response.status})`);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastSeq = afterSeq;
  let done = false;
  try {
    while (!done) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer = (buffer + decoder.decode(chunk.value, { stream: true })).replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const lines = frame.split("\n");
        const kind = lines.find((line) => line.startsWith("event: "))?.slice(7);
        if (kind === "done") {
          done = true;
          break;
        }
        if (kind === "agent_event") {
          const data = lines.find((line) => line.startsWith("data: "))?.slice(6);
          if (data) {
            const event = JSON.parse(data) as AgentEvent;
            if (event.task_id === taskId && Number.isInteger(event.seq) && event.seq > lastSeq) {
              lastSeq = event.seq;
              onEvent(event);
            }
          }
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
  return { lastSeq, done };
}

export const submitAgentTask = (id: string, message: string, intentHint?: IntentHint) =>
  fetchJson<AgentTask>(`${agentApiBase}/conversations/${encodeURIComponent(id)}/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, intent_hint: intentHint }),
  });

export const cancelAgentTask = (id: string) =>
  fetchJson<{ cancelled: boolean }>(`${agentApiBase}/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST" });

export const approveAgentProposal = (id: string) =>
  fetchJson<{ status: string }>(`${agentApiBase}/proposals/${encodeURIComponent(id)}/approve`, { method: "POST" });

export const rejectAgentProposal = (id: string) =>
  fetchJson<{ status: string }>(`${agentApiBase}/proposals/${encodeURIComponent(id)}/reject`, { method: "POST" });

export const reconcileAgentProposal = (id: string) =>
  fetchJson<{ status: string }>(`${agentApiBase}/proposals/${encodeURIComponent(id)}/reconcile`, { method: "POST" });
