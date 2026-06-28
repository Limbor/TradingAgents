import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Bot, BrainCircuit, RadioTower, Send, UserRound } from "lucide-react";
import { chatWsManager, type WSMessage } from "@/api/ws";
import { useChatStore } from "@/stores/useChatStore";

const EXAMPLES = [
  "帮我看看茅台",
  "筛选A股 top 3 score 60",
  "添加持仓 601899.SH 100 18 20",
  "分析一下当前持仓风险",
];

export default function Chat() {
  const [input, setInput] = useState("");
  const {
    messages,
    connected,
    running,
    currentRunId,
    setConnected,
    setRunning,
    setCurrentRunId,
    addMessage,
  } = useChatStore();

  useEffect(() => {
    const offOpen = chatWsManager.onOpen(() => setConnected(true));
    const offClose = chatWsManager.onClose(() => setConnected(false));
    const offMessage = chatWsManager.onMessage((message: WSMessage) => {
      if (message.type === "chat_reply") {
        const payload = message.payload;
        setRunning(Boolean(payload.run_id));
        setCurrentRunId((payload.run_id as string | undefined) ?? null);
        addMessage({
          role: "assistant",
          content: [
            payload.content as string,
            payload.skill_triggered ? `Skill: ${payload.skill_triggered}` : "",
            payload.params ? `Params: ${JSON.stringify(payload.params)}` : "",
          ]
            .filter(Boolean)
            .join("\n"),
          runId: payload.run_id as string | undefined,
          skillId: payload.skill_triggered as string | undefined,
        });
      } else if (message.type === "report_chunk") {
        addMessage({
          role: "assistant",
          content: message.payload.content as string,
          runId: message.run_id,
        });
      } else if (message.type === "scanner_candidates") {
        const count = (message.payload.candidates as unknown[] | undefined)?.length ?? 0;
        addMessage({
          role: "system",
          content: `Scanner produced ${count} candidate(s).`,
          runId: message.run_id,
        });
      } else if (message.type === "portfolio_update") {
        addMessage({
          role: "system",
          content: `Portfolio update: ${JSON.stringify(message.payload)}`,
          runId: message.run_id,
        });
      } else if (message.type === "run_complete") {
        setRunning(false);
        addMessage({
          role: "system",
          content: "Run complete.",
          runId: message.run_id,
        });
      } else if (message.type === "error") {
        setRunning(false);
        addMessage({
          role: "system",
          content: `Error: ${message.payload.message ?? "Run failed"}`,
          runId: message.run_id,
        });
      }
    });

    chatWsManager.connect();
    if (chatWsManager.isOpen()) setConnected(true);

    return () => {
      offOpen();
      offClose();
      offMessage();
    };
  }, [addMessage, setConnected, setCurrentRunId, setRunning]);

  const canSend = useMemo(() => connected && input.trim().length > 0 && !running, [
    connected,
    input,
    running,
  ]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!canSend) return;
    const message = input.trim();
    addMessage({ role: "user", content: message });
    chatWsManager.send(message);
    setInput("");
    setRunning(true);
  };

  return (
    <div className="mx-auto flex h-full max-w-7xl flex-col gap-4">
      <div className="flex items-center justify-between border-b border-stone-800 pb-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-teal-300">
            Natural language router
          </p>
          <h2 className="mt-2 text-xl font-semibold text-stone-50">Chat command center</h2>
          <p className="mt-1 text-sm text-stone-500">
            Route intent to stock analysis, portfolio management, or market scanning.
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-stone-800 bg-stone-900 px-3 py-2 text-xs">
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-teal-300" : "bg-red-300"}`} />
          {connected ? "Connected" : "Connecting"}
        </div>
      </div>

      <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <section className="flex min-h-0 flex-col rounded-lg border border-stone-800 bg-stone-900">
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
            {messages.map((message) => (
              <div
                key={message.id}
                className={`flex gap-3 ${message.role === "user" ? "justify-end" : "justify-start"}`}
              >
                {message.role !== "user" && (
                  <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-teal-500/30 bg-teal-500/10">
                    {message.role === "system" ? (
                      <RadioTower className="h-4 w-4 text-amber-300" />
                    ) : (
                      <Bot className="h-4 w-4 text-teal-300" />
                    )}
                  </div>
                )}
                <div
                  className={`max-w-[78%] whitespace-pre-wrap rounded-lg border px-3 py-2 text-sm leading-6 ${
                    message.role === "user"
                      ? "border-teal-500/30 bg-teal-500/15 text-stone-50"
                      : message.role === "system"
                        ? "border-amber-500/20 bg-amber-500/10 text-amber-100"
                        : "border-stone-800 bg-stone-950 text-stone-200"
                  }`}
                >
                  {message.content}
                  {message.runId && (
                    <div className="mt-2 border-t border-stone-800 pt-2 text-xs text-stone-500">
                      <Link to={`/analysis/${message.runId}`} className="text-teal-300 hover:text-teal-200">
                        Open run {message.runId.slice(0, 8)}
                      </Link>
                    </div>
                  )}
                </div>
                {message.role === "user" && (
                  <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-stone-700 bg-stone-950">
                    <UserRound className="h-4 w-4 text-stone-300" />
                  </div>
                )}
              </div>
            ))}
          </div>
          <form onSubmit={submit} className="border-t border-stone-800 p-3">
            <div className="flex gap-2">
              <input
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder="例如：帮我看看茅台 / 筛选A股 top 3 / 添加持仓 601899.SH 100 18 20"
                className="min-w-0 flex-1 rounded-lg border border-stone-700 bg-stone-950 px-3 py-2 text-sm outline-none transition focus:border-teal-400"
              />
              <button
                disabled={!canSend}
                className="inline-flex items-center gap-2 rounded-lg bg-teal-500 px-4 py-2 text-sm font-semibold text-stone-950 transition hover:bg-teal-400 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Send className="h-4 w-4" />
                Send
              </button>
            </div>
          </form>
        </section>

        <aside className="rounded-lg border border-stone-800 bg-stone-900 p-4">
          <div className="mb-4 flex items-center gap-2">
            <BrainCircuit className="h-4 w-4 text-teal-300" />
            <h3 className="text-sm font-semibold text-stone-100">Examples</h3>
          </div>
          <div className="space-y-2">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                onClick={() => setInput(example)}
                className="w-full rounded-lg border border-stone-800 bg-stone-950 p-3 text-left text-sm text-stone-300 transition hover:border-teal-500/50 hover:text-stone-50"
              >
                {example}
              </button>
            ))}
          </div>
          {currentRunId && (
            <div className="mt-4 rounded-lg border border-stone-800 bg-stone-950 p-3 text-xs text-stone-500">
              Current run:{" "}
              <Link to={`/analysis/${currentRunId}`} className="font-mono text-teal-300">
                {currentRunId.slice(0, 8)}
              </Link>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
