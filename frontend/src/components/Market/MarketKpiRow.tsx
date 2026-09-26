import { Activity, Landmark } from "lucide-react";
import type { MarketBreadth, MarketIndexEntry, MarketNorthbound } from "../../api/client";
import { Sparkline } from "./Sparkline";

function fmtPct(value: number | null): string {
  if (value === null || value === undefined) return "--";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function fmtYi(value: number | null): string {
  if (value === null || value === undefined) return "--";
  return `${(value / 1e8).toFixed(0)} 亿`;
}

function IndexCard({ idx }: { idx: MarketIndexEntry }) {
  const up = (idx.pct_change ?? 0) >= 0;
  return (
    <div className="rounded-lg border border-ui-line bg-ui-panel p-4">
      <div className="flex items-center justify-between text-xs text-ui-muted">
        <span>{idx.name}</span>
        <span className={`rounded px-1.5 py-0.5 ${idx.above_ma20 ? "bg-ui-danger/10 text-ui-danger" : "bg-ui-success/10 text-ui-success"}`}>
          MA20{idx.above_ma20 ? "上" : "下"}
        </span>
      </div>
      <div className="mt-2 flex items-end justify-between gap-2">
        <div>
          <div className="font-mono text-xl font-semibold text-ui-ink">{idx.close}</div>
          <div className={`mt-0.5 font-mono text-xs ${up ? "text-ui-danger" : "text-ui-success"}`}>{fmtPct(idx.pct_change)}</div>
        </div>
        <Sparkline values={idx.closes_20d} positive={up} />
      </div>
    </div>
  );
}

function BreadthCard({ breadth }: { breadth: MarketBreadth | null }) {
  const up = breadth?.up ?? null;
  const down = breadth?.down ?? null;
  const total = (up ?? 0) + (down ?? 0) + (breadth?.flat ?? 0);
  const upPct = total > 0 && up !== null ? (up / total) * 100 : 50;
  return (
    <div className="rounded-lg border border-ui-line bg-ui-panel p-4">
      <div className="flex items-center gap-2 text-xs text-ui-accent">
        <Activity className="h-4 w-4" />
        <span>市场宽度</span>
      </div>
      {breadth ? (
        <>
          <div className="mt-2 flex items-center justify-between font-mono text-sm">
            <span className="text-ui-danger">涨 {up ?? "--"}</span>
            <span className="text-ui-success">跌 {down ?? "--"}</span>
          </div>
          <div className="mt-1.5 flex h-1.5 overflow-hidden rounded-full bg-ui-hover">
            <div className="bg-ui-danger/80" style={{ width: `${upPct}%` }} />
            <div className="flex-1 bg-ui-success/80" />
          </div>
          <div className="mt-2 text-xs text-ui-faint">
            涨停 <span className="text-ui-danger">{breadth.limit_up ?? "--"}</span> · 跌停{" "}
            <span className="text-ui-success">{breadth.limit_down ?? "--"}</span> · 炸板{" "}
            <span className="text-ui-warning">{breadth.broken_limit ?? "--"}</span>
          </div>
        </>
      ) : (
        <div className="mt-3 text-xs text-ui-faint">暂无数据</div>
      )}
    </div>
  );
}

function FundsCard({ northbound, turnover }: { northbound: MarketNorthbound | null; turnover: number | null }) {
  return (
    <div className="rounded-lg border border-ui-line bg-ui-panel p-4">
      <div className="flex items-center gap-2 text-xs text-ui-accent">
        <Landmark className="h-4 w-4" />
        <span>资金面</span>
      </div>
      <div className="mt-2 space-y-1 text-xs text-ui-muted">
        <div className="flex justify-between">
          <span>北向当日</span>
          <span className={`font-mono ${(northbound?.latest_net ?? 0) >= 0 ? "text-ui-danger" : "text-ui-success"}`}>
            {northbound?.latest_net !== null && northbound?.latest_net !== undefined ? `${northbound.latest_net} 亿` : "暂无数据"}
          </span>
        </div>
        <div className="flex justify-between">
          <span>北向5日</span>
          <span className={`font-mono ${(northbound?.five_day_net ?? 0) >= 0 ? "text-ui-danger" : "text-ui-success"}`}>
            {northbound?.five_day_net !== null && northbound?.five_day_net !== undefined ? `${northbound.five_day_net} 亿` : "暂无数据"}
          </span>
        </div>
        <div className="flex justify-between">
          <span>两市成交</span>
          <span className="font-mono text-ui-body">{fmtYi(turnover)}</span>
        </div>
      </div>
    </div>
  );
}

interface MarketKpiRowProps {
  indices: MarketIndexEntry[];
  breadth: MarketBreadth | null;
  northbound: MarketNorthbound | null;
  turnoverAmount: number | null;
}

export function MarketKpiRow({ indices, breadth, northbound, turnoverAmount }: MarketKpiRowProps) {
  return (
    <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
      {indices.slice(0, 3).map((idx) => (
        <IndexCard key={idx.code} idx={idx} />
      ))}
      <BreadthCard breadth={breadth} />
      <FundsCard northbound={northbound} turnover={turnoverAmount} />
    </section>
  );
}
