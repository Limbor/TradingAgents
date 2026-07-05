/**
 * Pure portfolio computation utilities extracted from Dashboard.
 */

import type { Holding } from "@/api/client";

export interface PortfolioSummary {
  count: number;
  cost: number;
  value: number;
  pnl: number;
  pnlPct: number;
  concentration: number;
  topSymbol: string;
}

export function buildPortfolioSummary(holdings: Holding[]): PortfolioSummary {
  let cost = 0;
  let value = 0;
  let topValue = 0;
  let topSymbol = "";
  for (const item of holdings) {
    const rowCost = item.quantity * item.avg_cost;
    const rowValue = item.quantity * (item.current_price ?? item.avg_cost);
    cost += rowCost;
    value += rowValue;
    if (rowValue > topValue) {
      topValue = rowValue;
      topSymbol = item.symbol;
    }
  }
  const pnl = value - cost;
  const pnlPct = cost ? (pnl / cost) * 100 : 0;
  const concentration = value ? (topValue / value) * 100 : 0;
  return { count: holdings.length, cost, value, pnl, pnlPct, concentration, topSymbol };
}

export function holdingMarketValue(holding: Holding): number {
  return holding.quantity * (holding.current_price ?? holding.avg_cost);
}

export function holdingPnl(holding: Holding): number {
  return ((holding.current_price ?? holding.avg_cost) - holding.avg_cost) * holding.quantity;
}

export function formatMoney(value: number): string {
  if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(2)}万`;
  return value.toFixed(0);
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 2,
  }).format(value);
}
