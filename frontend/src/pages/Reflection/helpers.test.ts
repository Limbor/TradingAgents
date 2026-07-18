import { describe, expect, it } from "vitest";

import type { ReflectionCase, StrategyLesson } from "@/api/client";
import {
  attributionMeta,
  caseAttribution,
  caseExcess,
  caseOriginalDecision,
  confidenceCls,
  formatPct,
  isNeutralLesson,
  lessonHistory,
  lessonMetrics,
  lessonTrend,
  sparklinePoints,
} from "./helpers";

function lesson(overrides: Partial<StrategyLesson> = {}): StrategyLesson {
  return {
    id: "L1",
    lesson_type: "neutral_missed_upside",
    scope: "industry",
    target: "地产",
    finding: "地产板块超额显著",
    suggested_adjustment: "",
    evidence_count: 4,
    confidence: "high",
    active: true,
    expires_at: null,
    payload: {},
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-10T00:00:00Z",
    ...overrides,
  };
}

function reflectionCase(overrides: Partial<ReflectionCase> = {}): ReflectionCase {
  return {
    id: "C1",
    source_type: "daily_candidate",
    reflection_scope: "symbol",
    eligible_for_strategy_learning: true,
    status: "completed",
    symbol: "000002.SZ",
    name: "万科A",
    signal_date: "2026-07-01",
    horizon_days: 5,
    due_date: "2026-07-08",
    source_run_id: "",
    source_artifact_id: "",
    snapshot_payload: {},
    outcome_payload: {},
    attribution_payload: {},
    lesson_payload: {},
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-08T00:00:00Z",
    ...overrides,
  };
}

describe("isNeutralLesson", () => {
  it("detects the neutral: dimension prefix", () => {
    expect(isNeutralLesson(lesson({ payload: { dimension: "neutral:industry=地产" } }))).toBe(true);
  });
  it("treats directional / missing dimensions as non-neutral", () => {
    expect(isNeutralLesson(lesson({ payload: { dimension: "final_decision=BUY" } }))).toBe(false);
    expect(isNeutralLesson(lesson({ payload: {} }))).toBe(false);
  });
});

describe("lessonMetrics", () => {
  it("extracts numeric metrics and coerces non-numbers to null", () => {
    const m = lessonMetrics(
      lesson({
        payload: {
          dimension: "neutral:industry=地产",
          distinct_periods: 3,
          avg_excess: 0.1083,
          consistency: 0.75,
          sample_size: 6,
        },
      })
    );
    expect(m.isNeutral).toBe(true);
    expect(m.distinctPeriods).toBe(3);
    expect(m.avgExcess).toBeCloseTo(0.1083);
    expect(m.consistency).toBe(0.75);
    expect(m.sampleSize).toBe(6);
    expect(m.winRate).toBeNull();
  });
  it("falls back to evidence_count for sample size and accepts consistency_rate alias", () => {
    const m = lessonMetrics(lesson({ evidence_count: 9, payload: { consistency_rate: 0.5 } }));
    expect(m.sampleSize).toBe(9);
    expect(m.consistency).toBe(0.5);
  });
});

describe("attributionMeta", () => {
  it("maps known tokens to labels", () => {
    expect(attributionMeta("missed_upside").label).toBe("机会错失");
    expect(attributionMeta("validated_avoidance").label).toBe("规避有效");
    expect(attributionMeta("win").label).toBe("判断正确");
    expect(attributionMeta("loss").label).toBe("判断错误");
  });
  it("falls back to the raw token with neutral styling", () => {
    const meta = attributionMeta("unknown_token");
    expect(meta.label).toBe("unknown_token");
    expect(meta.cls).toContain("stone");
  });
});

describe("confidenceCls", () => {
  it("maps confidence levels", () => {
    expect(confidenceCls("high")).toContain("emerald");
    expect(confidenceCls("medium")).toContain("amber");
    expect(confidenceCls("low")).toContain("stone");
  });
});

describe("case helpers", () => {
  it("prefers attribution payload for excess, falling back to outcome", () => {
    expect(caseExcess(reflectionCase({ attribution_payload: { excess_return: 0.05 } }))).toBe(0.05);
    expect(caseExcess(reflectionCase({ outcome_payload: { excess_return: -0.02 } }))).toBe(-0.02);
    expect(caseExcess(reflectionCase())).toBeNull();
  });
  it("reads attribution token and original decision", () => {
    const c = reflectionCase({
      attribution_payload: { attribution: "missed_upside" },
      snapshot_payload: { final_decision: "WATCHLIST" },
    });
    expect(caseAttribution(c)).toBe("missed_upside");
    expect(caseOriginalDecision(c)).toBe("WATCHLIST");
  });
  it("falls back through quant_decision then source_type for original decision", () => {
    expect(caseOriginalDecision(reflectionCase({ snapshot_payload: { quant_decision: "BUY" } }))).toBe("BUY");
    expect(caseOriginalDecision(reflectionCase({ source_type: "daily_candidate" }))).toBe("daily_candidate");
  });
});

describe("formatPct", () => {
  it("formats ratios as percentages", () => {
    expect(formatPct(0.1083, 2)).toBe("10.83%");
    expect(formatPct(0.5, 1)).toBe("50.0%");
  });
  it("adds a + sign only when signed and non-negative", () => {
    expect(formatPct(0.1083, 2, true)).toBe("+10.83%");
    expect(formatPct(-0.1083, 2, true)).toBe("-10.83%");
  });
  it("renders an em dash for null", () => {
    expect(formatPct(null)).toBe("—");
  });
});

describe("lessonHistory", () => {
  it("normalizes the persisted history series into typed points", () => {
    const points = lessonHistory(
      lesson({
        payload: {
          history: [
            { date: "2026-06-01", win_rate: 0.4, lift: -0.1, n: 5 },
            { date: "2026-06-08", avg_excess: 0.08, consistency: 0.7, n: 6 },
          ],
        },
      })
    );
    expect(points).toHaveLength(2);
    expect(points[0]).toEqual({
      date: "2026-06-01",
      winRate: 0.4,
      lift: -0.1,
      avgExcess: null,
      consistency: null,
      n: 5,
    });
    expect(points[1]?.avgExcess).toBeCloseTo(0.08);
  });
  it("returns [] when history is missing or malformed", () => {
    expect(lessonHistory(lesson({ payload: {} }))).toEqual([]);
    expect(lessonHistory(lesson({ payload: { history: "nope" } }))).toEqual([]);
  });
});

describe("lessonTrend", () => {
  it("trends mean excess for neutral lessons", () => {
    const t = lessonTrend(
      lesson({
        payload: {
          dimension: "neutral:industry=地产",
          history: [{ date: "a", avg_excess: 0.05 }, { date: "b", avg_excess: 0.09 }],
        },
      })
    );
    expect(t.label).toBe("平均超额");
    expect(t.signed).toBe(true);
    expect(t.values).toEqual([0.05, 0.09]);
  });
  it("trends win rate for directional lessons", () => {
    const t = lessonTrend(
      lesson({
        payload: {
          dimension: "final_decision=BUY",
          history: [{ date: "a", win_rate: 0.3 }, { date: "b", win_rate: 0.6 }],
        },
      })
    );
    expect(t.label).toBe("胜率");
    expect(t.signed).toBe(false);
    expect(t.values).toEqual([0.3, 0.6]);
  });
});

describe("sparklinePoints", () => {
  it("maps a series to scaled svg coordinates (min at bottom, max at top)", () => {
    const path = sparklinePoints([0, 0.5, 1], 100, 20);
    const coords = path.split(" ");
    expect(coords).toHaveLength(3);
    expect(coords[0]).toBe("0.0,20.0"); // min -> bottom (y=height)
    expect(coords[2]).toBe("100.0,0.0"); // max -> top (y=0)
  });
  it("drops nulls and returns '' for fewer than 2 finite values", () => {
    expect(sparklinePoints([0.5])).toBe("");
    expect(sparklinePoints([null, 0.5])).toBe("");
    expect(sparklinePoints([])).toBe("");
  });
  it("handles a flat series without dividing by zero", () => {
    const path = sparklinePoints([0.4, 0.4], 100, 20);
    expect(path).toBe("0.0,20.0 100.0,20.0");
  });
});
