import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { AnalysisSummaryCard } from "./AnalysisSummaryCard";

afterEach(() => {
  cleanup();
});

describe("AnalysisSummaryCard plan direction", () => {
  it("renders long-style labels for a Buy plan", () => {
    render(
      <AnalysisSummaryCard
        summary={{
          rating: "Buy",
          symbol: "600519.SH",
          plan: { entry_zone: [1700, 1720], stop_loss: 1650, targets: [1800, 1850], position_pct: 20 },
        }}
      />,
    );

    expect(screen.getByText("建仓区间")).toBeTruthy();
    expect(screen.getByText("目标价")).toBeTruthy();
    expect(screen.queryByText(/评级偏空/)).toBeNull();
  });

  it("relabels the plan as reduce/exit for an Underweight rating", () => {
    render(
      <AnalysisSummaryCard
        onAddHolding={() => undefined}
        summary={{
          rating: "Underweight",
          symbol: "300086.SZ",
          plan: {
            entry_zone: [36.5, 37],
            stop_loss: 37.5,
            targets: [34.5, 33.53, 33],
            position_pct: 40,
            conditions: [
              { kind: "entry", description: "反弹到区间后主动减持" },
              { kind: "stop", description: "站上风控位后看空逻辑失效" },
              { kind: "take_profit", description: "跌至目标位后清理剩余仓位" },
            ],
          },
        }}
      />,
    );

    expect(screen.getByText("减仓/卖出区间")).toBeTruthy();
    expect(screen.getByText("下方目标价")).toBeTruthy();
    expect(screen.getByText("风控位")).toBeTruthy();
    expect(screen.getByText("[减仓]")).toBeTruthy();
    expect(screen.getByText("[看空失效]")).toBeTruthy();
    expect(screen.getByText("[下行止盈]")).toBeTruthy();
    expect(screen.getByText(/评级偏空/)).toBeTruthy();
    expect(screen.queryByText("建仓区间")).toBeNull();
    expect(screen.queryByText("[建仓]")).toBeNull();
    expect(screen.queryByText("[止损]")).toBeNull();
    expect(screen.queryByText("[止盈]")).toBeNull();
    expect(screen.queryByRole("button", { name: "加入持仓" })).toBeNull();
  });

  it("lets an explicit REDUCE action override a contradictory Buy rating", () => {
    render(
      <AnalysisSummaryCard
        onAddHolding={() => undefined}
        summary={{
          rating: "Buy",
          symbol: "300086.SZ",
          plan: {
            plan_action: "REDUCE",
            action_zone: [36.5, 37],
            invalidation_level: 37.5,
            objective_levels: [34.5, 33],
          },
        }}
      />,
    );

    expect(screen.getByText("减仓/卖出区间")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "加入持仓" })).toBeNull();
  });

  it("shows add-to-holdings only for an explicit long Buy plan", () => {
    render(
      <AnalysisSummaryCard
        onAddHolding={() => undefined}
        summary={{
          rating: "Buy",
          symbol: "600519.SH",
          plan: { plan_action: "ENTER", action_zone: [1700, 1720] },
        }}
      />,
    );

    expect(screen.getByRole("button", { name: "加入持仓" })).toBeTruthy();
  });

  it("detects inverted levels even without a bearish rating", () => {
    render(
      <AnalysisSummaryCard
        summary={{
          rating: "Hold",
          symbol: "300086.SZ",
          plan: { entry_zone: [36.5, 37], targets: [34.5, 33] },
        }}
      />,
    );

    expect(screen.getByText("减仓/卖出区间")).toBeTruthy();
    expect(screen.queryByText("建仓区间")).toBeNull();
  });

  it("surfaces a selection-analysis reversal before plan adoption", () => {
    render(
      <AnalysisSummaryCard
        summary={{
          rating: "Underweight",
          symbol: "600763.SH",
          plan: { plan_action: "REDUCE", action_zone: [83, 85] },
          selection_alignment: {
            status: "reversal",
            selection_decision: "BUY",
            analysis_rating: "Underweight",
            requires_review: true,
            explicit_explanation: true,
            explanation: "新增质押风险改变风险收益比。",
            plan_consistency: { status: "consistent" },
          },
        }}
      />,
    );

    expect(screen.getByText(/选股 BUY → 个股分析 Underweight/)).toBeTruthy();
    expect(screen.getByText(/需要复核/)).toBeTruthy();
    expect(screen.getByText("新增质押风险改变风险收益比。")).toBeTruthy();
  });

  it("shows the A-share lot guard and disables adoption of an adjusted plan", () => {
    render(
      <AnalysisSummaryCard
        summary={{
          rating: "Underweight",
          symbol: "601333.SH",
          plan: {
            plan_action: "HOLD",
            action_zone: [58.5, 62],
            target_quantity: 100,
            conditions: [
              {
                kind: "entry",
                trigger_action: "HOLD",
                description: "当前100股不支持部分减仓；该条件仅触发重新评估。",
              },
            ],
            execution_validation: {
              status: "adjusted",
              current_quantity: 100,
              lot_size: 100,
              warnings: ["已将不可执行的 REDUCE 主计划降级为 HOLD/人工复核"],
            },
          },
        }}
      />,
    );

    expect(screen.getByText(/A 股交易单位校验已拦截/)).toBeTruthy();
    expect(screen.getByText(/降级为 HOLD/)).toBeTruthy();
    expect(screen.getByText("执行后持仓")).toBeTruthy();
    expect(screen.getByText("100 股")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "纳入计划" })).toBeNull();
  });
});
