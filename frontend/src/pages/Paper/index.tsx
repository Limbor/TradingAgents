import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { MessageSquareText, RefreshCw } from "lucide-react";
import {
  acknowledgePaperAdvanceReview, advancePaper, createPaperSession, getPaperAdvanceReceipt, getPaperCurve, getPaperJob, getPaperPlan,
  getPaperStatus, getPaperTrades, listPaperAllocators, listPaperConfigs, listPaperSessions,
  listPaperStrategies, type PaperJob,
} from "@/api/paper";
import { queryKeys } from "@/api/queryKeys";
import { ApiHttpError } from "@/api/client";
import { CompositeDecision } from "./CompositeDecision";
import { PerformanceChart } from "./PerformanceChart";
import { HoldingsCard, TradesCard } from "./AccountDetails";
import { PlanCard } from "./PlanCard";
import Chat from "../AgentWorkspace";

const money = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? "—" : `¥${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
const signedMoney = (value: number | null) =>
  value == null ? "—" : value === 0 ? money(0) : `${value > 0 ? "+" : "-"}${money(Math.abs(value))}`;
const percent = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(2)}%`;
const card = "rounded-xl border border-ui-line bg-ui-panel p-4";
type ActiveJob = PaperJob & { sessionId: string };

export default function Paper() {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const selected = params.get("session") || "";
  const [createOpen, setCreateOpen] = useState(false);
  const [kind, setKind] = useState<"single" | "composite">("single");
  const [strategy, setStrategy] = useState("");
  const [config, setConfig] = useState("");
  const [allocatorPath, setAllocatorPath] = useState("");
  const [startDate, setStartDate] = useState("");
  const [cash, setCash] = useState("1000000");
  const [targetDate, setTargetDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<ActiveJob | null>(null);
  const [error, setError] = useState("");
  const [agentPrompt, setAgentPrompt] = useState<{ sessionId: string; text: string; nonce: number }>();
  const agentSection = useRef<HTMLDivElement>(null);
  const ledgerSection = useRef<HTMLDivElement>(null);

  const sessions = useQuery({ queryKey: queryKeys.paperSessions(), queryFn: listPaperSessions, retry: false });
  const strategies = useQuery({ queryKey: ["paper-strategies"], queryFn: listPaperStrategies, enabled: createOpen });
  const configs = useQuery({ queryKey: ["paper-configs"], queryFn: listPaperConfigs, enabled: createOpen });
  const allocators = useQuery({ queryKey: ["paper-allocators"], queryFn: listPaperAllocators, enabled: createOpen && kind === "composite", retry: false });
  const rows = Array.isArray(sessions.data) ? sessions.data : [];
  const strategyOptions = Array.isArray(strategies.data) ? strategies.data : [];
  const configOptions = Array.isArray(configs.data) ? configs.data : [];
  const allocatorOptions = Array.isArray(allocators.data) ? allocators.data : [];
  const listed = selected ? rows.find((row) => row.session_id === selected) : undefined;
  const requestedId = selected || rows[0]?.session_id || "";
  const status = useQuery({ queryKey: [...queryKeys.paperSession(requestedId), "status"], queryFn: () => getPaperStatus(requestedId), enabled: !!requestedId, retry: false });
  const direct = selected && !listed && status.data?.session?.session_id === selected
    ? status.data.session : undefined;
  const active = selected ? listed ?? direct : rows[0];
  const id = active?.session_id ?? "";
  const missingSession = !!selected && !listed && !sessions.isLoading && !sessions.isError &&
    !status.isLoading && !status.isFetching && !direct &&
    (!(status.error instanceof ApiHttpError) || status.error.status === 404);
  const isCompositeChild = active?.params?.composite_child === true;
  const parentId = typeof active?.params?.parent_composite_id === "string"
    ? active.params.parent_composite_id : "";
  useEffect(() => {
    setAgentPrompt(undefined);
  }, [id]);
  const serverOperation = status.data?.advance_operation;
  const serverLocked = !!serverOperation && ["queued", "running", "needs_review"].includes(serverOperation.state);
  const curve = useQuery({ queryKey: [...queryKeys.paperSession(id), "equity"], queryFn: () => getPaperCurve(id), enabled: !!id, retry: false });
  const trades = useQuery({ queryKey: [...queryKeys.paperSession(id), "trades"], queryFn: () => getPaperTrades(id), enabled: !!id, retry: false });
  const plan = useQuery({ queryKey: [...queryKeys.paperSession(id), "plan"], queryFn: () => getPaperPlan(id), enabled: !!id, retry: false });

  useEffect(() => {
    const options = Array.isArray(allocators.data) ? allocators.data : [];
    const first = options[0];
    if (kind !== "composite" || !first) return;
    if (options.some((item) => item.path === allocatorPath)) return;
    setAllocatorPath(first.path);
    setCash(String(first.initial_cash));
  }, [kind, allocators.data, allocatorPath]);

  useEffect(() => {
    if (!id || (job && job.sessionId === id)) return;
    const key = `paper-advance-job:${id}`;
    const stored = window.localStorage.getItem(key) || window.sessionStorage.getItem(key);
    const requestId = window.localStorage.getItem(`paper-advance-request:${id}`);
    let cancelled = false;
    if (requestId) {
      void getPaperAdvanceReceipt(id, requestId).then((receipt) => {
        if (cancelled) return;
        window.localStorage.setItem(key, receipt.job_id);
        const pending = receipt.state === "queued" || receipt.state === "running";
        setJob({ job_id: receipt.job_id, sessionId: id, state: pending ? "queued" : "error",
          progress: 0, message: pending ? "已找回推进作业" : "已找回作业回执，请核对账本", result: null });
        setBusy(pending);
      }).catch(() => {
        if (cancelled) return;
        setJob({ job_id: stored || "submission-unknown", sessionId: id,
          state: stored && stored !== "submission-unknown" ? "queued" : "error", progress: 0,
          message: stored && stored !== "submission-unknown" ? "正在恢复任务状态" : "提交结果待核对", result: null });
        setBusy(!!stored && stored !== "submission-unknown");
      });
    } else if (stored) {
      window.localStorage.setItem(key, stored);
      window.sessionStorage.removeItem(key);
      const pending = stored !== "submission-unknown";
      setJob({ job_id: stored, sessionId: id, state: pending ? "queued" : "error", progress: 0,
        message: pending ? "正在恢复任务状态" : "提交结果待核对", result: null });
      setBusy(pending);
    } else if (serverOperation && ["queued", "running", "needs_review"].includes(serverOperation.state)) {
      const needsReview = serverOperation.state === "needs_review";
      setJob({ job_id: serverOperation.job_id, sessionId: id,
        state: needsReview ? "error" : serverOperation.state === "running" ? "running" : "queued",
        progress: 0, message: needsReview ? "作业结果待核对" : "正在恢复账户推进作业", result: null });
      setBusy(!needsReview);
    }
    return () => { cancelled = true; };
  }, [id, job, serverOperation]);

  useEffect(() => {
    if (!job || (job.state !== "queued" && job.state !== "running")) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await getPaperJob(job.job_id);
        setJob({ ...next, sessionId: job.sessionId });
        if (next.state === "success") {
          setBusy(false);
          window.localStorage.removeItem(`paper-advance-job:${job.sessionId}`);
          window.localStorage.removeItem(`paper-advance-request:${job.sessionId}`);
          void client.invalidateQueries({ queryKey: queryKeys.paperSessions() });
          void client.invalidateQueries({ queryKey: queryKeys.paperSession(job.sessionId) });
        } else if (next.state === "error") {
          setBusy(false);
          setError(`${next.message || "模拟盘推进失败"}。请先核对账本，再决定是否重新推进。`);
          void client.invalidateQueries({ queryKey: queryKeys.paperSession(job.sessionId) });
        }
      } catch (cause) {
        if (cause instanceof ApiHttpError && cause.status === 404) {
          setJob({ ...job, state: "error", message: "任务记录已失效" });
          setBusy(false);
          setError("任务记录已失效。StockManager 可能在任务期间重启，请刷新账本核对是否完成后再推进。");
          void client.invalidateQueries({ queryKey: queryKeys.paperSession(job.sessionId) });
          return;
        }
        setError(cause instanceof Error ? `任务状态暂时无法读取：${cause.message}` : "任务状态暂时无法读取");
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [job, client]);

  const create = async () => {
    setError("");
    setBusy(true);
    try {
      const request = kind === "composite"
        ? { allocator_config_path: allocatorPath.trim(), initial_cash: Number(cash) }
        : { strategy, config, start_date: startDate, initial_cash: Number(cash) };
      const result = await createPaperSession(request);
      await client.invalidateQueries({ queryKey: queryKeys.paperSessions() });
      setParams({ session: result.session_id });
      setCreateOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建失败");
    } finally {
      setBusy(false);
    }
  };

  const advance = async () => {
    if (!id || !targetDate) return;
    const reviewKey = `paper-advance-job:${id}`;
    const requestKey = `paper-advance-request:${id}`;
    if ((job?.sessionId === id && job.state === "error") ||
        (window.localStorage.getItem(reviewKey) && (!job || job.sessionId !== id || job.state === "error"))) {
      setError("上一次推进仍需核对账本，请先完成核对");
      return;
    }
    if (serverLocked) {
      setError("账户已有推进作业或结果待核对，请先完成核对");
      return;
    }
    const fingerprint = status.data?.state_fingerprint;
    if (!fingerprint) {
      setError("当前账本缺少状态指纹，请刷新账本后再推进");
      return;
    }
    const ledgerDate = status.data?.snapshot?.as_of_date ?? "尚未推进";
    if (!window.confirm(`将模拟盘 ${id} 从账本日期 ${ledgerDate} 推进到 ${targetDate}？StockManager 会按策略计算并写入成交。`)) return;
    setError("");
    setBusy(true);
    const requestId = crypto.randomUUID();
    try {
      window.localStorage.setItem(reviewKey, "submission-unknown");
      window.localStorage.setItem(requestKey, requestId);
      const ack = await advancePaper(id, targetDate, fingerprint, requestId);
      window.localStorage.setItem(reviewKey, ack.job_id);
      setJob({ job_id: ack.job_id, sessionId: id, state: "queued", progress: 0, message: "任务已提交", result: null });
    } catch (cause) {
      try {
        const receipt = await getPaperAdvanceReceipt(id, requestId);
        window.localStorage.setItem(reviewKey, receipt.job_id);
        const pending = receipt.state === "queued" || receipt.state === "running";
        setJob({ job_id: receipt.job_id, sessionId: id, state: pending ? "queued" : "error",
          progress: 0, message: pending ? "已找回推进作业" : "已找回作业回执，请核对账本", result: null });
        setBusy(pending);
        if (!pending) setError("已找回作业回执，请先核对账本再继续推进。");
      } catch {
        setBusy(false);
        setJob({ job_id: "submission-unknown", sessionId: id, state: "error", progress: 0,
          message: "提交结果待核对", result: null });
        setError(`${cause instanceof Error ? cause.message : "提交结果未知"}。请求可能已到达 StockManager，请先核对账本。`);
      }
      void client.invalidateQueries({ queryKey: queryKeys.paperSession(id) });
    }
  };

  const releaseReview = async () => {
    if (!id || job?.sessionId !== id || job.state !== "error") return;
    try {
      const current = await getPaperStatus(id);
      client.setQueryData([...queryKeys.paperSession(id), "status"], current);
      const ledgerDate = current.snapshot?.as_of_date ?? "尚未推进";
      const operation = current.advance_operation;
      const reviewJobId = operation && ["queued", "running", "needs_review"].includes(operation.state)
        ? operation.job_id : job.job_id;
      const jobLabel = reviewJobId === "submission-unknown" ? "提交请求" : `作业 ${reviewJobId}`;
      if (!window.confirm(`${jobLabel}的结果不确定。当前账本日期：${ledgerDate}。请先核对持仓和成交；确认已完成核对并解除推进锁定？`)) return;
      if (operation && ["queued", "running", "needs_review"].includes(operation.state)) {
        if (!current.state_fingerprint) throw new Error("当前账本缺少状态指纹");
        await acknowledgePaperAdvanceReview(id, reviewJobId, current.state_fingerprint);
        client.setQueryData([...queryKeys.paperSession(id), "status"], {
          ...current, advance_operation: { ...operation, state: "reviewed" },
        });
      }
      window.localStorage.removeItem(`paper-advance-job:${id}`);
      window.localStorage.removeItem(`paper-advance-request:${id}`);
      setJob(null);
      setError("");
      void client.invalidateQueries({ queryKey: queryKeys.paperSessions() });
      void client.invalidateQueries({ queryKey: queryKeys.paperSession(id) });
    } catch (cause) {
      setError(cause instanceof Error ? `账本无法复读：${cause.message}` : "账本无法复读");
    }
  };

  const askAgent = (prompt: string) => {
    if (!id) return;
    setAgentPrompt((previous) => ({ sessionId: id, text: prompt, nonce: (previous?.nonce ?? 0) + 1 }));
  };

  const refreshAll = () => {
    setError("");
    void client.invalidateQueries({ queryKey: queryKeys.paperSessions() });
    if (id) void client.invalidateQueries({ queryKey: queryKeys.paperSession(id) });
  };

  const snapshot = status.data?.snapshot;
  const positionsCount = Object.keys(snapshot?.positions ?? {}).length;
  const initialCash = status.data?.session?.initial_cash ?? active?.initial_cash ?? 0;
  const returnPct = snapshot && initialCash > 0 ? snapshot.equity / initialCash - 1 : null;
  const recordedEquity = [...(curve.data?.daily_records ?? [])]
    .filter((record) => Number.isFinite(record.equity) && record.equity > 0 && record.date)
    .sort((a, b) => a.date.localeCompare(b.date));
  const latestEquity = recordedEquity[recordedEquity.length - 1];
  const previousEquity = recordedEquity[recordedEquity.length - 2];
  const dayChange = latestEquity && previousEquity && latestEquity.date.slice(0, 10) === snapshot?.as_of_date
    ? latestEquity.equity - previousEquity.equity : null;
  const dayChangePct = dayChange != null && previousEquity && previousEquity.equity > 0
    ? dayChange / previousEquity.equity : null;
  const connectionError = sessions.error instanceof Error ? sessions.error.message : "";
  const sectionError = [...(missingSession ? [] : [status]), curve, trades, plan]
    .map((query) => query.error instanceof Error ? query.error.message : "")
    .find(Boolean);
  const selectedAllocator = allocatorOptions.find((item) => item.path === allocatorPath);

  return (
    <div className="mx-auto max-w-[1600px] space-y-5 pb-10 text-ui-ink">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div><p className="text-xs font-semibold uppercase tracking-[0.2em] text-ui-accent">Paper trading</p><h1 className="mt-1 text-2xl font-semibold">模拟盘工作台</h1><p className="mt-1 text-sm text-ui-muted">策略账本由 StockManager 维护，Agent 解释策略行为并跟踪判断。</p></div>
        <div className="flex flex-wrap items-center gap-2">
          {(rows.length > 0 || selected) && <label className="text-xs text-ui-muted">当前会话<select aria-label="当前模拟盘会话" value={requestedId} onChange={(event) => setParams({ session: event.target.value })} className="ml-2 max-w-64 rounded-xl border border-ui-strong bg-ui-panel px-3 py-2 text-sm text-ui-ink">{selected && !listed && <option value={selected}>{direct ? "子策略" : missingSession ? "未找到" : "验证中"} · {selected}</option>}{rows.map((row) => <option key={row.session_id} value={row.session_id}>{row.params?.kind === "composite" ? "组合" : row.strategy} · {row.session_id}</option>)}</select></label>}
          <button className="rounded-lg border border-ui-strong px-3 py-2 text-sm hover:bg-ui-hover" onClick={refreshAll}><RefreshCw className="inline h-4 w-4" /> 刷新</button>
          <button className="rounded-lg bg-ui-accent px-3 py-2 text-sm font-medium text-ui-onAccent hover:bg-ui-accent" onClick={() => setCreateOpen(!createOpen)}>新建会话</button>
        </div>
      </header>

      {(error || connectionError) && <div role="alert" className="rounded-lg border border-ui-danger bg-ui-danger/50 p-3 text-sm text-ui-danger">{error || connectionError}。请确认 StockManager Web 服务已启动（默认 127.0.0.1:8787）。</div>}
      {sectionError && <div role="alert" className="rounded-lg border border-ui-warning bg-ui-warning/40 p-3 text-sm text-ui-warning">模拟盘部分数据读取失败：{sectionError}。可稍后刷新重试。</div>}

      {createOpen && <section className={card}>
        <h2 className="mb-3 font-medium">创建策略模拟会话</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-xs text-ui-muted">类型<select className="mt-1 w-full rounded bg-ui-hover p-2 text-sm text-ui-ink" value={kind} onChange={(e) => setKind(e.target.value as "single" | "composite")}><option value="single">单策略</option><option value="composite">组合策略</option></select></label>
          {kind === "single" ? <>
            <label className="text-xs text-ui-muted">策略<select className="mt-1 w-full rounded bg-ui-hover p-2 text-sm text-ui-ink" value={strategy} onChange={(e) => setStrategy(e.target.value)}><option value="">选择策略</option>{strategyOptions.map((item) => <option key={item.name}>{item.name}</option>)}</select></label>
            <label className="text-xs text-ui-muted">配置<select className="mt-1 w-full rounded bg-ui-hover p-2 text-sm text-ui-ink" value={config} onChange={(e) => setConfig(e.target.value)}><option value="">默认配置</option>{configOptions.map((item) => <option key={item.name}>{item.name}</option>)}</select></label>
            <label className="text-xs text-ui-muted">起始日期<input type="date" className="mt-1 w-full rounded bg-ui-hover p-2 text-sm text-ui-ink" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></label>
          </> : <label className="text-xs text-ui-muted sm:col-span-2">组合配置<select className="mt-1 w-full rounded bg-ui-hover p-2 text-sm text-ui-ink" value={allocatorPath} onChange={(e) => {
            const item = allocatorOptions.find((option) => option.path === e.target.value);
            setAllocatorPath(e.target.value);
            if (item) setCash(String(item.initial_cash));
          }}><option value="">选择组合配置</option>{allocatorOptions.map((item) => <option key={item.path} value={item.path}>{item.name} · {money(item.initial_cash)}</option>)}</select></label>}
          <label className="text-xs text-ui-muted">初始资金<input type="number" min="1" className="mt-1 w-full rounded bg-ui-hover p-2 text-sm text-ui-ink" value={cash} onChange={(e) => setCash(e.target.value)} /></label>
        </div>
        {kind === "composite" && selectedAllocator && <p className="mt-3 text-xs text-ui-muted">起始日 {selectedAllocator.start_date} · 子策略 {selectedAllocator.sleeves.join(" / ")}{selectedAllocator.status ? ` · 配置状态 ${selectedAllocator.status}` : ""}{selectedAllocator.description ? ` · ${selectedAllocator.description}` : ""}</p>}
        {kind === "composite" && allocators.isError && <p className="mt-3 text-xs text-ui-warning">组合配置列表不可用，请更新并重启 StockManager Web 服务。</p>}
        <p className="mt-3 text-xs text-ui-faint">历史起始日会重放策略过程；重放结果不代表当时实际前瞻跟踪记录。</p>
        <button disabled={busy || Number(cash) <= 0 || (kind === "single" && (!strategy || !startDate)) || (kind === "composite" && !allocatorPath)} className="mt-4 rounded bg-ui-accent px-4 py-2 text-sm text-ui-onAccent disabled:opacity-40" onClick={() => void create()}>创建</button>
      </section>}

      {missingSession && <div role="alert" className={`${card} text-sm text-ui-warning`}>未找到模拟盘会话 <strong className="break-all">{selected}</strong>。请从上方选择已有会话，或刷新列表后重试。</div>}
      {!id && !selected && !sessions.isLoading && !connectionError && <div className={`${card} text-sm text-ui-muted`}>还没有策略模拟会话。创建会话后，可以查看净值、持仓、成交和下一日计划。</div>}

      {id && <>
        {isCompositeChild && <div role="status" className={`${card} text-sm text-ui-muted`}>当前为组合子策略账本，可查看持仓、成交和 Agent 取证；请在所属组合账户推进交易日。{parentId && <Link to={`/paper?session=${encodeURIComponent(parentId)}`} className="ml-2 font-medium text-ui-accent underline underline-offset-2">查看组合账户</Link>}</div>}
        <nav aria-label="模拟盘内容跳转" className="sticky top-0 z-20 flex gap-2 rounded-lg bg-ui-canvas/95 py-2 backdrop-blur xl:hidden">
          <button className="rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm text-ui-body" onClick={() => agentSection.current?.scrollIntoView({ behavior: "auto", block: "start" })}>查看 Agent 对话</button>
          <button className="rounded-lg border border-ui-strong bg-ui-panel px-3 py-2 text-sm text-ui-body" onClick={() => ledgerSection.current?.scrollIntoView({ behavior: "auto", block: "start" })}>查看模拟账本</button>
        </nav>
        <div className="grid items-start gap-4 xl:grid-cols-[minmax(310px,0.82fr)_minmax(0,1.5fr)]">
        <div ref={agentSection} className="h-[620px] min-w-0 scroll-mt-12 xl:sticky xl:top-0 xl:h-[calc(100vh-170px)]"><Chat key={id} paperSessionId={id} embedded promptRequest={agentPrompt?.sessionId === id ? agentPrompt : undefined} /></div>
        <div ref={ledgerSection} role="region" aria-label="模拟盘账本" className="min-w-0 space-y-4 scroll-mt-12">
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-ui-line bg-ui-panel/70 px-4 py-3 text-xs text-ui-muted"><span className="rounded-full bg-ui-accent/10 px-2 py-1 text-ui-accent">Paper · {status.data?.kind === "composite" ? "组合策略" : "单策略"}</span><span>最新快照 · {snapshot?.as_of_date ?? "尚未推进"}</span><span>唯一模拟账本：StockManager</span></div>
        <section className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-4">
          <div className={card}><p className="text-xs text-ui-muted">总权益</p><p className="mt-2 text-xl font-semibold">{money(snapshot?.equity)}</p><p className="mt-1 text-xs text-ui-faint">截至 {snapshot?.as_of_date ?? "尚未推进"}</p></div>
          <div className={card}><p className="text-xs text-ui-muted">累计收益</p><p className="mt-2 text-xl font-semibold">{percent(returnPct)}</p><p className="mt-1 text-xs text-ui-faint">初始 {money(initialCash)}</p></div>
          <div className={card}><p className="text-xs text-ui-muted">现金</p><p className="mt-2 text-xl font-semibold">{money(snapshot?.cash)}</p><p className="mt-1 text-xs text-ui-faint">{positionsCount} 只持仓</p></div>
          <div className={card}><p className="text-xs text-ui-muted">最近账本日变动</p><p className="mt-2 text-xl font-semibold tabular-nums">{signedMoney(dayChange)}</p><p className="mt-1 text-xs text-ui-faint">{snapshot?.as_of_date ?? "—"} · {dayChangePct == null ? "无前一日记录" : `${dayChangePct >= 0 ? "+" : ""}${percent(dayChangePct)}`}</p></div>
        </section>

        <PerformanceChart curve={curve.data} status={status.data} />

        <div className="grid gap-4">
          <HoldingsCard snapshot={snapshot} onAsk={() => askAgent("分析这个模拟盘的当前持仓和风险")} />
          <PlanCard key={id} plan={plan.data} status={status.data} loading={plan.isLoading}
            onAsk={() => askAgent("解释这个模拟盘的下一日计划及依据")} />
        </div>

        {status.data?.kind === "composite" && <CompositeDecision status={status.data} onAsk={() => askAgent("为什么这个组合策略选择或切换了当前子策略？请用模拟盘决策和成交解释")} />}

        <TradesCard key={id} trades={trades.data} totalCount={status.data?.trades_count} />

        <section id="paper-advance-controls" className={`${card} flex flex-wrap items-end gap-3`}>{!isCompositeChild && <><label className="text-xs text-ui-muted">推进至交易日<input type="date" min={active?.last_date ?? undefined} value={targetDate} onChange={(e) => setTargetDate(e.target.value)} className="mt-1 block rounded bg-ui-hover p-2 text-sm text-ui-ink" /></label><button disabled={busy || serverLocked || !targetDate || (job?.sessionId === id && job.state === "error") || !!(active?.last_date && targetDate <= active.last_date)} onClick={() => void advance()} className="rounded bg-ui-accent px-4 py-2 text-sm text-ui-onAccent disabled:opacity-40">推进模拟盘</button>{job?.sessionId === id && <span className="text-sm text-ui-body">{job.message} {job.state === "running" ? `${job.progress}%` : ""}</span>}{job?.sessionId === id && job.state === "error" && <button className="rounded border border-ui-warning px-3 py-2 text-sm text-ui-warning" onClick={() => void releaseReview()}>核对账本后解除锁定</button>}</>}<button className="ml-auto flex items-center gap-1 rounded border border-ui-strong px-3 py-2 text-sm text-ui-body" onClick={() => askAgent("总结这个模拟盘当前状态、近期成交和下一日计划")}><MessageSquareText className="h-4 w-4" /> 与 Agent 讨论</button></section>
        </div>
        </div>
      </>}
    </div>
  );
}
