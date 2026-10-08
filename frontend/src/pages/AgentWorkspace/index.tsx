import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { createPortal } from "react-dom";
import { Link, useLocation, useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowRight, Check, CircleAlert, CircleCheck, Clock3, Copy, Database, LoaderCircle, LockKeyhole, MessageSquarePlus, PanelRightClose, PanelRightOpen, Send, Square, X } from "lucide-react";
import {
  approveAgentProposal, cancelAgentTask, closeAgentProposalReview, createAgentConversation, getAgentConversation,
  importLegacyAgentConversation, listAgentConversations, submitAgentTask,
  readAgentTaskStream, reconcileAgentProposal, rejectAgentProposal,
  type AgentConversation, type AgentConversationDetail, type AgentEvidence, type AgentTask, type UsageStats,
} from "@/api/agent";
import { cancelPaperAdvance, getPaperStatus } from "@/api/paper";
import { getConfig } from "@/api/client";
import { queryKeys } from "@/api/queryKeys";
import { ThemeToggle } from "@/components/Layout/Header";
import type { ChatNavState, IntentHint } from "@/lib/chatNav";
import { LEGACY_CHAT_IMPORT_MARKER, readLegacyChatBatches } from "@/lib/legacyChatImport";

import { ActivityTimeline, taskActivities } from "./ActivityTimeline";
import { MemoryDetails, MemoryProgress, RoleMemory, taskMemory } from "./MemoryPanel";
import { ModelPicker } from "./ModelPicker";
import { UsageSummary } from "./UsageSummary";
import { DecisionBriefCard } from "@/components/Chat/DecisionBriefCard";

interface Props {
  paperSessionId?: string;
  embedded?: boolean;
  promptRequest?: { text: string; nonce: number };
}

const activeStatuses = new Set(["queued", "planning", "running", "reviewing", "awaiting_approval", "executing_action"]);
const streamingStatuses = new Set(["queued", "planning", "running", "reviewing", "executing_action"]);
const conversationPageSize = 50;
const statusText: Record<string, string> = {
  queued: "排队中", planning: "制定计划", running: "执行中", reviewing: "核对证据",
  completed: "已完成", failed: "失败", cancelled: "已取消", interrupted: "已中断",
  needs_input: "需要补充信息",
  awaiting_approval: "等待确认", executing_action: "模拟盘执行中", needs_review: "执行结果待核对",
};
const proposalStatusText: Record<string, string> = {
  pending: "等待确认", executing: "模拟盘执行中", submitted: "等待作业回执",
  completed: "账本已推进", no_change: "账本未推进", stale: "提案已失效",
  unknown: "执行结果待核对", reviewed: "已人工核对", rejected: "已取消", expired: "已过期", failed: "执行失败",
};
const failedStepReasonText: Record<string, string> = {
  interrupted: "已中断", timeout: "已超时", cancelled: "已取消",
};

function paperDisplayName(configName?: string, strategy?: string, composite?: boolean): string {
  const friendly = [configName, strategy].find((value) => value && value.length <= 24 &&
    !/[_:/\\]/.test(value) && (/[\u3400-\u9fff]/.test(value) || /\s/.test(value)));
  return friendly || (composite ? "组合模拟盘" : "策略模拟盘");
}

function conversationTitle(title: string, paperId: string | null, paperName: string): string {
  if (paperId && title === `模拟盘 · ${paperId}`) return paperName;
  return paperId ? title.replace(paperId, paperName) : title;
}

function taskSteps(task: AgentTask) {
  const steps = task.events
    .filter((event) => event.event_type === "plan_created" || event.event_type === "plan_revised")
    .flatMap((event) => Array.isArray(event.payload.steps)
      ? event.payload.steps as Array<{ id: string; label: string }> : []);
  return steps.map((step) => {
    const started = task.events.some((event) => event.event_type === "step_started" && event.payload.id === step.id);
    const finished = task.events.find((event) => event.event_type === "step_completed" && event.payload.id === step.id);
    return { ...step, status: finished ? String(finished.payload.status) : streamingStatuses.has(task.status) ? started ? "running" : "queued" : started ? "ended" : "not_run",
      reason: finished && typeof finished.payload.reason === "string" ? finished.payload.reason : null };
  });
}

function isTextOnlyAnswer(task: AgentTask): boolean {
  return task.status === "completed" && task.evidence.length === 0 &&
    task.events.some((event) => event.event_type === "plan_created" &&
      Array.isArray(event.payload.steps) &&
      event.payload.steps.some((step) => typeof step === "object" && step !== null &&
        "tool" in step && step.tool === "chat_agent"));
}

function TaskTimeline({ task, onRetry, onInspect, retryDisabled }: {
  task: AgentTask;
  onRetry: () => void;
  onInspect: () => void;
  retryDisabled: boolean;
}) {
  const steps = taskSteps(task);
  const nativeTools = task.events.some((event) => event.event_type === "plan_created" &&
    event.payload.source === "native_tool_calls");
  const revised = task.events.some((event) => event.event_type === "plan_revised" &&
    event.payload.reason !== "模型原生工具调用");
  const revisionReasons = task.events.filter((event) => event.event_type === "plan_revised")
    .map((event) => event.payload.reason).filter((reason): reason is string =>
      typeof reason === "string" && reason !== "模型原生工具调用");
  const revisionReason = revisionReasons[revisionReasons.length - 1];
  const textOnly = isTextOnlyAnswer(task);
  return <div className="mt-5 border-y border-ui-line py-3 text-xs">
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <span className="font-medium text-ui-ink">执行过程{revised ? " · 已调整计划" : nativeTools ? " · 工具调用" : ""}</span>
      <div className="flex items-center gap-3 whitespace-nowrap"><span className="text-ui-accent">{textOnly ? "仅文字回答" : statusText[task.status] ?? task.status}</span>{task.evidence.length > 0 && <button onClick={onInspect} className="text-ui-accent underline-offset-2 hover:underline">查看证据</button>}</div>
    </div>
    {revisionReason && <p className="mt-2 leading-5 text-ui-muted">调整原因：{revisionReason}</p>}
    <div className="mt-2 space-y-2.5">
      {steps.length ? <div aria-label="执行步骤" className="space-y-2">{steps.map((step) => <div key={step.id} className="flex min-w-0 items-center gap-1.5 text-ui-body">
        {step.status === "completed" ? <CircleCheck className="h-3.5 w-3.5 shrink-0 text-ui-accent" /> :
          step.status === "failed" ? <CircleAlert className="h-3.5 w-3.5 shrink-0 text-ui-warning" /> :
          step.status === "running" ? <LoaderCircle className="h-3.5 w-3.5 shrink-0 animate-spin text-ui-accent" /> :
          <Clock3 className="h-3.5 w-3.5 shrink-0 text-ui-faint" />}
        <span className="min-w-0 flex-1 break-words leading-5">{step.label}</span><span className="whitespace-nowrap text-ui-faint">{step.status === "completed" ? "已完成" : step.status === "failed" ? failedStepReasonText[step.reason ?? ""] ?? "失败" : step.status === "running" ? "进行中" : step.status === "ended" ? "状态未回传" : step.status === "not_run" ? "未执行" : "待执行"}</span>
      </div>)}</div> : <p className="text-ui-muted">{activeStatuses.has(task.status) ? "正在解析任务目标…" : "本任务没有可展示的执行步骤。"}</p>}
      <ActivityTimeline activities={taskActivities(task)} />
      <MemoryProgress task={task} />
      {!task.result.content && <UsageSummary usage={task.usage_stats} />}
      {task.status === "reviewing" && <p className="pl-6 text-xs text-ui-muted">正在核对证据并形成回答…</p>}
      {textOnly && <p className="text-xs text-ui-muted">本轮未运行分析工具，也没有可引用的结果数据。</p>}
      {task.status === "failed" && <p role="alert" className="text-xs text-ui-danger">{task.error || "任务执行失败"}</p>}
      {['interrupted', 'failed'].includes(task.status) && !task.proposal && <div className="space-y-2"><p role="alert" className="text-xs text-ui-warning">{task.status === 'interrupted' ? '服务重启中断了本次任务；原有记录仍保留。' : '本次任务未完成。重试会核验已有查询，重新执行失败部分。'}</p><button disabled={retryDisabled} onClick={onRetry} className="rounded-md border border-ui-strong px-3 py-1.5 text-xs font-medium text-ui-body disabled:opacity-50">重新运行任务</button></div>}
      {task.status === "needs_review" && <p role="alert" className="text-xs text-ui-warning">外部执行状态不确定。请在模拟盘账本核对，系统不会自动重复提交。</p>}
    </div>
  </div>;
}

function ProposalCard({ task, paperName, busy, onApprove, onReject, onReconcile, onCloseReview, onCancelAdvance }: {
  task: AgentTask;
  paperName: string;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
  onReconcile: () => void;
  onCloseReview: () => void;
  onCancelAdvance: () => void;
}) {
  const proposal = task.proposal;
  if (!proposal) return null;
  const childLedgers = Array.isArray(proposal.result.child_ledgers)
    ? proposal.result.child_ledgers.map(asObject).filter((item) => typeof item.session_id === "string") : [];
  const jobState = proposal.result.job_state;
  const jobRunning = jobState === "queued" || jobState === "running";
  const jobProgress = typeof proposal.result.job_progress === "number" ? proposal.result.job_progress : null;
  return <div className="mt-4 overflow-hidden rounded-lg border border-ui-line bg-ui-panel text-sm">
    <div className="flex items-center justify-between gap-2 border-b border-ui-line px-4 py-3"><strong className="text-xs font-medium">模拟盘动作预览</strong><span className="shrink-0 rounded-full bg-ui-accentSoft px-2 py-1 text-xs text-ui-accent">{proposalStatusText[proposal.status] ?? proposal.status}</span></div>
    <div className="p-4">
    <strong className="block text-base font-medium text-ui-ink">推进至 {proposal.args.target_date}</strong>
    <p className="mt-1 truncate text-xs text-ui-muted" title={paperName}>目标账户：{paperName}</p>
    <dl className="mt-4 grid grid-cols-3 gap-2 text-xs"><div className="min-w-0"><dt className="whitespace-nowrap text-ui-faint">当前账本日</dt><dd className="mt-1 whitespace-nowrap font-medium tabular-nums">{proposal.baseline.as_of_date}</dd></div><div className="min-w-0"><dt className="whitespace-nowrap text-ui-faint">目标日期</dt><dd className="mt-1 whitespace-nowrap font-medium tabular-nums">{proposal.args.target_date}</dd></div><div className="min-w-0"><dt className="whitespace-nowrap text-ui-faint">提案时权益</dt><dd className="mt-1 truncate whitespace-nowrap font-medium tabular-nums" title={String(proposal.baseline.equity ?? "未知")}>{proposal.baseline.equity == null ? "—" : `¥${Number(proposal.baseline.equity).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`}</dd></div></dl>
    {proposal.status === "pending" && <div className="mt-4 flex flex-wrap gap-2"><button disabled={busy} onClick={onApprove} className="whitespace-nowrap rounded-md bg-ui-accent px-3 py-2 text-xs font-medium text-ui-onAccent disabled:opacity-50">确认推进</button><button disabled={busy} onClick={onReject} className="whitespace-nowrap rounded-md border border-ui-strong px-3 py-2 text-xs text-ui-body disabled:opacity-50">取消提案</button></div>}
    {proposal.status === "pending" && <p className="mt-3 text-xs leading-5 text-ui-muted">确认前不会写入账本；执行后核对日期、持仓和权益。提案到期后需要重新核对。</p>}
    {proposal.status === "completed" && <div className="mt-4 border-t border-ui-line pt-3 text-xs">
      <strong className="font-medium text-ui-accent">执行后账本</strong>
      <dl className="mt-2 grid grid-cols-3 gap-2"><div><dt className="text-ui-muted">实际账本日</dt><dd className="mt-1 whitespace-nowrap font-medium">{typeof proposal.result.as_of_date === "string" ? proposal.result.as_of_date : "未知"}</dd></div><div><dt className="text-ui-muted">执行后权益</dt><dd className="mt-1 truncate font-medium" title={String(proposal.result.equity ?? "未知")}>{typeof proposal.result.equity === "number" ? `¥${proposal.result.equity.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}` : "未知"}</dd></div><div><dt className="text-ui-muted">推进交易日</dt><dd className="mt-1 whitespace-nowrap font-medium">{typeof proposal.result.advanced_days === "number" ? `${proposal.result.advanced_days} 天` : "未知"}</dd></div></dl>
      <Link to={`/paper?session=${encodeURIComponent(proposal.session_id)}`} className="mt-3 inline-block font-medium text-ui-accent underline underline-offset-2">查看实际账本</Link>
    </div>}
    {proposal.status === "no_change" && <p role="status" className="mt-3 text-xs leading-5 text-ui-warning">StockManager 作业已结束，但账本日期仍为 {String(proposal.result.as_of_date || proposal.baseline.as_of_date)}；目标日期 {proposal.args.target_date} 尚未达到。</p>}
    {proposal.status === "stale" && <p role="status" className="mt-3 text-xs leading-5 text-ui-warning">确认前账户账本已变化，原提案失效且未执行。请重新核对账户后提出请求。</p>}
    {(proposal.status === "unknown" || task.status === "needs_review" || (proposal.status === "submitted" && jobRunning)) && <div className="mt-3 space-y-2 text-xs text-ui-warning">
      <p>{jobRunning
        ? `StockManager 作业仍在运行${jobProgress === null ? "" : `（${jobProgress}%）`}；完成后会自动核对。请勿重复提交。`
        : `执行结果待核对${proposal.result?.job_id ? `（任务 ${String(proposal.result.job_id)}）` : ""}；请查看模拟盘账本，勿重复提交。`}</p>
      {jobRunning && typeof proposal.result.job_message === "string" && <p className="break-words">当前步骤：{proposal.result.job_message}</p>}
      {jobRunning && typeof proposal.result.job_elapsed_seconds === "number" && <p>已运行 {Math.floor(proposal.result.job_elapsed_seconds / 60)} 分 {proposal.result.job_elapsed_seconds % 60} 秒；核对只刷新状态，不会结束作业。</p>}
      {jobRunning && proposal.result.job_cancel_requested === true && <p>正在安全停止，等待当前请求返回；停止后再核对账本。</p>}
      {typeof proposal.result?.observed_date === "string" && <p>最近核对的账本日期：{proposal.result.observed_date}</p>}
      {typeof proposal.result?.error === "string" && <p>{proposal.result.error}</p>}
      {typeof proposal.result?.job_error === "string" && <p>作业状态暂不可读：{proposal.result.job_error}</p>}
      {typeof proposal.result?.ledger_error === "string" && <p>账本暂不可读：{proposal.result.ledger_error}</p>}
      {(childLedgers.length > 0 || typeof proposal.result.child_audit_error === "string") && <div className="rounded border border-ui-warning/40 p-2">
        <strong className="block font-medium">子策略账本核对</strong>
        {childLedgers.map((item) => <div key={String(item.session_id)} className="mt-2 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <span className="max-w-full truncate" title={String(item.session_id)}>{String(item.session_id)}</span>
          <span>{typeof item.as_of_date === "string" ? `基准日 ${item.as_of_date}` : "基准日未知"}</span>
          {typeof item.equity === "number" && <span>权益 ¥{item.equity.toLocaleString("zh-CN")}</span>}
          {typeof item.error === "string" && <span>{item.error}</span>}
          <Link to={`/paper?session=${encodeURIComponent(String(item.session_id))}`} className="underline underline-offset-2">查看账本</Link>
        </div>)}
        {typeof proposal.result.child_audit_error === "string" && <p className="mt-2">{proposal.result.child_audit_error}</p>}
      </div>}
      <div className="flex flex-wrap gap-2"><button disabled={busy} onClick={onReconcile} className="whitespace-nowrap rounded-md border border-ui-warning px-3 py-2 font-medium disabled:opacity-50">{jobRunning ? "刷新进度" : "核对执行结果"}</button>
      {jobRunning && proposal.result.job_can_cancel === true && <button disabled={busy || proposal.result.job_cancel_requested === true} onClick={onCancelAdvance} className="whitespace-nowrap rounded-md border border-ui-warning px-3 py-2 font-medium disabled:opacity-50">停止推进</button>}
      {!jobRunning && <button disabled={busy} onClick={onCloseReview} className="whitespace-nowrap rounded-md border border-ui-warning px-3 py-2 font-medium disabled:opacity-50">已核对账本，关闭提案</button>}</div>
    </div>}
    {proposal.status === "reviewed" && <p role="status" className="mt-3 text-xs text-ui-muted">人工核对已记录。账本日期：{String(proposal.result.reviewed_date || "未知")}。此记录不代表作业成功。</p>}
    </div>
  </div>;
}

function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

function factText(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) return value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  return typeof value === "string" && value.trim() ? value : null;
}

function evidenceFacts(item: AgentEvidence): Array<[string, string]> {
  const result = item.result;
  if (result.error) return [];
  const facts: Array<[string, string | null]> = [];
  if (item.tool_name === "get_paper_session") {
    const snapshot = asObject(result.snapshot);
    const positions = asObject(snapshot.positions);
    facts.push(["账户", factText(result.session_id)], ["权益", factText(snapshot.equity)],
      ["现金", factText(snapshot.cash)], ["持仓", `${Object.keys(positions).length} 只`]);
  } else if (item.tool_name === "get_portfolio_summary") {
    const holdings = Array.isArray(result.holdings) ? result.holdings.map(asObject) : [];
    const pnl = holdings.reduce((sum, holding) => sum + (typeof holding.pnl === "number" ? holding.pnl : 0), 0);
    facts.push(["标的", factText(result.total_symbols)],
      ["估算盈亏", holdings.some((holding) => typeof holding.pnl === "number") ? factText(pnl) : null]);
  } else if (item.tool_name === "get_mcp_factor_snapshot") {
    const snapshot = asObject(result.snapshot);
    const rows = Array.isArray(snapshot.rows) ? snapshot.rows.map(asObject) : [];
    const row = rows.find((candidate) => candidate.ts_code === result.ts_code) ?? {};
    const raw = asObject(row.raw_factors);
    facts.push(["标的", factText(result.ts_code)], ["价格", factText(row.latest_price)],
      ["市盈率", factText(raw.pe_ttm)], ["市净率", factText(raw.pb)]);
  } else if (item.tool_name === "get_mcp_risk_announcements") {
    const rows = Array.isArray(result.rows) ? result.rows.map(asObject) : [];
    facts.push(["标的", factText(result.ts_code)],
      ["查询区间", factText(result.start_date) && factText(result.end_date)
        ? `${result.start_date} 至 ${result.end_date}` : null],
      ["关键词命中", typeof result.count === "number" ? `${result.count} 个日期` : null],
      ["最近命中", rows.length ? factText(rows.map((row) => row.ann_date).filter((value): value is string => typeof value === "string").sort().slice(-1)[0]) : null]);
  }
  return facts.filter((fact): fact is [string, string] => fact[1] !== null);
}

function EvidenceCard({ item }: { item: AgentEvidence }) {
  const facts = evidenceFacts(item);
  const failed = Boolean(item.result.error);
  const retrieved = new Date(item.retrieved_at);
  const historical = item.tool_name === "get_strategy_lessons";
  const status = failed ? "读取失败" : historical ? "历史参考" : item.warnings.length ? "有数据提示" : "已取证";
  const skillRunId = (item.tool_name === "skill" || item.tool_name.startsWith("skill:")) && typeof item.result.run_id === "string"
    ? item.result.run_id : null;
  return <article className="border-b border-ui-line py-3 first:pt-0 last:border-b-0">
    <div className="flex items-start gap-2">{failed ? <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-ui-danger" /> : <Database className="mt-0.5 h-4 w-4 shrink-0 text-ui-accent" />}<p className="min-w-0 flex-1 break-words text-xs leading-5 text-ui-body">{item.summary}</p><span className={`shrink-0 whitespace-nowrap text-xs ${failed ? "text-ui-danger" : item.warnings.length ? "text-ui-warning" : "text-ui-accent"}`}>{status}</span></div>
    {facts.length > 0 && <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 pl-6">{facts.map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-xs text-ui-faint">{label}</dt><dd className="truncate text-sm font-medium tabular-nums text-ui-ink" title={value}>{value}</dd></div>)}</dl>}
    <dl className="mt-3 space-y-1 pl-6 text-xs leading-5 text-ui-muted">
      <div className="flex gap-2"><dt className="w-12 shrink-0 text-ui-faint">来源</dt><dd className="min-w-0 truncate" title={item.source}>{historical ? "历史经验库" : item.source}</dd></div>
      <div className="flex gap-2"><dt className="w-12 shrink-0 text-ui-faint">{historical ? "经验截止" : "基准日"}</dt><dd>{historical ? String(item.result.memory_cutoff || "未知") : item.as_of_date || "未知"}</dd></div>
      {!Number.isNaN(retrieved.getTime()) && <div className="flex gap-2"><dt className="w-12 shrink-0 text-ui-faint">获取于</dt><dd>{retrieved.toLocaleString("zh-CN")}</dd></div>}
    </dl>
    {item.warnings.map((warning, index) => <p key={index} className="mt-2 flex min-w-0 gap-1.5 text-xs leading-5 text-ui-warning"><CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" /><span className="min-w-0 break-words [overflow-wrap:anywhere]">{warning}</span></p>)}
    {skillRunId && <div className="mt-3 flex gap-3 border-t border-ui-line pt-2 text-xs text-ui-accent">
      <Link to={`/analysis/${encodeURIComponent(skillRunId)}`} className="underline underline-offset-2">查看分析过程</Link>
      <Link to={`/library?run_id=${encodeURIComponent(skillRunId)}`} className="underline underline-offset-2">查看研究产物</Link>
    </div>}
  </article>;
}

function Inspector({ task, paperId, paperName, overlay, onClose, conversationUsage }: { task?: AgentTask; paperId?: string | null; paperName: string; overlay?: boolean; onClose: () => void; conversationUsage?: UsageStats }) {
  return <aside aria-label="任务证据与方案" className={`fixed inset-y-0 right-0 flex w-[min(100vw,360px)] min-h-0 flex-col overflow-hidden border-l border-ui-line bg-ui-panel shadow-xl ${overlay ? "z-[90]" : "z-50 xl:static xl:w-[252px] xl:shrink-0 xl:shadow-none"}`}>
    <div className="flex h-[58px] shrink-0 items-center justify-between border-b border-ui-line px-4"><div className="flex min-w-0 items-center gap-2"><strong className="whitespace-nowrap text-sm font-medium">任务档案</strong><span className="truncate text-xs text-ui-muted">{task ? isTextOnlyAnswer(task) ? "仅文字回答" : statusText[task.status] ?? task.status : "待命"}</span></div><button aria-label="收起任务档案" onClick={onClose} className="rounded p-1 text-ui-muted hover:bg-ui-hover"><PanelRightClose className="h-4 w-4" /></button></div>
    <div className="min-h-0 min-w-0 flex-1 space-y-5 overflow-y-auto overscroll-contain px-4 py-5 text-sm">
      {paperId && <section><h3 className="agent-section-title">账户范围</h3><strong className="mt-2 block truncate text-sm font-medium" title={paperId}>{paperName}</strong><div className="mt-1 flex min-w-0 items-center gap-2"><code className="min-w-0 flex-1 truncate text-xs text-ui-muted" title={paperId}>{paperId}</code><button type="button" aria-label="复制账户 ID" title="复制账户 ID" onClick={() => { void navigator.clipboard?.writeText(paperId); }} className="shrink-0 text-ui-muted hover:text-ui-accent"><Copy className="h-3.5 w-3.5" /></button></div><Link to={`/paper?session=${encodeURIComponent(paperId)}`} className="mt-2 inline-flex items-center gap-1 whitespace-nowrap text-xs text-ui-accent">查看完整账本 <ArrowRight className="h-3 w-3" /></Link></section>}
      <section className={paperId ? "border-t border-ui-line pt-4" : ""}><h3 className="agent-section-title">当前目标</h3><p className="mt-2 break-words text-sm leading-6 text-ui-body">{task?.goal || "输入交易问题后，这里显示目标、证据和结果。"}</p></section>
      <UsageSummary usage={conversationUsage} conversation />
      {task?.task_context && <section className="border-t border-ui-line pt-4">
        <h3 className="agent-section-title">研究条件</h3>
        <div className="mt-2 flex flex-wrap gap-2 text-xs text-ui-muted">
          {[...task.task_context.symbols, ...task.task_context.industries,
            task.task_context.horizon === 'medium_term' ? '中线' : task.task_context.horizon === 'short_term' ? '短线' : task.task_context.horizon === 'long_term' ? '长线' : '',
            task.task_context.filters.board_filter === 'main_board' ? '排除科创与创业板' : task.task_context.filters.board_filter === 'dual_growth_only' ? '仅科创与创业板' : '',
            task.task_context.filters.limit ? `${task.task_context.filters.limit} 个关注方向` : '',
          ].filter(Boolean).map(value => <span key={value} className="rounded-md border border-ui-line px-2 py-1">{value}</span>)}
        </div>
        <p className="mt-2 text-xs leading-5 text-ui-faint">{task.task_context.inherited_from ? '已接续上一轮研究对象与条件；行情依据会重新核验。' : '按本轮问题整理研究条件。'}</p>
      </section>}
      {task && <div className="space-y-4 border-t border-ui-line pt-4">{taskMemory(task) && <MemoryDetails trace={taskMemory(task)} />}<RoleMemory task={task} /></div>}
      <section className="border-t border-ui-line pt-4"><h3 className="agent-section-title">证据快照 <span className="font-normal text-ui-faint">{task?.evidence.length ?? 0} 项</span></h3>
        {task?.evidence.length ? <div className="mt-3">{task.evidence.map((item) => <EvidenceCard key={item.id} item={item} />)}</div> : <p className="mt-2 text-xs leading-5 text-ui-faint">等待工具返回可核对的数据来源。</p>}
      </section>
      <section className="border-t border-ui-line pt-4"><h3 className="agent-section-title">权限边界</h3><p className="mt-2 flex gap-2 text-xs leading-5 text-ui-muted"><LockKeyhole className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ui-accent" />{task?.proposal?.status === "pending" ? `已准备推进至 ${task.proposal.args.target_date}，需要针对该提案确认。` : task?.proposal?.status === "no_change" ? `本次作业未推进账本；目标日期 ${task.proposal.args.target_date} 尚未达到。` : task?.proposal?.status === "completed" ? `已核对账本推进结果；目标日期 ${task.proposal.args.target_date}。` : task?.proposal?.status === "stale" ? "确认前账户账本发生变化，提案未执行。" : task?.proposal ? "提案已处理；如需再次操作，请提交新任务。" : "当前不会修改持仓或模拟盘账本。模拟盘状态变更需另行确认。"}</p></section>
    </div>
  </aside>;
}

export default function AgentWorkspace({ paperSessionId, embedded = false, promptRequest }: Props = {}) {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const paperId = paperSessionId ?? new URLSearchParams(location.search).get("paper_session");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [pendingHint, setPendingHint] = useState<IntentHint | undefined>();
  const [modelSaving, setModelSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showInspector, setShowInspector] = useState(false);
  const [inspectedTaskId, setInspectedTaskId] = useState<string | null>(null);
  const consumedPrompt = useRef<string | number | null>(null);
  const legacyImportAttempted = useRef(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const requestedConversationId = new URLSearchParams(location.search).get("conversation");
  const conversations = useInfiniteQuery({
    queryKey: ["agent-conversations", paperId],
    queryFn: ({ pageParam }) => listAgentConversations(paperId, pageParam, conversationPageSize),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) => lastPage.length === conversationPageSize
      ? pages.length * conversationPageSize : undefined,
    refetchInterval: 10000,
  });
  const loaded = useMemo(() => {
    const seen = new Set<string>();
    return (conversations.data?.pages.flat() ?? []).filter((item) => {
      if ((item.paper_session_id || null) !== (paperId || null) || seen.has(item.id)) return false;
      seen.add(item.id);
      return true;
    });
  }, [conversations.data, paperId]);
  const requestedDetail = useQuery({
    queryKey: ["agent-conversation", requestedConversationId],
    queryFn: () => getAgentConversation(requestedConversationId!),
    enabled: Boolean(requestedConversationId && !loaded.some((item) => item.id === requestedConversationId)),
  });
  const scoped = useMemo(() => {
    const requested = requestedDetail.data;
    return requested && requested.id === requestedConversationId &&
      (requested.paper_session_id || null) === (paperId || null) &&
      !loaded.some((item) => item.id === requested.id)
      ? [...loaded, requested] : loaded;
  }, [loaded, paperId, requestedConversationId, requestedDetail.data]);
  const loadingRequested = Boolean(requestedConversationId &&
    !scoped.some((item) => item.id === requestedConversationId) && requestedDetail.isPending);
  const currentId = selectedId && scoped.some((item) => item.id === selectedId) ? selectedId
    : requestedConversationId && scoped.some((item) => item.id === requestedConversationId) ? requestedConversationId
      : loadingRequested ? null : scoped[0]?.id ?? null;
  const detail = useQuery({ queryKey: ["agent-conversation", currentId], queryFn: () => getAgentConversation(currentId!), enabled: Boolean(currentId), refetchInterval: 4000 });
  const paperStatus = useQuery({ queryKey: ["agent-paper-status", paperId], queryFn: () => getPaperStatus(paperId!), enabled: Boolean(paperId), retry: false, staleTime: 30_000 });
  const modelConfig = useQuery({ queryKey: queryKeys.config(), queryFn: getConfig, retry: false, staleTime: 30_000 });
  const paperName = paperStatus.data?.session?.session_id === paperId
    ? paperDisplayName(paperStatus.data.session.config_name, paperStatus.data.session.strategy, paperStatus.data.kind === "composite")
    : "模拟盘账户";
  const latestTask = detail.data?.tasks[detail.data.tasks.length - 1];
  const uncertainProposal = latestTask?.proposal?.status === "unknown" ? latestTask.proposal : null;
  const uncertainJobState = uncertainProposal?.result.job_state;
  const uncertainJobId = uncertainProposal?.result.job_id;
  const streamTaskId = latestTask && streamingStatuses.has(latestTask.status) ? latestTask.id : null;
  const inspectedTask = detail.data?.tasks.find((task) => task.id === inspectedTaskId) ?? latestTask;
  const running = Boolean(latestTask && activeStatuses.has(latestTask.status));
  const listedCurrent = scoped.find((item) => item.id === currentId);
  const legacyArchive = Boolean((detail.data?.id === currentId ? detail.data.legacy_archive : undefined) ?? listedCurrent?.legacy_archive);
  const messages = detail.data?.messages ?? [];

  useEffect(() => {
    if (!currentId || !uncertainProposal?.id || typeof uncertainJobId !== "string" ||
        (uncertainJobState && uncertainJobState !== "queued" && uncertainJobState !== "running")) return;
    let checking = false;
    const timer = window.setInterval(async () => {
      if (checking || document.visibilityState === "hidden") return;
      checking = true;
      try {
        await reconcileAgentProposal(uncertainProposal.id);
        await queryClient.invalidateQueries({ queryKey: ["agent-conversation", currentId] });
        await queryClient.invalidateQueries({ queryKey: ["agent-conversations"] });
        if (paperId) await queryClient.invalidateQueries({ queryKey: queryKeys.paperSession(paperId) });
      } catch { /* Keep the manual check available when StockManager is temporarily offline. */ }
      finally { checking = false; }
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [currentId, uncertainProposal?.id, uncertainJobId, uncertainJobState, queryClient, paperId]);

  const selectConversation = useCallback((id: string) => {
    setSelectedId(id);
    const search = new URLSearchParams(location.search);
    search.set("conversation", id);
    navigate({ pathname: location.pathname, search: search.toString() }, { replace: true });
  }, [location.pathname, location.search, navigate]);

  const selectCreatedConversation = useCallback((created: AgentConversation) => {
    queryClient.setQueryData<InfiniteData<AgentConversation[], number>>(
      ["agent-conversations", paperId], (data) => ({
        pages: data?.pages.map((page, index) => index === 0
          ? [created, ...page.filter((item) => item.id !== created.id)]
          : page.filter((item) => item.id !== created.id)) ?? [[created]],
        pageParams: data?.pageParams ?? [0],
      }),
    );
    selectConversation(created.id);
  }, [paperId, queryClient, selectConversation]);

  useEffect(() => {
    if (!streamTaskId || !currentId) return;
    const controller = new AbortController();
    const conversationId = currentId;
    const snapshot = queryClient.getQueryData<AgentConversationDetail>(["agent-conversation", conversationId]);
    const task = snapshot?.tasks.find((item) => item.id === streamTaskId);
    let cursor = task?.events[task.events.length - 1]?.seq ?? 0;
    void (async () => {
      while (!controller.signal.aborted) {
        try {
          const result = await readAgentTaskStream(streamTaskId, cursor, controller.signal, (event) => {
            cursor = event.seq;
            void queryClient.invalidateQueries({ queryKey: ["agent-conversation", conversationId] });
            void queryClient.invalidateQueries({ queryKey: ["agent-conversations"] });
          });
          cursor = result.lastSeq;
          if (result.done) {
            await queryClient.invalidateQueries({ queryKey: ["agent-conversation", conversationId] });
            await queryClient.invalidateQueries({ queryKey: ["agent-conversations"] });
            return;
          }
        } catch {
          if (controller.signal.aborted) return;
          // The slower REST refresh remains available while the stream reconnects.
        }
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    })();
    return () => controller.abort();
  }, [currentId, queryClient, streamTaskId]);

  useEffect(() => {
    if (!conversations.isSuccess || legacyImportAttempted.current) return;
    legacyImportAttempted.current = true;
    try {
      if (window.sessionStorage.getItem(LEGACY_CHAT_IMPORT_MARKER)) return;
      const batches = readLegacyChatBatches(window.localStorage);
      void (async () => {
        for (const batch of batches) {
          await importLegacyAgentConversation(batch.paperSessionId, batch.messages);
        }
        window.sessionStorage.setItem(LEGACY_CHAT_IMPORT_MARKER, "1");
        if (batches.length) await queryClient.invalidateQueries({ queryKey: ["agent-conversations"] });
      })().catch((cause) => setError(cause instanceof Error ? `旧版聊天记录导入失败：${cause.message}` : "旧版聊天记录导入失败"));
    } catch { /* Storage may be unavailable in a restricted browser. */ }
  }, [conversations.isSuccess, queryClient]);

  const send = useCallback(async (raw: string, intentHint?: IntentHint, retryTaskId?: string) => {
    const message = raw.trim();
    if (!message || busy || running || loadingRequested || modelSaving) return;
    setBusy(true);
    setError("");
    try {
      let id = legacyArchive ? null : currentId;
      if (!id) {
        const created = await createAgentConversation(paperId);
        id = created.id;
        selectCreatedConversation(created);
        await queryClient.invalidateQueries({ queryKey: ["agent-conversations"] });
      }
      await submitAgentTask(id, message, intentHint, undefined, retryTaskId);
      setInput("");
      setPendingHint(undefined);
      await queryClient.invalidateQueries({ queryKey: ["agent-conversation", id] });
      await queryClient.invalidateQueries({ queryKey: ["agent-conversations"] });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "发送失败");
      setInput(message);
    } finally {
      setBusy(false);
    }
  }, [busy, running, loadingRequested, legacyArchive, currentId, paperId, queryClient, selectCreatedConversation, modelSaving]);

  useEffect(() => {
    if (!promptRequest || !conversations.isSuccess || (currentId && !detail.isSuccess) || consumedPrompt.current === promptRequest.nonce) return;
    consumedPrompt.current = promptRequest.nonce;
    void send(promptRequest.text);
  }, [promptRequest, conversations.isSuccess, currentId, detail.isSuccess, send]);

  useEffect(() => {
    if (embedded || !conversations.isSuccess || (currentId && !detail.isSuccess)) return;
    const state = location.state as Partial<ChatNavState> | null;
    if (!state?.prompt || consumedPrompt.current === state.nonce) return;
    consumedPrompt.current = state.nonce || state.prompt;
    if (state.autoSend) void send(state.prompt, state.intentHint);
    else { setInput(state.prompt); setPendingHint(state.intentHint); }
    navigate(`${location.pathname}${location.search}`, { replace: true, state: null });
  }, [embedded, conversations.isSuccess, currentId, detail.isSuccess, location.pathname, location.search, location.state, navigate, send]);

  useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }, [messages.length, latestTask?.status]);

  useEffect(() => {
    if (!showInspector) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setShowInspector(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [showInspector]);

  const submit = (event: FormEvent) => { event.preventDefault(); void send(input, pendingHint); };
  const inspectTask = (taskId: string) => { setInspectedTaskId(taskId); setShowInspector(true); };
  const newConversation = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const created = await createAgentConversation(paperId);
      selectCreatedConversation(created);
      await queryClient.invalidateQueries({ queryKey: ["agent-conversations"] });
    } catch (exc) { setError(exc instanceof Error ? exc.message : "创建对话失败"); }
    finally { setBusy(false); }
  };
  const stop = async () => {
    if (!latestTask) return;
    try { await cancelAgentTask(latestTask.id); await queryClient.invalidateQueries({ queryKey: ["agent-conversation", currentId] }); }
    catch (exc) { setError(exc instanceof Error ? exc.message : "取消失败"); }
  };
  const decideProposal = async (decision: "approve" | "reject" | "reconcile" | "close_review" | "cancel_advance", task: AgentTask) => {
    if (!task.proposal) return;
    setBusy(true);
    setError("");
    try {
      if (decision === "approve") await approveAgentProposal(task.proposal.id);
      else if (decision === "reject") await rejectAgentProposal(task.proposal.id);
      else if (decision === "reconcile") await reconcileAgentProposal(task.proposal.id);
      else if (decision === "cancel_advance") {
        if (!window.confirm("停止这次模拟盘推进？系统会等待当前数据请求结束，随后核对账本；不会自动重新提交。")) return;
        await cancelPaperAdvance(task.proposal.session_id, String(task.proposal.result.job_id));
        await reconcileAgentProposal(task.proposal.id);
      }
      else {
        const status = await getPaperStatus(task.proposal.session_id);
        const fingerprint = status.state_fingerprint;
        if (!fingerprint) throw new Error("当前账本缺少状态指纹，请先查看模拟盘页面");
        const operation = status.advance_operation;
        if (!operation || !["completed", "reviewed"].includes(operation.state)) {
          throw new Error("模拟盘作业仍待核对，请先在模拟盘页面完成核对");
        }
        const date = status.snapshot?.as_of_date || status.session.last_date || "未知";
        if (!window.confirm(`请确认已核对账户 ${task.proposal.session_id} 的模拟盘账本。\n当前日期：${date}\n当前权益：¥${status.snapshot?.equity?.toLocaleString("zh-CN") ?? "未知"}\n组合账户还需逐一核对子策略账本。\n\n关闭后仅记录人工核对，不认定执行成功。`)) return;
        await closeAgentProposalReview(task.proposal.id, fingerprint);
      }
      await queryClient.invalidateQueries({ queryKey: ["agent-conversation", currentId] });
      await queryClient.invalidateQueries({ queryKey: ["agent-conversations"] });
      await queryClient.invalidateQueries({ queryKey: queryKeys.paperSession(task.proposal.session_id) });
    } catch (exc) { setError(exc instanceof Error ? exc.message : "操作失败"); }
    finally { setBusy(false); }
  };

  return <div className={`agent-workspace flex h-full min-h-0 w-full overflow-hidden bg-ui-canvas text-ui-ink ${embedded ? "rounded-lg border border-ui-line" : ""}`}>
    {!embedded && <aside aria-label="Agent 对话列表" className="hidden w-[184px] shrink-0 flex-col border-r border-ui-line bg-ui-subtle md:flex">
      <div className="flex h-[58px] items-center justify-between border-b border-ui-line px-3"><span className="text-sm font-medium">交易对话</span><button disabled={busy} onClick={() => void newConversation()} aria-label="新建对话" className="rounded p-1.5 text-ui-muted hover:bg-ui-hover hover:text-ui-accent disabled:opacity-50"><MessageSquarePlus className="h-4 w-4" /></button></div>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">{scoped.map((item: AgentConversation) => <button key={item.id} onClick={() => selectConversation(item.id)} title={conversationTitle(item.title, paperId, paperName)} className={`w-full rounded-md px-2.5 py-2.5 text-left ${currentId === item.id ? "bg-ui-accentSoft text-ui-ink" : "text-ui-body hover:bg-ui-hover"}`}><span className="block truncate text-[13px] font-medium">{conversationTitle(item.title, paperId, paperName)}</span><span className="mt-1 block truncate text-xs text-ui-muted">{statusText[item.latest_status || ""] ?? "新对话"}</span></button>)}{conversations.hasNextPage && <button disabled={conversations.isFetchingNextPage} onClick={() => void conversations.fetchNextPage()} aria-label="加载更多对话" className="w-full rounded px-2 py-2 text-xs text-ui-accent hover:bg-ui-accentSoft disabled:opacity-50">{conversations.isFetchingNextPage ? "加载中…" : "加载更多对话"}</button>}</div>
      <div className="border-t border-ui-line px-3 py-3 text-xs text-ui-faint">会话与证据保存在本机</div>
    </aside>}

    <section aria-label="交易 Agent 对话" className="flex min-w-0 flex-1 flex-col bg-ui-canvas">
      <header className="flex h-[58px] shrink-0 items-center justify-between gap-3 border-b border-ui-line bg-ui-panel px-4 sm:px-5"><div className="min-w-0"><p className="truncate text-[15px] font-medium" title={detail.data?.title ? conversationTitle(detail.data.title, paperId, paperName) : paperName}>{detail.data?.title ? conversationTitle(detail.data.title, paperId, paperName) : paperId ? paperName : "交易 Agent"}</p><p className="truncate text-xs text-ui-muted">{legacyArchive ? "历史聊天存档 · 数据未重新核对" : paperId ? `${paperName} · 已绑定模拟盘` : "分析 · 取证 · 风险核对"}</p></div><div className="flex shrink-0 items-center gap-2">{embedded && paperId && <Link to={`/chat?paper_session=${encodeURIComponent(paperId)}${currentId ? `&conversation=${encodeURIComponent(currentId)}` : ""}`} className="inline-flex items-center gap-1 whitespace-nowrap rounded border border-ui-line px-2 py-1 text-xs text-ui-accent hover:bg-ui-accentSoft">在工作台继续 <ArrowRight className="h-3.5 w-3.5" /></Link>}{running && <span className="hidden items-center gap-1 whitespace-nowrap text-xs text-ui-accent sm:flex">{latestTask?.status !== "awaiting_approval" && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}{statusText[latestTask!.status]}</span>}{latestTask && ["queued", "planning", "running", "reviewing"].includes(latestTask.status) && <button onClick={() => void stop()} aria-label="取消任务" className="rounded border border-ui-line p-1.5 text-ui-muted hover:bg-ui-subtle"><Square className="h-3.5 w-3.5" /></button>}{!showInspector && <button onClick={() => { setInspectedTaskId(null); setShowInspector(true); }} aria-label="展开任务档案" className="rounded p-1 text-ui-muted"><PanelRightOpen className="h-4 w-4" /></button>}{!embedded && <ThemeToggle />}</div></header>
      {(embedded || scoped.length > 0) && <div className={`flex items-center gap-2 border-b border-ui-line bg-ui-panel px-3 py-2 ${embedded ? "" : "md:hidden"}`}><select aria-label="选择 Agent 对话" value={currentId ?? ""} onChange={(event) => { if (event.target.value) selectConversation(event.target.value); else void newConversation(); }} className="min-w-0 flex-1 rounded border border-ui-line bg-ui-panel px-2 py-1.5 text-xs"><option value="">新对话</option>{scoped.map((item) => <option key={item.id} value={item.id}>{conversationTitle(item.title, paperId, paperName)}</option>)}</select>{conversations.hasNextPage && <button disabled={conversations.isFetchingNextPage} onClick={() => void conversations.fetchNextPage()} aria-label="加载更多对话" className="shrink-0 text-xs text-ui-accent disabled:opacity-50">更多</button>}<button disabled={busy} onClick={() => void newConversation()} aria-label="新建对话" className="rounded border border-ui-line p-1.5 text-ui-accent disabled:opacity-50"><MessageSquarePlus className="h-4 w-4" /></button></div>}
      {error && <div role="alert" className="flex items-center justify-between border-b border-ui-danger bg-ui-danger/10 px-4 py-2 text-xs text-ui-danger">{error}<button onClick={() => setError("")} aria-label="关闭错误"><X className="h-3.5 w-3.5" /></button></div>}
      <div ref={scrollRef} className="agent-thread min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-8"><div className="relative mx-auto max-w-[720px] space-y-6">
        {loadingRequested ? <p role="status" className="py-12 text-center text-sm text-ui-muted">正在打开历史对话…</p> : messages.length === 0 && <div className="mx-auto max-w-[590px] py-12 text-center"><div className="mx-auto mb-5 flex h-11 w-11 items-center justify-center rounded-lg bg-ui-accentSoft text-ui-accent"><Database className="h-5 w-5" /></div><h1 className="text-xl font-medium">从交易目标开始</h1><p className="mt-3 text-sm leading-7 text-ui-muted">Agent 会制定步骤，读取当前数据，标出来源和时点，再给出有条件的结论。</p><div className="mt-6 flex flex-wrap justify-center gap-2">{(paperId ? ["总结当前权益、持仓和近期成交", "解释下一交易日计划", "当前策略切换的依据是什么"] : ["分析太极实业的基本面与风险", "近期有哪些板块值得关注", "查找近期的研究报告"]).map((prompt) => <button key={prompt} onClick={() => void send(prompt)} className="rounded-md border border-ui-line bg-ui-panel px-3 py-2 text-xs text-ui-body hover:border-ui-accent">{prompt}</button>)}</div></div>}
        {messages.map((message) => {
          const task = detail.data?.tasks.find((item) => item.id === message.task_id);
          return <div key={message.id} className="space-y-3">
            <div className={message.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div className={message.role === "user" ? "max-w-[85%] rounded-lg border border-ui-line bg-ui-accentSoft px-3.5 py-2.5 text-sm leading-7 [text-wrap:pretty]" : "w-full max-w-[690px] text-sm leading-7"}>
                {message.role === "assistant" && task?.result.decision_briefs?.map((brief, index) => <div key={index} className="mb-4"><DecisionBriefCard brief={brief} onAsk={text => void send(text)} disabled={busy || running} /></div>)}
                {message.role === "user" ? <div className="whitespace-pre-wrap">{message.content}</div> : <div className="agent-prose prose prose-sm max-w-none"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown></div>}
                {message.role === "assistant" && task?.result.citations?.length ? <button onClick={() => inspectTask(task.id)} className="mt-2 flex items-center gap-1 text-xs text-ui-muted hover:text-ui-accent"><Check className="h-3.5 w-3.5 text-ui-accent" />已关联 {task.evidence.length} 项证据 · 查看任务档案</button> : null}
                {message.role === "assistant" && task && <UsageSummary usage={task.usage_stats} />}
              </div>
            </div>
            {message.role === "user" && task && <div className="max-w-[690px]"><TaskTimeline task={task} onRetry={() => void send(task.goal, undefined, task.id)} onInspect={() => inspectTask(task.id)} retryDisabled={busy || running} /><ProposalCard task={task} paperName={paperName} busy={busy} onApprove={() => void decideProposal("approve", task)} onReject={() => void decideProposal("reject", task)} onReconcile={() => void decideProposal("reconcile", task)} onCloseReview={() => void decideProposal("close_review", task)} onCancelAdvance={() => void decideProposal("cancel_advance", task)} /></div>}
          </div>;
        })}
      </div></div>
      {legacyArchive ? <div className="shrink-0 border-t border-ui-line bg-ui-panel px-4 py-3 sm:px-8"><div className="mx-auto flex max-w-[720px] items-center justify-between gap-3"><p className="text-xs text-ui-muted">旧版聊天记录仅供回看，历史数据未重新核对。</p><button onClick={() => void newConversation()} className="shrink-0 whitespace-nowrap rounded-md bg-ui-accent px-3 py-2 text-xs text-ui-onAccent">新建对话继续</button></div></div> : <form onSubmit={submit} className="shrink-0 border-t border-ui-line bg-ui-panel px-4 py-3 sm:px-8"><div className="mx-auto max-w-[720px]"><div className="flex items-end gap-2 rounded-lg border border-ui-line bg-ui-subtle p-2 focus-within:border-ui-accent"><textarea aria-label="交易问题" value={input} disabled={loadingRequested} onChange={(event) => { setInput(event.target.value); setPendingHint(undefined); }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(input, pendingHint); } }} placeholder={paperId ? "询问这个模拟盘的决策、风险或计划…" : "给 Agent 一个交易分析目标…"} className="min-h-[48px] max-h-[150px] min-w-0 flex-1 resize-y bg-transparent p-1.5 text-sm leading-6 outline-none placeholder:text-ui-faint disabled:opacity-50" /><button type="submit" disabled={!input.trim() || running || busy || loadingRequested || modelSaving} aria-label="发送" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-ui-accent text-ui-onAccent disabled:bg-ui-strong"><Send className="h-4 w-4" /></button></div><div className="mt-2 flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1"><ModelPicker config={modelConfig.data} onSavingChange={setModelSaving} disabled={busy || loadingRequested} /><span className="truncate text-xs text-ui-faint">{latestTask?.proposal ? "账户变更需单独确认" : "不会直接修改账本"}</span></div></div></form>}
    </section>
    {showInspector && (embedded ? createPortal(<><button type="button" aria-label="关闭任务档案遮罩" onClick={() => setShowInspector(false)} className="fixed inset-0 z-[80] bg-black/40" /><Inspector task={inspectedTask} paperId={paperId} paperName={paperName} conversationUsage={detail.data?.usage_stats} overlay onClose={() => setShowInspector(false)} /></>, document.body) : <><button type="button" aria-label="关闭任务档案遮罩" onClick={() => setShowInspector(false)} className="fixed inset-0 z-40 bg-black/40 xl:hidden" /><Inspector task={inspectedTask} paperId={paperId} paperName={paperName} conversationUsage={detail.data?.usage_stats} onClose={() => setShowInspector(false)} /></>)}
  </div>;
}
