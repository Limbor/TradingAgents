import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { Link, useLocation, useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowRight, Check, CircleAlert, CircleCheck, Clock3, Database, LoaderCircle, MessageSquarePlus, PanelRightClose, PanelRightOpen, Send, Square, X } from "lucide-react";
import {
  approveAgentProposal, cancelAgentTask, closeAgentProposalReview, createAgentConversation, getAgentConversation,
  importLegacyAgentConversation, listAgentConversations, submitAgentTask,
  readAgentTaskStream, reconcileAgentProposal, rejectAgentProposal,
  type AgentConversation, type AgentConversationDetail, type AgentEvidence, type AgentTask,
} from "@/api/agent";
import { getPaperStatus } from "@/api/paper";
import { ThemeToggle } from "@/components/Layout/Header";
import type { ChatNavState, IntentHint } from "@/lib/chatNav";
import { LEGACY_CHAT_IMPORT_MARKER, readLegacyChatBatches } from "@/lib/legacyChatImport";

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

function taskSteps(task: AgentTask) {
  const steps = task.events
    .filter((event) => event.event_type === "plan_created" || event.event_type === "plan_revised")
    .flatMap((event) => Array.isArray(event.payload.steps)
      ? event.payload.steps as Array<{ id: string; label: string }> : []);
  return steps.map((step) => {
    const started = task.events.some((event) => event.event_type === "step_started" && event.payload.id === step.id);
    const finished = task.events.find((event) => event.event_type === "step_completed" && event.payload.id === step.id);
    return { ...step, status: finished ? String(finished.payload.status) : started ? "running" : "queued" };
  });
}

function TaskTimeline({ task, onRetry, onInspect, retryDisabled }: {
  task: AgentTask;
  onRetry: () => void;
  onInspect: () => void;
  retryDisabled: boolean;
}) {
  const steps = taskSteps(task);
  const revised = task.events.some((event) => event.event_type === "plan_revised");
  const revisionReasons = task.events.filter((event) => event.event_type === "plan_revised")
    .map((event) => event.payload.reason).filter((reason): reason is string => typeof reason === "string");
  const revisionReason = revisionReasons[revisionReasons.length - 1];
  return <div className="agent-card mt-3 overflow-hidden">
    <div className="flex items-center justify-between border-b border-ui-line px-4 py-3 text-xs">
      <span className="font-semibold text-ui-ink">执行过程{revised ? " · 已调整计划" : ""}</span>
      <div className="flex items-center gap-3"><span className="text-ui-accent">{statusText[task.status] ?? task.status}</span>{task.evidence.length > 0 && <button onClick={onInspect} className="text-ui-muted underline-offset-2 hover:text-ui-accent hover:underline">查看证据</button>}</div>
    </div>
    {revisionReason && <p className="border-b border-ui-line px-4 py-2 text-xs leading-5 text-ui-muted">调整原因：{revisionReason}</p>}
    <div className="space-y-2.5 px-4 py-3">
      {steps.length ? steps.map((step) => <div key={step.id} className="flex items-center gap-2.5 text-xs text-ui-body">
        {step.status === "completed" ? <CircleCheck className="h-4 w-4 text-ui-accent" /> :
          step.status === "failed" ? <CircleAlert className="h-4 w-4 text-ui-warning" /> :
          step.status === "running" ? <LoaderCircle className="h-4 w-4 animate-spin text-ui-accent" /> :
          <Clock3 className="h-4 w-4 text-ui-faint" />}
        <span>{step.label}</span><span className="ml-auto text-ui-faint">{step.status === "completed" ? "已完成" : step.status === "failed" ? "失败" : step.status === "running" ? "进行中" : "待执行"}</span>
      </div>) : <p className="text-xs text-ui-muted">{activeStatuses.has(task.status) ? "正在解析任务目标…" : "本任务没有可展示的执行步骤。"}</p>}
      {task.status === "reviewing" && <p className="pl-6 text-xs text-ui-muted">正在核对证据并形成回答…</p>}
      {task.status === "failed" && <p role="alert" className="text-xs text-ui-danger">{task.error || "任务执行失败"}</p>}
      {task.status === "interrupted" && <div className="space-y-2"><p role="alert" className="text-xs text-ui-warning">服务重启中断了本次任务；原有记录仍保留。</p><button disabled={retryDisabled} onClick={onRetry} className="rounded-md border border-ui-strong px-3 py-1.5 text-xs font-medium text-ui-body disabled:opacity-50">重新运行任务</button></div>}
      {task.status === "needs_review" && <p role="alert" className="text-xs text-ui-warning">外部执行状态不确定。请在模拟盘账本核对，系统不会自动重复提交。</p>}
    </div>
  </div>;
}

function ProposalCard({ task, busy, onApprove, onReject, onReconcile, onCloseReview }: {
  task: AgentTask;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
  onReconcile: () => void;
  onCloseReview: () => void;
}) {
  const proposal = task.proposal;
  if (!proposal) return null;
  const childLedgers = Array.isArray(proposal.result.child_ledgers)
    ? proposal.result.child_ledgers.map(asObject).filter((item) => typeof item.session_id === "string") : [];
  return <div className="mt-3 rounded-md border border-ui-strong bg-ui-panel p-4 text-sm">
    <div className="flex items-center justify-between"><strong>模拟盘动作预览</strong><span className="text-xs text-ui-muted">{proposalStatusText[proposal.status] ?? proposal.status}</span></div>
    <div className="mt-3 grid grid-cols-2 gap-3 text-xs"><div><span className="block text-ui-faint">目标账户</span><strong className="mt-1 block break-all font-medium">{proposal.session_id}</strong></div><div><span className="block text-ui-faint">目标日期</span><strong className="mt-1 block font-medium">{proposal.args.target_date}</strong></div><div><span className="block text-ui-faint">当前基准日</span><strong className="mt-1 block font-medium">{proposal.baseline.as_of_date}</strong></div><div><span className="block text-ui-faint">当前权益</span><strong className="mt-1 block font-medium">{proposal.baseline.equity == null ? "—" : `¥${Number(proposal.baseline.equity).toLocaleString("zh-CN")}`}</strong></div></div>
    {proposal.status === "pending" && <p className="mt-3 text-xs leading-5 text-ui-muted">确认后 StockManager 将推进策略模拟盘；实际成交以执行后的账本为准。提案到期后需要重新核对。</p>}
    {proposal.status === "pending" && <div className="mt-4 flex gap-2"><button disabled={busy} onClick={onApprove} className="rounded-md bg-ui-accent px-3 py-1.5 text-xs font-medium text-ui-onAccent disabled:opacity-50">确认推进</button><button disabled={busy} onClick={onReject} className="rounded-md border border-ui-strong px-3 py-1.5 text-xs text-ui-body disabled:opacity-50">取消提案</button></div>}
    {proposal.status === "no_change" && <p role="status" className="mt-3 text-xs leading-5 text-ui-warning">StockManager 作业已结束，但账本日期仍为 {String(proposal.result.as_of_date || proposal.baseline.as_of_date)}；目标日期 {proposal.args.target_date} 尚未达到。</p>}
    {proposal.status === "stale" && <p role="status" className="mt-3 text-xs leading-5 text-ui-warning">确认前账户账本已变化，原提案失效且未执行。请重新核对账户后提出请求。</p>}
    {(proposal.status === "unknown" || task.status === "needs_review") && <div className="mt-3 space-y-2 text-xs text-ui-warning">
      <p>执行结果待核对{proposal.result?.job_id ? `（任务 ${String(proposal.result.job_id)}）` : ""}；请查看模拟盘账本，勿重复提交。</p>
      {typeof proposal.result?.observed_date === "string" && <p>最近核对的账本日期：{proposal.result.observed_date}</p>}
      {typeof proposal.result?.error === "string" && <p>{proposal.result.error}</p>}
      {(childLedgers.length > 0 || typeof proposal.result.child_audit_error === "string") && <div className="rounded border border-ui-warning/40 p-2">
        <strong className="block font-medium">子策略账本核对</strong>
        {childLedgers.map((item) => <div key={String(item.session_id)} className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="break-all">{String(item.session_id)}</span>
          <span>{typeof item.as_of_date === "string" ? `基准日 ${item.as_of_date}` : "基准日未知"}</span>
          {typeof item.equity === "number" && <span>权益 ¥{item.equity.toLocaleString("zh-CN")}</span>}
          {typeof item.error === "string" && <span>{item.error}</span>}
          <Link to={`/paper?session=${encodeURIComponent(String(item.session_id))}`} className="underline underline-offset-2">查看账本</Link>
        </div>)}
        {typeof proposal.result.child_audit_error === "string" && <p className="mt-2">{proposal.result.child_audit_error}</p>}
      </div>}
      <button disabled={busy} onClick={onReconcile} className="rounded-md border border-ui-warning px-3 py-1.5 font-medium disabled:opacity-50">核对执行结果</button>
      <button disabled={busy} onClick={onCloseReview} className="ml-2 rounded-md border border-ui-warning px-3 py-1.5 font-medium disabled:opacity-50">已核对账本，关闭提案</button>
    </div>}
    {proposal.status === "reviewed" && <p role="status" className="mt-3 text-xs text-ui-muted">人工核对已记录。账本日期：{String(proposal.result.reviewed_date || "未知")}。此记录不代表作业成功。</p>}
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
  const status = failed ? "读取失败" : item.warnings.length ? "有数据提示" : "已取证";
  return <article className={`rounded-md border p-3 ${failed ? "border-ui-danger/40 bg-ui-danger/5" : "border-ui-line bg-ui-subtle"}`}>
    <div className="flex items-start gap-2">{failed ? <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ui-danger" /> : <Database className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ui-accent" />}<p className="min-w-0 flex-1 text-xs leading-5 text-ui-body">{item.summary}</p><span className={`shrink-0 rounded px-1.5 py-0.5 text-xs ${failed ? "bg-ui-danger/10 text-ui-danger" : item.warnings.length ? "bg-ui-warning/10 text-ui-warning" : "bg-ui-accentSoft text-ui-accent"}`}>{status}</span></div>
    {facts.length > 0 && <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 pl-5">{facts.map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-xs text-ui-faint">{label}</dt><dd className="truncate text-sm font-medium tabular-nums text-ui-ink" title={value}>{value}</dd></div>)}</dl>}
    <dl className="mt-3 space-y-1 border-t border-ui-line pt-2 text-xs leading-5 text-ui-muted">
      <div className="flex gap-2"><dt className="w-12 shrink-0 text-ui-faint">来源</dt><dd className="min-w-0 break-all">{item.source}</dd></div>
      <div className="flex gap-2"><dt className="w-12 shrink-0 text-ui-faint">基准日</dt><dd>{item.as_of_date || "未知"}</dd></div>
      {!Number.isNaN(retrieved.getTime()) && <div className="flex gap-2"><dt className="w-12 shrink-0 text-ui-faint">获取于</dt><dd>{retrieved.toLocaleString("zh-CN")}</dd></div>}
    </dl>
    {item.warnings.map((warning, index) => <p key={index} className="mt-2 flex gap-1.5 text-xs leading-5 text-ui-warning"><CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />{warning}</p>)}
  </article>;
}

function Inspector({ task, paperId, overlay, onClose }: { task?: AgentTask; paperId?: string | null; overlay?: boolean; onClose: () => void }) {
  return <aside aria-label="任务证据与方案" className={`fixed inset-y-0 right-0 z-50 flex w-[min(100vw,360px)] min-h-0 flex-col border-l border-ui-line bg-ui-panel shadow-xl ${overlay ? "" : "lg:static lg:w-[284px] lg:shrink-0 lg:shadow-none"}`}>
    <div className="flex h-[54px] items-center justify-between border-b border-ui-line px-4"><div><strong className="text-sm font-semibold">任务档案</strong><span className="ml-2 text-xs text-ui-muted">{task ? statusText[task.status] ?? task.status : "待命"}</span></div><button aria-label="收起任务档案" onClick={onClose} className="rounded p-1 text-ui-muted hover:bg-ui-hover"><PanelRightClose className="h-4 w-4" /></button></div>
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4 text-sm">
      <section><h3 className="agent-section-title">当前目标</h3><p className="mt-2 leading-6 text-ui-body">{task?.goal || "输入交易问题后，这里显示目标、证据和结果。"}</p></section>
      {paperId && <section className="border-t border-ui-line pt-4"><h3 className="agent-section-title">模拟盘范围</h3><p className="mt-2 break-all text-xs text-ui-body">{paperId}</p><Link to={`/paper?session=${encodeURIComponent(paperId)}`} className="mt-2 inline-flex items-center gap-1 text-xs text-ui-accent">查看账本 <ArrowRight className="h-3 w-3" /></Link></section>}
      <section className="border-t border-ui-line pt-4"><h3 className="agent-section-title">证据快照 <span className="font-normal text-ui-faint">{task?.evidence.length ?? 0} 项</span></h3>
        {task?.evidence.length ? <div className="mt-3 space-y-2">{task.evidence.map((item) => <EvidenceCard key={item.id} item={item} />)}</div> : <p className="mt-2 text-xs leading-5 text-ui-faint">等待工具返回可核对的数据来源。</p>}
      </section>
      <section className="border-t border-ui-line pt-4"><h3 className="agent-section-title">操作权限</h3><p className="mt-2 text-xs leading-5 text-ui-muted">{task?.proposal?.status === "pending" ? `已准备推进至 ${task.proposal.args.target_date}，需要针对该提案确认。` : task?.proposal?.status === "no_change" ? `本次作业未推进账本；目标日期 ${task.proposal.args.target_date} 尚未达到。` : task?.proposal?.status === "completed" ? `已核对账本推进结果；目标日期 ${task.proposal.args.target_date}。` : task?.proposal?.status === "stale" ? "确认前账户账本发生变化，提案未执行。" : task?.proposal ? "提案已处理；如需再次操作，请提交新任务。" : "当前不会修改持仓或模拟盘账本。模拟盘状态变更需另行确认。"}</p></section>
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showInspector, setShowInspector] = useState(() => !embedded && window.matchMedia("(min-width: 1024px)").matches);
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
  const latestTask = detail.data?.tasks[detail.data.tasks.length - 1];
  const streamTaskId = latestTask && streamingStatuses.has(latestTask.status) ? latestTask.id : null;
  const inspectedTask = detail.data?.tasks.find((task) => task.id === inspectedTaskId) ?? latestTask;
  const running = Boolean(latestTask && activeStatuses.has(latestTask.status));
  const listedCurrent = scoped.find((item) => item.id === currentId);
  const legacyArchive = Boolean((detail.data?.id === currentId ? detail.data.legacy_archive : undefined) ?? listedCurrent?.legacy_archive);
  const messages = detail.data?.messages ?? [];

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

  const send = useCallback(async (raw: string, intentHint?: IntentHint) => {
    const message = raw.trim();
    if (!message || busy || running || loadingRequested) return;
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
      await submitAgentTask(id, message, intentHint);
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
  }, [busy, running, loadingRequested, legacyArchive, currentId, paperId, queryClient, selectCreatedConversation]);

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
  const decideProposal = async (decision: "approve" | "reject" | "reconcile" | "close_review", task: AgentTask) => {
    if (!task.proposal) return;
    setBusy(true);
    setError("");
    try {
      if (decision === "approve") await approveAgentProposal(task.proposal.id);
      else if (decision === "reject") await rejectAgentProposal(task.proposal.id);
      else if (decision === "reconcile") await reconcileAgentProposal(task.proposal.id);
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
    } catch (exc) { setError(exc instanceof Error ? exc.message : "操作失败"); }
    finally { setBusy(false); }
  };

  return <div className={`agent-workspace flex h-full min-h-0 w-full overflow-hidden bg-ui-canvas text-ui-ink ${embedded ? "rounded-lg border border-ui-line" : ""}`}>
    {!embedded && <aside aria-label="Agent 对话列表" className="hidden w-[190px] shrink-0 flex-col border-r border-ui-line bg-ui-subtle md:flex">
      <div className="flex h-[54px] items-center justify-between border-b border-ui-line px-3"><span className="text-xs font-semibold text-ui-muted">交易任务</span><button disabled={busy} onClick={() => void newConversation()} aria-label="新建对话" className="rounded p-1.5 text-ui-accent hover:bg-ui-accentSoft disabled:opacity-50"><MessageSquarePlus className="h-4 w-4" /></button></div>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">{scoped.map((item: AgentConversation) => <button key={item.id} onClick={() => selectConversation(item.id)} className={`w-full rounded-md px-2.5 py-2 text-left text-xs ${currentId === item.id ? "bg-ui-accentSoft text-ui-accent" : "text-ui-muted hover:bg-ui-hover"}`}><span className="block truncate font-medium">{item.title}</span><span className="mt-1 block text-xs opacity-75">{statusText[item.latest_status || ""] ?? "新对话"}</span></button>)}{conversations.hasNextPage && <button disabled={conversations.isFetchingNextPage} onClick={() => void conversations.fetchNextPage()} aria-label="加载更多对话" className="w-full rounded px-2 py-2 text-xs text-ui-accent hover:bg-ui-accentSoft disabled:opacity-50">{conversations.isFetchingNextPage ? "加载中…" : "加载更多对话"}</button>}</div>
      <div className="border-t border-ui-line px-3 py-3 text-xs text-ui-faint">任务与证据保存在本机</div>
    </aside>}

    <section aria-label="交易 Agent 对话" className="flex min-w-0 flex-1 flex-col bg-ui-canvas">
      <header className="flex h-[54px] shrink-0 items-center justify-between border-b border-ui-line bg-ui-panel px-4"><div className="min-w-0"><p className="truncate text-sm font-semibold">{detail.data?.title || (paperId ? `模拟盘 · ${paperId}` : "交易 Agent")}</p><p className="text-xs text-ui-faint">{legacyArchive ? "历史聊天存档 · 数据未重新核对" : paperId ? "已绑定 StockManager 模拟盘" : "分析 · 取证 · 风险核对"}</p></div><div className="flex items-center gap-2">{embedded && paperId && <Link to={`/chat?paper_session=${encodeURIComponent(paperId)}${currentId ? `&conversation=${encodeURIComponent(currentId)}` : ""}`} className="inline-flex items-center gap-1 rounded border border-ui-line px-2 py-1 text-xs text-ui-accent hover:bg-ui-accentSoft">在工作台继续 <ArrowRight className="h-3.5 w-3.5" /></Link>}{running && <span className="flex items-center gap-1 text-xs text-ui-accent">{latestTask?.status !== "awaiting_approval" && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}{statusText[latestTask!.status]}</span>}{latestTask && ["queued", "planning", "running", "reviewing"].includes(latestTask.status) && <button onClick={() => void stop()} aria-label="取消任务" className="rounded border border-ui-line p-1.5 text-ui-muted hover:bg-ui-subtle"><Square className="h-3.5 w-3.5" /></button>}{!showInspector && <button onClick={() => { setInspectedTaskId(null); setShowInspector(true); }} aria-label="展开任务档案" className="rounded p-1 text-ui-muted"><PanelRightOpen className="h-4 w-4" /></button>}{!embedded && <ThemeToggle />}</div></header>
      {(embedded || scoped.length > 0) && <div className={`flex items-center gap-2 border-b border-ui-line bg-ui-panel px-3 py-2 ${embedded ? "" : "md:hidden"}`}><select aria-label="选择 Agent 对话" value={currentId ?? ""} onChange={(event) => { if (event.target.value) selectConversation(event.target.value); else void newConversation(); }} className="min-w-0 flex-1 rounded border border-ui-line bg-ui-panel px-2 py-1.5 text-xs"><option value="">新对话</option>{scoped.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select>{conversations.hasNextPage && <button disabled={conversations.isFetchingNextPage} onClick={() => void conversations.fetchNextPage()} aria-label="加载更多对话" className="shrink-0 text-xs text-ui-accent disabled:opacity-50">更多</button>}<button disabled={busy} onClick={() => void newConversation()} aria-label="新建对话" className="rounded border border-ui-line p-1.5 text-ui-accent disabled:opacity-50"><MessageSquarePlus className="h-4 w-4" /></button></div>}
      {error && <div role="alert" className="flex items-center justify-between border-b border-ui-danger bg-ui-danger/10 px-4 py-2 text-xs text-ui-danger">{error}<button onClick={() => setError("")} aria-label="关闭错误"><X className="h-3.5 w-3.5" /></button></div>}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-8"><div className="mx-auto max-w-[760px] space-y-5">
        {loadingRequested ? <p role="status" className="py-12 text-center text-sm text-ui-muted">正在打开历史对话…</p> : messages.length === 0 && <div className="mx-auto max-w-[590px] py-12 text-center"><div className="mx-auto mb-5 flex h-11 w-11 items-center justify-center rounded-xl bg-ui-accentSoft text-ui-accent"><Database className="h-5 w-5" /></div><h1 className="text-xl font-semibold">从交易目标开始</h1><p className="mt-3 text-sm leading-6 text-ui-muted">Agent 会制定步骤，读取当前数据，标出来源和时点，再给出有条件的结论。</p><div className="mt-6 flex flex-wrap justify-center gap-2">{(paperId ? ["总结当前权益、持仓和近期成交", "解释下一交易日计划", "当前策略切换的依据是什么"] : ["看看当前持仓风险", "分析我的组合", "查找近期的研究报告"]).map((prompt) => <button key={prompt} onClick={() => void send(prompt)} className="rounded-md border border-ui-line bg-ui-panel px-3 py-2 text-xs text-ui-body hover:border-ui-accent">{prompt}</button>)}</div></div>}
        {messages.map((message) => {
          const task = detail.data?.tasks.find((item) => item.id === message.task_id);
          return <div key={message.id} className="space-y-3">
            <div className={message.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div className={message.role === "user" ? "max-w-[85%] rounded-lg border border-ui-strong bg-ui-accentSoft px-3.5 py-2.5 text-sm leading-6" : "w-full max-w-[690px] text-sm leading-6"}>
                {message.role === "user" ? <div className="whitespace-pre-wrap">{message.content}</div> : <div className="agent-prose prose prose-sm max-w-none"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown></div>}
                {message.role === "assistant" && task?.result.citations?.length ? <button onClick={() => inspectTask(task.id)} className="mt-2 flex items-center gap-1 text-xs text-ui-muted hover:text-ui-accent"><Check className="h-3.5 w-3.5 text-ui-accent" />已关联 {task.evidence.length} 项证据 · 查看任务档案</button> : null}
              </div>
            </div>
            {message.role === "user" && task && <div className="max-w-[690px]"><TaskTimeline task={task} onRetry={() => void send(task.goal)} onInspect={() => inspectTask(task.id)} retryDisabled={busy || running} /><ProposalCard task={task} busy={busy} onApprove={() => void decideProposal("approve", task)} onReject={() => void decideProposal("reject", task)} onReconcile={() => void decideProposal("reconcile", task)} onCloseReview={() => void decideProposal("close_review", task)} /></div>}
          </div>;
        })}
      </div></div>
      {legacyArchive ? <div className="shrink-0 border-t border-ui-line bg-ui-panel px-4 py-3 sm:px-8"><div className="mx-auto flex max-w-[760px] items-center justify-between gap-3"><p className="text-xs text-ui-muted">旧版聊天记录仅供回看，历史数据未重新核对。</p><button onClick={() => void newConversation()} className="shrink-0 rounded-md bg-ui-accent px-3 py-2 text-xs text-ui-onAccent">新建对话继续</button></div></div> : <form onSubmit={submit} className="shrink-0 border-t border-ui-line bg-ui-panel px-4 py-3 sm:px-8"><div className="mx-auto max-w-[760px]"><div className="flex items-end gap-2 rounded-lg border border-ui-strong bg-ui-subtle p-2 focus-within:border-ui-accent"><textarea aria-label="交易问题" value={input} disabled={loadingRequested} onChange={(event) => { setInput(event.target.value); setPendingHint(undefined); }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(input, pendingHint); } }} placeholder={paperId ? "询问这个模拟盘的决策、风险或计划…" : "给 Agent 一个交易分析目标…"} className="min-h-[48px] max-h-[150px] flex-1 resize-y bg-transparent p-1.5 text-sm leading-6 outline-none placeholder:text-ui-faint disabled:opacity-50" /><button type="submit" disabled={!input.trim() || running || busy || loadingRequested} aria-label="发送" className="flex h-8 w-8 items-center justify-center rounded-md bg-ui-accent text-ui-onAccent disabled:bg-ui-strong"><Send className="h-4 w-4" /></button></div><p className="mt-2 text-xs text-ui-faint">{paperId ? `账户 ${paperId} · ` : ""}{latestTask?.proposal ? "模拟盘动作会在确认后执行。" : "不会修改持仓或模拟盘账本。数据来源和基准日会记录在任务档案中。"}</p></div></form>}
    </section>
    {showInspector && <><button type="button" aria-label="关闭任务档案遮罩" onClick={() => setShowInspector(false)} className={`fixed inset-0 z-40 bg-black/40 ${embedded ? "" : "lg:hidden"}`} /><Inspector task={inspectedTask} paperId={paperId} overlay={embedded} onClose={() => setShowInspector(false)} /></>}
  </div>;
}
