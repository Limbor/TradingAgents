import { describe, expect, it } from "vitest";

import {
  analyzeStockHint,
  dailyPipelineHint,
  dailyReviewHint,
  marketScannerHint,
  positionAdviceHint,
  riskMonitorHint,
} from "@/lib/chatNav";

/**
 * The builders are the frontend half of the intent_hint contract: skill_id and
 * param names must match the backend pydantic Input schemas exactly, otherwise
 * route_hint falls back to text routing and the jump loses its determinism.
 */
describe("chatNav intent hint builders", () => {
  it("analyzeStockHint targets stock_analysis with a ticker", () => {
    expect(analyzeStockHint("600519.SH")).toEqual({
      skill_id: "stock_analysis",
      params: { ticker: "600519.SH" },
    });
  });

  it("dailyPipelineHint carries limit and only adds industries when provided", () => {
    expect(dailyPipelineHint(5)).toEqual({
      skill_id: "daily_pipeline",
      params: { limit: 5 },
    });
    expect(dailyPipelineHint(3, ["半导体"])).toEqual({
      skill_id: "daily_pipeline",
      params: { limit: 3, industries: ["半导体"] },
    });
    // Empty industry list must not leak an empty array into params.
    expect(dailyPipelineHint(3, []).params).toEqual({ limit: 3 });
    expect(dailyPipelineHint(5, ["医药生物"], ["CXO概念"])).toEqual({
      skill_id: "daily_pipeline",
      params: {
        limit: 5,
        industries: ["医药生物"],
        concepts: ["CXO概念"],
      },
    });
    expect(
      dailyPipelineHint(5, ["电子"], [], {
        taxonomy: "CITICS",
        level: "L1",
        codes: ["CI005009.CI"],
      }),
    ).toEqual({
      skill_id: "daily_pipeline",
      params: {
        limit: 5,
        industries: ["电子"],
        industry_taxonomy: "CITICS",
        industry_level: "L1",
        industry_codes: ["CI005009.CI"],
      },
    });
  });

  it("positionAdviceHint defaults intent to review", () => {
    expect(positionAdviceHint("600519.SH")).toEqual({
      skill_id: "position_advisor",
      params: { symbol: "600519.SH", intent: "review" },
    });
    expect(positionAdviceHint("000001.SZ", "reduce").params.intent).toBe("reduce");
  });

  it("dailyReviewHint targets the review workflow with dashboard defaults", () => {
    expect(dailyReviewHint()).toEqual({
      skill_id: "daily_review",
      params: { daily_limit: 5, candidate_limit: 120 },
    });
    expect(dailyReviewHint(8, 160).params).toEqual({
      daily_limit: 8,
      candidate_limit: 160,
    });
  });

  it("riskMonitorHint uses skill defaults (empty params)", () => {
    expect(riskMonitorHint()).toEqual({ skill_id: "risk_monitor", params: {} });
  });

  it("marketScannerHint only adds min_score when provided", () => {
    expect(marketScannerHint(3)).toEqual({
      skill_id: "market_scanner",
      params: { limit: 3 },
    });
    expect(marketScannerHint(3, 60)).toEqual({
      skill_id: "market_scanner",
      params: { limit: 3, min_score: 60 },
    });
  });
});
