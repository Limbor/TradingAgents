import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BarChart3,
  BriefcaseBusiness,
  ClipboardList,
  Edit3,
  Plus,
  RotateCcw,
  Save,
  ShieldAlert,
  Trash2,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { AdjustPositionDialog } from "@/components/Portfolio/AdjustPositionDialog";
import { PlanListCard } from "@/components/Portfolio/PlanListCard";
import {
  adjustHolding,
  deleteHolding,
  listHoldings,
  advanceTradingDay,
  upsertHolding,
  type Holding,
} from "@/api/client";
import {
  buildPortfolioSummary,
  holdingMarketValue,
  holdingPnl,
  formatMoney,
  formatNumber,
} from "@/utils/portfolio";
import { displayNameOf } from "@/components/common/StockName";
import { queryKeys } from "@/api/queryKeys";

type HoldingForm = {
  symbol: string;
  quantity: string;
  avg_cost: string;
  current_price: string;
  notes: string;
};

const EMPTY_FORM: HoldingForm = {
  symbol: "",
  quantity: "",
  avg_cost: "",
  current_price: "",
  notes: "",
};

export default function Portfolio() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const holdingsQuery = useQuery({
    queryKey: queryKeys.holdings(),
    queryFn: listHoldings,
  });
  const [form, setForm] = useState<HoldingForm>(EMPTY_FORM);
  const [editingSymbol, setEditingSymbol] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [adjustTarget, setAdjustTarget] = useState<{ holding: Holding; action: "add" | "reduce" } | null>(null);
  const [adjusting, setAdjusting] = useState(false);
  const [adjustError, setAdjustError] = useState<string | null>(null);

  const holdings = holdingsQuery.data ?? [];
  const summary = useMemo(() => buildPortfolioSummary(holdings), [holdings]);
  const sortedHoldings = useMemo(
    () =>
      [...holdings].sort(
        (a, b) => holdingMarketValue(b) - holdingMarketValue(a)
      ),
    [holdings]
  );

  const save = async () => {
    const symbol = form.symbol.trim().toUpperCase();
    const quantity = Number(form.quantity);
    const avgCost = Number(form.avg_cost);
    const currentPrice = form.current_price.trim() ? Number(form.current_price) : null;
    if (!symbol || !Number.isFinite(quantity) || !Number.isFinite(avgCost)) {
      setError("请填写有效的代码、数量和成本。");
      return;
    }
    if (quantity < 0 || avgCost < 0 || (currentPrice !== null && currentPrice < 0)) {
      setError("数量、成本和现价不能为负。");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await upsertHolding({
        symbol,
        quantity,
        avg_cost: avgCost,
        current_price: currentPrice,
        notes: form.notes.trim() || null,
      });
      await holdingsQuery.refetch();
      resetForm();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (symbol: string) => {
    await deleteHolding(symbol);
    if (editingSymbol === symbol) resetForm();
    await holdingsQuery.refetch();
  };

  const edit = (holding: Holding) => {
    setEditingSymbol(holding.symbol);
    setForm({
      symbol: holding.symbol,
      quantity: String(holding.quantity),
      avg_cost: String(holding.avg_cost),
      current_price: holding.current_price === null ? "" : String(holding.current_price),
      notes: holding.notes ?? "",
    });
    setError(null);
  };

  const adjust = async (action: "add" | "reduce", quantity: number, price: number) => {
    if (!adjustTarget) return;
    const holding = adjustTarget.holding;
    setAdjusting(true);
    setAdjustError(null);
    setNotice(null);
    try {
      const result = await adjustHolding(holding.symbol, { action, quantity, price });
      await holdingsQuery.refetch();
      const name = displayNameOf(holding.name, holding.symbol);
      if (action === "add") {
        setNotice(`已加仓 ${name} ${quantity} 股 @ ${price}`);
      } else if (result.closed) {
        setNotice(`已清仓 ${name}，实现盈亏 ${formatPnl(result.realized_pnl)}`);
      } else {
        setNotice(`已减仓 ${name} ${quantity} 股 @ ${price}，实现盈亏 ${formatPnl(result.realized_pnl)}`);
      }
      setAdjustTarget(null);
    } catch (exc) {
      setAdjustError(exc instanceof Error ? exc.message : "操作失败");
    } finally {
      setAdjusting(false);
    }
  };

  const resetForm = () => {
    setEditingSymbol(null);
    setForm(EMPTY_FORM);
    setError(null);
  };

  const refreshPrices = async () => {
    setRefreshing(true);
    setError(null);
    try {
      const result = await advanceTradingDay();
      await holdingsQuery.refetch();
      await queryClient.invalidateQueries({ queryKey: queryKeys.plans() });
      await queryClient.invalidateQueries({ queryKey: queryKeys.reflectionCases() });
      if (result.plan_alerts.length) {
        setError(`${result.plan_alerts.length} 个计划触发提醒，见下方"交易计划"`);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "刷新失败");
    } finally {
      setRefreshing(false);
    }
  };

  const openRiskScan = () => {
    navigate("/chat", {
      state: { prompt: "分析当前持仓风险", autoSend: true },
    });
  };

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-stone-800 pb-5">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-teal-300">
            Portfolio
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-stone-50">持仓管理</h2>
          <p className="mt-1 text-sm text-stone-500">仓位、盈亏、集中度和风险扫描入口</p>
        </div>
        <button
          onClick={openRiskScan}
          disabled={holdings.length === 0}
          className="inline-flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm font-medium text-amber-200 transition hover:border-amber-400/60 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <ShieldAlert className="h-4 w-4" />
          风险扫描
        </button>
        <button
          onClick={refreshPrices}
          disabled={holdings.length === 0 || refreshing}
          className="inline-flex items-center gap-2 rounded-lg border border-teal-500/30 bg-teal-500/10 px-3 py-2 text-sm font-medium text-teal-200 transition hover:border-teal-400/60 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RotateCcw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          进入下一交易日
        </button>
      </div>

      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Metric label="持仓数量" value={summary.count} />
        <Metric label="持仓市值" value={formatMoney(summary.value)} />
        <Metric
          label="浮动盈亏"
          value={formatMoney(summary.pnl)}
          tone={summary.pnl >= 0 ? "emerald" : "red"}
          sub={`${summary.pnlPct >= 0 ? "+" : ""}${summary.pnlPct.toFixed(2)}%`}
        />
        <Metric
          label="最大仓位"
          value={summary.count ? `${summary.concentration.toFixed(1)}%` : "-"}
          tone={summary.concentration > 50 ? "amber" : "teal"}
          sub={summary.topSymbol || "暂无持仓"}
        />
      </section>

      <div className="grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
        <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2 text-stone-100">
              {editingSymbol ? (
                <Edit3 className="h-4 w-4 text-teal-300" />
              ) : (
                <Plus className="h-4 w-4 text-teal-300" />
              )}
              <h3 className="text-sm font-semibold">{editingSymbol ? "编辑持仓" : "新增持仓"}</h3>
            </div>
            {editingSymbol && (
              <button
                onClick={resetForm}
                className="inline-flex items-center gap-1 rounded border border-stone-700 px-2 py-1 text-xs text-stone-400 transition hover:border-stone-500 hover:text-stone-100"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                重置
              </button>
            )}
          </div>
          <div className="space-y-3">
            <Input
              label="股票代码"
              value={form.symbol}
              onChange={(symbol) => setForm({ ...form, symbol })}
              placeholder="贵州茅台 / 600519 / 600519.SH"
            />
            <div className="grid grid-cols-3 gap-2">
              <Input
                label="数量"
                value={form.quantity}
                onChange={(quantity) => setForm({ ...form, quantity })}
                placeholder="100"
                type="number"
              />
              <Input
                label="成本"
                value={form.avg_cost}
                onChange={(avg_cost) => setForm({ ...form, avg_cost })}
                placeholder="1500"
                type="number"
              />
              <Input
              label="现价"
              value={form.current_price}
              onChange={(current_price) => setForm({ ...form, current_price })}
              placeholder="留空自动取最新收盘"
              type="number"
            />
            </div>
            <Input
              label="备注"
              value={form.notes}
              onChange={(notes) => setForm({ ...form, notes })}
              placeholder="核心仓 / 观察仓 / 止损线..."
            />
            {error && (
              <div className="rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
                {error}
              </div>
            )}
            <button
              onClick={save}
              disabled={saving}
              className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-teal-500 px-4 py-2 text-sm font-semibold text-stone-950 transition hover:bg-teal-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Save className="h-4 w-4" />
              {saving ? "保存中..." : editingSymbol ? "保存修改" : "加入持仓"}
            </button>
          </div>
        </section>

        <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-stone-100">
              <BriefcaseBusiness className="h-4 w-4 text-teal-300" />
              <h3 className="text-sm font-semibold">持仓列表</h3>
            </div>
            <div className="flex items-center gap-2 text-xs text-stone-500">
              <ClipboardList className="h-3.5 w-3.5" />
              {summary.count} 条记录
            </div>
          </div>

          {notice && (
            <div className="mb-3 rounded border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
              {notice}
            </div>
          )}

          {holdingsQuery.isLoading ? (
            <p className="text-sm text-stone-400">Loading holdings...</p>
          ) : sortedHoldings.length === 0 ? (
            <div className="rounded-lg border border-dashed border-stone-700 bg-stone-950 px-4 py-10 text-center">
              <BriefcaseBusiness className="mx-auto h-8 w-8 text-stone-600" />
              <p className="mt-3 text-sm text-stone-400">暂无持仓记录</p>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-stone-800">
              <table className="w-full min-w-[840px] text-left text-sm">
                <thead className="bg-stone-950 text-xs uppercase text-stone-500">
                  <tr>
                    <th className="px-3 py-2">标的</th>
                    <th className="px-3 py-2 text-right">数量</th>
                    <th className="px-3 py-2 text-right">成本</th>
                    <th className="px-3 py-2 text-right">现价</th>
                    <th className="px-3 py-2 text-right">市值</th>
                    <th className="px-3 py-2 text-right">盈亏</th>
                    <th className="px-3 py-2 text-right">仓位</th>
                    <th className="px-3 py-2">备注</th>
                    <th className="px-3 py-2 text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedHoldings.map((item) => {
                    const value = holdingMarketValue(item);
                    const pnl = holdingPnl(item);
                    const weight = summary.value ? (value / summary.value) * 100 : 0;
                    const displayName = displayNameOf(item.name, item.symbol);
                    return (
                      <tr key={item.symbol} className="border-t border-stone-800">
                        <td className="px-3 py-2">
                          <div className="font-semibold text-stone-100">{displayName}</div>
                          {displayName !== item.symbol && (
                            <div className="mt-0.5 font-mono text-xs text-stone-500">{item.symbol}</div>
                          )}
                          {item.latest_analysis && (
                            <div
                              className="mt-1 max-w-64 truncate text-xs text-stone-500"
                              title={[
                                item.latest_analysis.date ? `分析日期 ${item.latest_analysis.date}` : "",
                                item.latest_analysis.summary ?? "",
                              ]
                                .filter(Boolean)
                                .join(" · ")}
                            >
                              <span className="mr-1 rounded bg-teal-500/10 px-1.5 py-0.5 text-teal-200">
                                {item.latest_analysis.rating || "最近分析"}
                              </span>
                              {item.latest_analysis.date && (
                                <span className="mr-1 text-stone-500">{item.latest_analysis.date}</span>
                              )}
                              {item.latest_analysis.summary && <span>{item.latest_analysis.summary}</span>}
                            </div>
                          )}
                          <div className="mt-1 h-1.5 w-24 overflow-hidden rounded-full bg-stone-800">
                            <div
                              className={`h-full rounded-full ${weight > 50 ? "bg-amber-300" : "bg-teal-300"}`}
                              style={{ width: `${Math.min(weight, 100)}%` }}
                            />
                          </div>
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-stone-300">{formatNumber(item.quantity)}</td>
                        <td className="px-3 py-2 text-right font-mono text-stone-300">{formatNumber(item.avg_cost)}</td>
                        <td className="px-3 py-2 text-right font-mono text-stone-300">
                          {formatNumber(item.current_price ?? item.avg_cost)}
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-stone-100">{formatMoney(value)}</td>
                        <td className={`px-3 py-2 text-right font-mono ${pnl >= 0 ? "text-emerald-300" : "text-red-300"}`}>
                          {formatMoney(pnl)}
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-stone-300">{weight.toFixed(1)}%</td>
                        <td className="max-w-40 truncate px-3 py-2 text-stone-500">{item.notes || "-"}</td>
                        <td className="px-3 py-2">
                          <div className="flex justify-end gap-1.5">
                            <button
                              onClick={() => { setNotice(null); setAdjustError(null); setAdjustTarget({ holding: item, action: "add" }); }}
                              className="rounded border border-stone-700 p-1.5 text-stone-400 transition hover:bg-stone-800 hover:text-teal-300"
                              title="加仓"
                            >
                              <TrendingUp className="h-4 w-4" />
                            </button>
                            <button
                              onClick={() => { setNotice(null); setAdjustError(null); setAdjustTarget({ holding: item, action: "reduce" }); }}
                              className="rounded border border-stone-700 p-1.5 text-stone-400 transition hover:bg-stone-800 hover:text-amber-300"
                              title="减仓"
                            >
                              <TrendingDown className="h-4 w-4" />
                            </button>
                            <button
                              onClick={() => edit(item)}
                              className="rounded border border-stone-700 p-1.5 text-stone-400 transition hover:bg-stone-800 hover:text-teal-300"
                              title="编辑"
                            >
                              <Edit3 className="h-4 w-4" />
                            </button>
                            <button
                              onClick={() => remove(item.symbol)}
                              className="rounded border border-stone-700 p-1.5 text-stone-400 transition hover:bg-stone-800 hover:text-red-300"
                              title="删除"
                            >
                              <Trash2 className="h-4 w-4" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      <PlanListCard />

      {adjustTarget && (
        <AdjustPositionDialog
          holding={adjustTarget.holding}
          action={adjustTarget.action}
          submitting={adjusting}
          error={adjustError}
          onConfirm={adjust}
          onClose={() => { setAdjustTarget(null); setAdjustError(null); }}
        />
      )}

      {sortedHoldings.length > 0 && (
        <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
          <div className="mb-4 flex items-center gap-2 text-stone-100">
            <BarChart3 className="h-4 w-4 text-teal-300" />
            <h3 className="text-sm font-semibold">仓位分布</h3>
          </div>
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
	            {sortedHoldings.slice(0, 9).map((item) => {
	              const value = holdingMarketValue(item);
	              const weight = summary.value ? (value / summary.value) * 100 : 0;
	              const displayName = displayNameOf(item.name, item.symbol);
	              return (
	                <div key={item.symbol} className="rounded-lg border border-stone-800 bg-stone-950 px-3 py-2">
	                  <div className="mb-2 flex items-center justify-between gap-2 text-xs">
	                    <span className="truncate text-stone-100">{displayName}</span>
	                    <span className="text-stone-500">{weight.toFixed(1)}%</span>
	                  </div>
	                  {displayName !== item.symbol && (
	                    <div className="mb-2 font-mono text-xs text-stone-600">{item.symbol}</div>
	                  )}
	                  <div className="h-2 overflow-hidden rounded-full bg-stone-800">
                    <div
                      className={`h-full rounded-full ${weight > 50 ? "bg-amber-300" : "bg-teal-300"}`}
                      style={{ width: `${Math.min(weight, 100)}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}

function formatPnl(value: number | null) {
  if (value == null) return "0";
  return `${value >= 0 ? "+" : ""}${formatMoney(value)}`;
}

function Metric({
  label,
  value,
  sub,
  tone = "stone",
}: {
  label: string;
  value: string | number;
  sub?: string;
  tone?: "stone" | "teal" | "emerald" | "red" | "amber";
}) {
  const toneClass = {
    stone: "text-stone-50",
    teal: "text-teal-300",
    emerald: "text-emerald-300",
    red: "text-red-300",
    amber: "text-amber-300",
  }[tone];
  return (
    <div className="rounded-lg border border-stone-800 bg-stone-900 px-4 py-3">
      <div className="text-xs text-stone-500">{label}</div>
      <div className={`mt-1 font-mono text-xl font-semibold ${toneClass}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-stone-500">{sub}</div>}
    </div>
  );
}

function Input({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: "text" | "number";
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-stone-400">{label}</span>
      <input
        type={type}
        min={type === "number" ? 0 : undefined}
        step={type === "number" ? "any" : undefined}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-stone-700 bg-stone-950 px-3 py-2 text-sm text-stone-100 outline-none transition placeholder:text-stone-600 focus:border-teal-400"
      />
    </label>
  );
}
