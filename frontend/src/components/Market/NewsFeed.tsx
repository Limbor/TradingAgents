import { useState } from "react";
import { MessageCircleQuestion, Newspaper } from "lucide-react";
import type { TaggedNewsItem } from "../../api/client";
import { filterNews, type HoldingRef, type PolarityFilter } from "./newsFilter";

const POLARITY_CN: Record<string, string> = { bullish: "利好", bearish: "利空", neutral: "中性" };
const SCOPE_CN: Record<string, string> = { market: "全市场", industry: "行业", stock: "个股" };

function polarityClass(polarity: TaggedNewsItem["polarity"]): string {
  if (polarity === "bullish") return "border-red-500/40 bg-red-500/10 text-red-300";
  if (polarity === "bearish") return "border-emerald-500/40 bg-emerald-500/10 text-emerald-300";
  return "border-stone-600 bg-stone-800 text-stone-400";
}

function NewsItemRow({ item, onAsk }: { item: TaggedNewsItem; onAsk: (item: TaggedNewsItem) => void }) {
  const isHigh = item.impact_level === "high";
  return (
    <div className={`rounded-lg border p-3 ${isHigh ? "border-amber-500/40 bg-amber-500/5" : "border-stone-800 bg-stone-950"}`}>
      <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
        {item.polarity && (
          <span className={`rounded border px-1.5 py-0.5 ${polarityClass(item.polarity)}`}>{POLARITY_CN[item.polarity]}</span>
        )}
        {item.impact_scope && (
          <span className="rounded border border-stone-700 bg-stone-900 px-1.5 py-0.5 text-stone-400">
            {SCOPE_CN[item.impact_scope]}
          </span>
        )}
        {isHigh && <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-amber-300">高影响</span>}
        {item.datetime && <span className="ml-auto text-stone-600">{item.datetime}</span>}
      </div>
      <p className="mt-1.5 text-xs font-medium leading-relaxed text-stone-200">{item.title}</p>
      {item.interpretation && <p className="mt-1 text-xs text-stone-500">{item.interpretation}</p>}
      <button
        onClick={() => onAsk(item)}
        className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-teal-300 transition hover:text-teal-200"
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
    <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
      <div className="mb-3 flex items-center gap-2">
        <Newspaper className="h-4 w-4 text-teal-300" />
        <h3 className="text-sm font-semibold text-stone-100">要闻解读</h3>
        <span className="text-xs text-stone-500">({filtered.length} 条)</span>
      </div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1 rounded-lg border border-stone-700 p-0.5 text-xs">
          {(["all", "bullish", "bearish"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setPolarity(tab)}
              className={`rounded px-2 py-1 transition ${polarity === tab ? "bg-stone-800 text-stone-100" : "text-stone-500 hover:text-stone-300"}`}
            >
              {tab === "all" ? "全部" : tab === "bullish" ? "利好" : "利空"}
            </button>
          ))}
        </div>
        <label className="flex cursor-pointer items-center gap-1.5 text-xs text-stone-400">
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
        <div className="py-8 text-center text-sm text-stone-500">没有匹配的新闻</div>
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
