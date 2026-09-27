import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { MessageSquareText, RefreshCw } from "lucide-react";
import {
  acknowledgePaperAdvanceReview, advancePaper, createPaperSession, getPaperCurve, getPaperJob, getPaperPlan,
  getPaperStatus, getPaperTrades, listPaperAllocators, listPaperConfigs, listPaperSessions,
  listPaperStrategies, type PaperJob,
} from "@/api/paper";
import { queryKeys } from "@/api/queryKeys";
import { ApiHttpError } from "@/api/client";
import { CompositeDecision } from "./CompositeDecision";
import Chat from "../AgentWorkspace";

const money = (value: number | null | undefined) =>
  value == null ? "—" : `¥${Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toFixed(2)}%`;
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

  const sessions = useQuery({ queryKey: queryKeys.paperSessions(), queryFn: listPaperSessions, retry: false });
  const strategies = useQuery({ queryKey: ["paper-strategies"], queryFn: listPaperStrategies, enabled: createOpen });
  const configs = useQuery({ queryKey: ["paper-configs"], queryFn: listPaperConfigs, enabled: createOpen });
  const allocators = useQuery({ queryKey: ["paper-allocators"], queryFn: listPaperAllocators, enabled: createOpen && kind === "composite", retry: false });
  const rows = Array.isArray(sessions.data) ? sessions.data : [];
  const strategyOptions = Array.isArray(strategies.data) ? strategies.data : [];
  const configOptions = Array.isArray(configs.data) ? configs.data : [];
  const allocatorOptions = Array.isArray(allocators.data) ? allocators.data : [];
  const active = rows.find((row) => row.session_id === selected) ?? rows[0];
  const id = active?.session_id ?? "";
  useEffect(() => {
    setAgentPrompt(undefined);
  }, [id]);
  const status = useQuery({ queryKey: [...queryKeys.paperSession(id), "status"], queryFn: () => getPaperStatus(id), enabled: !!id, retry: false });
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
    if (stored) {
      window.localStorage.setItem(key, stored);
      window.sessionStorage.removeItem(key);
      setJob({ job_id: stored, sessionId: id, state: "queued", progress: 0, message: "正在恢复任务状态", result: null });
      setBusy(true);
    } else if (serverOperation && ["queued", "running", "needs_review"].includes(serverOperation.state)) {
      const needsReview = serverOperation.state === "needs_review";
      setJob({ job_id: serverOperation.job_id, sessionId: id,
        state: needsReview ? "error" : serverOperation.state === "running" ? "running" : "queued",
        progress: 0, message: needsReview ? "作业结果待核对" : "正在恢复账户推进作业", result: null });
      setBusy(!needsReview);
    }
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
    try {
      window.localStorage.setItem(reviewKey, "submission-unknown");
      const ack = await advancePaper(id, targetDate, fingerprint);
      window.localStorage.setItem(reviewKey, ack.job_id);
      setJob({ job_id: ack.job_id, sessionId: id, state: "queued", progress: 0, message: "任务已提交", result: null });
    } catch (cause) {
      setBusy(false);
      setJob({ job_id: "submission-unknown", sessionId: id, state: "error", progress: 0,
        message: "提交结果待核对", result: null });
      setError(`${cause instanceof Error ? cause.message : "提交结果未知"}。请求可能已到达 StockManager，请先核对账本。`);
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
  const positions = Object.entries(snapshot?.positions ?? {});
  const daily = curve.data?.daily_records ?? [];
  const initialCash = status.data?.session?.initial_cash ?? active?.initial_cash ?? 0;
  const returnPct = snapshot && initialCash > 0 ? snapshot.equity / initialCash - 1 : null;
  const connectionError = sessions.error instanceof Error ? sessions.error.message : "";
  const sectionError = [status, curve, trades, plan]
    .map((query) => query.error instanceof Error ? query.error.message : "")
    .find(Boolean);
  const selectedAllocator = allocatorOptions.find((item) => item.path === allocatorPath);

  return (
    <div className="mx-auto max-w-[1600px] space-y-5 pb-10 text-ui-ink">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div><p className="text-xs font-semibold uppercase tracking-[0.2em] text-ui-accent">Paper trading</p><h1 className="mt-1 text-2xl font-semibold">模拟盘工作台</h1><p className="mt-1 text-sm text-ui-muted">策略账本由 StockManager 维护，Agent 解释策略行为并跟踪判断。</p></div>
        <div className="flex flex-wrap items-center gap-2">
          {rows.length > 0 && <label className="text-xs text-ui-muted">当前会话<select aria-label="当前模拟盘会话" value={id} onChange={(event) => setParams({ session: event.target.value })} className="ml-2 max-w-64 rounded-xl border border-ui-strong bg-ui-panel px-3 py-2 text-sm text-ui-ink">{rows.map((row) => <option key={row.session_id} value={row.session_id}>{row.params?.kind === "composite" ? "组合" : row.strategy} · {row.session_id}</option>)}</select></label>}
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

      {!id && !sessions.isLoading && !connectionError && <div className={`${card} text-sm text-ui-muted`}>还没有策略模拟会话。创建会话后，可以查看净值、持仓、成交和下一日计划。</div>}

      {id && <>
        <div className="grid items-start gap-4 xl:grid-cols-[minmax(310px,0.82fr)_minmax(0,1.5fr)]">
        <div className="h-[620px] min-w-0 xl:sticky xl:top-0 xl:h-[calc(100vh-170px)]"><Chat key={id} paperSessionId={id} embedded promptRequest={agentPrompt?.sessionId === id ? agentPrompt : undefined} /></div>
        <div className="min-w-0 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-ui-line bg-ui-panel/70 px-4 py-3 text-xs text-ui-muted"><span className="rounded-full bg-ui-accent/10 px-2 py-1 text-ui-accent">Paper · {status.data?.kind === "composite" ? "组合策略" : "单策略"}</span><span>最新快照 · {snapshot?.as_of_date ?? "尚未推进"}</span><span>唯一模拟账本：StockManager</span></div>
        <section className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-4">
          <div className={card}><p className="text-xs text-ui-muted">总权益</p><p className="mt-2 text-xl font-semibold">{money(snapshot?.equity)}</p><p className="mt-1 text-xs text-ui-faint">截至 {snapshot?.as_of_date ?? "尚未推进"}</p></div>
          <div className={card}><p className="text-xs text-ui-muted">累计收益</p><p className="mt-2 text-xl font-semibold">{percent(returnPct)}</p><p className="mt-1 text-xs text-ui-faint">初始 {money(initialCash)}</p></div>
          <div className={card}><p className="text-xs text-ui-muted">现金</p><p className="mt-2 text-xl font-semibold">{money(snapshot?.cash)}</p><p className="mt-1 text-xs text-ui-faint">{positions.length} 只持仓</p></div>
          <div className={card}><p className="text-xs text-ui-muted">成交笔数</p><p className="mt-2 text-xl font-semibold">{status.data?.trades_count ?? "—"}</p><p className="mt-1 text-xs text-ui-faint">{status.data?.kind === "composite" ? "组合策略" : "单策略"}</p></div>
        </section>

        <section className={card}>
          <div className="mb-3 flex items-center justify-between"><h2 className="font-medium">净值曲线</h2><span className="text-xs text-ui-faint">{daily.length} 个交易日</span></div>
          {daily.length > 0 ? <div className="h-56"><ResponsiveContainer width="100%" height="100%"><LineChart data={daily}><CartesianGrid stroke="rgb(var(--ui-line))" strokeDasharray="3 3" /><XAxis dataKey="date" tick={{ fill: "rgb(var(--ui-muted))", fontSize: 12 }} minTickGap={28} /><YAxis tick={{ fill: "rgb(var(--ui-muted))", fontSize: 12 }} domain={["auto", "auto"]} width={75} /><Tooltip contentStyle={{ backgroundColor: "rgb(var(--ui-panel))", color: "rgb(var(--ui-ink))", border: "1px solid rgb(var(--ui-strong))", borderRadius: 6 }} formatter={(value) => money(Number(value))} /><Line type="monotone" dataKey="equity" stroke="rgb(var(--ui-accent))" strokeWidth={2} dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer></div> : <p className="py-8 text-center text-sm text-ui-faint">推进到交易日后显示净值曲线</p>}
        </section>

        <div className="grid gap-4 2xl:grid-cols-2">
          <section className={card}><div className="mb-3 flex items-center justify-between"><h2 className="font-medium">当前持仓</h2><button className="text-xs text-ui-accent" onClick={() => askAgent("分析这个模拟盘的当前持仓和风险")}>问 Agent</button></div>{positions.length ? <div className="overflow-x-auto"><table className="w-full min-w-[650px] text-left text-sm"><thead className="text-xs text-ui-faint"><tr><th className="pb-2">标的</th><th>数量</th><th>成本 / 现价</th><th>市值 / 权重</th><th>浮动盈亏</th><th>当日盈亏</th></tr></thead><tbody>{positions.map(([code, pos]) => <tr key={code} className="border-t border-ui-line"><td className="py-2">{pos.name || code}<span className="block text-xs text-ui-faint">{code}</span></td><td>{pos.shares}</td><td>{money(pos.avg_cost)}<span className="block text-xs text-ui-faint">{money(pos.last_price)}</span></td><td>{money(pos.value)}<span className="block text-xs text-ui-faint">{snapshot?.equity ? percent(pos.value / snapshot.equity) : "—"}</span></td><td>{money((pos.last_price - pos.avg_cost) * pos.shares)}</td><td>{money(pos.day_pnl)}</td></tr>)}</tbody></table></div> : <p className="text-sm text-ui-faint">暂无持仓</p>}</section>
          <section className={card}><div className="mb-3 flex items-center justify-between"><h2 className="font-medium">下一日计划</h2><button className="text-xs text-ui-accent" onClick={() => askAgent("解释这个模拟盘的下一日计划及依据")}>问 Agent</button></div><p className="mb-2 text-xs text-ui-faint">信号日期 {plan.data?.signal_date ?? "—"}{plan.data?.active_sleeve ? ` · 当前子策略 ${plan.data.active_sleeve}` : ""}</p>{plan.data?.reason && <p className="mb-2 text-sm text-ui-body">{plan.data.reason}</p>}{plan.data?.items?.length ? <div className="space-y-2">{plan.data.items.map((item, index) => <div key={`${item.code}-${index}`} className="rounded border border-ui-line p-2 text-sm"><span className="font-medium text-ui-accent">{item.action}</span> {item.name || item.code} <span className="text-ui-faint">{item.code}</span><p className="text-xs text-ui-muted">{item.reason || `预计变化 ${money(item.diff_value)}`}</p></div>)}</div> : <p className="text-sm text-ui-faint">{plan.isLoading ? "正在读取计划…" : plan.data ? "当前信号无调仓动作" : "尚无计划"}</p>}</section>
        </div>

        {status.data?.kind === "composite" && <CompositeDecision status={status.data} onAsk={() => askAgent("为什么这个组合策略选择或切换了当前子策略？请用模拟盘决策和成交解释")} />}

        <section className={card}><h2 className="mb-3 font-medium">最近成交</h2>{trades.data?.length ? <div className="max-h-72 overflow-auto"><table className="w-full text-left text-sm"><thead className="text-xs text-ui-faint"><tr><th>日期</th><th>标的</th><th>方向</th><th>数量</th><th>价格</th></tr></thead><tbody>{trades.data.slice(0, 30).map((trade, index) => <tr key={index} className="border-t border-ui-line"><td className="py-2">{trade.trade_date}</td><td>{trade.name || trade.code}</td><td>{trade.side}</td><td>{trade.shares}</td><td>{money(trade.price)}</td></tr>)}</tbody></table></div> : <p className="text-sm text-ui-faint">暂无成交</p>}</section>

        <section id="paper-advance-controls" className={`${card} flex flex-wrap items-end gap-3`}><label className="text-xs text-ui-muted">推进至交易日<input type="date" min={active?.last_date ?? undefined} value={targetDate} onChange={(e) => setTargetDate(e.target.value)} className="mt-1 block rounded bg-ui-hover p-2 text-sm text-ui-ink" /></label><button disabled={busy || serverLocked || !targetDate || (job?.sessionId === id && job.state === "error") || !!(active?.last_date && targetDate <= active.last_date)} onClick={() => void advance()} className="rounded bg-ui-accent px-4 py-2 text-sm text-ui-onAccent disabled:opacity-40">推进模拟盘</button>{job?.sessionId === id && <span className="text-sm text-ui-body">{job.message} {job.state === "running" ? `${job.progress}%` : ""}</span>}{job?.sessionId === id && job.state === "error" && <button className="rounded border border-ui-warning px-3 py-2 text-sm text-ui-warning" onClick={() => void releaseReview()}>核对账本后解除锁定</button>}<button className="ml-auto flex items-center gap-1 rounded border border-ui-strong px-3 py-2 text-sm text-ui-body" onClick={() => askAgent("总结这个模拟盘当前状态、近期成交和下一日计划")}><MessageSquareText className="h-4 w-4" /> 与 Agent 讨论</button></section>
        </div>
        </div>
      </>}
    </div>
  );
}
