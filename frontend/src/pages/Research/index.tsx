import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowRight, FlaskConical, History, Play } from "lucide-react";
import { createBacktest, getBacktestCatalog, listBacktests } from "@/api/client";

const percent = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : "—";
const metric = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—";

export default function Research() {
  const catalog = useQuery({ queryKey: ["backtest-catalog"], queryFn: getBacktestCatalog, retry: false });
  const backtests = useQuery({ queryKey: ["backtests"], queryFn: listBacktests, refetchInterval: 10_000 });
  const [form, setForm] = useState({ strategy_name: "ff_residual_csi800_main", config_name: "prod_ff_residual_csi800_tv15", start_date: "2024-01-01", end_date: "2026-01-01" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const latest = backtests.data?.[0];

  useEffect(() => {
    if (!catalog.data) return;
    setForm((current) => {
      const strategy = catalog.data!.strategies.some((item) => item.name === current.strategy_name)
        ? current.strategy_name : catalog.data!.strategies[0]?.name ?? current.strategy_name;
      const config = catalog.data!.configs.some((item) => item.name === current.config_name)
        ? current.config_name : catalog.data!.configs[0]?.name ?? current.config_name;
      return strategy === current.strategy_name && config === current.config_name
        ? current : { ...current, strategy_name: strategy, config_name: config };
    });
  }, [catalog.data]);

  const submit = async () => {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await createBacktest(form);
      setNotice(`回测已提交 · ${result.job_id || result.id}`);
      await backtests.refetch();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "回测提交失败");
    } finally { setBusy(false); }
  };

  return <div className="mx-auto max-w-7xl space-y-5 pb-10">
    <header className="flex flex-wrap items-end justify-between gap-4"><div><p className="text-xs font-semibold uppercase tracking-[0.2em] text-teal-300">Quant research</p><h1 className="mt-2 text-2xl font-semibold">策略研究</h1><p className="mt-1 text-sm text-stone-400">选择 StockManager 的版本化策略与配置，运行历史验证，再决定是否进入模拟盘观察。</p></div><Link className="inline-flex items-center gap-2 rounded-xl border border-stone-700 px-4 py-2 text-sm text-stone-200 hover:border-teal-500/50" to="/paper">打开模拟盘 <ArrowRight className="h-4 w-4" /></Link></header>
    <div className="grid gap-5 xl:grid-cols-[minmax(300px,0.9fr)_minmax(0,1.5fr)]">
      <section className="rounded-2xl border border-stone-800 bg-stone-900/70 p-5"><div className="flex items-center gap-2"><FlaskConical className="h-5 w-5 text-teal-300" /><h2 className="font-semibold">运行回测</h2></div><p className="mt-2 text-xs leading-5 text-stone-500">此回测提供量化策略证据，不代表包含 Agent 复核的完整交易流程。</p>
        <div className="mt-5 space-y-4">
          <label className="block text-xs text-stone-400">策略<select aria-label="strategy_name" value={form.strategy_name} onChange={(event) => setForm({ ...form, strategy_name: event.target.value })} className="mt-1.5 w-full rounded-xl border border-stone-700 bg-stone-950 px-3 py-2.5 text-sm text-stone-100">{(catalog.data?.strategies ?? [{ name: form.strategy_name, sha1: "" }]).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label>
          <label className="block text-xs text-stone-400">配置<select aria-label="config_name" value={form.config_name} onChange={(event) => setForm({ ...form, config_name: event.target.value })} className="mt-1.5 w-full rounded-xl border border-stone-700 bg-stone-950 px-3 py-2.5 text-sm text-stone-100">{(catalog.data?.configs ?? [{ name: form.config_name, sha1: "" }]).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label>
          <div className="grid grid-cols-2 gap-3">{(["start_date", "end_date"] as const).map((key) => <label key={key} className="block text-xs text-stone-400">{key === "start_date" ? "开始日期" : "结束日期"}<input aria-label={key} type="date" value={form[key]} onChange={(event) => setForm({ ...form, [key]: event.target.value })} className="mt-1.5 w-full rounded-xl border border-stone-700 bg-stone-950 px-3 py-2.5 text-sm text-stone-100" /></label>)}</div>
        </div>
        <button onClick={() => void submit()} disabled={busy || catalog.isError || form.start_date >= form.end_date} className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-teal-500 px-4 py-2.5 text-sm font-semibold text-stone-950 disabled:opacity-40"><Play className="h-4 w-4" />{busy ? "正在提交" : "提交回测"}</button>
        {catalog.isError && <p role="alert" className="mt-3 text-xs text-amber-300">策略目录不可用，请检查 StockManager 连接。</p>}{error && <p role="alert" className="mt-3 text-xs text-rose-300">{error}</p>}{notice && <p role="status" className="mt-3 text-xs text-teal-300">{notice}</p>}
      </section>
      <div className="space-y-5">
        <section className="rounded-2xl border border-stone-800 bg-stone-900/70 p-5"><div className="flex items-center justify-between"><div><p className="text-xs text-stone-500">最近一次验证</p><h2 className="mt-1 font-semibold">{latest ? `${latest.start_date} → ${latest.end_date}` : "暂无回测记录"}</h2></div><span className="rounded-full bg-stone-800 px-2.5 py-1 text-xs text-stone-300">{latest?.status ?? "待运行"}</span></div>
          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">{[["累计收益", percent(latest?.result?.total_return)], ["Alpha", percent(latest?.result?.alpha)], ["最大回撤", percent(latest?.result?.max_drawdown)], ["Sharpe", metric(latest?.result?.sharpe)]].map(([label, value]) => <div key={label} className="rounded-xl border border-stone-800 bg-stone-950/60 p-3"><p className="text-xs text-stone-500">{label}</p><strong className="mt-2 block text-lg font-semibold">{value}</strong></div>)}</div>
          <p className="mt-4 text-xs text-stone-500">指标取自已保存的回测结果；任务进行中或结果缺失时显示“—”。</p>
        </section>
        <section className="rounded-2xl border border-stone-800 bg-stone-900/70 p-5"><div className="flex items-center gap-2"><History className="h-4 w-4 text-teal-300" /><h2 className="font-semibold">历史任务</h2></div><div className="mt-4 space-y-2">{(backtests.data ?? []).slice(0, 12).map((item) => <div key={item.id} className="grid gap-2 rounded-xl border border-stone-800 bg-stone-950/50 p-3 text-xs text-stone-300 sm:grid-cols-[minmax(0,1fr)_repeat(4,auto)] sm:items-center"><span>{item.start_date} → {item.end_date}</span><span>收益 {percent(item.result?.total_return)}</span><span>回撤 {percent(item.result?.max_drawdown)}</span><span>Sharpe {metric(item.result?.sharpe)}</span><span className="text-teal-300">{item.status}</span></div>)}{!backtests.isLoading && !(backtests.data?.length) && <p className="py-6 text-center text-sm text-stone-500">还没有历史回测任务</p>}</div></section>
      </div>
    </div>
  </div>;
}
