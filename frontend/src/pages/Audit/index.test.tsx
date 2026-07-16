import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import Audit from ".";

vi.mock("@/api/client", () => ({
  getDecisionAuditSummary: vi.fn().mockResolvedValue({
    decision_count: 2, open_count: 1, realized_count: 1,
    execution_count: 1, linked_execution_count: 1, execution_link_rate: 1,
    execution_validation: { sample_count: 1, win_rate: 1, average_directional_return: 0.04, statistically_usable: false },
    validation: { overall: { sample_count: 1, win_rate: 1, average_return: 0.05, statistically_usable: false }, by_decision: {}, strategy_claims_allowed: false, effectiveness_claim_allowed: false, warnings: [] },
  }),
  listDecisionRecords: vi.fn().mockResolvedValue([{
    id: "decision:1", source_type: "daily_pipeline", symbol: "600519.SH",
    decision_date: "2026-07-01", decision: "BUY", horizon_days: 5,
    reference_price: 100, status: "realized", execution_count: 1, outcome_count: 1, payload: {},
  }]),
  listBacktests: vi.fn().mockResolvedValue([]),
  getBacktestCatalog: vi.fn().mockResolvedValue({
    strategies: [{ name: "ff_residual_csi800_main", sha1: "s1" }],
    configs: [{ name: "prod_ff_residual_csi800_tv15", sha1: "c1" }],
  }),
  evaluateDecisionAudit: vi.fn(), createBacktest: vi.fn(),
  recordDecisionExecution: vi.fn(),
}));

test("shows the audit ledger and blocks claims with insufficient samples", async () => {
  render(<QueryClientProvider client={new QueryClient()}><Audit /></QueryClientProvider>);
  expect(await screen.findByText("600519.SH")).toBeInTheDocument();
  expect(screen.getByText("样本不足", { selector: "div" })).toBeInTheDocument();
  expect(screen.getByText(/至少需要 20 个已实现样本/)).toBeInTheDocument();
  expect(screen.getByText("候选量化规则回测")).toBeInTheDocument();
  fireEvent.click(screen.getByText("记录执行"));
  expect(screen.getByLabelText("执行数量")).toBeInTheDocument();
  expect(screen.getByLabelText("执行价格")).toHaveValue("100");
});
