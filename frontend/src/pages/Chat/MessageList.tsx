import { useLayoutEffect, useRef } from "react";
import { Bot, RadioTower, UserRound, Wrench } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "@/stores/useChatStore";
import { TaskCard } from "./TaskCard";
import { ToolCard } from "./ToolCard";

interface MessageListProps {
  messages: ChatMessage[];
  sendReady: boolean;
  onAnalyze: (symbol: string, context?: Record<string, unknown>) => void;
  onSendPrompt: (prompt: string) => void;
  onPrefillInput: (text: string) => void;
}

type MessageGroup =
  | { type: "single"; message: ChatMessage }
  | { type: "tools"; id: string; messages: ChatMessage[] };

/**
 * Collapse consecutive tool messages into one group so back-to-back tool
 * replies render as a single stacked container instead of separate bubbles.
 * Pure render-layer transform; the store keeps flat messages.
 */
export function groupMessages(messages: ChatMessage[]): MessageGroup[] {
  const groups: MessageGroup[] = [];
  for (const message of messages) {
    const last = groups[groups.length - 1];
    if (message.kind === "tool") {
      if (last && last.type === "tools") {
        last.messages.push(message);
      } else {
        groups.push({ type: "tools", id: message.id, messages: [message] });
      }
    } else {
      groups.push({ type: "single", message });
    }
  }
  // A lone tool message keeps its regular card look.
  return groups.map((group) => {
    const only =
      group.type === "tools" && group.messages.length === 1 ? group.messages[0] : undefined;
    return only ? { type: "single" as const, message: only } : group;
  });
}

export function MessageList({ messages, sendReady, onAnalyze, onSendPrompt, onPrefillInput }: MessageListProps) {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const initiallyPositionedRef = useRef(false);
  const pinnedToBottomRef = useRef(true);
  const previousLastMessageIdRef = useRef<string | undefined>(undefined);

  useLayoutEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    const lastMessage = messages[messages.length - 1];
    const sentNewUserMessage =
      lastMessage?.role === "user" && lastMessage.id !== previousLastMessageIdRef.current;
    previousLastMessageIdRef.current = lastMessage?.id;

    if (!initiallyPositionedRef.current) {
      // Position before the first paint. scrollIntoView would also scroll the
      // outer AppShell and visibly animate the whole Chat page top -> bottom.
      container.scrollTop = container.scrollHeight;
      initiallyPositionedRef.current = true;
      pinnedToBottomRef.current = true;
      return;
    }

    // A send creates several rapid updates (user message, task card, progress
    // steps, streamed result). Starting a smooth scroll for every update makes
    // those animations fight and visibly bounce at the bottom. Pin immediately
    // instead, before paint, while still letting users read older messages.
    if (sentNewUserMessage) pinnedToBottomRef.current = true;
    if (pinnedToBottomRef.current) {
      container.scrollTop = container.scrollHeight;
    }
  }, [messages]);

  const handleScroll = () => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const distanceFromBottom =
      container.scrollHeight - container.clientHeight - container.scrollTop;
    pinnedToBottomRef.current = distanceFromBottom <= 48;
  };

  return (
    <div
      ref={scrollContainerRef}
      data-testid="message-scroll-container"
      onScroll={handleScroll}
      style={{ overflowAnchor: "none" }}
      className="min-h-0 flex-1 overflow-y-auto py-4"
    >
      <div className="space-y-4">
        {groupMessages(messages).map((group) => {
          if (group.type === "tools") {
            return (
              <div
                key={group.id}
                className="ml-11 max-w-[85%] rounded-lg border border-ui-line bg-ui-subtle/60"
              >
                <div className="flex items-center gap-1.5 border-b border-ui-line px-3 py-1.5 text-xs text-ui-muted">
                  <Wrench className="h-3.5 w-3.5 text-indigo-300/80" />
                  <span>工具调用 ×{group.messages.length}</span>
                </div>
                <div className="divide-y divide-ui-line">
                  {group.messages.map((message) => (
                    <ToolCard key={message.id} message={message} bare />
                  ))}
                </div>
              </div>
            );
          }
          const message = group.message;
          return message.kind === "task" ? (
            <TaskCard
              key={message.id}
              message={message}
              onAnalyze={onAnalyze}
              onSendPrompt={onSendPrompt}
              onPrefillInput={onPrefillInput}
            />
          ) : message.kind === "tool" ? (
            <ToolCard key={message.id} message={message} />
          ) : (
            <div
              key={message.id}
              className={`flex gap-3 ${message.role === "user" ? "justify-end" : "justify-start"}`}
            >
              {message.role !== "user" && (
                <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-ui-accent/30 bg-ui-accent/10">
                  {message.role === "system" ? (
                    <RadioTower className="h-4 w-4 text-ui-warning" />
                  ) : (
                    <Bot className="h-4 w-4 text-ui-accent" />
                  )}
                </div>
              )}
              <div
                className={`max-w-[80%] rounded-lg border px-3 py-2 text-sm leading-6 ${
                  message.role === "user"
                    ? "border-ui-accent/30 bg-ui-accent/15 text-ui-ink"
                    : message.role === "system"
                      ? "border-ui-warning/20 bg-ui-warning/10 text-ui-warning"
                      : "border-ui-line bg-ui-subtle text-ui-body"
                }`}
              >
                <AssistantContent message={message} sendReady={sendReady} onSendPrompt={onSendPrompt} />
              </div>
              {message.role === "user" && (
                <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-ui-strong bg-ui-subtle">
                  <UserRound className="h-4 w-4 text-ui-body" />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AssistantContent({
  message,
  sendReady,
  onSendPrompt,
}: {
  message: ChatMessage;
  sendReady: boolean;
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
              className={`inline-block rounded-full border border-ui-accent/30 bg-ui-accent/10 px-2.5 py-0.5 text-xs text-ui-accent transition-colors hover:bg-ui-accent/25 hover:border-ui-accent/50 ${
                sendReady ? "" : "opacity-50"
              }`}
            >
              {opt}
            </button>
          ))}
        </div>
      )}
      {citations && citations.length > 0 && (
        <div className="mt-2 pt-2 border-t border-ui-strong space-y-1">
          <div className="text-xs text-ui-muted">
            数据来源：
            {citations.map((c, i) => (
              <span key={i} className="ml-1 text-ui-accent/70">
                {c.source || c.tool}
                {c.as_of_date ? ` · 基准日 ${c.as_of_date}` : ""}
                {c.summary ? ` (${c.summary})` : ""}
                {i < citations.length - 1 ? ", " : ""}
              </span>
            ))}
          </div>
          {citations.some((c) => c.warnings && c.warnings.length > 0) && (
            <div className="text-xs text-ui-warning/80">
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
