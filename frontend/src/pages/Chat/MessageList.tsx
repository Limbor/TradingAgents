import { useRef, useEffect } from "react";
import { Bot, RadioTower, UserRound } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "@/stores/useChatStore";
import { TaskCard } from "./TaskCard";
import { ToolCard } from "./ToolCard";

interface MessageListProps {
  messages: ChatMessage[];
  onAnalyze: (symbol: string, context?: Record<string, unknown>) => void;
  onSendPrompt: (prompt: string) => void;
}

export function MessageList({ messages, onAnalyze, onSendPrompt }: MessageListProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto py-4">
      <div className="space-y-4">
        {messages.map((message) =>
          message.kind === "task" ? (
            <TaskCard
              key={message.id}
              message={message}
              onAnalyze={onAnalyze}
              onSendPrompt={onSendPrompt}
            />
          ) : message.kind === "tool" ? (
            <ToolCard key={message.id} message={message} />
          ) : (
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
                className={`max-w-[80%] rounded-lg border px-3 py-2 text-sm leading-6 ${
                  message.role === "user"
                    ? "border-teal-500/30 bg-teal-500/15 text-stone-50"
                    : message.role === "system"
                      ? "border-amber-500/20 bg-amber-500/10 text-amber-100"
                      : "border-stone-800 bg-stone-950 text-stone-200"
                }`}
              >
                <AssistantContent message={message} onSendPrompt={onSendPrompt} />
              </div>
              {message.role === "user" && (
                <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-stone-700 bg-stone-950">
                  <UserRound className="h-4 w-4 text-stone-300" />
                </div>
              )}
            </div>
          )
        )}
        <div ref={messagesEndRef} />
      </div>
    </div>
  );
}

function AssistantContent({
  message,
  onSendPrompt,
}: {
  message: ChatMessage;
  onSendPrompt: (prompt: string) => void;
}) {
  const { content, citations, clarifyOptions, role } = message;

  return (
    <>
      {/* User input is rendered as plain text (never markdown-rendered) to
          avoid injecting untrusted markup/links. Assistant & system replies
          are markdown-rendered so **bold**, lists, tables, code render. */}
      {role === "user" ? (
        <div className="whitespace-pre-wrap">{content}</div>
      ) : (
        <div className="prose prose-invert prose-sm max-w-none whitespace-pre-wrap break-words">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      )}
      {clarifyOptions && clarifyOptions.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {clarifyOptions.map((opt, i) => (
            <button
              key={i}
              type="button"
              onClick={() => onSendPrompt(opt)}
              className="inline-block rounded-full border border-teal-500/30 bg-teal-500/10 px-2.5 py-0.5 text-xs text-teal-300 transition-colors hover:bg-teal-500/25 hover:border-teal-500/50"
            >
              {opt}
            </button>
          ))}
        </div>
      )}
      {citations && citations.length > 0 && (
        <div className="mt-2 pt-2 border-t border-stone-700 space-y-1">
          <div className="text-xs text-stone-400">
            数据来源：
            {citations.map((c, i) => (
              <span key={i} className="ml-1 text-teal-400/70">
                {c.source || c.tool}
                {c.as_of_date ? ` · 基准日 ${c.as_of_date}` : ""}
                {c.summary ? ` (${c.summary})` : ""}
                {i < citations.length - 1 ? ", " : ""}
              </span>
            ))}
          </div>
          {citations.some((c) => c.warnings && c.warnings.length > 0) && (
            <div className="text-xs text-amber-300/80">
              {citations
                .flatMap((c) => c.warnings || [])
                .map((w, i) => (
                  <span key={i} className="mr-2">⚠ {w}</span>
                ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}
