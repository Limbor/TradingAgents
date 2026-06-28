import { create } from "zustand";

export type ChatRole = "user" | "assistant" | "system";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  runId?: string;
  skillId?: string;
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
  reset: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [
    {
      id: "welcome",
      role: "assistant",
      content:
        "Tell me what you want to do: analyze a stock, scan the market, or manage portfolio holdings.",
      timestamp: new Date().toISOString(),
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
          timestamp: new Date().toISOString(),
        },
      ],
    })),
  reset: () =>
    set({
      messages: [],
      connected: false,
      running: false,
      currentRunId: null,
    }),
}));
