import { describe, expect, it } from "vitest";
import type { PaperCurve } from "@/api/paper";
import { buildRecentPaperPnl } from "./dailyPnl";

describe("recent paper account P&L", () => {
  it("uses the preceding ledger day to calculate all seven displayed changes", () => {
    const curve: PaperCurve = { benchmark_curve: [], daily_records: Array.from({ length: 8 }, (_, index) => ({
      date: `2026-09-${String(index + 1).padStart(2, "0")}T00:00:00`,
      equity: 100000 + index * 100,
      cash: 2000,
      positions_value: 98000 + index * 100,
    })) };
    const result = buildRecentPaperPnl(curve, "2026-09-08");

    expect(result.rows).toHaveLength(7);
    expect(result.rows[0]).toMatchObject({ date: "2026-09-08", equity: 100700,
      positionsValue: 98700, change: 100 });
    expect(result.rows[6]).toMatchObject({ date: "2026-09-02", change: 100 });
    expect(result.change).toBe(700);
    expect(result.changePct).toBeCloseTo(0.007);
  });

  it("does not invent a prior day or show records after the ledger snapshot", () => {
    const curve: PaperCurve = { benchmark_curve: [], daily_records: [
      { date: "2026-09-28", equity: 100000, cash: 2000 },
      { date: "2026-09-29", equity: 101000, cash: 2000 },
    ] };
    const result = buildRecentPaperPnl(curve, "2026-09-28");

    expect(result.rows).toHaveLength(1);
    expect(result.rows[0]).toMatchObject({ date: "2026-09-28", change: null,
      positionsValue: null });
    expect(result.change).toBeNull();
  });
});
