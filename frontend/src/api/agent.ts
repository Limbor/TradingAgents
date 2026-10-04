import { agentApiBase, fetchJson } from "./client";
import { authHeaders } from "./auth";
import type { IntentHint } from "@/lib/chatNav";
import type { DecisionBrief } from "@/components/Chat/DecisionBriefCard";

export interface AgentConversation {
  id: string;
  title: string;
  paper_session_id: string | null;
  created_at: string;
  updated_at: string;
  latest_status?: string | null;
  legacy_archive?: number | boolean;
}

export interface TaskModelSelection {
  provider: string;
  model: string;
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

export interface AgentAnswer {
  summary: string;
  verdict: "informational" | "conditional" | "insufficient_evidence";
  reasons: string[];
  risks: string[];
  assumptions: string[];
  evidence_refs: string[];
  next_actions: string[];
}

export interface SpecialistRun {
  run_id: string;
  root_id: string;
  parent_id: string | null;
  role: string;
  kind: 'agent' | 'model' | 'tool' | 'workflow';
  status: string;
  model: string;
  output: Record<string, unknown>;
  evidence_refs: string[];
  memory_refs: string[];
}

export interface ResearchTaskContext {
  target: string;
  symbols: string[];
  industries: string[];
  filters: { board_filter?: string; limit?: number };
  horizon: string | null;
  dimensions: string[];
  inherited_from: string | null;
  last_skill: string | null;
}

export interface AgentTask {
  task_context?: ResearchTaskContext;
  usage_stats?: UsageStats;
  agent_runs?: SpecialistRun[];
  id: string;
  conversation_id: string;
  goal: string;
  status: string;
  result: { content?: string; citations?: unknown[]; read_only?: boolean; answer?: AgentAnswer; memory_trace?: Record<string, unknown>; decision_briefs?: DecisionBrief[] };
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
  usage_stats?: UsageStats;
  messages: AgentMessage[];
  tasks: AgentTask[];
}

export interface UsageGroup {
  role: string;
  provider: string;
  model: string;
  model_calls: number;
  reported_calls: number;
  missing_calls: number;
  pending_calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  reasoning_tokens: number;
  cache_unknown_calls: number;
  reasoning_unknown_calls: number;
  unpriced_calls: number;
  cost_cny: number | null;
}

export interface UsageStats extends Omit<UsageGroup, "role" | "provider" | "model"> {
  groups: UsageGroup[];
  currency: string;
  price_date: string;
  price_source: string;
  incomplete: boolean;
  cost_complete: boolean;
  cache_hit_rate: number | null;
}

export interface LegacyArchiveMessage {
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export const listAgentConversations = (paperSessionId: string | null, offset = 0, limit = 50) => {
  const params = new URLSearchParams({
    paper_session_id: paperSessionId ?? "", offset: String(offset), limit: String(limit),
  });
  return fetchJson<AgentConversation[]>(`${agentApiBase}/conversations?${params}`);
};

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

export const submitAgentTask = (id: string, message: string, intentHint?: IntentHint, modelSelection?: TaskModelSelection, retryTaskId?: string) =>
  fetchJson<AgentTask>(`${agentApiBase}/conversations/${encodeURIComponent(id)}/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, intent_hint: intentHint, model_selection: modelSelection, retry_task_id: retryTaskId }),
  });

export const cancelAgentTask = (id: string) =>
  fetchJson<{ cancelled: boolean }>(`${agentApiBase}/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST" });

export const approveAgentProposal = (id: string) =>
  fetchJson<{ status: string }>(`${agentApiBase}/proposals/${encodeURIComponent(id)}/approve`, { method: "POST" });

export const rejectAgentProposal = (id: string) =>
  fetchJson<{ status: string }>(`${agentApiBase}/proposals/${encodeURIComponent(id)}/reject`, { method: "POST" });

export const reconcileAgentProposal = (id: string) =>
  fetchJson<{ status: string }>(`${agentApiBase}/proposals/${encodeURIComponent(id)}/reconcile`, { method: "POST" });

export const closeAgentProposalReview = (id: string, observedStateFingerprint: string) =>
  fetchJson<{ status: string }>(`${agentApiBase}/proposals/${encodeURIComponent(id)}/close-review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ observed_state_fingerprint: observedStateFingerprint, confirmed: true }),
  });
