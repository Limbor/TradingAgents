import { describe, expect, it } from "vitest";

import type { ArtifactInfo } from "@/api/client";
import { parseCandidates } from "@/components/Chat";
import { artifactTradeDate } from "./index";

const artifact = {
  id: "scanner-report-old",
  created_at: "2026-07-31T09:00:00Z",
} as ArtifactInfo;

describe("Library historical candidate date", () => {
  it("reads legacy market_asof_date instead of silently using today", () => {
    expect(artifactTradeDate(
      { market_asof_date: "2024-05-17" }, artifact, parseCandidates([{ symbol: "600519.SH" }]),
    )).toBe("2024-05-17");
  });

  it("falls back through candidate price date and artifact creation date", () => {
    const candidates = parseCandidates([{ symbol: "600519.SH", price_trade_date: "2025-06-20" }]);
    expect(artifactTradeDate({}, artifact, candidates)).toBe("2025-06-20");
    expect(artifactTradeDate({}, artifact, [])).toBe("2026-07-31");
  });
});
