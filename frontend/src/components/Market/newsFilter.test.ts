import { describe, expect, it } from "vitest";
import type { TaggedNewsItem } from "../../api/client";
import { filterNews, holdingCode, newsMatchesHoldings } from "./newsFilter";

function makeNews(overrides: Partial<TaggedNewsItem> = {}): TaggedNewsItem {
  return {
    title: "标题",
    content: "内容",
    datetime: "2025-06-06 09:00",
    polarity: "neutral",
    impact_scope: "market",
    impact_level: "low",
    industries: [],
    symbols: [],
    interpretation: "解读",
    ...overrides,
  };
}

describe("holdingCode", () => {
  it("extracts 6-digit code from common symbol formats", () => {
    expect(holdingCode("600519.SH")).toBe("600519");
    expect(holdingCode("sz000001")).toBe("000001");
    expect(holdingCode("300750")).toBe("300750");
  });

  it("returns empty string when no code present", () => {
    expect(holdingCode("AAPL")).toBe("");
    expect(holdingCode("")).toBe("");
  });
});

describe("newsMatchesHoldings", () => {
  const holdings = [{ symbol: "600519.SH", name: "贵州茅台" }];

  it("matches by symbol code", () => {
    const item = makeNews({ symbols: ["600519"] });
    expect(newsMatchesHoldings(item, holdings)).toBe(true);
  });

  it("matches by holding name in title/content", () => {
    const item = makeNews({ title: "贵州茅台发布年报" });
    expect(newsMatchesHoldings(item, holdings)).toBe(true);
  });

  it("does not match unrelated news or empty holdings", () => {
    const item = makeNews({ title: "某新能源公司公告", symbols: ["300750"] });
    expect(newsMatchesHoldings(item, holdings)).toBe(false);
    expect(newsMatchesHoldings(item, [])).toBe(false);
  });
});

describe("filterNews", () => {
  const holdings = [{ symbol: "600519.SH", name: "贵州茅台" }];
  const news = [
    makeNews({ title: "利好A", polarity: "bullish", symbols: ["600519"] }),
    makeNews({ title: "利空B", polarity: "bearish" }),
    makeNews({ title: "中性C", polarity: "neutral" }),
    makeNews({ title: "未打标D", polarity: null }),
  ];

  it("keeps everything with the all tab and no holdings switch", () => {
    expect(filterNews(news, "all", false, holdings)).toHaveLength(4);
  });

  it("filters by polarity", () => {
    const bullish = filterNews(news, "bullish", false, holdings);
    expect(bullish.map((n) => n.title)).toEqual(["利好A"]);
    const bearish = filterNews(news, "bearish", false, holdings);
    expect(bearish.map((n) => n.title)).toEqual(["利空B"]);
  });

  it("combines polarity tab with holdings-only switch", () => {
    const mine = filterNews(news, "all", true, holdings);
    expect(mine.map((n) => n.title)).toEqual(["利好A"]);
    expect(filterNews(news, "bearish", true, holdings)).toHaveLength(0);
  });
});
