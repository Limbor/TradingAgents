import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { chatWsManager, type ChatSendOptions } from "@/api/ws";
import type { ChatContext, ChatNavState, IntentHint } from "@/lib/chatNav";
import { analyzeStockHint } from "@/lib/chatNav";
import { useChatStore } from "@/stores/useChatStore";
import { useChatWebSocket } from "./hooks";
import { MessageList } from "./MessageList";
import { InputBar } from "./InputBar";

export default function Chat() {
  const [input, setInput] = useState("");
  const location = useLocation();
  const boundPaperId = new URLSearchParams(location.search).get("paper_session");
  const navigate = useNavigate();
  const autoSentNonceRef = useRef<string | null>(null);
  // Context/hint waiting to ride along with the next manual submit (set when a
  // jump could not auto-send). Cleared whenever the user edits the input.
  const pendingSendRef = useRef<ChatSendOptions | null>(null);
  const { messages, connected, running } = useChatStore();

  // Wire up WebSocket message handling
  useChatWebSocket();

  const sendReady = connected && !running;
  const canSend = useMemo(
    () => sendReady && input.trim().length > 0,
    [sendReady, input],
  );

  const sendPrompt = useCallback((message: string, opts?: ChatSendOptions) => {
    const text = message.trim();
    if (!text) return;
    if (!connected || running) {
      // Never drop silently: prefill the input, keep context/hint pending.
      setInput(text);
      pendingSendRef.current = opts ?? null;
      useChatStore.getState().addMessage({
        role: "system",
        content: running
          ? "当前有任务运行中，已为你填充输入框，任务结束后可直接发送。"
          : "连接尚未就绪，已为你填充输入框，连接恢复后可直接发送。",
      });
      return;
    }
    useChatStore.getState().addMessage({ role: "user", content: text });
    const sendOptions = boundPaperId
      ? { ...opts, context: { ...opts?.context, paper_session_context: { session_id: boundPaperId } } }
      : opts;
    chatWsManager.send(text, sendOptions);
    setInput("");
    pendingSendRef.current = null;
    useChatStore.getState().setRunning(true);
  }, [connected, running, boundPaperId]);

  // Auto-send from navigation state (e.g. Dashboard quick action)
  useEffect(() => {
    const state = location.state as Partial<ChatNavState> | null;
    const prompt = state?.prompt?.trim();
    if (!prompt) return;
    // nonce-based dedup: same prompt via two navigations still fires twice.
    const nonce = state?.nonce ?? `legacy:${prompt}`;
    if (autoSentNonceRef.current === nonce) return;
    const opts: ChatSendOptions = { context: state?.context, intentHint: state?.intentHint };
    if (state?.autoSend) {
      if (!connected) return; // wait for the socket; nonce not consumed yet
      autoSentNonceRef.current = nonce; // mark before send (StrictMode guard)
      sendPrompt(prompt, opts);
      navigate(`${location.pathname}${location.search}`, { replace: true, state: null });
    } else {
      autoSentNonceRef.current = nonce;
      setInput(prompt);
      pendingSendRef.current = opts.context || opts.intentHint ? opts : null;
      navigate(`${location.pathname}${location.search}`, { replace: true, state: null });
    }
  }, [connected, location.pathname, location.search, location.state, navigate, sendPrompt]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!canSend) return;
    sendPrompt(input, pendingSendRef.current ?? undefined);
  };

  const handleInputChange = (value: string) => {
    // Manual edits invalidate any pending page context/hint.
    pendingSendRef.current = null;
    setInput(value);
  };

  const handleQuickAction = (prompt: string, hint?: IntentHint) => {
    if (!prompt) {
      setInput("帮我看看 ");
      pendingSendRef.current = null;
      return;
    }
    sendPrompt(prompt, hint ? { intentHint: hint } : undefined);
  };

  const handleAnalyzeSymbol = (symbol: string, context?: Record<string, unknown>) => {
    sendPrompt(`帮我分析 ${symbol}`, {
      context: context as ChatContext | undefined,
      intentHint: analyzeStockHint(symbol),
    });
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
          {boundPaperId && <p className="mt-1 text-xs text-teal-300">已绑定策略模拟盘 {boundPaperId} · 可追问净值、成交和下一日计划</p>}
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-stone-800 bg-stone-900 px-3 py-1.5 text-xs">
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-teal-300" : "bg-red-300"}`} />
          {connected ? "已连接" : "连接中..."}
        </div>
      </div>

      {/* Messages */}
      <MessageList
        messages={messages}
        sendReady={sendReady}
        onAnalyze={handleAnalyzeSymbol}
        onSendPrompt={sendPrompt}
        onPrefillInput={handleInputChange}
      />

      {/* Input */}
      <InputBar
        input={input}
        setInput={handleInputChange}
        canSend={canSend}
        running={running}
        onSubmit={submit}
        onQuickAction={handleQuickAction}
      />
    </div>
  );
}
