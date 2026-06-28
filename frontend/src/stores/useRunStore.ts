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

  startRun: (runId) =>
    set({
      currentRunId: runId,
      status: "running",
      agentStatuses: {},
      reportSections: {},
      toolCalls: [],
      error: null,
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
    set((state) => ({
      toolCalls: [...state.toolCalls, call],
    })),

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
    }),
}));
