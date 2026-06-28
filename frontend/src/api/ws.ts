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
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const url = `${protocol}//${host}/ws/run/${runId}`;

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

  connect(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    this.ws = new WebSocket(`${protocol}//${host}/ws/chat`);

    this.ws.onopen = () => {
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

    this.ws.onclose = () => {
      this.closeHandlers.forEach((handler) => handler());
    };
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
    this.ws?.close(1000, "Client disconnect");
    this.ws = null;
  }
}

export const chatWsManager = new ChatWebSocketManager();
