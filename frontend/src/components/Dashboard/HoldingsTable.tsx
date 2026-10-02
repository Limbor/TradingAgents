import type { Holding } from "@/api/client";
import { holdingMarketValue, holdingPnl, formatMoney, formatNumber } from "@/utils/portfolio";
import { displayNameOf } from "@/components/common/StockName";

/** Raw holding fields handed off to stock_analysis as holding_context.
 *  The backend enriches these with derived metrics (P&L, position weight). */
function holdingContextFields(holding: Holding): Record<string, unknown> {
  return {
    symbol: holding.symbol,
    name: holding.name,
    quantity: holding.quantity,
    avg_cost: holding.avg_cost,
    current_price: holding.current_price,
  };
}

interface HoldingsTableProps {
  holdings: Holding[];
  totalValue: number;
  onAnalyze: (symbol: string, context?: Record<string, unknown>) => void;
  onManage: () => void;
}

export function HoldingsTable({ holdings, totalValue, onAnalyze, onManage }: HoldingsTableProps) {
  const sorted = [...holdings].sort((a, b) => holdingMarketValue(b) - holdingMarketValue(a));
  return (
    <div className="overflow-x-auto rounded-lg border border-ui-line">
      <table className="w-full min-w-[860px] text-left text-sm">
        <thead className="bg-ui-subtle text-xs uppercase text-ui-faint">
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
            const displayName = displayNameOf(holding.name, holding.symbol);
            return (
              <tr key={holding.symbol} className="border-t border-ui-line">
                <td className="px-3 py-2">
                  <div className="font-semibold text-ui-ink">{displayName}</div>
                  {displayName !== holding.symbol && (
                    <div className="mt-0.5 font-mono text-xs text-ui-faint">{holding.symbol}</div>
                  )}
                  {holding.latest_analysis && (
                    <div
                      className="mt-1 max-w-64 truncate text-xs text-ui-faint"
                      title={[
                        holding.latest_analysis.date ? `分析日期 ${holding.latest_analysis.date}` : "",
                        holding.latest_analysis.summary ?? "",
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    >
                      <span className="mr-1 rounded bg-ui-accent/10 px-1.5 py-0.5 text-ui-accent">
                        {holding.latest_analysis.rating || "最近分析"}
                      </span>
                      {holding.latest_analysis.date && (
                        <span className="mr-1 text-ui-faint">{holding.latest_analysis.date}</span>
                      )}
                      {holding.latest_analysis.summary && (
                        <span>{holding.latest_analysis.summary}</span>
                      )}
                    </div>
                  )}
                  <div className="mt-1 h-1.5 w-24 overflow-hidden rounded-full bg-ui-hover">
                    <div
                      className={`h-full rounded-full ${weight > 50 ? "bg-ui-warning" : "bg-ui-accent"}`}
                      style={{ width: `${Math.min(weight, 100)}%` }}
                    />
                  </div>
                </td>
                <td className="px-3 py-2 text-right font-mono text-ui-body">{formatNumber(holding.quantity)}</td>
                <td className="px-3 py-2 text-right font-mono text-ui-body">{formatNumber(holding.avg_cost)}</td>
                <td className="px-3 py-2 text-right font-mono text-ui-body">{formatNumber(price)}</td>
                <td className="px-3 py-2 text-right font-mono text-ui-ink">{formatMoney(value)}</td>
                <td className={`px-3 py-2 text-right font-mono ${pnl >= 0 ? "text-ui-success" : "text-ui-danger"}`}>
                  {formatMoney(pnl)}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${pnlPct >= 0 ? "text-ui-success" : "text-ui-danger"}`}>
                  {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
                </td>
                <td className="px-3 py-2 text-right font-mono text-ui-body">{weight.toFixed(1)}%</td>
                <td className="max-w-44 truncate px-3 py-2 text-ui-faint">{holding.notes || "-"}</td>
                <td className="px-3 py-2">
                  <div className="flex justify-end gap-1.5">
                    <button
                      onClick={() => onAnalyze(holding.symbol, { holding_context: holdingContextFields(holding) })}
                      className="rounded border border-ui-strong px-2 py-1 text-xs text-ui-muted transition hover:border-ui-accent/50 hover:text-ui-accent"
                    >
                      分析
                    </button>
                    <button
                      onClick={onManage}
                      className="rounded border border-ui-strong px-2 py-1 text-xs text-ui-muted transition hover:border-ui-accent/50 hover:text-ui-ink"
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
