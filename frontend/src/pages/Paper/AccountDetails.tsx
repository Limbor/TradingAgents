import { useMemo, useState } from "react";
import type { PaperCurve, PaperStatus, PaperTrade } from "@/api/paper";
import { buildRecentPaperPnl } from "./dailyPnl";

const currency = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value)
    ? `¥${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}` : "—";
const ratio = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : "—";
const signedCurrency = (value: number | null) =>
  value == null ? "—" : value === 0 ? currency(0) : `${value > 0 ? "+" : "-"}${currency(Math.abs(value))}`;
const pnlTone = (value: number | null) => value == null ? "text-ui-muted" : value > 0
  ? "text-ui-danger" : value < 0 ? "text-ui-success" : "text-ui-body";
const valid = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

export function HoldingsCard({ snapshot, onAsk }: {
  snapshot: PaperStatus["snapshot"] | undefined;
  onAsk: () => void;
}) {
  const positions = Object.entries(snapshot?.positions ?? {}).sort((a, b) => (b[1].value || 0) - (a[1].value || 0));
  const marketValue = positions.reduce((sum, [, position]) => sum + (valid(position.value) ? position.value : 0), 0);
  const exposure = valid(snapshot?.equity) && snapshot.equity > 0 ? marketValue / snapshot.equity : null;
  return <section className="rounded-xl border border-ui-line bg-ui-panel p-4">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div><h2 className="font-medium">当前持仓</h2><p className="mt-1 text-xs text-ui-faint">{positions.length} 只 · 市值 {currency(marketValue)} · 仓位 {ratio(exposure)} · 账本日 {snapshot?.as_of_date ?? "—"}</p></div>
      <button className="text-xs text-ui-accent" onClick={onAsk}>问 Agent</button>
    </div>
    {positions.length ? <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[720px] text-left text-sm">
      <thead className="text-xs text-ui-faint"><tr><th className="pb-2 font-medium">标的</th><th className="pb-2 text-right font-medium">股数</th><th className="pb-2 text-right font-medium">成本 / 现价</th><th className="pb-2 text-right font-medium">市值 / 权重</th><th className="pb-2 text-right font-medium">浮动盈亏</th><th className="pb-2 text-right font-medium">当日盈亏</th></tr></thead>
      <tbody>{positions.map(([code, position]) => {
        const pnl = valid(position.last_price) && position.last_price > 0 && valid(position.avg_cost) && position.avg_cost > 0 && valid(position.shares)
          ? (position.last_price - position.avg_cost) * position.shares : null;
        const pnlRate = valid(position.avg_cost) && position.avg_cost > 0 && valid(position.last_price) && position.last_price > 0
          ? position.last_price / position.avg_cost - 1 : null;
        const dayPnl = valid(position.day_pnl) ? position.day_pnl : null;
        const weight = valid(snapshot?.equity) && snapshot.equity > 0 && valid(position.value)
          ? position.value / snapshot.equity : null;
        return <tr key={code} className="border-t border-ui-line align-top">
          <td className="py-3 pr-2"><span className="font-medium text-ui-ink">{position.name || code}</span><span className="block text-xs text-ui-faint">{code}</span></td>
          <td className="py-3 text-right tabular-nums">{valid(position.shares) ? position.shares.toLocaleString("zh-CN") : "—"}</td>
          <td className="py-3 text-right tabular-nums">{currency(position.avg_cost)}<span className="block text-xs text-ui-faint">{currency(position.last_price)}</span></td>
          <td className="py-3 text-right tabular-nums">{currency(position.value)}<span className="block text-xs text-ui-faint">{ratio(weight)}</span></td>
          <td className={`py-3 text-right tabular-nums ${pnlTone(pnl)}`}>{signedCurrency(pnl)}<span className="block text-xs opacity-75">{ratio(pnlRate)}</span></td>
          <td className={`py-3 text-right tabular-nums ${pnlTone(dayPnl)}`}>{signedCurrency(dayPnl)}<span className="block text-xs opacity-75">{ratio(position.day_pnl_pct)}</span></td>
        </tr>;
      })}</tbody>
    </table></div> : <p className="py-7 text-center text-sm text-ui-faint">当前账本没有持仓。</p>}
    {positions.length > 0 && <p className="mt-3 text-xs text-ui-faint">浮动盈亏按账本成本和最新估值估算，未扣除未来卖出费用；“—”表示源数据未提供。</p>}
  </section>;
}

export function DailyPnlCard({ curve, ledgerDate }: { curve?: PaperCurve; ledgerDate?: string | null }) {
  const history = useMemo(() => buildRecentPaperPnl(curve, ledgerDate), [curve, ledgerDate]);
  return <section className="rounded-xl border border-ui-line bg-ui-panel p-4">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div><h2 className="font-medium">近 7 个账本交易日盈亏</h2><p className="mt-1 text-xs text-ui-faint">按 StockManager 每日账户权益核算，最新账本日 {history.asOfDate ?? "—"}</p></div>
      <div className="text-right text-xs text-ui-muted">区间合计 <strong className={`ml-1 text-sm tabular-nums ${pnlTone(history.change)}`}>{signedCurrency(history.change)}</strong>
        <span className="ml-1 tabular-nums">{history.changePct == null ? "" : `(${history.changePct >= 0 ? "+" : ""}${ratio(history.changePct)})`}</span></div>
    </div>
    {history.rows.length ? <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[700px] text-left text-sm">
      <thead className="text-xs text-ui-faint"><tr><th className="pb-2 font-medium">账本日</th><th className="pb-2 text-right font-medium">账户权益</th><th className="pb-2 text-right font-medium">当日盈亏</th><th className="pb-2 text-right font-medium">当日收益率</th><th className="pb-2 text-right font-medium">现金</th><th className="pb-2 text-right font-medium">持仓市值</th></tr></thead>
      <tbody>{history.rows.map((row) => <tr key={row.date} className="border-t border-ui-line tabular-nums">
        <td className="py-2.5 whitespace-nowrap text-ui-body">{row.date}</td>
        <td className="py-2.5 text-right">{currency(row.equity)}</td>
        <td className={`py-2.5 text-right font-medium ${pnlTone(row.change)}`}>{signedCurrency(row.change)}</td>
        <td className={`py-2.5 text-right ${pnlTone(row.change)}`}>{row.changePct == null ? "—" : `${row.changePct >= 0 ? "+" : ""}${ratio(row.changePct)}`}</td>
        <td className="py-2.5 text-right">{currency(row.cash)}</td>
        <td className="py-2.5 text-right">{currency(row.positionsValue)}</td>
      </tr>)}</tbody>
    </table></div> : <p className="py-7 text-center text-sm text-ui-faint">尚无逐日权益记录，模拟盘推进后显示。</p>}
    <p className="mt-3 text-xs leading-5 text-ui-faint">当日盈亏＝本日权益－上一账本交易日权益，包含已实现与未实现变动；首日若无前一日记录则显示“—”。这是账户合计，不是逐只持仓的历史盈亏。</p>
  </section>;
}

function tradeSide(side: string) {
  const action = side.toUpperCase();
  if (action === "BUY" || action === "ADD") return { label: "买入", tone: "text-ui-danger bg-ui-danger/10" };
  if (action === "SELL" || action === "REDUCE") return { label: "卖出", tone: "text-ui-success bg-ui-success/10" };
  return { label: action || "其他", tone: "text-ui-muted bg-ui-hover" };
}

export function TradesCard({ trades, totalCount }: { trades?: PaperTrade[]; totalCount?: number }) {
  const [side, setSide] = useState<"all" | "buy" | "sell">("all");
  const [day, setDay] = useState("all");
  const rows = useMemo(() => [...(trades ?? [])].sort((a, b) => b.trade_date.localeCompare(a.trade_date)), [trades]);
  const dates = [...new Set(rows.map((trade) => trade.trade_date.slice(0, 10)))];
  const shown = rows.filter((trade) => {
    const action = trade.side.toUpperCase();
    return (day === "all" || trade.trade_date.slice(0, 10) === day) &&
      (side === "all" || (side === "buy" ? ["BUY", "ADD"].includes(action) : ["SELL", "REDUCE"].includes(action)));
  });
  return <section className="rounded-xl border border-ui-line bg-ui-panel p-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 className="font-medium">成交与操作流水</h2><p className="mt-1 text-xs text-ui-faint">显示最近 {rows.length} 笔{valid(totalCount) ? ` / 账本共 ${totalCount} 笔` : ""}；按成交日核对实际执行。</p></div>
      {rows.length > 0 && <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-md border border-ui-line p-0.5" aria-label="成交方向筛选">
          {([['all', '全部'], ['buy', '买入'], ['sell', '卖出']] as const).map(([value, label]) =>
            <button key={value} type="button" aria-pressed={side === value} onClick={() => setSide(value)}
              className={`rounded px-2.5 py-1 text-xs ${side === value ? "bg-ui-hover font-medium text-ui-ink" : "text-ui-muted"}`}>{label}</button>)}
        </div>
        <select aria-label="成交日期" value={day} onChange={(event) => setDay(event.target.value)} className="rounded-md border border-ui-line bg-ui-panel px-2 py-1 text-xs text-ui-body">
          <option value="all">全部日期</option>{dates.map((date) => <option key={date} value={date}>{date}</option>)}
        </select>
      </div>}
    </div>
    {shown.length ? <div className="mt-4 max-h-96 overflow-auto"><table className="w-full min-w-[760px] text-left text-sm">
      <thead className="sticky top-0 bg-ui-panel text-xs text-ui-faint"><tr><th className="pb-2 font-medium">成交日</th><th className="pb-2 font-medium">标的</th><th className="pb-2 font-medium">方向</th><th className="pb-2 text-right font-medium">股数</th><th className="pb-2 text-right font-medium">成交价</th><th className="pb-2 text-right font-medium">成交额</th><th className="pb-2 text-right font-medium">费用</th></tr></thead>
      <tbody>{shown.map((trade, index) => {
        const action = tradeSide(trade.side);
        return <tr key={`${trade.trade_date}-${trade.code}-${index}`} className="border-t border-ui-line align-top">
          <td className="py-3 whitespace-nowrap tabular-nums">{trade.trade_date.slice(0, 10)}</td>
          <td className="py-3"><span className="font-medium text-ui-ink">{trade.name || trade.code}</span><span className="block text-xs text-ui-faint">{trade.code}</span>{trade.note && <span className="mt-1 block max-w-64 break-words text-xs text-ui-faint">{trade.note}</span>}</td>
          <td className="py-3"><span className={`rounded px-2 py-0.5 text-xs font-medium ${action.tone}`}>{action.label}</span></td>
          <td className="py-3 text-right tabular-nums">{valid(trade.shares) ? trade.shares.toLocaleString("zh-CN") : "—"}</td>
          <td className="py-3 text-right tabular-nums">{currency(trade.price)}</td>
          <td className="py-3 text-right tabular-nums">{currency(trade.amount)}</td>
          <td className="py-3 text-right tabular-nums">{currency(trade.fee)}</td>
        </tr>;
      })}</tbody>
    </table></div> : <p className="py-7 text-center text-sm text-ui-faint">{rows.length ? "当前筛选下没有成交。" : "暂无成交记录。"}</p>}
  </section>;
}
