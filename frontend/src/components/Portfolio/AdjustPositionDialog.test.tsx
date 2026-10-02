import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdjustPositionDialog } from "@/components/Portfolio/AdjustPositionDialog";

afterEach(() => cleanup());

describe("AdjustPositionDialog", () => {
  it("blocks a 50-share partial sell from a 100-share A-share holding", () => {
    render(
      <AdjustPositionDialog
        holding={{
          symbol: "600519.SH",
          quantity: 100,
          avg_cost: 100,
          current_price: 110,
          notes: null,
          updated_at: "2026-07-11T00:00:00Z",
        }}
        action="reduce"
        initialQuantity={50}
        initialPrice={108}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("卖出数量")).toHaveValue(50);
    expect(screen.getByLabelText("卖出价")).toHaveValue(108);
    expect(screen.getByText(/不支持部分减仓/)).toHaveTextContent("一次性清仓 100 股");
    expect(screen.getByRole("button", { name: "确认减仓" })).toBeDisabled();
  });

  it("allows a full 100-share exit", () => {
    render(
      <AdjustPositionDialog
        holding={{
          symbol: "600519.SH",
          quantity: 100,
          avg_cost: 100,
          current_price: 110,
          notes: null,
          updated_at: "2026-07-11T00:00:00Z",
        }}
        action="reduce"
        initialQuantity={100}
        initialPrice={108}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText(/减仓后/)).toHaveTextContent("0 股");
    expect(screen.getByText(/清仓/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认减仓" })).toBeEnabled();
  });
});
