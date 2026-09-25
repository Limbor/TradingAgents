interface SparklineProps {
  values: number[];
  width?: number;
  height?: number;
  positive?: boolean;
}

/** Inline SVG sparkline — no chart library dependency. */
export function Sparkline({ values, width = 96, height = 28, positive = true }: SparklineProps) {
  if (!values || values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = width / (values.length - 1);
  const points = values
    .map((v, i) => `${(i * step).toFixed(1)},${(height - 2 - ((v - min) / range) * (height - 4)).toFixed(1)}`)
    .join(" ");
  const color = positive ? "#f87171" : "#34d399"; // A股习惯：涨红跌绿
  return (
    <svg width={width} height={height} className="shrink-0" aria-hidden="true">
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}
