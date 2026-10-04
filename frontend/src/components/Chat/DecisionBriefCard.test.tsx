import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AnalysisSummaryCard, parseAnalysisSummaryFromStructured } from "./AnalysisSummaryCard";
import { DecisionBriefCard, type DecisionBrief } from "./DecisionBriefCard";

afterEach(cleanup);
const brief: DecisionBrief = { action_state: "wait_trigger", summary: "等待财报确认盈利改善",
  symbol: "600000.SH", horizon: "medium_term", horizon_days: 60, as_of_date: "2026-09-30",
  entry_conditions: [{ description: "营收与现金流同步改善", source: "已披露半年报" }],
  exit_conditions: [{ description: "盈利改善逻辑失效" }], evidence_gaps: ["缺少同业估值对比"] };

it("shows understandable conditions and asks about the same symbol", () => {
  const ask = vi.fn();
  render(<DecisionBriefCard brief={brief} onAsk={ask} />);
  expect(screen.getByText("等待条件满足")).toBeTruthy();
  expect(screen.getByText("什么条件下考虑进入")).toBeTruthy();
  expect(screen.getByText("缺少同业估值对比")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "把进入和退出条件说具体" }));
  expect(ask).toHaveBeenCalledWith("600000.SH：把进入和退出条件说具体");
});

it("structured decision supersedes legacy rating and account actions", () => {
  const summary = parseAnalysisSummaryFromStructured({ symbol: "600000.SH", rating: "Buy", decision_brief: brief,
    plan: { position_pct: 50, order_quantity: 1000, entry_zone: [10, 11] } });
  render(<AnalysisSummaryCard summary={summary!} onAddHolding={vi.fn()} />);
  expect(screen.getByLabelText("个股条件决策")).toBeTruthy();
  expect(screen.queryByText("Buy")).toBeNull();
  expect(screen.queryByRole("button", { name: "加入持仓" })).toBeNull();
  expect(screen.queryByText("本次可执行数量")).toBeNull();
});
