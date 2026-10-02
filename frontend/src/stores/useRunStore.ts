import type { AgentEvent } from "@/api/agent";
import { create } from "zustand";

interface AgentStatus {
  agent: string;
  status: "pending" | "running" | "completed" | "failed";
  duration_ms?: number;
}

interface ReportSection {
  section: string;
  content: string;
  is_final: boolean;
}

interface ToolCall {
  activity_id?: string;
  status?: string;
  tool: string;
  args: Record<string, unknown>;
  timestamp: string;
}

interface RunState {
  currentRunId: string | null;
  status: "idle" | "running" | "completed" | "failed" | "cancelled";
  agentStatuses: Record<string, AgentStatus>;
  reportSections: Record<string, ReportSection>;
  toolCalls: ToolCall[];
  error: string | null;
  progressEvents: AgentEvent[];
  addProgressEvent: (event: AgentEvent) => void;

  startRun: (runId: string) => void;
  updateAgentStatus: (status: AgentStatus) => void;
  updateReportSection: (section: ReportSection) => void;
  addToolCall: (call: ToolCall) => void;
  completeRun: () => void;
  cancelRun: () => void;
  failRun: (error: string) => void;
  reset: () => void;
}

export const useRunStore = create<RunState>((set) => ({
  currentRunId: null,
  status: "idle",
  agentStatuses: {},
  reportSections: {},
  toolCalls: [],
  error: null,
  progressEvents: [],

  startRun: (runId) =>
    set({
      currentRunId: runId,
      status: "running",
      agentStatuses: {},
      reportSections: {},
      toolCalls: [],
      error: null,
      progressEvents: [],
    }),

  updateAgentStatus: (status) =>
    set((state) => ({
      agentStatuses: { ...state.agentStatuses, [status.agent]: status },
    })),

  updateReportSection: (section) =>
    set((state) => ({
      reportSections: { ...state.reportSections, [section.section]: section },
    })),

  addToolCall: (call) =>
    set((state) => {
      const existing = call.activity_id ? state.toolCalls.findIndex((c) => c.activity_id === call.activity_id) : -1;
      const toolCalls = [...state.toolCalls];
      if (existing >= 0) toolCalls[existing] = call;
      else toolCalls.push(call);
      return { toolCalls };
    }),
  addProgressEvent: (event) => set((state) => ({ progressEvents: [...state.progressEvents, event] })),

  completeRun: () => set({ status: "completed" }),
  cancelRun: () => set({ status: "cancelled" }),
  failRun: (error) => set({ status: "failed", error }),
  reset: () =>
    set({
      currentRunId: null,
      status: "idle",
      agentStatuses: {},
      reportSections: {},
      toolCalls: [],
      error: null,
      progressEvents: [],
    }),
}));
