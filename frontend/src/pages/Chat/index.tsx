import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { chatWsManager, type ChatSendOptions } from "@/api/ws";
import type { ChatContext, ChatNavState, IntentHint } from "@/lib/chatNav";
import { analyzeStockHint } from "@/lib/chatNav";
import { useChatStore } from "@/stores/useChatStore";
import { useChatWebSocket } from "./hooks";
import { MessageList } from "./MessageList";
import { InputBar } from "./InputBar";
import { CaseInspector } from "./CaseInspector";

interface ChatProps {
  paperSessionId?: string;
  embedded?: boolean;
  promptRequest?: { text: string; nonce: number };
}

export default function Chat({ paperSessionId, embedded = false, promptRequest }: ChatProps = {}) {
  const [input, setInput] = useState("");
  const location = useLocation();
  const boundPaperId = paperSessionId ?? new URLSearchParams(location.search).get("paper_session");
  const scope = boundPaperId ? `paper:${boundPaperId}` : "general";
  const navigate = useNavigate();
  const autoSentNonceRef = useRef<string | null>(null);
  const paperPromptNonceRef = useRef<number | null>(null);
  // Context/hint waiting to ride along with the next manual submit (set when a
  // jump could not auto-send). Cleared whenever the user edits the input.
  const pendingSendRef = useRef<ChatSendOptions | null>(null);
  const { messages, connected, running, setActiveScope, setResponseScope } = useChatStore();
  const visibleMessages = useMemo(() => messages.filter((message) =>
    scope === "general" ? !message.scope || message.scope === "general" : message.scope === scope,
  ), [messages, scope]);

  // Wire up WebSocket message handling
  useChatWebSocket();

  useEffect(() => {
    setActiveScope(scope);
  }, [scope, setActiveScope]);

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
    setActiveScope(scope);
    setResponseScope(scope);
    useChatStore.getState().addMessage({ role: "user", content: text, scope });
    const sendOptions = boundPaperId
      ? { ...opts, context: { ...opts?.context, paper_session_context: { session_id: boundPaperId } } }
      : opts;
    chatWsManager.send(text, sendOptions);
    setInput("");
    pendingSendRef.current = null;
    useChatStore.getState().setRunning(true);
  }, [connected, running, boundPaperId, scope, setActiveScope, setResponseScope]);

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

  useEffect(() => {
    if (!promptRequest || !connected || paperPromptNonceRef.current === promptRequest.nonce) return;
    paperPromptNonceRef.current = promptRequest.nonce;
    sendPrompt(promptRequest.text);
  }, [promptRequest, connected, sendPrompt]);

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
    <div className={`mx-auto flex min-h-0 w-full flex-col ${embedded ? "h-full" : "h-full max-w-[1600px]"}`}>
      {!embedded && <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-teal-300">Trading agent</p>
          <h2 className="mt-1 text-2xl font-semibold text-stone-50">交易 Agent</h2>
          <p className="mt-1 text-sm text-stone-400">提出交易目标，跟踪执行步骤、证据来源和判断结果。</p>
        </div>
        {boundPaperId && <Link className="rounded-xl border border-teal-500/30 bg-teal-500/10 px-3 py-2 text-xs text-teal-300" to={`/paper?session=${encodeURIComponent(boundPaperId)}`}>返回模拟盘 · {boundPaperId}</Link>}
      </div>}
      <div className={`grid min-h-0 flex-1 gap-4 ${embedded ? "grid-cols-1" : "grid-cols-1 lg:grid-cols-[minmax(0,1fr)_320px]"}`}>
      <section aria-label="交易 Agent 对话" className="flex min-h-[520px] min-w-0 flex-col rounded-2xl border border-stone-800 bg-stone-900/70">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-stone-800 px-4 py-3">
          <div><h3 className="text-sm font-semibold text-stone-100">{embedded ? "交易 Agent" : "当前对话"}</h3><p className="mt-0.5 text-xs text-stone-500">{boundPaperId ? `已绑定策略模拟盘 ${boundPaperId}` : "分析 · 取证 · 风险核对"}</p></div>
          <div className="flex items-center gap-2 rounded-full border border-stone-800 bg-stone-950 px-3 py-1.5 text-xs">
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-teal-300" : "bg-red-300"}`} />
          {connected ? "已连接" : "连接中..."}
          </div>
        </div>
      {boundPaperId && visibleMessages.length === 0 ? <div className="flex min-h-0 flex-1 flex-col justify-center gap-5 overflow-y-auto p-5"><div className="rounded-2xl border border-teal-500/20 bg-teal-500/5 p-4"><p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal-300">Current session</p><h4 className="mt-2 text-base font-semibold text-stone-100">从这个模拟盘开始提问</h4><p className="mt-2 text-sm leading-6 text-stone-400">Agent 会读取当前会话的策略决策、持仓、成交和下一日计划，并在回答中标出数据时点和来源。</p></div><div className="space-y-2">{["总结当前权益、持仓和近期成交", "解释下一交易日计划", "当前策略切换的依据是什么"].map((prompt) => <button key={prompt} onClick={() => sendPrompt(prompt)} className="block w-full rounded-xl border border-stone-800 bg-stone-950/50 px-3 py-2.5 text-left text-sm text-stone-300 transition hover:border-teal-500/40 hover:text-teal-200">{prompt} →</button>)}</div></div> : <MessageList
        messages={visibleMessages}
        sendReady={sendReady}
        onAnalyze={handleAnalyzeSymbol}
        onSendPrompt={sendPrompt}
        onPrefillInput={handleInputChange}
      />}

      <div className="px-4 pb-3"><InputBar
        input={input}
        setInput={handleInputChange}
        canSend={canSend}
        running={running}
        onSubmit={submit}
        onQuickAction={handleQuickAction}
        paperMode={Boolean(boundPaperId)}
      /></div>
      </section>
      {!embedded && <CaseInspector messages={visibleMessages} paperSessionId={boundPaperId} running={running} />}
      </div>
    </div>
  );
}
