import type { PaperCurve } from "@/api/paper";

export interface PaperDailyPnlRow {
  date: string;
  equity: number;
  cash: number | null;
  positionsValue: number | null;
  change: number | null;
  changePct: number | null;
}

export interface RecentPaperPnl {
  rows: PaperDailyPnlRow[];
  change: number | null;
  changePct: number | null;
  asOfDate: string | null;
}

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

/** Equity changes include realized and unrealized P&L on StockManager's ledger dates. */
export function buildRecentPaperPnl(curve?: PaperCurve, ledgerDate?: string | null): RecentPaperPnl {
  const daily = new Map<string, PaperCurve["daily_records"][number]>();
  for (const record of curve?.daily_records ?? []) {
    const date = typeof record.date === "string" && /^\d{4}-\d{2}-\d{2}/.test(record.date)
      ? record.date.slice(0, 10) : null;
    if (date && (!ledgerDate || date <= ledgerDate) && finite(record.equity) && record.equity > 0) {
      daily.set(date, record);
    }
  }
  const ordered = [...daily.entries()].sort(([a], [b]) => a.localeCompare(b));
  const start = Math.max(0, ordered.length - 7);
  const rows = ordered.slice(start).map(([date, record], offset) => {
    const prior = ordered[start + offset - 1]?.[1];
    const change = prior && prior.equity > 0 ? record.equity - prior.equity : null;
    return {
      date,
      equity: record.equity,
      cash: finite(record.cash) ? record.cash : null,
      positionsValue: finite(record.positions_value) ? record.positions_value : null,
      change,
      changePct: change != null && prior ? change / prior.equity : null,
    };
  }).reverse();
  const priorPeriod = ordered[start - 1]?.[1];
  const latest = rows[0];
  const change = latest && priorPeriod && priorPeriod.equity > 0
    ? latest.equity - priorPeriod.equity : null;
  return { rows, change, changePct: change != null && priorPeriod ? change / priorPeriod.equity : null,
    asOfDate: latest?.date ?? null };
}
