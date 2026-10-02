import type { ChipProfile, ChipProfilePoint, TradeReviewCandle } from "@/api/client";

interface CandlestickChartProps {
  candles: TradeReviewCandle[];
  plan?: Record<string, unknown>;
  days?: number;
  chipProfile?: ChipProfile;
}

const WIDTH = 860;
const HEIGHT = 350;
const PRICE_TOP = 20;
const PRICE_BOTTOM = 255;
const VOLUME_TOP = 275;
const VOLUME_BOTTOM = 330;

export function CandlestickChart({ candles, plan = {}, days = 60, chipProfile }: CandlestickChartProps) {
  const rows = candles.slice(-days).filter((row) => row.high !== null && row.low !== null && row.close !== null);
  if (rows.length < 2) {
    return <div className="flex h-48 items-center justify-center text-ui-faint">K 线数据不足</div>;
  }

  const zone = numberArray(plan.action_zone ?? plan.entry_zone);
  const invalidation = finiteNumber(plan.invalidation_level ?? plan.stop_loss);
  const objectives = numberArray(plan.objective_levels ?? plan.targets);
  const chipByDate = new Map((chipProfile?.series ?? []).map((point) => [point.trade_date, point]));
  const chipRows = rows.map((row) => chipByDate.get(row.trade_date));
  const chipValues = chipRows.flatMap((point) => point ? [point.avg_cost, point.cost_70_low, point.cost_70_high] : []);
  const priceValues = rows.flatMap((row) => [row.low, row.high, row.ma5, row.ma10, row.ma20].filter(isNumber));
  const overlays = [...zone, ...objectives, ...(invalidation === undefined ? [] : [invalidation])];
  const rawMin = Math.min(...priceValues, ...chipValues, ...overlays);
  const rawMax = Math.max(...priceValues, ...chipValues, ...overlays);
  const padding = Math.max((rawMax - rawMin) * 0.08, rawMax * 0.005, 0.01);
  const minPrice = rawMin - padding;
  const maxPrice = rawMax + padding;
  const maxVolume = Math.max(...rows.map((row) => row.volume ?? 0), 1);
  const step = WIDTH / rows.length;
  const candleWidth = Math.max(2, Math.min(8, step * 0.55));
  const x = (index: number) => step * index + step / 2;
  const y = (price: number) => PRICE_BOTTOM - ((price - minPrice) / (maxPrice - minPrice)) * (PRICE_BOTTOM - PRICE_TOP);
  const volumeY = (volume: number) => VOLUME_BOTTOM - (volume / maxVolume) * (VOLUME_BOTTOM - VOLUME_TOP);

  return (
    <div className="overflow-x-auto rounded-md border border-ui-line bg-ui-panel">
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`最近 ${rows.length} 个交易日 K 线`} className="block h-auto min-w-[860px] w-full">
      <rect x="0" y="0" width={WIDTH} height={HEIGHT} className="fill-ui-panel" />
      {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
        const price = maxPrice - (maxPrice - minPrice) * ratio;
        const lineY = PRICE_TOP + (PRICE_BOTTOM - PRICE_TOP) * ratio;
        return (
          <g key={ratio}>
            <line x1="0" y1={lineY} x2={WIDTH} y2={lineY} className="stroke-ui-line" strokeDasharray="3 5" />
            <text x={WIDTH - 4} y={lineY - 3} textAnchor="end" className="fill-ui-muted text-xs">{price.toFixed(2)}</text>
          </g>
        );
      })}

      {zone.length >= 2 && (
        <g>
          <rect x="0" y={y(zone[zone.length - 1]!)} width={WIDTH} height={Math.max(2, y(zone[0]!) - y(zone[zone.length - 1]!))} className="fill-ui-accent/10" />
          <text x="5" y={y(zone[zone.length - 1]!) - 4} className="fill-ui-accent text-xs">行动区</text>
        </g>
      )}
      {invalidation !== undefined && <PriceLine value={invalidation} y={y(invalidation)} label="失效" tone="red" />}
      {objectives.map((value, index) => <PriceLine key={`${value}-${index}`} value={value} y={y(value)} label={`目标${index + 1}`} tone="emerald" />)}

      <ChipBand points={chipRows} x={x} y={y} />

      <Polyline rows={rows} field="ma5" x={x} y={y} className="stroke-ui-warning" />
      <Polyline rows={rows} field="ma10" x={x} y={y} className="stroke-ui-info" />
      <Polyline rows={rows} field="ma20" x={x} y={y} className="stroke-ui-body" />
      <ChipPolyline points={chipRows} x={x} y={y} />

      {rows.map((row, index) => {
        const open = row.open ?? row.close!;
        const close = row.close!;
        const rising = close >= open;
        const wickTone = rising ? "stroke-ui-danger" : "stroke-ui-success";
        const bodyTone = rising ? "stroke-ui-danger fill-ui-danger" : "stroke-ui-success fill-ui-panel";
        const volumeTone = rising ? "fill-ui-danger" : "fill-ui-success";
        const bodyTop = y(Math.max(open, close));
        const bodyHeight = Math.max(1.5, Math.abs(y(open) - y(close)));
        const volTop = volumeY(row.volume ?? 0);
        return (
          <g key={row.trade_date}>
            <title>{`${row.trade_date} O ${open.toFixed(2)} H ${row.high!.toFixed(2)} L ${row.low!.toFixed(2)} C ${close.toFixed(2)}`}</title>
            <line x1={x(index)} y1={y(row.high!)} x2={x(index)} y2={y(row.low!)} className={wickTone} strokeWidth="1" />
            <rect x={x(index) - candleWidth / 2} y={bodyTop} width={candleWidth} height={bodyHeight} className={bodyTone} />
            <rect x={x(index) - candleWidth / 2} y={volTop} width={candleWidth} height={VOLUME_BOTTOM - volTop} className={volumeTone} opacity="0.35" />
          </g>
        );
      })}

      <line x1="0" y1={PRICE_BOTTOM} x2={WIDTH} y2={PRICE_BOTTOM} className="stroke-ui-strong" />
      <line x1="0" y1={VOLUME_TOP} x2={WIDTH} y2={VOLUME_TOP} className="stroke-ui-line" />
      <text x="5" y="14" className="fill-ui-muted text-xs">MA5</text>
      <text x="37" y="14" className="fill-ui-warning text-xs">—</text>
      <text x="57" y="14" className="fill-ui-muted text-xs">MA10</text>
      <text x="99" y="14" className="fill-ui-info text-xs">—</text>
      <text x="119" y="14" className="fill-ui-muted text-xs">MA20</text>
      <text x="161" y="14" className="fill-ui-body text-xs">—</text>
      {chipProfile?.status === "available" && (
        <>
          <text x="183" y="14" className="fill-ui-muted text-xs">筹码成本</text>
          <text x="245" y="14" className="fill-ui-accent text-xs">—</text>
        </>
      )}
      <text x="320" y="14" className="fill-ui-muted text-xs">红涨 · 绿跌</text>
      <text x="4" y="346" className="fill-ui-muted text-xs">{rows[0]!.trade_date}</text>
      <text x={WIDTH - 4} y="346" textAnchor="end" className="fill-ui-muted text-xs">{rows[rows.length - 1]!.trade_date}</text>
    </svg>
    </div>
  );
}

function ChipBand({ points, x, y }: {
  points: Array<ChipProfilePoint | undefined>;
  x: (index: number) => number;
  y: (value: number) => number;
}) {
  const valid = points.flatMap((point, index) => point ? [{ point, index }] : []);
  if (valid.length < 2) return null;
  const upper = valid.map(({ point, index }) => `${x(index)},${y(point.cost_70_high)}`);
  const lower = [...valid].reverse().map(({ point, index }) => `${x(index)},${y(point.cost_70_low)}`);
  return (
    <g>
      <polygon points={[...upper, ...lower].join(" ")} className="fill-ui-accent/10" />
      <polyline points={upper.join(" ")} fill="none" className="stroke-ui-accent/40" strokeWidth="1" />
      <polyline points={[...lower].reverse().join(" ")} fill="none" className="stroke-ui-accent/40" strokeWidth="1" />
    </g>
  );
}

function ChipPolyline({ points, x, y }: {
  points: Array<ChipProfilePoint | undefined>;
  x: (index: number) => number;
  y: (value: number) => number;
}) {
  const line = points.flatMap((point, index) => point ? [`${x(index)},${y(point.avg_cost)}`] : []);
  return line.length > 1 ? <polyline points={line.join(" ")} fill="none" className="stroke-ui-accent" strokeWidth="1.4" opacity="0.9" /> : null;
}

function PriceLine({ value, y, label, tone }: { value: number; y: number; label: string; tone: "red" | "emerald" }) {
  const className = tone === "red" ? "stroke-ui-danger fill-ui-danger" : "stroke-ui-success fill-ui-success";
  return (
    <g>
      <line x1="0" y1={y} x2={WIDTH} y2={y} className={className} strokeDasharray="6 4" opacity="0.75" />
      <text x="5" y={y - 4} className={`${className} text-xs`}>{label} {value.toFixed(2)}</text>
    </g>
  );
}

function Polyline({ rows, field, x, y, className }: {
  rows: TradeReviewCandle[];
  field: "ma5" | "ma10" | "ma20";
  x: (index: number) => number;
  y: (value: number) => number;
  className: string;
}) {
  const points = rows.flatMap((row, index) => isNumber(row[field]) ? [`${x(index)},${y(row[field]!)}`] : []);
  return points.length > 1 ? <polyline points={points.join(" ")} fill="none" className={className} strokeWidth="1.2" opacity="0.9" /> : null;
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function finiteNumber(value: unknown): number | undefined {
  const result = Number(value);
  return Number.isFinite(result) ? result : undefined;
}

function numberArray(value: unknown): number[] {
  return Array.isArray(value) ? value.map(Number).filter(Number.isFinite) : [];
}
