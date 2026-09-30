import { describe, expect, it } from "vitest";
import type { PaperCurve, PaperStatus } from "@/api/paper";
import { buildPaperPerformance } from "./performance";

const account = (daily_records: PaperCurve["daily_records"], benchmark_curve: PaperCurve["benchmark_curve"] = []): PaperCurve =>
  ({ daily_records, benchmark_curve });
const composite = (sleeve_curves: NonNullable<PaperStatus["sleeve_curves"]>): PaperStatus => ({
  kind: "composite", sleeve_curves,
} as PaperStatus);

describe("paper performance comparison", () => {
  it("rebases the account, both sleeve signals and SSE index at one shared day", () => {
    const result = buildPaperPerformance(account([
      { date: "2026-09-25T00:00:00", equity: 100000, cash: 0 },
      { date: "2026-09-28T00:00:00", equity: 110000, cash: 0 },
    ], [
      { date: "2026-09-25", equity: 3000 }, { date: "2026-09-28", equity: 3060 },
    ]), composite({
      defensive: [{ date: "2026-09-25", equity: 100000 }, { date: "2026-09-28", equity: 105000 }],
      attack: [{ date: "2026-09-25", equity: 100000 }, { date: "2026-09-28", equity: 95000 }],
    }));

    expect(result.baseDate).toBe("2026-09-25");
    expect(result.lines.map((line) => line.label)).toEqual(["模拟盘账户", "上证指数", "defensive", "attack"]);
    expect(result.rows[0]).toMatchObject({ account: 0, sleeve_0: 0, sleeve_1: 0, benchmark: 0 });
    expect(result.rows[1]?.account).toBeCloseTo(10);
    expect(result.rows[1]?.sleeve_0).toBeCloseTo(5);
    expect(result.rows[1]?.sleeve_1).toBeCloseTo(-5);
    expect(result.rows[1]?.benchmark).toBeCloseTo(2);
  });

  it("starts all lines at the first common date when the benchmark starts later", () => {
    const result = buildPaperPerformance(account([
      { date: "2026-09-25", equity: 100, cash: 0 },
      { date: "2026-09-28", equity: 105, cash: 0 },
      { date: "2026-09-29", equity: 110, cash: 0 },
    ], [
      { date: "2026-09-28", equity: 3000 }, { date: "2026-09-29", equity: 3030 },
    ]));

    expect(result.baseDate).toBe("2026-09-28");
    expect(result.rows).toHaveLength(2);
    expect(result.rows[0]).toMatchObject({ account: 0, benchmark: 0 });
    expect(result.rows[1]?.account).toBeCloseTo((110 / 105 - 1) * 100);
    expect(result.rows[1]?.benchmark).toBeCloseTo(1);
  });

  it("omits unavailable reference lines without inventing prices", () => {
    const result = buildPaperPerformance(account([
      { date: "2026-09-25", equity: 100, cash: 0 },
      { date: "2026-09-28", equity: 101, cash: 0 },
    ], [{ date: "2026-09-28", equity: 3000 }]), composite({
      stale: [{ date: "2026-09-22", equity: 100 }, { date: "2026-09-23", equity: 101 }],
    }));

    expect(result.lines.map((line) => line.key)).toEqual(["account"]);
    expect(result.benchmarkAvailable).toBe(false);
    expect(result.rows[1]).not.toHaveProperty("benchmark");
  });

  it("retains the index when a sleeve has an incompatible trading span", () => {
    const result = buildPaperPerformance(account([
      { date: "2026-09-24", equity: 100, cash: 0 },
      { date: "2026-09-25", equity: 101, cash: 0 },
      { date: "2026-09-28", equity: 102, cash: 0 },
      { date: "2026-09-29", equity: 103, cash: 0 },
    ], [
      { date: "2026-09-28", equity: 3000 }, { date: "2026-09-29", equity: 3030 },
    ]), composite({
      old_arm: [{ date: "2026-09-24", equity: 100 }, { date: "2026-09-25", equity: 105 }],
    }));

    expect(result.baseDate).toBe("2026-09-28");
    expect(result.lines.map((line) => line.key)).toEqual(["account", "benchmark"]);
    expect(result.benchmarkAvailable).toBe(true);
  });
});
