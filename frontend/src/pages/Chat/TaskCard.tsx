import React from "react";
import { Link } from "react-router-dom";
import { Bot, CheckCircle2, ExternalLink, Loader2, XCircle } from "lucide-react";
import type { ChatMessage, ChatTaskStatus } from "@/stores/useChatStore";
import {
  CandidateTable,
  parseCandidates,
  RiskAlertList,
  parseRisks,
  AnalysisSummaryCard,
  parseAnalysisSummary,
  parseAnalysisSummaryFromStructured,
} from "@/components/Chat";

interface TaskCardProps {
  message: ChatMessage;
  onAnalyze: (symbol: string, selectionContext?: Record<string, unknown>) => void;
  onSendPrompt: (prompt: string) => void;
}

export const TaskCard = React.memo(function TaskCard({
  message,
  onAnalyze,
  onSendPrompt,
}: TaskCardProps) {
  const status = message.taskStatus ?? "running";
  return (
    <div className="flex justify-start gap-3">
      <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-teal-500/30 bg-teal-500/10">
        <Bot className="h-4 w-4 text-teal-300" />
      </div>
      <div className="w-full max-w-[90%] rounded-lg border border-stone-800 bg-stone-950 p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-stone-50">{message.content}</div>
            {message.skillId && (
              <div className="mt-0.5 text-xs text-stone-500">{message.skillId}</div>
            )}
          </div>
          <StatusBadge status={status} />
        </div>

        <div className="mt-3 space-y-1.5">
          {(message.steps ?? []).map((step) => (
            <div key={step.id} className="flex gap-2 text-xs">
              <StepIcon status={step.status} />
              <div className="min-w-0">
                <span className="text-stone-200">{step.label}</span>
                {step.detail && <span className="ml-2 text-stone-500">{step.detail}</span>}
              </div>
            </div>
          ))}
        </div>

        {message.result?.trim() && (
          <div className="mt-3">
            <TaskResult
              result={message.result}
              runId={message.runId}
              skillId={message.skillId}
              onAnalyze={onAnalyze}
              onSendPrompt={onSendPrompt}
            />
          </div>
        )}

        {message.runId && (
          <div className="mt-3 flex items-center justify-between border-t border-stone-800 pt-2 text-xs">
            <span className="font-mono text-stone-500">run {message.runId.slice(0, 8)}</span>
            <Link
              to={`/library?run_id=${message.runId}`}
              className="inline-flex items-center gap-1 text-teal-300 hover:text-teal-200"
            >
              产物
              <ExternalLink className="h-3.5 w-3.5" />
            </Link>
          </div>
        )}
      </div>
    </div>
  );
});

/* --- TaskResult --- */

function TaskResult({
  result,
  runId,
  skillId,
  onAnalyze,
  onSendPrompt,
}: {
  result: string;
  runId?: string;
  skillId?: string;
  onAnalyze: (symbol: string, selectionContext?: Record<string, unknown>) => void;
  onSendPrompt: (prompt: string) => void;
}) {
  const structured = tryParseStructured(result);

  if (structured?.type === "analysis_summary") {
    const summary = parseAnalysisSummaryFromStructured(structured.data, runId);
    if (summary) {
      return (
        <div className="space-y-3">
          <AnalysisSummaryCard
            summary={summary}
            selectionPlan={structured.selectionContext}
            artifactId={structured.artifactId}
            onAddHolding={(symbol) => onSendPrompt(`添加持仓 ${symbol} 100 0 0`)}
            onAnalyzeMore={(symbol) => onSendPrompt(`对比 ${symbol} 同行业标的`)}
          />
          {structured.remainingText.trim() && (
            <details className="group">
              <summary className="cursor-pointer text-xs text-stone-500 hover:text-stone-300">
                展开完整报告文本
              </summary>
              <div className="mt-2 max-h-60 overflow-y-auto rounded-lg border border-stone-800 bg-stone-900 p-3 whitespace-pre-wrap text-sm leading-6 text-stone-300">
                {structured.remainingText}
              </div>
            </details>
          )}
        </div>
      );
    }
  }

  if (structured?.type === "candidates") {
    const candidates = parseCandidates(structured.data);
    return (
      <div className="space-y-3">
        <CandidateTable
          candidates={candidates}
          warnings={structured.warnings}
          asOfDate={structured.asOfDate}
          dataWindowNote={structured.dataWindowNote}
          sessionState={structured.sessionState}
          onAnalyze={onAnalyze}
          actionContext={{ runId }}
        />
        {structured.remainingText.trim() && (
          <details className="group">
            <summary className="cursor-pointer text-xs text-stone-500 hover:text-stone-300">
              展开选股报告正文
            </summary>
            <div className="mt-2 max-h-60 overflow-y-auto rounded-lg border border-stone-800 bg-stone-900 p-3 whitespace-pre-wrap text-sm leading-6 text-stone-300">
              {structured.remainingText}
            </div>
          </details>
        )}
      </div>
    );
  }

  if (structured?.type === "risks") {
    const risks = parseRisks(structured.data);
    return (
      <div className="space-y-3">
        <RiskAlertList risks={risks} onAnalyze={onAnalyze} />
        {structured.remainingText.trim() && (
          <details className="group">
            <summary className="cursor-pointer text-xs text-stone-500 hover:text-stone-300">
              展开风险报告正文
            </summary>
            <div className="mt-2 max-h-60 overflow-y-auto rounded-lg border border-stone-800 bg-stone-900 p-3 whitespace-pre-wrap text-sm leading-6 text-stone-300">
              {structured.remainingText}
            </div>
          </details>
        )}
      </div>
    );
  }

  if (skillId === "stock_analysis" && result.length > 100) {
    const summary = parseAnalysisSummary(result, runId);
    if (summary) {
      return (
        <div className="space-y-3">
          <AnalysisSummaryCard
            summary={summary}
            onAddHolding={(symbol) => onSendPrompt(`添加持仓 ${symbol} 100 0 0`)}
            onAnalyzeMore={(symbol) => onSendPrompt(`对比 ${symbol} 同行业标的`)}
          />
          <details className="group">
            <summary className="cursor-pointer text-xs text-stone-500 hover:text-stone-300">
              展开完整报告文本
            </summary>
            <div className="mt-2 max-h-60 overflow-y-auto rounded-lg border border-stone-800 bg-stone-900 p-3 whitespace-pre-wrap text-sm leading-6 text-stone-300">
              {result}
            </div>
          </details>
        </div>
      );
    }
  }

  const remainingText = structured?.remainingText?.trim();
  return (
    <div className="max-h-80 overflow-y-auto rounded-lg border border-stone-800 bg-stone-900 p-3 whitespace-pre-wrap text-sm leading-6 text-stone-200">
      {remainingText || result}
    </div>
  );
}

function tryParseStructured(result: string): {
  type: string;
  data: unknown;
  warnings?: string[];
  asOfDate?: string;
  dataWindowNote?: string;
  sessionState?: string;
  selectionContext?: Record<string, unknown>;
  artifactId?: string;
  remainingText: string;
} | null {
  const extract = (parsed: Record<string, unknown>) => ({
    warnings: Array.isArray(parsed.warnings) ? (parsed.warnings as string[]) : undefined,
    asOfDate: typeof parsed.asOfDate === "string" ? parsed.asOfDate : undefined,
    dataWindowNote: typeof parsed.dataWindowNote === "string" ? parsed.dataWindowNote : undefined,
    selectionContext:
      parsed.selectionContext && typeof parsed.selectionContext === "object"
        ? (parsed.selectionContext as Record<string, unknown>)
        : undefined,
    artifactId: typeof parsed.artifactId === "string" ? parsed.artifactId : undefined,
    sessionState: typeof parsed.sessionState === "string" ? parsed.sessionState : undefined,
  });
  const lines = result.split("\n");
  for (const [index, line] of lines.entries()) {
    const trimmed = line.trim();
    if (trimmed.startsWith("{") && trimmed.includes("__type")) {
      try {
        const parsed = JSON.parse(trimmed) as Record<string, unknown>;
        if (parsed.__type && parsed.data) {
          return {
            type: String(parsed.__type),
            data: parsed.data,
            ...extract(parsed),
            remainingText: lines.filter((_, lineIndex) => lineIndex !== index).join("\n").trim(),
          };
        }
      } catch {
        // not JSON
      }
    }
  }
  try {
    const parsed = JSON.parse(result.trim()) as Record<string, unknown>;
    if (parsed.__type && parsed.data) {
      return { type: String(parsed.__type), data: parsed.data, ...extract(parsed), remainingText: "" };
    }
  } catch {
    // not JSON
  }
  return null;
}

/* --- Sub-components --- */

function StatusBadge({ status }: { status: ChatTaskStatus }) {
  const copy = {
    queued: "排队中",
    running: "运行中",
    completed: "完成",
    failed: "失败",
  }[status];
  const tone =
    status === "completed"
      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
      : status === "failed"
        ? "border-red-500/30 bg-red-500/10 text-red-300"
        : "border-teal-500/30 bg-teal-500/10 text-teal-300";
  return <span className={`shrink-0 rounded border px-2 py-1 text-xs ${tone}`}>{copy}</span>;
}

function StepIcon({ status }: { status: ChatTaskStatus }) {
  if (status === "completed") return <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-300" />;
  if (status === "failed") return <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-300" />;
  return <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-teal-300" />;
}
