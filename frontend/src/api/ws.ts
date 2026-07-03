export interface WSMessage {
  type: string;
  run_id: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface AgentStatusPayload {
  agent: string;
  status: "running" | "completed" | "failed";
  duration_ms?: number;
}

export interface ReportChunkPayload {
  section: string;
  content: string;
  is_final: boolean;
}

export interface ToolCallPayload {
  tool: string;
  args: Record<string, unknown>;
}

type WSEventHandler = (message: WSMessage) => void;

function wsBaseUrl(): string {
  const env = (import.meta as unknown as { env?: Record<string, string | boolean | undefined> }).env;
  const explicit = env?.VITE_WS_BASE_URL as string | undefined;
  if (explicit) return explicit.replace(/\/$/, "");

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const isLocalDev =
    Boolean(env?.DEV) &&
    ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
  const backendHost = isLocalDev
    ? `${window.location.hostname === "::1" ? "[::1]" : window.location.hostname}:8422`
    : window.location.host;
  return `${protocol}//${backendHost}`;
}

class WebSocketManager {
  private ws: WebSocket | null = null;
  private handlers: Map<string, Set<WSEventHandler>> = new Map();
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;

  connect(runId: string): void {
    this.reconnectAttempts = 0;
    this._connect(runId);
  }

  private _connect(runId: string): void {
    const url = `${wsBaseUrl()}/ws/run/${runId}`;

    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
    };

    this.ws.onmessage = (event) => {
      try {
        const message: WSMessage = JSON.parse(event.data);
        this.dispatch(message);
      } catch {
        // Ignore malformed messages
      }
    };

    this.ws.onclose = (event) => {
      if (!event.wasClean && this.reconnectAttempts < this.maxReconnectAttempts) {
        this.reconnectAttempts++;
        const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 30000);
        setTimeout(() => this._connect(runId), delay);
      }
    };
  }

  on(eventType: string, handler: WSEventHandler): () => void {
    if (!this.handlers.has(eventType)) {
      this.handlers.set(eventType, new Set());
    }
    this.handlers.get(eventType)!.add(handler);
    return () => {
      this.handlers.get(eventType)?.delete(handler);
    };
  }

  private dispatch(message: WSMessage): void {
    const handlers = this.handlers.get(message.type);
    if (handlers) {
      handlers.forEach((h) => h(message));
    }
    const allHandlers = this.handlers.get("*");
    if (allHandlers) {
      allHandlers.forEach((h) => h(message));
    }
  }

  disconnect(): void {
    this.ws?.close(1000, "Client disconnect");
    this.ws = null;
  }
}

export const wsManager = new WebSocketManager();

type ChatMessageHandler = (message: WSMessage) => void;

class ChatWebSocketManager {
  private ws: WebSocket | null = null;
  private handlers: Set<ChatMessageHandler> = new Set();
  private openHandlers: Set<() => void> = new Set();
  private closeHandlers: Set<() => void> = new Set();
  private reconnectAttempts = 0;
  private reconnectTimer: number | null = null;
  private manuallyClosed = false;

  connect(): void {
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    this.manuallyClosed = false;
    this.ws = new WebSocket(`${wsBaseUrl()}/ws/chat`);

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.openHandlers.forEach((handler) => handler());
    };

    this.ws.onmessage = (event) => {
      try {
        const message: WSMessage = JSON.parse(event.data);
        this.handlers.forEach((handler) => handler(message));
      } catch {
        // Ignore malformed messages
      }
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };

    this.ws.onclose = () => {
      this.closeHandlers.forEach((handler) => handler());
      this.ws = null;
      if (!this.manuallyClosed) {
        this.scheduleReconnect();
      }
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return;
    this.reconnectAttempts += 1;
    const delay = Math.min(1000 * 2 ** Math.min(this.reconnectAttempts, 5), 30000);
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  onMessage(handler: ChatMessageHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  onOpen(handler: () => void): () => void {
    this.openHandlers.add(handler);
    return () => this.openHandlers.delete(handler);
  }

  onClose(handler: () => void): () => void {
    this.closeHandlers.add(handler);
    return () => this.closeHandlers.delete(handler);
  }

  isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  send(message: string): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error("Chat socket is not connected");
    }
    this.ws.send(JSON.stringify({ message }));
  }

  disconnect(): void {
    this.manuallyClosed = true;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close(1000, "Client disconnect");
    this.ws = null;
  }
}

export const chatWsManager = new ChatWebSocketManager();
