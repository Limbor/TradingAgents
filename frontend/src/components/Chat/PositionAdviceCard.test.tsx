import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { PositionAdviceCard } from "@/components/Chat/PositionAdviceCard";

describe("PositionAdviceCard", () => {
  it("renders an actionable reduction with confirmation boundary", () => {
    render(
      <MemoryRouter>
        <PositionAdviceCard
        advice={{
          symbol: "600519.SH",
          action: "REDUCE",
          metrics: { current_price: 110, pnl_pct: 10, position_pct: 60 },
          risk: { level: "orange" },
          execution: {
            quantity_change: -50,
            target_position_pct: 30,
            requires_user_confirmation: true,
          },
          reasons: ["仓位超过集中度阈值"],
          warnings: ["交易计划不可用"],
        }}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText("REDUCE")).toBeInTheDocument();
    expect(screen.getByText("-50 股")).toBeInTheDocument();
    expect(screen.getByText("30.00%")).toBeInTheDocument();
    expect(screen.getByText(/不会自动修改持仓/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认减仓/ })).toBeInTheDocument();
  });
});
