import type { TaggedNewsItem } from "../../api/client";

export type PolarityFilter = "all" | "bullish" | "bearish";

export interface HoldingRef {
  symbol: string;
  name?: string | null;
}

/** Normalize a holding symbol like "600519.SH" / "sh600519" to its 6-digit code. */
export function holdingCode(symbol: string): string {
  const match = String(symbol ?? "").match(/\d{6}/);
  return match ? match[0] : "";
}

/** True when a news item mentions any of the holdings (by 6-digit code or name). */
export function newsMatchesHoldings(item: TaggedNewsItem, holdings: HoldingRef[]): boolean {
  if (holdings.length === 0) return false;
  const codes = new Set(holdings.map((h) => holdingCode(h.symbol)).filter(Boolean));
  for (const symbol of item.symbols ?? []) {
    if (codes.has(holdingCode(symbol))) return true;
  }
  const text = `${item.title} ${item.content ?? ""} ${(item.industries ?? []).join(" ")}`;
  return holdings.some((h) => {
    const name = (h.name ?? "").trim();
    return name.length >= 2 && text.includes(name);
  });
}

/** Pure news-feed filter: polarity tab + optional "only my holdings" switch. */
export function filterNews(
  news: TaggedNewsItem[],
  polarity: PolarityFilter,
  holdingsOnly: boolean,
  holdings: HoldingRef[],
): TaggedNewsItem[] {
  return news.filter((item) => {
    if (polarity !== "all" && item.polarity !== polarity) return false;
    if (holdingsOnly && !newsMatchesHoldings(item, holdings)) return false;
    return true;
  });
}
