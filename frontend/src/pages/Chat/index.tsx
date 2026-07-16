import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { chatWsManager } from "@/api/ws";
import { useChatStore } from "@/stores/useChatStore";
import { useChatWebSocket } from "./hooks";
import { MessageList } from "./MessageList";
import { InputBar } from "./InputBar";

export default function Chat() {
  const [input, setInput] = useState("");
  const location = useLocation();
  const navigate = useNavigate();
  const autoSentRef = useRef<string | null>(null);
  const { messages, connected, running } = useChatStore();

  // Wire up WebSocket message handling
  useChatWebSocket();

  const canSend = useMemo(
    () => connected && input.trim().length > 0 && !running,
    [connected, input, running],
  );

  const sendPrompt = useCallback((message: string, context?: Record<string, unknown>) => {
    if (!connected || !message.trim() || running) return;
    useChatStore.getState().addMessage({ role: "user", content: message.trim() });
    chatWsManager.send(message.trim(), context);
    setInput("");
    useChatStore.getState().setRunning(true);
  }, [connected, running]);

  // Auto-send from navigation state (e.g. Dashboard quick action)
  useEffect(() => {
    const state = location.state as { prompt?: string; autoSend?: boolean; context?: Record<string, unknown> } | null;
    const prompt = state?.prompt?.trim();
    if (!prompt || autoSentRef.current === prompt) return;
    if (state?.autoSend && connected && !running) {
      autoSentRef.current = prompt;
      sendPrompt(prompt, state?.context);
      navigate(location.pathname, { replace: true, state: null });
    } else {
      setInput(prompt);
    }
  }, [connected, location.pathname, location.state, navigate, running, sendPrompt]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!canSend) return;
    sendPrompt(input);
  };

  const handleQuickAction = (prompt: string) => {
    if (!prompt) {
      setInput("帮我看看 ");
      return;
    }
    if (connected && !running) {
      sendPrompt(prompt);
    } else {
      setInput(prompt);
    }
  };

  const handleAnalyzeSymbol = (symbol: string, context?: Record<string, unknown>) => {
    sendPrompt(`帮我分析 ${symbol}`, context);
  };

  return (
    <div className="mx-auto flex h-full max-w-5xl flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-stone-800 pb-3">
        <div>
          <h2 className="text-lg font-semibold text-stone-50">Trading Agent</h2>
          <p className="text-xs text-stone-500">
            自然语言驱动: 股票分析 · 选股推荐 · 持仓管理 · 风险监控
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-stone-800 bg-stone-900 px-3 py-1.5 text-xs">
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-teal-300" : "bg-red-300"}`} />
          {connected ? "已连接" : "连接中..."}
        </div>
      </div>

      {/* Messages */}
      <MessageList
        messages={messages}
        onAnalyze={handleAnalyzeSymbol}
        onSendPrompt={sendPrompt}
      />

      {/* Input */}
      <InputBar
        input={input}
        setInput={setInput}
        canSend={canSend}
        running={running}
        onSubmit={submit}
        onQuickAction={handleQuickAction}
      />
    </div>
  );
}
