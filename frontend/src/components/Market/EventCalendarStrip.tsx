import { CalendarDays, LockOpen } from "lucide-react";
import type { MacroReading, UnlockEvent } from "../../api/client";
import { holdingCode, type HoldingRef } from "./newsFilter";

interface EventCalendarStripProps {
  macro: MacroReading[];
  unlocks: UnlockEvent[];
  holdings: HoldingRef[];
}

export function EventCalendarStrip({ macro, unlocks, holdings }: EventCalendarStripProps) {
  if (macro.length === 0 && unlocks.length === 0) return null;
  const holdingCodes = new Set(holdings.map((h) => holdingCode(h.symbol)).filter(Boolean));

  return (
    <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
      <div className="mb-3 flex items-center gap-2">
        <CalendarDays className="h-4 w-4 text-teal-300" />
        <h3 className="text-sm font-semibold text-stone-100">大事日历</h3>
      </div>
      <div className="flex flex-wrap gap-2">
        {macro.map((item) => (
          <span
            key={item.name}
            className="inline-flex items-center gap-1.5 rounded border border-stone-700 bg-stone-950 px-2 py-1 text-xs text-stone-300"
            title={item.date}
          >
            <span className="text-stone-500">{item.name}</span>
            <span className="font-mono">{item.value ?? "--"}</span>
            {item.date && <span className="text-[10px] text-stone-600">{item.date}</span>}
          </span>
        ))}
        {unlocks.map((event) => {
          const held = holdingCodes.has(holdingCode(event.symbol));
          return (
            <span
              key={`${event.symbol}-${event.date}`}
              className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 text-xs ${
                held
                  ? "border-red-500/40 bg-red-500/10 text-red-300"
                  : "border-stone-700 bg-stone-950 text-stone-400"
              }`}
              title={event.market_value !== null ? `解禁市值 ${(event.market_value / 1e8).toFixed(1)} 亿` : undefined}
            >
              <LockOpen className="h-3 w-3" />
              <span>{event.date?.slice(5)}</span>
              <span className={held ? "font-semibold" : ""}>
                {event.name}
                {held ? "（持仓）" : ""}
              </span>
            </span>
          );
        })}
      </div>
    </section>
  );
}
