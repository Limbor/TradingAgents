import { describe, expect, it } from "vitest";

import type { Holding } from "@/api/client";
import {
  buildPortfolioSummary,
  formatMoney,
  holdingMarketValue,
  holdingPnl,
} from "@/utils/portfolio";

const holdings: Holding[] = [
  {
    symbol: "600519.SH",
    name: "贵州茅台",
    quantity: 10,
    avg_cost: 100,
    current_price: 120,
    notes: null,
    updated_at: "2026-07-11T00:00:00Z",
  },
  {
    symbol: "000001.SZ",
    quantity: 20,
    avg_cost: 50,
    current_price: null,
    notes: null,
    updated_at: "2026-07-11T00:00:00Z",
  },
];

describe("portfolio utilities", () => {
  it("builds value, pnl, and concentration from holdings", () => {
    expect(buildPortfolioSummary(holdings)).toEqual({
      count: 2,
      cost: 2000,
      value: 2200,
      pnl: 200,
      pnlPct: 10,
      concentration: (1200 / 2200) * 100,
      topSymbol: "600519.SH",
    });
  });

  it("falls back to cost when the latest price is missing", () => {
    expect(holdingMarketValue(holdings[1]!)).toBe(1000);
    expect(holdingPnl(holdings[1]!)).toBe(0);
  });

  it("formats large amounts in ten-thousands", () => {
    expect(formatMoney(12345)).toBe("1.23万");
    expect(formatMoney(-120)).toBe("-120");
  });
});
