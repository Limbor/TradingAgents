import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { MessageSquareText, RefreshCw } from "lucide-react";
import {
  advancePaper, createPaperSession, getPaperCurve, getPaperJob, getPaperPlan,
  getPaperStatus, getPaperTrades, listPaperConfigs, listPaperSessions,
  listPaperStrategies, type PaperJob,
} from "@/api/paper";
import { queryKeys } from "@/api/queryKeys";
import { useGoChat } from "@/lib/chatNav";

const money = (value: number | null | undefined) =>
  value == null ? "—" : `¥${Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toFixed(2)}%`;
const card = "rounded-xl border border-stone-800 bg-stone-900 p-4";

export default function Paper() {
  const client = useQueryClient();
  const goChat = useGoChat();
  const [params, setParams] = useSearchParams();
  const selected = params.get("session") || "";
  const [createOpen, setCreateOpen] = useState(false);
  const [kind, setKind] = useState<"single" | "composite">("single");
  const [strategy, setStrategy] = useState("");
  const [config, setConfig] = useState("");
  const [allocatorPath, setAllocatorPath] = useState("config/allocators/phase61_follow_20260701_100k.json");
  const [startDate, setStartDate] = useState("");
  const [cash, setCash] = useState("1000000");
  const [targetDate, setTargetDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<PaperJob | null>(null);
  const [error, setError] = useState("");

  const sessions = useQuery({ queryKey: queryKeys.paperSessions(), queryFn: listPaperSessions, retry: false });
  const strategies = useQuery({ queryKey: ["paper-strategies"], queryFn: listPaperStrategies, enabled: createOpen });
  const configs = useQuery({ queryKey: ["paper-configs"], queryFn: listPaperConfigs, enabled: createOpen });
  const rows = sessions.data ?? [];
  const active = rows.find((row) => row.session_id === selected) ?? rows[0];
  const id = active?.session_id ?? "";
  const status = useQuery({ queryKey: [...queryKeys.paperSession(id), "status"], queryFn: () => getPaperStatus(id), enabled: !!id, retry: false });
  const curve = useQuery({ queryKey: [...queryKeys.paperSession(id), "equity"], queryFn: () => getPaperCurve(id), enabled: !!id, retry: false });
  const trades = useQuery({ queryKey: [...queryKeys.paperSession(id), "trades"], queryFn: () => getPaperTrades(id), enabled: !!id, retry: false });
  const plan = useQuery({ queryKey: [...queryKeys.paperSession(id), "plan"], queryFn: () => getPaperPlan(id), enabled: !!id, retry: false });

  useEffect(() => {
    if (!job || (job.state !== "queued" && job.state !== "running")) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await getPaperJob(job.job_id);
        setJob(next);
        if (next.state === "success") {
          setBusy(false);
          void client.invalidateQueries({ queryKey: queryKeys.paperSessions() });
          void client.invalidateQueries({ queryKey: queryKeys.paperSession(id) });
        } else if (next.state === "error") {
          setBusy(false);
          setError(next.message || "模拟盘推进失败");
        }
      } catch (cause) {
        setBusy(false);
        setError(cause instanceof Error ? cause.message : "无法读取任务状态");
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [job, client, id]);

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
    if (!window.confirm(`将模拟盘 ${id} 推进到 ${targetDate}？StockManager 会按策略计算并写入成交。`)) return;
    setError("");
    setBusy(true);
    try {
      const ack = await advancePaper(id, targetDate);
      setJob({ job_id: ack.job_id, state: "queued", progress: 0, message: "任务已提交", result: null });
    } catch (cause) {
      setBusy(false);
      setError(cause instanceof Error ? cause.message : "推进失败");
    }
  };

  const askAgent = (prompt: string) => {
    if (!id) return;
    goChat({ prompt, autoSend: true, context: {
      paper_session_context: { session_id: id, strategy: active?.strategy, as_of_date: status.data?.snapshot?.as_of_date },
    } });
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

  return (
    <div className="mx-auto max-w-7xl space-y-5 pb-10 text-stone-100">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div><h1 className="text-xl font-semibold">策略模拟盘</h1><p className="text-sm text-stone-400">StockManager 策略账本 · TradingAgents 分析工作台</p></div>
        <div className="flex gap-2">
          <button className="rounded-lg border border-stone-700 px-3 py-2 text-sm hover:bg-stone-800" onClick={() => void sessions.refetch()}><RefreshCw className="inline h-4 w-4" /> 刷新</button>
          <button className="rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium hover:bg-teal-500" onClick={() => setCreateOpen(!createOpen)}>新建会话</button>
        </div>
      </header>

      {(error || connectionError) && <div role="alert" className="rounded-lg border border-rose-700 bg-rose-950/50 p-3 text-sm text-rose-200">{error || connectionError}。请确认 StockManager Web 服务已启动（默认 127.0.0.1:8787）。</div>}
      {sectionError && <div role="alert" className="rounded-lg border border-amber-700 bg-amber-950/40 p-3 text-sm text-amber-200">模拟盘部分数据读取失败：{sectionError}。可稍后刷新重试。</div>}

      {createOpen && <section className={card}>
        <h2 className="mb-3 font-medium">创建策略模拟会话</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-xs text-stone-400">类型<select className="mt-1 w-full rounded bg-stone-800 p-2 text-sm text-white" value={kind} onChange={(e) => setKind(e.target.value as "single" | "composite")}><option value="single">单策略</option><option value="composite">组合策略</option></select></label>
          {kind === "single" ? <>
            <label className="text-xs text-stone-400">策略<select className="mt-1 w-full rounded bg-stone-800 p-2 text-sm text-white" value={strategy} onChange={(e) => setStrategy(e.target.value)}><option value="">选择策略</option>{strategies.data?.map((item) => <option key={item.name}>{item.name}</option>)}</select></label>
            <label className="text-xs text-stone-400">配置<select className="mt-1 w-full rounded bg-stone-800 p-2 text-sm text-white" value={config} onChange={(e) => setConfig(e.target.value)}><option value="">默认配置</option>{configs.data?.map((item) => <option key={item.name}>{item.name}</option>)}</select></label>
            <label className="text-xs text-stone-400">起始日期<input type="date" className="mt-1 w-full rounded bg-stone-800 p-2 text-sm text-white" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></label>
          </> : <label className="text-xs text-stone-400 sm:col-span-2">组合配置路径<input className="mt-1 w-full rounded bg-stone-800 p-2 text-sm text-white" value={allocatorPath} onChange={(e) => setAllocatorPath(e.target.value)} /></label>}
          <label className="text-xs text-stone-400">初始资金<input type="number" min="1" className="mt-1 w-full rounded bg-stone-800 p-2 text-sm text-white" value={cash} onChange={(e) => setCash(e.target.value)} /></label>
        </div>
        <button disabled={busy || Number(cash) <= 0 || (kind === "single" && (!strategy || !startDate)) || (kind === "composite" && !allocatorPath)} className="mt-4 rounded bg-teal-600 px-4 py-2 text-sm disabled:opacity-40" onClick={() => void create()}>创建</button>
      </section>}

      {rows.length > 0 && <div className="flex flex-wrap gap-2">{rows.map((row) => <button key={row.session_id} onClick={() => setParams({ session: row.session_id })} className={`rounded-lg border px-3 py-2 text-sm ${id === row.session_id ? "border-teal-500 bg-teal-950/40 text-teal-200" : "border-stone-700 bg-stone-900 text-stone-300"}`}>{row.params?.kind === "composite" ? "组合" : row.strategy} · {row.last_date ?? "待启动"}</button>)}</div>}

      {!id && !sessions.isLoading && !connectionError && <div className={`${card} text-sm text-stone-400`}>还没有策略模拟会话。创建会话后，可以查看净值、持仓、成交和下一日计划。</div>}

      {id && <>
        <section className="grid gap-3 md:grid-cols-4">
          <div className={card}><p className="text-xs text-stone-400">总权益</p><p className="mt-2 text-xl font-semibold">{money(snapshot?.equity)}</p><p className="mt-1 text-xs text-stone-500">截至 {snapshot?.as_of_date ?? "尚未推进"}</p></div>
          <div className={card}><p className="text-xs text-stone-400">累计收益</p><p className="mt-2 text-xl font-semibold">{percent(returnPct)}</p><p className="mt-1 text-xs text-stone-500">初始 {money(initialCash)}</p></div>
          <div className={card}><p className="text-xs text-stone-400">现金</p><p className="mt-2 text-xl font-semibold">{money(snapshot?.cash)}</p><p className="mt-1 text-xs text-stone-500">{positions.length} 只持仓</p></div>
          <div className={card}><p className="text-xs text-stone-400">成交笔数</p><p className="mt-2 text-xl font-semibold">{status.data?.trades_count ?? "—"}</p><p className="mt-1 text-xs text-stone-500">{status.data?.kind === "composite" ? "组合策略" : "单策略"}</p></div>
        </section>

        <section className={card}>
          <div className="mb-3 flex items-center justify-between"><h2 className="font-medium">净值曲线</h2><span className="text-xs text-stone-500">{daily.length} 个交易日</span></div>
          {daily.length > 0 ? <div className="h-56"><ResponsiveContainer width="100%" height="100%"><LineChart data={daily}><CartesianGrid stroke="#44403c" strokeDasharray="3 3" /><XAxis dataKey="date" tick={{ fill: "#a8a29e", fontSize: 11 }} minTickGap={28} /><YAxis tick={{ fill: "#a8a29e", fontSize: 11 }} domain={["auto", "auto"]} width={75} /><Tooltip contentStyle={{ background: "#292524", border: "1px solid #57534e" }} formatter={(value) => money(Number(value))} /><Line type="monotone" dataKey="equity" stroke="#5eead4" strokeWidth={2} dot={false} /></LineChart></ResponsiveContainer></div> : <p className="py-8 text-center text-sm text-stone-500">推进到交易日后显示净值曲线</p>}
        </section>

        <div className="grid gap-4 lg:grid-cols-2">
          <section className={card}><div className="mb-3 flex items-center justify-between"><h2 className="font-medium">当前持仓</h2><button className="text-xs text-teal-300" onClick={() => askAgent("分析这个模拟盘的当前持仓和风险")}>问 Agent</button></div>{positions.length ? <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-xs text-stone-500"><tr><th className="pb-2">标的</th><th>数量</th><th>市值</th><th>当日盈亏</th></tr></thead><tbody>{positions.map(([code, pos]) => <tr key={code} className="border-t border-stone-800"><td className="py-2">{pos.name || code}<span className="block text-xs text-stone-500">{code}</span></td><td>{pos.shares}</td><td>{money(pos.value)}</td><td>{money(pos.day_pnl)}</td></tr>)}</tbody></table></div> : <p className="text-sm text-stone-500">暂无持仓</p>}</section>
          <section className={card}><div className="mb-3 flex items-center justify-between"><h2 className="font-medium">下一日计划</h2><button className="text-xs text-teal-300" onClick={() => askAgent("解释这个模拟盘的下一日计划及依据")}>问 Agent</button></div><p className="mb-2 text-xs text-stone-500">信号日期 {plan.data?.signal_date ?? "—"}</p>{plan.data?.reason && <p className="mb-2 text-sm text-stone-300">{plan.data.reason}</p>}{plan.data?.items?.length ? <div className="space-y-2">{plan.data.items.map((item, index) => <div key={`${item.code}-${index}`} className="rounded border border-stone-800 p-2 text-sm"><span className="font-medium text-teal-300">{item.action}</span> {item.name || item.code} <span className="text-stone-500">{item.code}</span><p className="text-xs text-stone-400">{item.reason || `预计变化 ${money(item.diff_value)}`}</p></div>)}</div> : <p className="text-sm text-stone-500">暂无待执行计划</p>}</section>
        </div>

        {status.data?.kind === "composite" && <section className={card}><div className="flex items-center justify-between"><h2 className="font-medium">组合决策</h2><button className="text-xs text-teal-300" onClick={() => askAgent("为什么这个组合策略选择或切换了当前子策略？请用模拟盘决策和成交解释")}>问 Agent</button></div><pre className="mt-3 overflow-auto whitespace-pre-wrap text-xs text-stone-300">{JSON.stringify(status.data.decision ?? status.data.summary ?? {}, null, 2)}</pre>{status.data.caveat && <p className="mt-2 text-xs text-amber-300">{status.data.caveat}</p>}</section>}

        <section className={card}><h2 className="mb-3 font-medium">最近成交</h2>{trades.data?.length ? <div className="max-h-72 overflow-auto"><table className="w-full text-left text-sm"><thead className="text-xs text-stone-500"><tr><th>日期</th><th>标的</th><th>方向</th><th>数量</th><th>价格</th></tr></thead><tbody>{trades.data.slice(0, 30).map((trade, index) => <tr key={index} className="border-t border-stone-800"><td className="py-2">{trade.trade_date}</td><td>{trade.name || trade.code}</td><td>{trade.side}</td><td>{trade.shares}</td><td>{money(trade.price)}</td></tr>)}</tbody></table></div> : <p className="text-sm text-stone-500">暂无成交</p>}</section>

        <section className={`${card} flex flex-wrap items-end gap-3`}><label className="text-xs text-stone-400">推进至交易日<input type="date" min={active?.last_date ?? undefined} value={targetDate} onChange={(e) => setTargetDate(e.target.value)} className="mt-1 block rounded bg-stone-800 p-2 text-sm text-white" /></label><button disabled={busy || !targetDate || !!(active?.last_date && targetDate <= active.last_date)} onClick={() => void advance()} className="rounded bg-teal-600 px-4 py-2 text-sm disabled:opacity-40">推进模拟盘</button>{job && <span className="text-sm text-stone-300">{job.message} {job.state === "running" ? `${job.progress}%` : ""}</span>}<button className="ml-auto flex items-center gap-1 rounded border border-stone-700 px-3 py-2 text-sm text-stone-200" onClick={() => askAgent("总结这个模拟盘当前状态、近期成交和下一日计划")}><MessageSquareText className="h-4 w-4" /> 与 Agent 讨论</button></section>
      </>}
    </div>
  );
}
