import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { PaperCurve, PaperStatus } from "@/api/paper";
import { buildPaperPerformance } from "./performance";

const dateLabel = (value: string | number) => String(value).slice(0, 10);
const percentPoints = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? `${value >= 0 ? "+" : ""}${value.toFixed(2)}%` : "—";

export function PerformanceChart({ curve, status }: { curve?: PaperCurve; status?: PaperStatus }) {
  const data = useMemo(() => buildPaperPerformance(curve, status), [curve, status]);
  const [hidden, setHidden] = useState<string[]>([]);
  const visible = data.lines.filter((line) => !hidden.includes(line.key));

  return <section className="rounded-xl border border-ui-line bg-ui-panel p-4">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div><h2 className="font-medium">累计收益对比</h2><p className="mt-1 text-xs text-ui-muted">同一交易日归一为 0%，比较相对涨跌；账户金额见上方权益卡。</p></div>
      <span className="whitespace-nowrap text-xs text-ui-faint">{data.baseDate && data.endDate ? `${data.baseDate} — ${data.endDate}` : "暂无区间"}</span>
    </div>
    {data.rows.length >= 2 ? <>
      <div className="mt-4 flex flex-wrap gap-2" aria-label="收益曲线图例">
        {data.lines.map((line) => {
          const selected = !hidden.includes(line.key);
          const last = [...data.rows].reverse().find((row) => typeof row[line.key] === "number");
          return <button key={line.key} type="button" aria-pressed={selected}
            onClick={() => setHidden((current) => current.includes(line.key)
              ? current.filter((key) => key !== line.key) : [...current, line.key])}
            className={`inline-flex max-w-full items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs ${selected ? "border-ui-strong bg-ui-subtle text-ui-body" : "border-ui-line text-ui-faint"}`}>
            <span className="h-0.5 w-4 shrink-0" style={{ backgroundColor: line.color }} />
            <span className="max-w-36 truncate" title={line.label}>{line.label}</span>
            <strong className="tabular-nums">{percentPoints(last?.[line.key])}</strong>
            {last && last.date !== data.endDate && <span className="whitespace-nowrap text-ui-faint">截至 {String(last.date).slice(5)}</span>}
          </button>;
        })}
      </div>
      <div className="mt-4 h-72" role="img" aria-label={`累计收益对比：${data.lines.map((line) => line.label).join("、")}`}>
        <ResponsiveContainer width="100%" height="100%"><LineChart data={data.rows} margin={{ top: 8, right: 14, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="rgb(var(--ui-line))" strokeDasharray="3 3" />
          <XAxis dataKey="date" tick={{ fill: "rgb(var(--ui-muted))", fontSize: 11 }} tickFormatter={(value) => dateLabel(value).slice(5)} minTickGap={35} />
          <YAxis tick={{ fill: "rgb(var(--ui-muted))", fontSize: 11 }} tickFormatter={(value: number) => `${value.toFixed(0)}%`} width={54} domain={["auto", "auto"]} />
          <ReferenceLine y={0} stroke="rgb(var(--ui-strong))" />
          <Tooltip contentStyle={{ backgroundColor: "rgb(var(--ui-panel))", color: "rgb(var(--ui-ink))", border: "1px solid rgb(var(--ui-strong))", borderRadius: 8 }}
            labelFormatter={(value) => dateLabel(value)} formatter={(value, name) => [percentPoints(value), name]} />
          {visible.map((line) => <Line key={line.key} type="monotone" dataKey={line.key} name={line.label}
            stroke={line.color} strokeWidth={line.kind === "account" ? 2.7 : 1.8} strokeDasharray={line.kind === "sleeve" ? "5 3" : undefined}
            dot={false} connectNulls={false} isAnimationActive={false} />)}
        </LineChart></ResponsiveContainer>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ui-faint">
        <span>共同基准日 {data.baseDate} · {data.rows.length} 个账户交易日</span>
        {data.lines.some((line) => line.kind === "sleeve") && <span>虚线为子策略影子信号，非组合账户权益。</span>}
        {!data.benchmarkAvailable && <span>参考指数数据暂缺，未补造基准曲线。</span>}
      </div>
    </> : <p className="py-9 text-center text-sm text-ui-faint">至少两个可比较的交易日后显示收益曲线。</p>}
  </section>;
}
