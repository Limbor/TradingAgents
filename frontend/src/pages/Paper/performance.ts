import type { PaperCurve, PaperStatus } from "@/api/paper";

export interface PerformanceLine {
  key: string;
  label: string;
  color: string;
  kind: "account" | "sleeve" | "benchmark";
}

export interface PerformanceData {
  rows: Array<Record<string, string | number | null>>;
  lines: PerformanceLine[];
  baseDate: string | null;
  endDate: string | null;
  benchmarkAvailable: boolean;
}

const chartColors = ["rgb(var(--ui-info))", "rgb(var(--ui-danger))", "rgb(var(--ui-muted))"];

function dateKey(raw: unknown): string | null {
  if (typeof raw !== "string" || !/^\d{4}-\d{2}-\d{2}/.test(raw)) return null;
  return raw.slice(0, 10);
}

function seriesMap(rows: Array<{ date: string; equity: number }> | undefined): Map<string, number> {
  const values = new Map<string, number>();
  for (const row of rows ?? []) {
    const day = dateKey(row.date);
    const value = Number(row.equity);
    if (day && Number.isFinite(value) && value > 0) values.set(day, value);
  }
  return values;
}

/** Compare independently scaled accounts and index closes from one shared date. */
export function buildPaperPerformance(curve?: PaperCurve, status?: PaperStatus): PerformanceData {
  const account = seriesMap(curve?.daily_records);
  const dates = [...account.keys()].sort();
  const sources: Array<{ line: PerformanceLine; values: Map<string, number> }> = [];
  const benchmark = seriesMap(curve?.benchmark_curve);
  // Keep the index comparison when a sleeve has a shorter or offset history.
  if (benchmark.size) sources.push({
    line: { key: "benchmark", label: status?.kind === "composite" ? "上证指数" : "策略基准",
      color: "rgb(var(--ui-warning))", kind: "benchmark" },
    values: benchmark,
  });
  const sleeves = status?.kind === "composite" ? status.sleeve_curves ?? {} : {};
  Object.entries(sleeves).forEach(([name, rows], index) => {
    sources.push({
      line: { key: `sleeve_${index}`, label: name, kind: "sleeve",
        color: chartColors[index % chartColors.length] ?? "rgb(var(--ui-info))" },
      values: seriesMap(rows),
    });
  });
  // A series with no common trading span cannot be compared honestly.
  const included: typeof sources = [];
  let shared = dates;
  for (const source of sources) {
    const overlap = shared.filter((day) => source.values.has(day));
    if (overlap.length >= 2) {
      included.push(source);
      shared = overlap;
    }
  }
  const baseDate = shared[0] ?? null;
  const accountBase = baseDate ? account.get(baseDate) : undefined;
  const lines: PerformanceLine[] = [
    { key: "account", label: "模拟盘账户", color: "rgb(var(--ui-accent))", kind: "account" },
    ...included.map((source) => source.line),
  ];
  const rows = accountBase ? dates.filter((day) => day >= baseDate!).map((day) => {
    const row: Record<string, string | number | null> = {
      date: day, account: ((account.get(day)! / accountBase) - 1) * 100,
    };
    for (const source of included) {
      const value = source.values.get(day);
      const base = source.values.get(baseDate!);
      row[source.line.key] = value && base ? ((value / base) - 1) * 100 : null;
    }
    return row;
  }) : [];
  return { rows, lines, baseDate, endDate: rows.length ? String(rows[rows.length - 1]?.date) : null,
    benchmarkAvailable: included.some((source) => source.line.kind === "benchmark") };
}
