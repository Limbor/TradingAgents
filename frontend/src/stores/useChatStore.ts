import { create } from "zustand";

export type ChatRole = "user" | "assistant" | "system";
export type ChatMessageKind = "text" | "task";
export type ChatTaskStatus = "queued" | "running" | "completed" | "failed";

export interface ChatTaskStep {
  id: string;
  label: string;
  detail?: string;
  status: ChatTaskStatus;
  timestamp: string;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  kind?: ChatMessageKind;
  content: string;
  runId?: string;
  skillId?: string;
  taskStatus?: ChatTaskStatus;
  steps?: ChatTaskStep[];
  result?: string;
  timestamp: string;
}

interface ChatState {
  messages: ChatMessage[];
  connected: boolean;
  running: boolean;
  currentRunId: string | null;
  setConnected: (connected: boolean) => void;
  setRunning: (running: boolean) => void;
  setCurrentRunId: (runId: string | null) => void;
  addMessage: (message: Omit<ChatMessage, "id" | "timestamp">) => void;
  createTask: (task: {
    runId: string;
    skillId?: string;
    title: string;
    detail?: string;
  }) => void;
  addTaskStep: (
    runId: string,
    step: { label: string; detail?: string; status?: ChatTaskStatus }
  ) => void;
  appendTaskResult: (runId: string, content: string) => void;
  finishTask: (runId: string, status: "completed" | "failed", detail?: string) => void;
  reset: () => void;
}

function now() {
  return new Date().toISOString();
}

function newStep(
  label: string,
  detail?: string,
  status: ChatTaskStatus = "running"
): ChatTaskStep {
  return {
    id: crypto.randomUUID(),
    label,
    detail,
    status,
    timestamp: now(),
  };
}

function closeActiveSteps(
  steps: ChatTaskStep[] | undefined,
  status: "completed" | "failed" = "completed"
): ChatTaskStep[] {
  return (steps ?? []).map((step) =>
    step.status === "running" || step.status === "queued"
      ? { ...step, status }
      : step
  );
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [
    {
      id: "welcome",
      role: "assistant",
      kind: "text",
      content:
        "我会以交易 Agent 的方式执行任务：你可以让我分析股票、扫描机会、管理持仓或检查风险。",
      timestamp: now(),
    },
  ],
  connected: false,
  running: false,
  currentRunId: null,
  setConnected: (connected) => set({ connected }),
  setRunning: (running) => set({ running }),
  setCurrentRunId: (runId) => set({ currentRunId: runId }),
  addMessage: (message) =>
    set((state) => ({
      messages: [
        ...state.messages,
        {
          ...message,
          id: crypto.randomUUID(),
          kind: message.kind ?? "text",
          timestamp: now(),
        },
      ],
    })),
  createTask: (task) =>
    set((state) => {
      const existing = state.messages.some(
        (message) => message.kind === "task" && message.runId === task.runId
      );
      if (existing) return state;
      return {
        messages: [
          ...state.messages,
          {
            id: `task-${task.runId}`,
            role: "assistant",
            kind: "task",
            content: task.title,
            runId: task.runId,
            skillId: task.skillId,
            taskStatus: "running",
            steps: [newStep("已识别任务", task.detail, "completed")],
            result: "",
            timestamp: now(),
          },
        ],
      };
    }),
  addTaskStep: (runId, step) =>
    set((state) => ({
      messages: state.messages.map((message) => {
        if (message.kind !== "task" || message.runId !== runId) return message;
        const nextStatus = step.status ?? "running";
        const isTerminal =
          message.taskStatus === "completed" || message.taskStatus === "failed";
        if (isTerminal && (nextStatus === "running" || nextStatus === "queued")) {
          return message;
        }
        const lastStep = message.steps?.[message.steps.length - 1];
        if (
          lastStep &&
          lastStep.label === step.label &&
          lastStep.detail === step.detail &&
          lastStep.status === nextStatus
        ) {
          return message;
        }

        // If a completed/failed step arrives for the same label as a running step,
        // update the existing running step in-place instead of adding a duplicate.
        if (nextStatus === "completed" || nextStatus === "failed") {
          const existingIdx = message.steps?.findIndex(
            (s) => s.label === step.label && (s.status === "running" || s.status === "queued")
          );
          if (existingIdx !== undefined && existingIdx >= 0 && message.steps) {
            const existingStep = message.steps[existingIdx]!;
            const updatedSteps = [...message.steps];
            updatedSteps[existingIdx] = {
              ...existingStep,
              status: nextStatus,
              detail: step.detail ?? existingStep.detail,
            };
            return {
              ...message,
              taskStatus: nextStatus === "failed" ? "failed" : message.taskStatus ?? "running",
              steps: updatedSteps,
            };
          }
        }

        const closedSteps = closeActiveSteps(
          message.steps,
          nextStatus === "failed" ? "failed" : "completed"
        );
        return {
          ...message,
          taskStatus: nextStatus === "failed" ? "failed" : message.taskStatus ?? "running",
          steps: [
            ...closedSteps,
            newStep(step.label, step.detail, nextStatus),
          ],
        };
      }),
    })),
  appendTaskResult: (runId, content) =>
    set((state) => ({
      messages: state.messages.map((message) => {
        if (message.kind !== "task" || message.runId !== runId) return message;
        const current = message.result?.trim();
        return {
          ...message,
          result: current ? `${current}\n\n${content}` : content,
        };
      }),
    })),
  finishTask: (runId, status, detail) =>
    set((state) => ({
      messages: state.messages.map((message) => {
        if (message.kind !== "task" || message.runId !== runId) return message;
        if (message.taskStatus === status) {
          const lastStep = message.steps?.[message.steps.length - 1];
          const terminalLabel = status === "completed" ? "任务完成" : "任务失败";
          if (lastStep?.label === terminalLabel && lastStep.status === status) {
            return message;
          }
        }
        const closedSteps = closeActiveSteps(
          message.steps,
          status === "failed" ? "failed" : "completed"
        );
        return {
          ...message,
          taskStatus: status,
          steps: [
            ...closedSteps,
            newStep(status === "completed" ? "任务完成" : "任务失败", detail, status),
          ],
        };
      }),
    })),
  reset: () =>
    set({
      messages: [],
      connected: false,
      running: false,
      currentRunId: null,
    }),
}));
