import type { ReflectionCase, StrategyLesson } from "@/api/client";

/** Pure presentation helpers for the Reflection page, extracted so they can be
 * unit-tested without rendering React. */

export interface LessonMetrics {
  isNeutral: boolean;
  distinctPeriods: number;
  avgExcess: number | null;
  winRate: number | null;
  consistency: number | null;
  sampleSize: number | null;
}

function numOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** A lesson mined from the neutral (excess + consistency) channel is keyed by a
 * "neutral:" dimension prefix; directional lessons are win/loss driven. */
export function isNeutralLesson(lesson: Pick<StrategyLesson, "payload">): boolean {
  return String((lesson.payload?.dimension as string) ?? "").startsWith("neutral:");
}

export function lessonMetrics(lesson: StrategyLesson): LessonMetrics {
  const p = lesson.payload ?? {};
  return {
    isNeutral: isNeutralLesson(lesson),
    distinctPeriods: numOrNull(p.distinct_periods) ?? 0,
    avgExcess: numOrNull(p.avg_excess),
    winRate: numOrNull(p.win_rate),
    consistency: numOrNull(p.consistency ?? p.consistency_rate),
    sampleSize: numOrNull(p.sample_size ?? p.samples ?? lesson.evidence_count),
  };
}

export interface AttributionMeta {
  label: string;
  cls: string;
}

const ATTRIBUTION_MAP: Record<string, AttributionMeta> = {
  missed_upside: { label: "机会错失", cls: "border-amber-500/30 text-amber-300" },
  validated_avoidance: { label: "规避有效", cls: "border-teal-500/30 text-teal-300" },
  win: { label: "判断正确", cls: "border-emerald-500/30 text-emerald-300" },
  loss: { label: "判断错误", cls: "border-red-500/30 text-red-300" },
};

/** Map a raw attribution token to a badge label + tailwind classes. Unknown
 * tokens fall back to the raw token with neutral styling. */
export function attributionMeta(attribution: string): AttributionMeta {
  return (
    ATTRIBUTION_MAP[attribution] ?? { label: attribution, cls: "border-stone-700 text-stone-400" }
  );
}

export function confidenceCls(confidence: string): string {
  if (confidence === "high") return "border-emerald-500/40 text-emerald-300";
  if (confidence === "medium") return "border-amber-500/40 text-amber-300";
  return "border-stone-600 text-stone-400";
}

/** Excess return prefers the attribution payload, then the raw outcome. */
export function caseExcess(item: ReflectionCase): number | null {
  return (
    numOrNull(item.attribution_payload?.excess_return) ??
    numOrNull(item.outcome_payload?.excess_return)
  );
}

export function caseAttribution(item: ReflectionCase): string {
  return String((item.attribution_payload?.attribution as string) ?? "");
}

export function caseOriginalDecision(item: ReflectionCase): string {
  return String(
    (item.snapshot_payload?.final_decision as string) ??
      (item.snapshot_payload?.quant_decision as string) ??
      item.source_type
  );
}

/** Format a 0..1 ratio as a signed/unsigned percentage string. */
export function formatPct(value: number | null, digits = 2, signed = false): string {
  if (value === null) return "—";
  const pct = value * 100;
  const sign = signed && pct >= 0 ? "+" : "";
  return `${sign}${pct.toFixed(digits)}%`;
}

export interface HistoryPoint {
  date: string;
  winRate: number | null;
  lift: number | null;
  avgExcess: number | null;
  consistency: number | null;
  n: number | null;
}

/** Normalize the miner-persisted ``payload.history`` series (one snapshot per
 * mining day) into typed points. Malformed / missing history yields []. */
export function lessonHistory(lesson: Pick<StrategyLesson, "payload">): HistoryPoint[] {
  const raw = lesson.payload?.history;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((h): h is Record<string, unknown> => typeof h === "object" && h !== null)
    .map((h) => ({
      date: String(h.date ?? ""),
      winRate: numOrNull(h.win_rate),
      lift: numOrNull(h.lift),
      avgExcess: numOrNull(h.avg_excess),
      consistency: numOrNull(h.consistency),
      n: numOrNull(h.n),
    }));
}

export interface LessonTrend {
  label: string;
  values: (number | null)[];
  signed: boolean;
}

/** The primary metric to trend for a lesson: neutral lessons trend their mean
 * excess, directional lessons trend win rate. */
export function lessonTrend(lesson: StrategyLesson): LessonTrend {
  const history = lessonHistory(lesson);
  if (isNeutralLesson(lesson)) {
    return { label: "平均超额", values: history.map((h) => h.avgExcess), signed: true };
  }
  return { label: "胜率", values: history.map((h) => h.winRate), signed: false };
}

/** Build an SVG polyline points string from a numeric series scaled to a
 * width x height box. Nulls are dropped; returns "" for fewer than 2 finite
 * values (a single point cannot form a trend line). */
export function sparklinePoints(
  values: (number | null)[],
  width = 96,
  height = 24
): string {
  const nums = values.filter((v): v is number => v !== null && Number.isFinite(v));
  if (nums.length < 2) return "";
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const span = max - min || 1;
  const step = width / (nums.length - 1);
  return nums
    .map((v, i) => {
      const x = i * step;
      const y = height - ((v - min) / span) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}
