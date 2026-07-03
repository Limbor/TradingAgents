import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  Bot,
  CheckCircle2,
  ExternalLink,
  Loader2,
  RadioTower,
  Rocket,
  Send,
  ShieldAlert,
  Sparkles,
  TrendingUp,
  UserRound,
  XCircle,
} from "lucide-react";
import { chatWsManager, type WSMessage } from "@/api/ws";
import { type ChatMessage, type ChatTaskStatus, useChatStore } from "@/stores/useChatStore";
import {
  CandidateTable,
  parseCandidates,
  RiskAlertList,
  parseRisks,
  AnalysisSummaryCard,
  parseAnalysisSummary,
} from "@/components/Chat";

const QUICK_ACTIONS = [
  { label: "每日选股", prompt: "每日选股 top 5", icon: Sparkles },
  { label: "风险扫描", prompt: "分析当前持仓风险", icon: ShieldAlert },
  { label: "分析个股...", prompt: "", icon: TrendingUp },
  { label: "扫描A股", prompt: "筛选A股 top 3 score 60", icon: Rocket },
];

export default function Chat() {
  const [input, setInput] = useState("");
  const location = useLocation();
  const navigate = useNavigate();
  const autoSentRef = useRef<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const {
    messages,
    connected,
    running,
    setConnected,
    setRunning,
    setCurrentRunId,
    addMessage,
    createTask,
    addTaskStep,
    appendTaskResult,
    finishTask,
  } = useChatStore();

  useEffect(() => {
    const offOpen = chatWsManager.onOpen(() => setConnected(true));
    const offClose = chatWsManager.onClose(() => setConnected(false));
    const offMessage = chatWsManager.onMessage((message: WSMessage) => {
      if (message.type === "chat_reply") {
        const payload = message.payload;
        const runId = payload.run_id as string | undefined;
        setRunning(Boolean(runId));
        setCurrentRunId(runId ?? null);
        if (runId) {
          createTask({
            runId,
            skillId: payload.skill_triggered as string | undefined,
            title: skillTitle(payload.skill_triggered as string | undefined),
            detail: payload.reason as string | undefined,
          });
          addTaskStep(runId, {
            label: "准备执行",
            detail: formatParams(payload.params),
            status: "running",
          });
        } else {
          addMessage({
            role: "assistant",
            content: String(payload.content ?? "我还没有找到合适的技能。"),
          });
        }
      } else if (message.type === "skill_progress" || message.type === "progress_update") {
        addTaskStep(message.run_id, {
          label: progressStepLabel(message.payload),
          detail: progressStepDetail(message.payload),
          status: normalizeTaskStatus(message.payload.status),
        });
      } else if (message.type === "report_chunk") {
        addTaskStep(message.run_id, {
          label: eventStepLabel(message),
          detail: "已生成一段可读结论",
          status: "completed",
        });
        appendTaskResult(message.run_id, String(message.payload.content ?? ""));
      } else if (message.type === "scanner_candidates") {
        const count = (message.payload.candidates as unknown[] | undefined)?.length ?? 0;
        addTaskStep(message.run_id, {
          label: "完成候选池筛选",
          detail: `找到 ${count} 个候选标的`,
          status: "completed",
        });
        appendTaskResult(message.run_id, JSON.stringify({ __type: "candidates", data: message.payload.candidates }));
      } else if (message.type === "daily_pipeline_candidates") {
        const rows =
          (message.payload.reviewed_candidates as unknown[] | undefined) ??
          (message.payload.candidates as unknown[] | undefined) ??
          (message.payload.decision_pack as unknown[] | undefined);
        const count = (rows as unknown[] | undefined)?.length ?? 0;
        const warnings = (message.payload.warnings as string[] | undefined) ?? undefined;
        addTaskStep(message.run_id, {
          label: "完成每日选股打分",
          detail: `输出 ${count} 个候选标的${count === 0 ? "（无候选）" : ""}`,
          status: count === 0 ? "failed" : "completed",
        });
        appendTaskResult(
          message.run_id,
          JSON.stringify({ __type: "candidates", data: rows, warnings }),
        );
      } else if (message.type === "risk_monitor_results") {
        const rows = message.payload.risks as unknown[] | undefined;
        addTaskStep(message.run_id, {
          label: "完成持仓风险扫描",
          detail: `检查 ${rows?.length ?? 0} 个持仓风险项`,
          status: "completed",
        });
        appendTaskResult(message.run_id, JSON.stringify({ __type: "risks", data: rows }));
      } else if (message.type === "portfolio_update") {
        addTaskStep(message.run_id, {
          label: "更新持仓数据",
          detail: JSON.stringify(message.payload),
          status: "completed",
        });
      } else if (message.type === "skill_start") {
        addTaskStep(message.run_id, {
          label: "技能开始执行",
          status: "completed",
        });
      } else if (message.type === "skill_complete") {
        addTaskStep(message.run_id, {
          label: "技能执行完成",
          status: "completed",
        });
        setRunning(false);
        setCurrentRunId(null);
        finishTask(message.run_id, "completed");
      } else if (message.type === "run_complete") {
        setRunning(false);
        setCurrentRunId(null);
        finishTask(message.run_id, "completed");
      } else if (message.type === "error") {
        setRunning(false);
        setCurrentRunId(null);
        finishTask(message.run_id, "failed", String(message.payload.message ?? "Run failed"));
      } else if (message.type === "heartbeat") {
        return;
      } else if (message.run_id) {
        addTaskStep(message.run_id, {
          label: eventStepLabel(message),
          detail: formatPayloadBrief(message.payload),
          status: "running",
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
  }, [
    addMessage,
    addTaskStep,
    appendTaskResult,
    createTask,
    finishTask,
    setConnected,
    setCurrentRunId,
    setRunning,
  ]);

  // Auto-scroll on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const canSend = useMemo(() => connected && input.trim().length > 0 && !running, [
    connected,
    input,
    running,
  ]);

  const sendPrompt = (message: string) => {
    if (!connected || !message.trim() || running) return;
    addMessage({ role: "user", content: message.trim() });
    chatWsManager.send(message.trim());
    setInput("");
    setRunning(true);
  };

  useEffect(() => {
    const state = location.state as { prompt?: string; autoSend?: boolean } | null;
    const prompt = state?.prompt?.trim();
    if (!prompt || autoSentRef.current === prompt) return;
    if (state?.autoSend && connected && !running) {
      autoSentRef.current = prompt;
      sendPrompt(prompt);
      navigate(location.pathname, { replace: true, state: null });
    } else {
      setInput(prompt);
    }
  }, [connected, location.pathname, location.state, navigate, running]);

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

  const handleAnalyzeSymbol = (symbol: string) => {
    sendPrompt(`帮我分析 ${symbol}`);
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

      {/* Messages area - full width */}
      <div className="min-h-0 flex-1 overflow-y-auto py-4">
        <div className="space-y-4">
          {messages.map((message) =>
            message.kind === "task" ? (
              <TaskCard
                key={message.id}
                message={message}
                onAnalyze={handleAnalyzeSymbol}
                onSendPrompt={sendPrompt}
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

      {/* Input area + Quick actions */}
      <div className="border-t border-stone-800 pt-3">
        <form onSubmit={submit} className="flex gap-2">
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="例如: 帮我看看茅台 / 每日选股 / 分析持仓风险..."
            className="min-w-0 flex-1 rounded-lg border border-stone-700 bg-stone-900 px-4 py-2.5 text-sm outline-none transition focus:border-teal-400"
          />
          <button
            disabled={!canSend}
            className="inline-flex items-center gap-2 rounded-lg bg-teal-500 px-5 py-2.5 text-sm font-semibold text-stone-950 transition hover:bg-teal-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Send className="h-4 w-4" />
          </button>
        </form>

        {/* Quick action chips */}
        <div className="mt-2.5 flex flex-wrap gap-2 pb-1">
          {QUICK_ACTIONS.map((action) => (
            <button
              key={action.label}
              onClick={() => handleQuickAction(action.prompt)}
              disabled={running && !!action.prompt}
              className="inline-flex items-center gap-1.5 rounded-full border border-stone-700 bg-stone-900 px-3 py-1.5 text-xs text-stone-300 transition hover:border-teal-500/50 hover:text-stone-100 disabled:opacity-50"
            >
              <action.icon className="h-3.5 w-3.5 text-teal-300" />
              {action.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/* --- TaskCard --- */

function TaskCard({
  message,
  onAnalyze,
  onSendPrompt,
}: {
  message: ChatMessage;
  onAnalyze: (symbol: string) => void;
  onSendPrompt: (prompt: string) => void;
}) {
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
              to={`/analysis/${message.runId}`}
              className="inline-flex items-center gap-1 text-teal-300 hover:text-teal-200"
            >
              详情
              <ExternalLink className="h-3.5 w-3.5" />
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}

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
  onAnalyze: (symbol: string) => void;
  onSendPrompt: (prompt: string) => void;
}) {
  const structured = tryParseStructured(result);

  if (structured?.type === "candidates") {
    const candidates = parseCandidates(structured.data);
    return (
      <div className="space-y-3">
        <CandidateTable
          candidates={candidates}
          warnings={structured.warnings}
          onAnalyze={onAnalyze}
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

function tryParseStructured(result: string): { type: string; data: unknown; warnings?: string[]; remainingText: string } | null {
  const lines = result.split("\n");
  for (const [index, line] of lines.entries()) {
    const trimmed = line.trim();
    if (trimmed.startsWith("{") && trimmed.includes("__type")) {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed.__type && parsed.data) {
          return {
            type: parsed.__type,
            data: parsed.data,
            warnings: Array.isArray(parsed.warnings) ? parsed.warnings : undefined,
            remainingText: lines.filter((_, lineIndex) => lineIndex !== index).join("\n").trim(),
          };
        }
      } catch {
        // not JSON
      }
    }
  }
  try {
    const parsed = JSON.parse(result.trim());
    if (parsed.__type && parsed.data) {
      return { type: parsed.__type, data: parsed.data, warnings: Array.isArray(parsed.warnings) ? parsed.warnings : undefined, remainingText: "" };
    }
  } catch {
    // not JSON
  }
  return null;
}

/* --- Utilities --- */

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

function skillTitle(skillId?: string) {
  const titles: Record<string, string> = {
    stock_analysis: "股票深度分析",
    market_scanner: "市场机会扫描",
    portfolio_management: "持仓管理",
    daily_pipeline: "每日选股管线",
    risk_monitor: "持仓风险监控",
  };
  return skillId ? titles[skillId] ?? "交易任务" : "交易任务";
}

const AGENT_STAGE_LABELS: Record<string, string> = {
  "Market Analyst": "市场分析",
  "Sentiment Analyst": "情绪分析",
  "News Analyst": "新闻与公告分析",
  "Fundamentals Analyst": "基本面分析",
  "Bull Researcher": "多方观点",
  "Bear Researcher": "空方观点",
  "Research Manager": "研究经理裁决",
  Trader: "交易计划",
  "Aggressive Analyst": "激进风控观点",
  "Conservative Analyst": "保守风控观点",
  "Neutral Analyst": "中性风控观点",
  "Portfolio Manager": "组合经理决策",
};

function eventStepLabel(message: WSMessage) {
  const typeLabels: Record<string, string> = {
    agent_status: "Agent 状态更新",
    tool_call: "调用数据工具",
    report_chunk: `生成 ${String(message.payload.section ?? "报告")} 片段`,
    skill_complete: "技能执行完成",
    report_complete: "报告汇总完成",
  };
  if (message.type === "agent_status") {
    const agent = String(message.payload.agent ?? "Agent");
    const label = AGENT_STAGE_LABELS[agent] ?? agent;
    const status = String(message.payload.status ?? "running");
    if (status === "completed") return `${label}完成`;
    if (status === "failed") return `${label}失败`;
    return `${label}进行中`;
  }
  return typeLabels[message.type] ?? message.type;
}

function progressStepLabel(payload: Record<string, unknown>) {
  const step = payload.step_label ?? payload.stepLabel;
  if (typeof step === "string" && step.trim()) return step;
  const stage = payload.stage_label ?? payload.stageLabel;
  const base = typeof stage === "string" && stage.trim() ? stage : "任务进度";
  const status = String(payload.status ?? "running");
  if (status === "completed") return `${base}完成`;
  if (status === "failed") return `${base}失败`;
  if (status === "queued") return `${base}排队中`;
  return `${base}进行中`;
}

function progressStepDetail(payload: Record<string, unknown>) {
  const detail = payload.detail;
  const progress = payload.progress_pct ?? payload.progressPct;
  const parts: string[] = [];
  if (typeof detail === "string" && detail.trim()) parts.push(detail);
  if (typeof progress === "number") parts.push(`${Math.round(progress)}%`);
  return parts.length ? parts.join(" · ") : undefined;
}

function normalizeTaskStatus(status: unknown): ChatTaskStatus {
  if (status === "queued" || status === "completed" || status === "failed") return status;
  return "running";
}

function formatParams(params: unknown) {
  if (!params || typeof params !== "object") return undefined;
  return JSON.stringify(params);
}

function formatPayloadBrief(payload: Record<string, unknown>) {
  if ("agent" in payload && "status" in payload) return undefined;
  if ("skill_id" in payload) return undefined;
  const keys = Object.keys(payload);
  if (keys.length === 0) return undefined;
  return JSON.stringify(payload).slice(0, 240);
}
