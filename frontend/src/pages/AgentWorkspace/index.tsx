import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowRight, Check, CircleAlert, CircleCheck, Clock3, Database, LoaderCircle, MessageSquarePlus, PanelRightClose, PanelRightOpen, Send, Square, X } from "lucide-react";
import {
  approveAgentProposal, cancelAgentTask, createAgentConversation, getAgentConversation,
  listAgentConversations, submitAgentTask,
  reconcileAgentProposal, rejectAgentProposal,
  type AgentConversation, type AgentTask,
} from "@/api/agent";
import type { ChatNavState, IntentHint } from "@/lib/chatNav";

interface Props {
  paperSessionId?: string;
  embedded?: boolean;
  promptRequest?: { text: string; nonce: number };
}

const activeStatuses = new Set(["queued", "planning", "running", "reviewing", "awaiting_approval", "executing_action"]);
const statusText: Record<string, string> = {
  queued: "排队中", planning: "制定计划", running: "执行中", reviewing: "核对证据",
  completed: "已完成", failed: "失败", cancelled: "已取消", interrupted: "已中断",
  awaiting_approval: "等待确认", executing_action: "模拟盘执行中", needs_review: "执行结果待核对",
};

function taskSteps(task: AgentTask) {
  const plan = task.events.find((event) => event.event_type === "plan_created");
  const steps = Array.isArray(plan?.payload.steps) ? plan.payload.steps as Array<{ id: string; label: string }> : [];
  return steps.map((step) => {
    const started = task.events.some((event) => event.event_type === "step_started" && event.payload.id === step.id);
    const finished = task.events.find((event) => event.event_type === "step_completed" && event.payload.id === step.id);
    return { ...step, status: finished ? String(finished.payload.status) : started ? "running" : "queued" };
  });
}

function TaskTimeline({ task }: { task: AgentTask }) {
  const steps = taskSteps(task);
  return <div className="agent-card mt-3 overflow-hidden">
    <div className="flex items-center justify-between border-b border-[#dfe6df] px-4 py-3 text-xs">
      <span className="font-semibold text-[#1f2922]">执行过程</span>
      <span className="text-[#087d68]">{statusText[task.status] ?? task.status}</span>
    </div>
    <div className="space-y-2.5 px-4 py-3">
      {steps.length ? steps.map((step) => <div key={step.id} className="flex items-center gap-2.5 text-xs text-[#44544a]">
        {step.status === "completed" ? <CircleCheck className="h-4 w-4 text-[#087d68]" /> :
          step.status === "failed" ? <CircleAlert className="h-4 w-4 text-amber-700" /> :
          step.status === "running" ? <LoaderCircle className="h-4 w-4 animate-spin text-[#087d68]" /> :
          <Clock3 className="h-4 w-4 text-[#92a398]" />}
        <span>{step.label}</span><span className="ml-auto text-[#829087]">{step.status === "completed" ? "已完成" : step.status === "failed" ? "失败" : step.status === "running" ? "进行中" : "待执行"}</span>
      </div>) : <p className="text-xs text-[#68776d]">正在解析任务目标…</p>}
      {task.status === "reviewing" && <p className="pl-6 text-xs text-[#68776d]">正在核对证据并形成回答…</p>}
      {task.status === "failed" && <p role="alert" className="text-xs text-red-700">{task.error || "任务执行失败"}</p>}
      {task.status === "interrupted" && <p role="alert" className="text-xs text-amber-700">服务重启中断了本次任务，可重新发送问题。</p>}
      {task.status === "needs_review" && <p role="alert" className="text-xs text-amber-700">外部执行状态不确定。请在模拟盘账本核对，系统不会自动重复提交。</p>}
    </div>
  </div>;
}

function ProposalCard({ task, busy, onApprove, onReject, onReconcile }: {
  task: AgentTask;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
  onReconcile: () => void;
}) {
  const proposal = task.proposal;
  if (!proposal) return null;
  return <div className="mt-3 rounded-md border border-[#cbd8ce] bg-white p-4 text-sm">
    <div className="flex items-center justify-between"><strong>模拟盘动作预览</strong><span className="text-xs text-[#68776d]">{proposal.status === "pending" ? "等待确认" : statusText[task.status] ?? proposal.status}</span></div>
    <div className="mt-3 grid grid-cols-2 gap-3 text-xs"><div><span className="block text-[#829087]">目标账户</span><strong className="mt-1 block break-all font-medium">{proposal.session_id}</strong></div><div><span className="block text-[#829087]">目标日期</span><strong className="mt-1 block font-medium">{proposal.args.target_date}</strong></div><div><span className="block text-[#829087]">当前基准日</span><strong className="mt-1 block font-medium">{proposal.baseline.as_of_date}</strong></div><div><span className="block text-[#829087]">当前权益</span><strong className="mt-1 block font-medium">{proposal.baseline.equity == null ? "—" : `¥${Number(proposal.baseline.equity).toLocaleString("zh-CN")}`}</strong></div></div>
    <p className="mt-3 text-xs leading-5 text-[#68776d]">确认后 StockManager 将推进策略模拟盘；实际成交以执行后的账本为准。提案到期后需要重新核对。</p>
    {proposal.status === "pending" && <div className="mt-4 flex gap-2"><button disabled={busy} onClick={onApprove} className="rounded-md bg-[#087d68] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">确认推进</button><button disabled={busy} onClick={onReject} className="rounded-md border border-[#cbd8ce] px-3 py-1.5 text-xs text-[#405047] disabled:opacity-50">取消提案</button></div>}
    {(proposal.status === "unknown" || task.status === "needs_review") && <div className="mt-3 space-y-2 text-xs text-amber-700"><p>执行结果待核对{proposal.result?.job_id ? `（任务 ${String(proposal.result.job_id)}）` : ""}；请查看模拟盘账本，勿重复提交。</p>{typeof proposal.result?.observed_date === "string" && <p>最近核对的账本日期：{proposal.result.observed_date}</p>}{typeof proposal.result?.error === "string" && <p>{proposal.result.error}</p>}<button disabled={busy} onClick={onReconcile} className="rounded-md border border-amber-400 px-3 py-1.5 font-medium disabled:opacity-50">核对执行结果</button></div>}
  </div>;
}

function Inspector({ task, paperId, onClose }: { task?: AgentTask; paperId?: string | null; onClose: () => void }) {
  return <aside aria-label="任务证据与方案" className="fixed inset-y-0 right-0 z-50 flex w-[min(90vw,320px)] min-h-0 flex-col border-l border-[#dfe6df] bg-white shadow-xl lg:static lg:w-[284px] lg:shrink-0 lg:shadow-none">
    <div className="flex h-[54px] items-center justify-between border-b border-[#dfe6df] px-4"><div><strong className="text-sm font-semibold">任务档案</strong><span className="ml-2 text-xs text-[#68776d]">{task ? statusText[task.status] ?? task.status : "待命"}</span></div><button aria-label="收起任务档案" onClick={onClose} className="rounded p-1 text-[#68776d] hover:bg-[#f1f5f1]"><PanelRightClose className="h-4 w-4" /></button></div>
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4 text-sm">
      <section><h3 className="agent-section-title">当前目标</h3><p className="mt-2 leading-6 text-[#405047]">{task?.goal || "输入交易问题后，这里显示目标、证据和结果。"}</p></section>
      {paperId && <section className="border-t border-[#e8eee8] pt-4"><h3 className="agent-section-title">模拟盘范围</h3><p className="mt-2 break-all text-xs text-[#405047]">{paperId}</p><Link to={`/paper?session=${encodeURIComponent(paperId)}`} className="mt-2 inline-flex items-center gap-1 text-xs text-[#087d68]">查看账本 <ArrowRight className="h-3 w-3" /></Link></section>}
      <section className="border-t border-[#e8eee8] pt-4"><h3 className="agent-section-title">证据快照 <span className="font-normal text-[#829087]">{task?.evidence.length ?? 0} 项</span></h3>
        {task?.evidence.length ? <div className="mt-3 space-y-2">{task.evidence.map((item) => <div key={item.id} className="rounded-md border border-[#dfe6df] bg-[#f8faf8] p-3"><div className="flex items-start gap-2"><Database className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#087d68]" /><p className="text-xs leading-5 text-[#33443a]">{item.summary}</p></div><p className="mt-2 pl-5 text-[11px] text-[#68776d]">{item.source} · 基准日 {item.as_of_date || "未知"}</p>{item.warnings.map((warning, index) => <p key={index} className="mt-1 pl-5 text-xs text-amber-700">{warning}</p>)}</div>)}</div> : <p className="mt-2 text-xs leading-5 text-[#829087]">等待工具返回可核对的数据来源。</p>}
      </section>
      <section className="border-t border-[#e8eee8] pt-4"><h3 className="agent-section-title">操作权限</h3><p className="mt-2 text-xs leading-5 text-[#68776d]">{task?.proposal ? `已准备推进至 ${task.proposal.args.target_date}，需要针对该提案确认。` : "当前不会修改持仓或模拟盘账本。模拟盘状态变更需另行确认。"}</p></section>
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
  const consumedPrompt = useRef<string | number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const conversations = useQuery({ queryKey: ["agent-conversations"], queryFn: listAgentConversations, refetchInterval: 4000 });
  const scoped = useMemo(() => (Array.isArray(conversations.data) ? conversations.data : []).filter((item) => (item.paper_session_id || null) === (paperId || null)), [conversations.data, paperId]);
  const currentId = selectedId && scoped.some((item) => item.id === selectedId) ? selectedId : scoped[0]?.id ?? null;
  const detail = useQuery({ queryKey: ["agent-conversation", currentId], queryFn: () => getAgentConversation(currentId!), enabled: Boolean(currentId), refetchInterval: 1200 });
  const latestTask = detail.data?.tasks[detail.data.tasks.length - 1];
  const running = Boolean(latestTask && activeStatuses.has(latestTask.status));
  const messages = detail.data?.messages ?? [];

  const send = useCallback(async (raw: string, intentHint?: IntentHint) => {
    const message = raw.trim();
    if (!message || busy || running) return;
    setBusy(true);
    setError("");
    try {
      let id = currentId;
      if (!id) {
        const created = await createAgentConversation(paperId);
        id = created.id;
        setSelectedId(id);
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
  }, [busy, running, currentId, paperId, queryClient]);

  useEffect(() => {
    if (!promptRequest || !conversations.isSuccess || consumedPrompt.current === promptRequest.nonce) return;
    consumedPrompt.current = promptRequest.nonce;
    void send(promptRequest.text);
  }, [promptRequest, conversations.isSuccess, send]);

  useEffect(() => {
    if (embedded || !conversations.isSuccess) return;
    const state = location.state as Partial<ChatNavState> | null;
    if (!state?.prompt || consumedPrompt.current === state.nonce) return;
    consumedPrompt.current = state.nonce || state.prompt;
    if (state.autoSend) void send(state.prompt, state.intentHint);
    else { setInput(state.prompt); setPendingHint(state.intentHint); }
    navigate(`${location.pathname}${location.search}`, { replace: true, state: null });
  }, [embedded, conversations.isSuccess, location.pathname, location.search, location.state, navigate, send]);

  useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }, [messages.length, latestTask?.status]);

  const submit = (event: FormEvent) => { event.preventDefault(); void send(input, pendingHint); };
  const newConversation = async () => {
    setError("");
    try {
      const created = await createAgentConversation(paperId);
      setSelectedId(created.id);
      await queryClient.invalidateQueries({ queryKey: ["agent-conversations"] });
    } catch (exc) { setError(exc instanceof Error ? exc.message : "创建对话失败"); }
  };
  const stop = async () => {
    if (!latestTask) return;
    try { await cancelAgentTask(latestTask.id); await queryClient.invalidateQueries({ queryKey: ["agent-conversation", currentId] }); }
    catch (exc) { setError(exc instanceof Error ? exc.message : "取消失败"); }
  };
  const decideProposal = async (decision: "approve" | "reject" | "reconcile", task: AgentTask) => {
    if (!task.proposal) return;
    setBusy(true);
    setError("");
    try {
      if (decision === "approve") await approveAgentProposal(task.proposal.id);
      else if (decision === "reject") await rejectAgentProposal(task.proposal.id);
      else await reconcileAgentProposal(task.proposal.id);
      await queryClient.invalidateQueries({ queryKey: ["agent-conversation", currentId] });
      await queryClient.invalidateQueries({ queryKey: ["agent-conversations"] });
    } catch (exc) { setError(exc instanceof Error ? exc.message : "操作失败"); }
    finally { setBusy(false); }
  };

  return <div className={`agent-workspace flex min-h-0 w-full overflow-hidden border border-[#dfe6df] bg-[#f4f5f2] text-[#1f2922] ${embedded ? "h-full rounded-lg" : "h-full rounded-lg"}`}>
    {!embedded && <aside aria-label="Agent 对话列表" className="hidden w-[190px] shrink-0 flex-col border-r border-[#dfe6df] bg-[#f8faf8] md:flex">
      <div className="flex h-[54px] items-center justify-between border-b border-[#dfe6df] px-3"><span className="text-xs font-semibold text-[#68776d]">交易任务</span><button onClick={() => void newConversation()} aria-label="新建对话" className="rounded p-1.5 text-[#087d68] hover:bg-[#e1f4ea]"><MessageSquarePlus className="h-4 w-4" /></button></div>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">{scoped.map((item: AgentConversation) => <button key={item.id} onClick={() => setSelectedId(item.id)} className={`w-full rounded-md px-2.5 py-2 text-left text-xs ${currentId === item.id ? "bg-[#e1f4ea] text-[#075d4e]" : "text-[#526157] hover:bg-[#edf2ed]"}`}><span className="block truncate font-medium">{item.title}</span><span className="mt-1 block text-[11px] opacity-75">{statusText[item.latest_status || ""] ?? "新对话"}</span></button>)}</div>
      <div className="border-t border-[#dfe6df] px-3 py-3 text-[11px] text-[#829087]">任务与证据保存在本机</div>
    </aside>}

    <section aria-label="交易 Agent 对话" className="flex min-w-0 flex-1 flex-col bg-[#f4f5f2]">
      <header className="flex h-[54px] shrink-0 items-center justify-between border-b border-[#dfe6df] bg-white px-4"><div className="min-w-0"><p className="truncate text-sm font-semibold">{detail.data?.title || (paperId ? `模拟盘 · ${paperId}` : "交易 Agent")}</p><p className="text-[11px] text-[#829087]">{paperId ? "已绑定 StockManager 模拟盘" : "分析 · 取证 · 风险核对"}</p></div><div className="flex items-center gap-2">{running && <span className="flex items-center gap-1 text-xs text-[#087d68]">{latestTask?.status !== "awaiting_approval" && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}{statusText[latestTask!.status]}</span>}{latestTask && ["queued", "planning", "running", "reviewing"].includes(latestTask.status) && <button onClick={() => void stop()} aria-label="取消任务" className="rounded border border-[#dfe6df] p-1.5 text-[#68776d] hover:bg-[#f8faf8]"><Square className="h-3.5 w-3.5" /></button>}{!embedded && !showInspector && <button onClick={() => setShowInspector(true)} aria-label="展开任务档案" className="rounded p-1 text-[#68776d]"><PanelRightOpen className="h-4 w-4" /></button>}</div></header>
      {(embedded || scoped.length > 0) && <div className={`flex items-center gap-2 border-b border-[#dfe6df] bg-white px-3 py-2 ${embedded ? "" : "md:hidden"}`}><select aria-label="选择 Agent 对话" value={currentId ?? ""} onChange={(event) => setSelectedId(event.target.value)} className="min-w-0 flex-1 rounded border border-[#dfe6df] bg-white px-2 py-1.5 text-xs"><option value="">新对话</option>{scoped.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select><button onClick={() => void newConversation()} aria-label="新建对话" className="rounded border border-[#dfe6df] p-1.5 text-[#087d68]"><MessageSquarePlus className="h-4 w-4" /></button></div>}
      {error && <div role="alert" className="flex items-center justify-between border-b border-red-200 bg-red-50 px-4 py-2 text-xs text-red-800">{error}<button onClick={() => setError("")} aria-label="关闭错误"><X className="h-3.5 w-3.5" /></button></div>}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-8"><div className="mx-auto max-w-[760px] space-y-5">
        {messages.length === 0 && <div className="mx-auto max-w-[590px] py-12 text-center"><div className="mx-auto mb-5 flex h-11 w-11 items-center justify-center rounded-xl bg-[#e1f4ea] text-[#087d68]"><Database className="h-5 w-5" /></div><h1 className="text-xl font-semibold">从交易目标开始</h1><p className="mt-3 text-sm leading-6 text-[#68776d]">Agent 会制定步骤，读取当前数据，标出来源和时点，再给出有条件的结论。</p><div className="mt-6 flex flex-wrap justify-center gap-2">{(paperId ? ["总结当前权益、持仓和近期成交", "解释下一交易日计划", "当前策略切换的依据是什么"] : ["看看当前持仓风险", "分析我的组合", "查找近期的研究报告"]).map((prompt) => <button key={prompt} onClick={() => void send(prompt)} className="rounded-md border border-[#dfe6df] bg-white px-3 py-2 text-xs text-[#405047] hover:border-[#087d68]">{prompt}</button>)}</div></div>}
        {messages.map((message) => {
          const task = detail.data?.tasks.find((item) => item.id === message.task_id);
          return <div key={message.id} className="space-y-3">
            <div className={message.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div className={message.role === "user" ? "max-w-[85%] rounded-lg border border-[#c9e9db] bg-[#e1f4ea] px-3.5 py-2.5 text-sm leading-6" : "w-full max-w-[690px] text-sm leading-6"}>
                {message.role === "user" ? <div className="whitespace-pre-wrap">{message.content}</div> : <div className="agent-prose prose prose-sm max-w-none"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown></div>}
                {message.role === "assistant" && task?.result.citations?.length ? <div className="mt-2 flex items-center gap-1 text-xs text-[#68776d]"><Check className="h-3.5 w-3.5 text-[#087d68]" />已关联 {task.evidence.length} 项证据 · 只读任务</div> : null}
              </div>
            </div>
            {message.role === "user" && task && <div className="max-w-[690px]"><TaskTimeline task={task} /><ProposalCard task={task} busy={busy} onApprove={() => void decideProposal("approve", task)} onReject={() => void decideProposal("reject", task)} onReconcile={() => void decideProposal("reconcile", task)} /></div>}
          </div>;
        })}
      </div></div>
      <form onSubmit={submit} className="shrink-0 border-t border-[#dfe6df] bg-white px-4 py-3 sm:px-8"><div className="mx-auto max-w-[760px]"><div className="flex items-end gap-2 rounded-lg border border-[#cbd8ce] bg-[#f8faf8] p-2 focus-within:border-[#087d68]"><textarea aria-label="交易问题" value={input} onChange={(event) => { setInput(event.target.value); setPendingHint(undefined); }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(input, pendingHint); } }} placeholder={paperId ? "询问这个模拟盘的决策、风险或计划…" : "给 Agent 一个交易分析目标…"} className="min-h-[48px] max-h-[150px] flex-1 resize-y bg-transparent p-1.5 text-sm leading-6 outline-none placeholder:text-[#9aa89e]" /><button type="submit" disabled={!input.trim() || running || busy} aria-label="发送" className="flex h-8 w-8 items-center justify-center rounded-md bg-[#087d68] text-white disabled:bg-[#b9c9be]"><Send className="h-4 w-4" /></button></div><p className="mt-2 text-[11px] text-[#829087]">{paperId ? `账户 ${paperId} · ` : ""}{latestTask?.proposal ? "模拟盘动作会在确认后执行。" : "不会修改持仓或模拟盘账本。数据来源和基准日会记录在任务档案中。"}</p></div></form>
    </section>
    {!embedded && showInspector && <Inspector task={latestTask} paperId={paperId} onClose={() => setShowInspector(false)} />}
  </div>;
}
