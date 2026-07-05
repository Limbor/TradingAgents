import { useRef, useEffect } from "react";
import { Bot, RadioTower, UserRound } from "lucide-react";
import type { ChatMessage } from "@/stores/useChatStore";
import { TaskCard } from "./TaskCard";

interface MessageListProps {
  messages: ChatMessage[];
  onAnalyze: (symbol: string, selectionContext?: Record<string, unknown>) => void;
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
                className={`max-w-[80%] whitespace-pre-wrap rounded-lg border px-3 py-2 text-sm leading-6 ${
                  message.role === "user"
                    ? "border-teal-500/30 bg-teal-500/15 text-stone-50"
                    : message.role === "system"
                      ? "border-amber-500/20 bg-amber-500/10 text-amber-100"
                      : "border-stone-800 bg-stone-950 text-stone-200"
                }`}
              >
                {message.content}
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
