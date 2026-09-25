import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CandidateTable, parseCandidates } from "./CandidateTable";

const { getTradeReview, saveCandidateAction } = vi.hoisted(() => ({
  getTradeReview: vi.fn(),
  saveCandidateAction: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/client")>();
  return { ...original, getTradeReview, saveCandidateAction };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderTable(onAnalyze?: (symbol: string, context?: Record<string, unknown>) => void) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <CandidateTable
        actionContext={{ tradeDate: "2026-07-31" }}
        onAnalyze={onAnalyze}
        candidates={[{
          rank: 1,
          symbol: "600519.SH",
          decision: "BUY",
          quantDecision: "BUY",
          llmView: "support",
          latestPrice: 10.5,
          entryZone: [10, 11],
          stopLoss: 9,
          targets: [13],
          raw: {
            final_decision: "BUY",
            quant_decision: "BUY",
            quant_score: 78,
            llm_view: "positive",
            catalyst_strength: "likely",
            risk_flags: ["pledge_watch"],
            factor_scores: { quality: 82 },
            data_coverage: { flow: "ok" },
          },
        }]}
      />
    </QueryClientProvider>,
  );
}

describe("CandidateTable trade review", () => {
  it("parses and displays the attack/defensive strategy scores", () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const candidates = parseCandidates([{
      symbol: "600519.SH",
      score: 81,
      active_sleeve: "attack",
      strategy_scores: {
        attack_score: 82.4,
        attack_coverage: 1,
        defensive_score: 64,
        defensive_coverage: 0.5,
      },
    }]);
    render(
      <QueryClientProvider client={queryClient}>
        <CandidateTable candidates={candidates} />
      </QueryClientProvider>,
    );

    expect(screen.getByText(/进 82\.4 \(100%\)/)).toBeInTheDocument();
    expect(screen.getByText(/稳 64 \(50%\)/)).toBeInTheDocument();
    expect(screen.getByTitle("当前采用进攻轨评分；括号内为因子覆盖率")).toBeInTheDocument();
  });

  it("hands the complete selection evidence snapshot to stock analysis", () => {
    const onAnalyze = vi.fn();
    renderTable(onAnalyze);

    fireEvent.click(screen.getByRole("button", { name: "分析" }));

    expect(onAnalyze).toHaveBeenCalledWith("600519.SH", {
      selection_context: expect.objectContaining({
        final_decision: "BUY",
        quant_decision: "BUY",
        quant_score: 78,
        llm_view: "positive",
        catalyst_strength: "likely",
        risk_flags: ["pledge_watch"],
        factor_scores: { quality: 82 },
        data_coverage: { flow: "ok" },
      }),
    });
  });

  it("loads K-lines lazily and only adopts an authoritative actionable plan", async () => {
    getTradeReview.mockResolvedValue({
      status: "success",
      as_of_date: "2026-07-31",
      effective_trade_date: "2026-07-31",
      source: "stockmanager",
      method: "trade_review_snapshot_v1",
      warnings: [],
      ts_code: "600519.SH",
      degraded: false,
      gate_authority: "stockmanager_mcp",
      candles: Array.from({ length: 30 }, (_, index) => ({
        trade_date: `2026-07-${String(index + 1).padStart(2, "0")}`,
        open: 10, high: 11, low: 9.5, close: 10.5, volume: 1000,
        ma5: 10, ma10: 10, ma20: 10, ma60: null, volume_ma5: 1000,
      })),
      metrics: {},
      plan: { action_zone: [10, 11], invalidation_level: 9, objective_levels: [13] },
      pretrade_gate: {
        status: "actionable",
        reliability_score: 85,
        reasons: ["all passed"],
        checks: [{ code: "tradability", passed: true, severity: "hard", detail: "当前可交易" }],
      },
      recommendation_reliability: {
        score: 82, level: "high", components: {}, note: "不代表收益率。",
      },
    });
    saveCandidateAction.mockResolvedValue({ status: "saved", plan_id: "plan:1", message: "已加入" });

    renderTable();
    expect(getTradeReview).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTitle("展开候选解释"));
    expect(await screen.findByText("可以执行")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /K 线/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "加入可执行计划" }));

    await waitFor(() => expect(saveCandidateAction).toHaveBeenCalledWith(expect.objectContaining({
      action: "adopt",
      payload: expect.objectContaining({
        trade_review: expect.objectContaining({ gate_authority: "stockmanager_mcp" }),
      }),
    })));
  });

  it("disables plan enrollment when the hard gate rejects", async () => {
    getTradeReview.mockResolvedValue({
      status: "success", as_of_date: "2026-07-31", source: "stockmanager",
      method: "trade_review_snapshot_v1", warnings: [], ts_code: "600519.SH",
      degraded: false, gate_authority: "stockmanager_mcp", candles: [], metrics: {}, plan: {},
      pretrade_gate: { status: "reject", reliability_score: 25, reasons: ["停牌"], checks: [] },
      recommendation_reliability: { score: 30, level: "low", components: {}, note: "不代表收益率。" },
    });
    renderTable();
    fireEvent.click(screen.getByTitle("展开候选解释"));
    expect(await screen.findByText("取消计划")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "不建议加入" })).toBeDisabled();
  });
});
