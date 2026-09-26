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
    <section className="rounded-lg border border-ui-line bg-ui-panel p-4">
      <div className="mb-3 flex items-center gap-2">
        <CalendarDays className="h-4 w-4 text-ui-accent" />
        <h3 className="text-sm font-semibold text-ui-ink">大事日历</h3>
      </div>
      <div className="flex flex-wrap gap-2">
        {macro.map((item) => (
          <span
            key={item.name}
            className="inline-flex items-center gap-1.5 rounded border border-ui-strong bg-ui-subtle px-2 py-1 text-xs text-ui-body"
            title={item.date}
          >
            <span className="text-ui-faint">{item.name}</span>
            <span className="font-mono">{item.value ?? "--"}</span>
            {item.date && <span className="text-[10px] text-ui-faint">{item.date}</span>}
          </span>
        ))}
        {unlocks.map((event) => {
          const held = holdingCodes.has(holdingCode(event.symbol));
          return (
            <span
              key={`${event.symbol}-${event.date}`}
              className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 text-xs ${
                held
                  ? "border-ui-danger/40 bg-ui-danger/10 text-ui-danger"
                  : "border-ui-strong bg-ui-subtle text-ui-muted"
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
