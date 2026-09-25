import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";
import Audit from ".";

vi.mock("@/api/client", () => ({
  getDecisionAuditSummary: vi.fn().mockResolvedValue({
    decision_count: 2, open_count: 1, realized_count: 1,
    execution_count: 1, linked_execution_count: 1, execution_link_rate: 1,
    execution_validation: { sample_count: 1, win_rate: 1, average_directional_return: 0.04, statistically_usable: false },
    validation: {
      overall: { sample_count: 7, win_rate: 0.143, average_return: -0.03, average_directional_return: -0.03, statistically_usable: false },
      by_decision: {},
      by_source: { daily_pipeline: { sample_count: 7, win_rate: 0.143, average_return: -0.03, average_directional_return: -0.03, statistically_usable: false } },
      unique_signal_count: 6, unique_symbol_count: 5, unique_signal_date_count: 4,
      duplicate_signal_count: 1,
      strategy_claims_allowed: false, effectiveness_claim_allowed: false, warnings: [],
    },
  }),
  listDecisionRecords: vi.fn().mockResolvedValue([
    {
      id: "decision:1", source_type: "daily_pipeline", symbol: "600519.SH",
      decision_date: "2026-07-01", decision: "BUY", horizon_days: 5,
      reference_price: 100, status: "realized", execution_count: 1, outcome_count: 1, payload: {},
    },
    {
      id: "decision:2", source_type: "daily_pipeline", symbol: "000001.SZ",
      decision_date: "2026-07-02", decision: "MONITOR", horizon_days: 5,
      reference_price: 10, status: "open", execution_count: 0, outcome_count: 0, payload: {},
    },
  ]),
  listBacktests: vi.fn().mockResolvedValue([]),
  getBacktestCatalog: vi.fn().mockResolvedValue({
    strategies: [{ name: "ff_residual_csi800_main", sha1: "s1" }],
    configs: [{ name: "prod_ff_residual_csi800_tv15", sha1: "c1" }],
  }),
  evaluateDecisionAudit: vi.fn().mockResolvedValue({
    evaluated_outcomes: 0, skipped: 0, selected_records: 20,
    attempted_outcomes: 0, due_records: 0, not_due_records: 20,
    as_of_date: "2026-08-06", warnings: [],
  }),
  recordDecisionExecution: vi.fn(),
}));

test("shows the audit ledger and blocks claims with insufficient samples", async () => {
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><Audit /></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText("600519.SH")).toBeInTheDocument();
  expect(screen.getByText("样本不足", { selector: "div" })).toBeInTheDocument();
  expect(screen.getByText(/至少需要 20 个已实现样本/)).toBeInTheDocument();
  expect(screen.getByText("前往策略研究 →")).toHaveAttribute("href", "/research");
  expect(screen.getByText(/同源同日同标的重复样本 1 条/)).toBeInTheDocument();
  expect(screen.getAllByText(/daily_pipeline/).length).toBeGreaterThan(0);
  expect(screen.getAllByText("记录执行")).toHaveLength(1);
  fireEvent.click(screen.getByText("评估到期收益"));
  expect(await screen.findByText(/当前没有新增到期收益/)).toBeInTheDocument();
  fireEvent.click(screen.getByText("记录执行"));
  expect(screen.getByLabelText("执行数量")).toBeInTheDocument();
  expect(screen.getByLabelText("执行价格")).toHaveValue("100");
});
