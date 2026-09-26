import React, { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";
import remarkGfm from "remark-gfm";
import { Bot, CheckCircle2, ChevronDown, ExternalLink, Loader2, XCircle } from "lucide-react";
import { upsertHolding } from "@/api/client";
import { useChatStore } from "@/stores/useChatStore";
import type { ChatMessage, ChatTaskStatus, ChatTaskStep } from "@/stores/useChatStore";
import {
  CandidateTable,
  parseCandidates,
  RiskAlertList,
  parseRisks,
  AnalysisSummaryCard,
  parseAnalysisSummary,
  parseAnalysisSummaryFromStructured,
  PositionAdviceCard,
  type AnalysisSummary,
} from "@/components/Chat";

interface TaskCardProps {
  message: ChatMessage;
  onAnalyze: (symbol: string, context?: Record<string, unknown>) => void;
  onSendPrompt: (prompt: string) => void;
  onPrefillInput: (text: string) => void;
}

export const TaskCard = React.memo(function TaskCard({
  message,
  onAnalyze,
  onSendPrompt,
  onPrefillInput,
}: TaskCardProps) {
  const status = message.taskStatus ?? "running";
  return (
    <div className="flex justify-start gap-3">
      <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-ui-accent/30 bg-ui-accent/10">
        <Bot className="h-4 w-4 text-ui-accent" />
      </div>
      <div className="w-full max-w-[90%] rounded-lg border border-ui-line bg-ui-subtle p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-ui-ink">{message.content}</div>
            {message.skillId && (
              <div className="mt-0.5 text-xs text-ui-faint">{message.skillId}</div>
            )}
          </div>
          <StatusBadge status={status} />
        </div>

        <StepTimeline steps={message.steps ?? []} taskStatus={status} />

        {message.result?.trim() && (
          <div className="mt-3">
            <TaskResult
              result={message.result}
              runId={message.runId}
              skillId={message.skillId}
              onAnalyze={onAnalyze}
              onSendPrompt={onSendPrompt}
              onPrefillInput={onPrefillInput}
            />
          </div>
        )}

        {message.runId && (
          <div className="mt-3 flex items-center justify-between border-t border-ui-line pt-2 text-xs">
            <span className="font-mono text-ui-faint">run {message.runId.slice(0, 8)}</span>
            <Link
              to={`/library?run_id=${message.runId}`}
              className="inline-flex items-center gap-1 text-ui-accent hover:text-ui-accent"
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

interface HoldingDraft {
  symbol: string;
  quantity: string;
  avgCost: string;
}

/** Mid of the analysis entry zone, falling back to the target price. */
function deriveEntryPrice(summary: AnalysisSummary): number | null {
  const action = summary.plan?.plan_action;
  if (action === "REDUCE" || action === "EXIT" || action === "HOLD") return null;
  const rating = (summary.rating ?? "").toLowerCase();
  if (rating.includes("sell") || rating.includes("underweight")) return null;
  const zone = summary.plan?.action_zone ?? summary.plan?.entry_zone;
  if (zone && zone.length > 0) {
    const mid = (Math.min(...zone) + Math.max(...zone)) / 2;
    if (Number.isFinite(mid) && mid > 0) return Number(mid.toFixed(2));
  }
  const target = Number(String(summary.target_price ?? "").replace(/[^\d.]/g, ""));
  if (Number.isFinite(target) && target > 0) return target;
  return null;
}

function TaskResult({
  result,
  runId,
  skillId,
  onAnalyze,
  onSendPrompt,
  onPrefillInput,
}: {
  result: string;
  runId?: string;
  skillId?: string;
  onAnalyze: (symbol: string, context?: Record<string, unknown>) => void;
  onSendPrompt: (prompt: string) => void;
  onPrefillInput: (text: string) => void;
}) {
  const parsedResult = parseStructuredResults(result);
  const structured = parsedResult.blocks.length === 1
    ? { ...parsedResult.blocks[0]!, remainingText: parsedResult.remainingText }
    : null;
  // "加入持仓" now hits REST directly (no LLM/regex round-trip): the analysis
  // conclusion pre-fills price, then the user confirms quantity/cost inline.
  const [holdingDraft, setHoldingDraft] = useState<HoldingDraft | null>(null);
  const [savingHolding, setSavingHolding] = useState(false);

  const beginAddHolding = (symbol: string, summary: AnalysisSummary) => {
    const price = deriveEntryPrice(summary);
    if (price === null) {
      // No usable price in the conclusion: prefill the input for manual entry.
      onPrefillInput(`添加持仓 ${symbol} 100 `);
      return;
    }
    setHoldingDraft({ symbol, quantity: "100", avgCost: String(price) });
  };

  const confirmAddHolding = async () => {
    // Guard against rapid double-clicks before the disabled state re-renders.
    if (savingHolding || !holdingDraft) return;
    const quantity = Number(holdingDraft.quantity);
    const avgCost = Number(holdingDraft.avgCost);
    if (!Number.isFinite(quantity) || quantity <= 0 || !Number.isFinite(avgCost) || avgCost <= 0) {
      useChatStore.getState().addMessage({
        role: "system",
        content: "数量和成本必须是大于 0 的数字，请修改后重试。",
      });
      return;
    }
    setSavingHolding(true);
    try {
      await upsertHolding({
        symbol: holdingDraft.symbol,
        quantity,
        avg_cost: avgCost,
        current_price: null,
        notes: null,
      });
      useChatStore.getState().addMessage({
        role: "system",
        content: `✓ 已添加持仓 ${holdingDraft.symbol}（数量 ${quantity}，成本 ${avgCost}）`,
      });
      setHoldingDraft(null);
    } catch (e) {
      useChatStore.getState().addMessage({
        role: "system",
        content: `添加持仓失败：${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setSavingHolding(false);
    }
  };

  const holdingConfirm = holdingDraft && (
    <AddHoldingConfirm
      draft={holdingDraft}
      saving={savingHolding}
      onChange={setHoldingDraft}
      onCancel={() => setHoldingDraft(null)}
      onConfirm={confirmAddHolding}
    />
  );

  if (parsedResult.blocks.length > 1) {
    return (
      <CompositeTaskResult
        blocks={parsedResult.blocks}
        remainingText={parsedResult.remainingText}
        runId={runId}
        onAnalyze={onAnalyze}
      />
    );
  }

  if (structured?.type === "analysis_summary") {
    const summary = parseAnalysisSummaryFromStructured(structured.data, runId);
    if (summary) {
      return (
        <div className="space-y-3">
          <AnalysisSummaryCard
            summary={summary}
            selectionPlan={structured.selectionContext}
            artifactId={structured.artifactId}
            onAddHolding={(symbol) => beginAddHolding(symbol, summary)}
            onAnalyzeMore={(symbol) => onSendPrompt(`对比 ${symbol} 同行业标的`)}
          />
          {holdingConfirm}
          {structured.remainingText.trim() && (
            <details className="group">
              <summary className="cursor-pointer text-xs text-ui-faint hover:text-ui-body">
                展开完整报告文本
              </summary>
              <MarkdownPanel text={structured.remainingText} className="mt-2 max-h-60" />
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
        <AdaptiveAlphaBadge meta={structured.adaptiveAlpha} />
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
            <summary className="cursor-pointer text-xs text-ui-faint hover:text-ui-body">
              展开选股报告正文
            </summary>
            <MarkdownPanel text={structured.remainingText} className="mt-2 max-h-60" />
          </details>
        )}
      </div>
    );
  }

  if (structured?.type === "shadow_candidates") {
    return (
      <div className="space-y-2">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-ui-warning">
          扩展池观察候选（Shadow）
        </h4>
        <CandidateTable
          candidates={parseCandidates(structured.data)}
          warnings={structured.warnings}
          onAnalyze={onAnalyze}
          actionContext={{ runId }}
        />
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
            <summary className="cursor-pointer text-xs text-ui-faint hover:text-ui-body">
              展开风险报告正文
            </summary>
            <MarkdownPanel text={structured.remainingText} className="mt-2 max-h-60" />
          </details>
        )}
      </div>
    );
  }

  if (structured?.type === "position_advice" && structured.data && typeof structured.data === "object") {
    return (
      <div className="space-y-3">
        <PositionAdviceCard advice={structured.data as Record<string, unknown>} />
        {structured.remainingText.trim() && (
          <details className="group">
            <summary className="cursor-pointer text-xs text-ui-faint hover:text-ui-body">展开完整建议</summary>
            <MarkdownPanel text={structured.remainingText} className="mt-2 max-h-60" />
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
            onAddHolding={(symbol) => beginAddHolding(symbol, summary)}
            onAnalyzeMore={(symbol) => onSendPrompt(`对比 ${symbol} 同行业标的`)}
          />
          {holdingConfirm}
          <details className="group">
            <summary className="cursor-pointer text-xs text-ui-faint hover:text-ui-body">
              展开完整报告文本
            </summary>
            <MarkdownPanel text={result} className="mt-2 max-h-60" />
          </details>
        </div>
      );
    }
  }

  const remainingText = structured?.remainingText?.trim();
  return <MarkdownPanel text={remainingText || result} className="max-h-80" />;
}

/**
 * Safe Markdown rendering for task reports. react-markdown escapes raw HTML by
 * default; GFM adds the tables used by market and sector reports.
 */
function MarkdownPanel({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div
      className={className + " overflow-y-auto rounded-lg border border-ui-line bg-ui-panel p-3 text-sm leading-6 text-ui-body"}
    >
      <div className="prose prose-invert prose-sm max-w-none break-words">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      </div>
    </div>
  );
}

/** Inline confirmation for the REST add-holding flow (quantity/cost editable). */
function AddHoldingConfirm({
  draft,
  saving,
  onChange,
  onCancel,
  onConfirm,
}: {
  draft: HoldingDraft;
  saving: boolean;
  onChange: (draft: HoldingDraft) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="flex flex-wrap items-end gap-2 rounded-lg border border-ui-accent/30 bg-ui-accent/5 p-3 text-xs">
      <div className="font-medium text-ui-body">添加持仓 {draft.symbol}</div>
      <label className="flex flex-col gap-1">
        <span className="text-ui-faint">数量</span>
        <input
          type="number"
          value={draft.quantity}
          onChange={(e) => onChange({ ...draft, quantity: e.target.value })}
          className="w-24 rounded border border-ui-strong bg-ui-subtle px-2 py-1.5 font-mono text-ui-ink outline-none transition focus:border-ui-accent"
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-ui-faint">成本</span>
        <input
          type="number"
          value={draft.avgCost}
          onChange={(e) => onChange({ ...draft, avgCost: e.target.value })}
          className="w-24 rounded border border-ui-strong bg-ui-subtle px-2 py-1.5 font-mono text-ui-ink outline-none transition focus:border-ui-accent"
        />
      </label>
      <button
        type="button"
        onClick={onConfirm}
        disabled={saving}
        className="rounded bg-ui-accent px-3 py-1.5 font-semibold text-ui-onAccent transition hover:bg-ui-accent disabled:cursor-not-allowed disabled:opacity-50"
      >
        {saving ? "保存中..." : "确认添加"}
      </button>
      <button
        type="button"
        onClick={onCancel}
        disabled={saving}
        className="rounded border border-ui-strong px-3 py-1.5 text-ui-muted transition hover:bg-ui-hover disabled:opacity-50"
      >
        取消
      </button>
    </div>
  );
}

const ADAPTIVE_ALPHA_REASONS: Record<string, string> = {
  not_applicable: "样本不足，维持静态权重",
  style_mismatch: "风格不匹配，维持静态权重",
  no_suggestion: "暂无预测评分，维持静态权重",
  no_candidates: "本次没有候选，权重未参与实际融合",
};

function formatAlphaValue(value: unknown): string {
  return typeof value === "number" ? value.toFixed(2) : "—";
}

// Surfaces whether the adaptive-alpha override actually changed fusion weights
// for this daily-pipeline run (only present when the feature is enabled).
export function AdaptiveAlphaBadge({ meta }: { meta?: Record<string, unknown> }) {
  if (!meta || meta.enabled !== true) return null;
  const applied = meta.applied === true;
  if (applied) {
    const staticAlpha = formatAlphaValue(meta.static_alpha);
    const suggested = formatAlphaValue(meta.suggested_alpha);
    const n = typeof meta.n === "number" ? meta.n : undefined;
    return (
      <div className="flex items-center gap-2 rounded-lg border border-ui-accent/30 bg-ui-accent/10 px-3 py-2 text-xs text-ui-accent">
        <span className="font-medium">自适应α已生效</span>
        <span className="text-ui-accent/80">
          quant权重 {staticAlpha} → {suggested}
          {n !== undefined ? `（有效样本 n=${n}）` : ""}
        </span>
      </div>
    );
  }
  const reason =
    (typeof meta.reason === "string" && ADAPTIVE_ALPHA_REASONS[meta.reason]) ||
    "本次未生效，维持静态权重";
  return (
    <div className="flex items-center gap-2 rounded-lg border border-ui-strong bg-ui-hover/60 px-3 py-2 text-xs text-ui-muted">
      <span className="font-medium text-ui-body">自适应α已开启</span>
      <span>{reason}</span>
    </div>
  );
}

interface StructuredResultBlock {
  type: string;
  data: unknown;
  warnings?: string[];
  asOfDate?: string;
  dataWindowNote?: string;
  sessionState?: string;
  selectionContext?: Record<string, unknown>;
  artifactId?: string;
  adaptiveAlpha?: Record<string, unknown>;
}

function parseStructuredResults(result: string): {
  blocks: StructuredResultBlock[];
  remainingText: string;
} {
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
    adaptiveAlpha:
      parsed.adaptiveAlpha && typeof parsed.adaptiveAlpha === "object"
        ? (parsed.adaptiveAlpha as Record<string, unknown>)
        : undefined,
  });
  const lines = result.split("\n");
  const blocks: StructuredResultBlock[] = [];
  const remainingLines: string[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("{") && trimmed.includes("__type")) {
      try {
        const parsed = JSON.parse(trimmed) as Record<string, unknown>;
        if (
          parsed.__type
          && Object.prototype.hasOwnProperty.call(parsed, "data")
        ) {
          blocks.push({
            type: String(parsed.__type),
            data: parsed.data,
            ...extract(parsed),
          });
          continue;
        }
      } catch {
        // not JSON
      }
    }
    remainingLines.push(line);
  }
  return { blocks, remainingText: remainingLines.join("\n").trim() };
}

function CompositeTaskResult({
  blocks,
  remainingText,
  runId,
  onAnalyze,
}: {
  blocks: StructuredResultBlock[];
  remainingText: string;
  runId?: string;
  onAnalyze: (symbol: string, context?: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-4">
      {blocks.map((block, index) => {
        if (block.type === "risks") {
          return (
            <section key={`${block.type}-${index}`} className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-ui-muted">
                持仓风险结果
              </h4>
              <RiskAlertList risks={parseRisks(block.data)} onAnalyze={onAnalyze} />
            </section>
          );
        }
        if (block.type === "candidates") {
          return (
            <section key={`${block.type}-${index}`} className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-ui-muted">
                次日选股结果
              </h4>
              <AdaptiveAlphaBadge meta={block.adaptiveAlpha} />
              <CandidateTable
                candidates={parseCandidates(block.data)}
                warnings={block.warnings}
                asOfDate={block.asOfDate}
                dataWindowNote={block.dataWindowNote}
                sessionState={block.sessionState}
                onAnalyze={onAnalyze}
                actionContext={{ runId }}
              />
            </section>
          );
        }
        if (block.type === "shadow_candidates") {
          return (
            <section key={`${block.type}-${index}`} className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-ui-warning">
                扩展池观察候选（Shadow）
              </h4>
              <CandidateTable
                candidates={parseCandidates(block.data)}
                warnings={block.warnings}
                onAnalyze={onAnalyze}
                actionContext={{ runId }}
              />
            </section>
          );
        }
        return null;
      })}
      {remainingText && (
        <details className="group">
          <summary className="cursor-pointer text-xs text-ui-faint hover:text-ui-body">
            展开完整复盘报告
          </summary>
          <MarkdownPanel text={remainingText} className="mt-2 max-h-80" />
        </details>
      )}
    </div>
  );
}

/* --- Sub-components --- */

const RECENT_STEP_COUNT = 3;
const DETAIL_MAX_CHARS = 90;

function formatDuration(ms: number): string {
  const totalSeconds = Math.max(0, Math.round((Number.isFinite(ms) ? ms : 0) / 1000));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  return `${Math.floor(totalSeconds / 60)}m ${totalSeconds % 60}s`;
}

function StepRow({ step }: { step: ChatTaskStep }) {
  const detail = step.detail ?? "";
  const truncated = detail.length > DETAIL_MAX_CHARS ? `${detail.slice(0, DETAIL_MAX_CHARS)}…` : detail;
  return (
    <div className="relative flex gap-2 text-xs">
      <span className="absolute -left-4 top-0 bg-ui-subtle">
        <StepIcon status={step.status} />
      </span>
      <div className="min-w-0">
        <span className="text-ui-body">{step.label}</span>
        {detail && (
          <span className="ml-2 break-all font-mono text-xs text-ui-faint" title={detail}>
            {truncated}
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * Collapsible vertical timeline for task steps: while running it pins the
 * current step with elapsed time and shows only the most recent few steps;
 * once terminal it folds into a one-line summary that expands on demand.
 */
export function StepTimeline({
  steps,
  taskStatus,
}: {
  steps: ChatTaskStep[];
  taskStatus: ChatTaskStatus;
}) {
  const running = taskStatus === "running" || taskStatus === "queued";
  const [expanded, setExpanded] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  // Tick every second while running so the elapsed-time readout stays live.
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);

  if (steps.length === 0) return null;

  const first = steps[0];
  const latest = steps[steps.length - 1];
  if (!first || !latest) return null;

  const startMs = Date.parse(first.timestamp);
  const endMs = running ? now : Date.parse(latest.timestamp);
  const duration = formatDuration(endMs - startMs);

  const timeline = (visible: ChatTaskStep[]) => (
    <div className="relative space-y-2 pl-4">
      <div className="absolute bottom-1.5 left-[6px] top-1.5 w-0.5 bg-ui-hover" aria-hidden />
      {visible.map((step) => (
        <StepRow key={step.id} step={step} />
      ))}
    </div>
  );

  if (running) {
    const visible = expanded ? steps : steps.slice(-RECENT_STEP_COUNT);
    const hidden = steps.length - RECENT_STEP_COUNT;
    return (
      <div className="mt-3 space-y-2">
        <div className="flex items-center gap-2 rounded-lg border border-ui-accent/20 bg-ui-accent/5 px-2.5 py-1.5 text-xs">
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-ui-accent" />
          <span className="min-w-0 truncate text-ui-ink">{latest.label}</span>
          <span className="ml-auto shrink-0 font-mono text-ui-faint">{duration}</span>
          <span className="shrink-0 rounded border border-ui-strong px-1.5 py-0.5 text-xs text-ui-muted">
            第 {steps.length} 步
          </span>
        </div>
        {timeline(visible)}
        {hidden > 0 && (
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="text-xs text-ui-faint transition hover:text-ui-body"
          >
            {expanded ? "收起步骤" : `展开全部 ${steps.length} 步`}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex items-center gap-1.5 text-xs text-ui-faint transition hover:text-ui-body"
      >
        <ChevronDown
          className={`h-3.5 w-3.5 transition-transform ${expanded ? "" : "-rotate-90"}`}
        />
        <span>
          共 {steps.length} 步 · 耗时 {duration}
        </span>
      </button>
      {expanded && <div className="mt-2">{timeline(steps)}</div>}
    </div>
  );
}

function StatusBadge({ status }: { status: ChatTaskStatus }) {
  const copy = {
    queued: "排队中",
    running: "运行中",
    completed: "完成",
    failed: "失败",
  }[status];
  const tone =
    status === "completed"
      ? "border-ui-success/30 bg-ui-success/10 text-ui-success"
      : status === "failed"
        ? "border-ui-danger/30 bg-ui-danger/10 text-ui-danger"
        : "border-ui-accent/30 bg-ui-accent/10 text-ui-accent";
  return <span className={`shrink-0 rounded border px-2 py-1 text-xs ${tone}`}>{copy}</span>;
}

function StepIcon({ status }: { status: ChatTaskStatus }) {
  if (status === "completed") return <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ui-success" />;
  if (status === "failed") return <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ui-danger" />;
  return <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-ui-accent" />;
}
