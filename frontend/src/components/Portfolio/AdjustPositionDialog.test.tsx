import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AdjustPositionDialog } from "@/components/Portfolio/AdjustPositionDialog";

describe("AdjustPositionDialog", () => {
  it("prefills advisor quantity and price while requiring confirmation", () => {
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
    expect(screen.getByText(/减仓后/)).toHaveTextContent("50 股");
    expect(screen.getByRole("button", { name: "确认减仓" })).toBeEnabled();
  });
});
