import { useState } from "react";
import { MessageCircleQuestion, Newspaper } from "lucide-react";
import type { TaggedNewsItem } from "../../api/client";
import { filterNews, type HoldingRef, type PolarityFilter } from "./newsFilter";

const POLARITY_CN: Record<string, string> = { bullish: "利好", bearish: "利空", neutral: "中性" };
const SCOPE_CN: Record<string, string> = { market: "全市场", industry: "行业", stock: "个股" };

function polarityClass(polarity: TaggedNewsItem["polarity"]): string {
  if (polarity === "bullish") return "border-ui-danger/40 bg-ui-danger/10 text-ui-danger";
  if (polarity === "bearish") return "border-ui-success/40 bg-ui-success/10 text-ui-success";
  return "border-ui-strong bg-ui-hover text-ui-muted";
}

function NewsItemRow({ item, onAsk }: { item: TaggedNewsItem; onAsk: (item: TaggedNewsItem) => void }) {
  const isHigh = item.impact_level === "high";
  return (
    <div className={`rounded-lg border p-3 ${isHigh ? "border-ui-warning/40 bg-ui-warning/5" : "border-ui-line bg-ui-subtle"}`}>
      <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
        {item.polarity && (
          <span className={`rounded border px-1.5 py-0.5 ${polarityClass(item.polarity)}`}>{POLARITY_CN[item.polarity]}</span>
        )}
        {item.impact_scope && (
          <span className="rounded border border-ui-strong bg-ui-panel px-1.5 py-0.5 text-ui-muted">
            {SCOPE_CN[item.impact_scope]}
          </span>
        )}
        {isHigh && <span className="rounded border border-ui-warning/40 bg-ui-warning/10 px-1.5 py-0.5 text-ui-warning">高影响</span>}
        {item.datetime && <span className="ml-auto text-ui-faint">{item.datetime}</span>}
      </div>
      <p className="mt-1.5 text-xs font-medium leading-relaxed text-ui-body">{item.title}</p>
      {item.interpretation && <p className="mt-1 text-xs text-ui-faint">{item.interpretation}</p>}
      <button
        onClick={() => onAsk(item)}
        className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-ui-accent transition hover:text-ui-accent"
      >
        <MessageCircleQuestion className="h-3 w-3" /> 问AI
      </button>
    </div>
  );
}

interface NewsFeedProps {
  news: TaggedNewsItem[];
  holdings: HoldingRef[];
  onAsk: (item: TaggedNewsItem) => void;
}

export function NewsFeed({ news, holdings, onAsk }: NewsFeedProps) {
  const [polarity, setPolarity] = useState<PolarityFilter>("all");
  const [holdingsOnly, setHoldingsOnly] = useState(false);

  const filtered = filterNews(news, polarity, holdingsOnly, holdings);
  const high = filtered.filter((item) => item.impact_level === "high");
  const rest = filtered.filter((item) => item.impact_level !== "high");

  return (
    <section className="rounded-lg border border-ui-line bg-ui-panel p-4">
      <div className="mb-3 flex items-center gap-2">
        <Newspaper className="h-4 w-4 text-ui-accent" />
        <h3 className="text-sm font-semibold text-ui-ink">要闻解读</h3>
        <span className="text-xs text-ui-faint">({filtered.length} 条)</span>
      </div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1 rounded-lg border border-ui-strong p-0.5 text-xs">
          {(["all", "bullish", "bearish"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setPolarity(tab)}
              className={`rounded px-2 py-1 transition ${polarity === tab ? "bg-ui-hover text-ui-ink" : "text-ui-faint hover:text-ui-body"}`}
            >
              {tab === "all" ? "全部" : tab === "bullish" ? "利好" : "利空"}
            </button>
          ))}
        </div>
        <label className="flex cursor-pointer items-center gap-1.5 text-xs text-ui-muted">
          <input
            type="checkbox"
            checked={holdingsOnly}
            onChange={(e) => setHoldingsOnly(e.target.checked)}
            className="h-3.5 w-3.5 accent-teal-500"
          />
          只看影响我的持仓
        </label>
      </div>
      {filtered.length === 0 ? (
        <div className="py-8 text-center text-sm text-ui-faint">没有匹配的新闻</div>
      ) : (
        <div className="max-h-[560px] space-y-2 overflow-y-auto pr-1">
          {high.map((item, i) => (
            <NewsItemRow key={`high-${i}`} item={item} onAsk={onAsk} />
          ))}
          {rest.map((item, i) => (
            <NewsItemRow key={`rest-${i}`} item={item} onAsk={onAsk} />
          ))}
        </div>
      )}
    </section>
  );
}
