import type { Holding } from "@/api/client";
import { holdingMarketValue, holdingPnl, formatMoney, formatNumber } from "@/utils/portfolio";

interface HoldingsTableProps {
  holdings: Holding[];
  totalValue: number;
  onAnalyze: (symbol: string) => void;
  onManage: () => void;
}

export function HoldingsTable({ holdings, totalValue, onAnalyze, onManage }: HoldingsTableProps) {
  const sorted = [...holdings].sort((a, b) => holdingMarketValue(b) - holdingMarketValue(a));
  return (
    <div className="overflow-x-auto rounded-lg border border-stone-800">
      <table className="w-full min-w-[860px] text-left text-sm">
        <thead className="bg-stone-950 text-xs uppercase text-stone-500">
          <tr>
            <th className="px-3 py-2">标的</th>
            <th className="px-3 py-2 text-right">数量</th>
            <th className="px-3 py-2 text-right">成本</th>
            <th className="px-3 py-2 text-right">现价</th>
            <th className="px-3 py-2 text-right">市值</th>
            <th className="px-3 py-2 text-right">盈亏</th>
            <th className="px-3 py-2 text-right">收益率</th>
            <th className="px-3 py-2 text-right">仓位</th>
            <th className="px-3 py-2">备注</th>
            <th className="px-3 py-2 text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((holding) => {
            const price = holding.current_price ?? holding.avg_cost;
            const value = holdingMarketValue(holding);
            const pnl = holdingPnl(holding);
            const pnlPct = holding.avg_cost ? ((price - holding.avg_cost) / holding.avg_cost) * 100 : 0;
            const weight = totalValue ? (value / totalValue) * 100 : 0;
            const displayName = holding.name && holding.name !== holding.symbol ? holding.name : holding.symbol;
            return (
              <tr key={holding.symbol} className="border-t border-stone-800">
                <td className="px-3 py-2">
                  <div className="font-semibold text-stone-100">{displayName}</div>
                  {displayName !== holding.symbol && (
                    <div className="mt-0.5 font-mono text-xs text-stone-500">{holding.symbol}</div>
                  )}
                  {holding.latest_analysis && (
                    <div
                      className="mt-1 max-w-64 truncate text-xs text-stone-500"
                      title={[
                        holding.latest_analysis.date ? `分析日期 ${holding.latest_analysis.date}` : "",
                        holding.latest_analysis.summary ?? "",
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    >
                      <span className="mr-1 rounded bg-teal-500/10 px-1.5 py-0.5 text-teal-200">
                        {holding.latest_analysis.rating || "最近分析"}
                      </span>
                      {holding.latest_analysis.date && (
                        <span className="mr-1 text-stone-500">{holding.latest_analysis.date}</span>
                      )}
                      {holding.latest_analysis.summary && (
                        <span>{holding.latest_analysis.summary}</span>
                      )}
                    </div>
                  )}
                  <div className="mt-1 h-1.5 w-24 overflow-hidden rounded-full bg-stone-800">
                    <div
                      className={`h-full rounded-full ${weight > 50 ? "bg-amber-300" : "bg-teal-300"}`}
                      style={{ width: `${Math.min(weight, 100)}%` }}
                    />
                  </div>
                </td>
                <td className="px-3 py-2 text-right font-mono text-stone-300">{formatNumber(holding.quantity)}</td>
                <td className="px-3 py-2 text-right font-mono text-stone-300">{formatNumber(holding.avg_cost)}</td>
                <td className="px-3 py-2 text-right font-mono text-stone-300">{formatNumber(price)}</td>
                <td className="px-3 py-2 text-right font-mono text-stone-100">{formatMoney(value)}</td>
                <td className={`px-3 py-2 text-right font-mono ${pnl >= 0 ? "text-emerald-300" : "text-red-300"}`}>
                  {formatMoney(pnl)}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${pnlPct >= 0 ? "text-emerald-300" : "text-red-300"}`}>
                  {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
                </td>
                <td className="px-3 py-2 text-right font-mono text-stone-300">{weight.toFixed(1)}%</td>
                <td className="max-w-44 truncate px-3 py-2 text-stone-500">{holding.notes || "-"}</td>
                <td className="px-3 py-2">
                  <div className="flex justify-end gap-1.5">
                    <button
                      onClick={() => onAnalyze(holding.symbol)}
                      className="rounded border border-stone-700 px-2 py-1 text-xs text-stone-400 transition hover:border-teal-500/50 hover:text-teal-300"
                    >
                      分析
                    </button>
                    <button
                      onClick={onManage}
                      className="rounded border border-stone-700 px-2 py-1 text-xs text-stone-400 transition hover:border-teal-500/50 hover:text-stone-100"
                    >
                      编辑
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
