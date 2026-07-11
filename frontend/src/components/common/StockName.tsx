import { memo } from "react";

/**
 * Resolve a stock's display name: prefer the human-readable name when it
 * differs from the ticker symbol, otherwise fall back to the symbol.
 *
 * Centralizes the `name && name !== symbol ? name : symbol` pattern that was
 * duplicated across plan / holding / alert / reflection components.
 */
export function displayNameOf(
  name: string | null | undefined,
  symbol: string,
): string {
  return name && name !== symbol ? name : symbol;
}

/**
 * Render a stock identity: the display name plus the symbol when the two
 * differ. Styling is overridable via props so callers keep their existing
 * visual treatment.
 */
export const StockName = memo(function StockName({
  name,
  symbol,
  className,
  nameClassName = "font-medium text-stone-100",
  symbolClassName = "ml-1 font-mono text-xs text-stone-500",
}: {
  name: string | null | undefined;
  symbol: string;
  className?: string;
  nameClassName?: string;
  symbolClassName?: string;
}) {
  const distinct = !!name && name !== symbol;
  return (
    <span className={className}>
      <span className={nameClassName}>{displayNameOf(name, symbol)}</span>
      {distinct && <span className={symbolClassName}>{symbol}</span>}
    </span>
  );
});
