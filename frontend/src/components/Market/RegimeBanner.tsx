import { RefreshCw, TrendingDown, TrendingUp, Minus, AlertTriangle } from "lucide-react";
import type { MarketRegime } from "../../api/client";

const BAND_STYLES: Record<string, { label: string; badge: string; bar: string }> = {
  "Strong Bullish": { label: "强多", badge: "border-red-500/40 bg-red-500/15 text-red-300", bar: "bg-red-400" },
  "Mildly Bullish": { label: "偏多", badge: "border-orange-500/40 bg-orange-500/15 text-orange-300", bar: "bg-orange-400" },
  Sideways: { label: "震荡", badge: "border-stone-600 bg-stone-800 text-stone-300", bar: "bg-stone-400" },
  "Mildly Bearish": { label: "偏空", badge: "border-teal-500/40 bg-teal-500/15 text-teal-300", bar: "bg-teal-400" },
  "Strong Bearish": { label: "强空", badge: "border-emerald-500/40 bg-emerald-500/15 text-emerald-300", bar: "bg-emerald-400" },
};

const BAND_ORDER = ["Strong Bearish", "Mildly Bearish", "Sideways", "Mildly Bullish", "Strong Bullish"];

const CONFIDENCE_CN: Record<string, string> = { low: "低", medium: "中", high: "高" };
const DIMENSION_CN: Record<string, string> = { funds: "资金", sentiment: "情绪", policy: "政策", macro: "宏观" };

interface RegimeBannerProps {
  regime: MarketRegime | null;
  asofDate: string;
  generatedAt: string;
  isStale: boolean;
  refreshing: boolean;
  onRefresh: () => void;
}

export function RegimeBanner({ regime, asofDate, generatedAt, isStale, refreshing, onRefresh }: RegimeBannerProps) {
  const band = regime ? BAND_STYLES[regime.trend_band] ?? BAND_STYLES.Sideways : null;
  const bandIndex = regime ? BAND_ORDER.indexOf(regime.trend_band) : -1;

  return (
    <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
      <div className="flex flex-col justify-between gap-3 lg:flex-row lg:items-start">
        <div className="min-w-0 flex-1">
          {regime && band ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded border px-2.5 py-1 text-sm font-semibold ${band.badge}`}>
                  {band.label} · {regime.trend_band}
                </span>
                {/* 5-tier band strip */}
                <div className="flex items-center gap-0.5" title={regime.trend_band}>
                  {BAND_ORDER.map((name, i) => (
                    <span
                      key={name}
                      className={`h-1.5 w-6 rounded-sm ${i === bandIndex ? BAND_STYLES[name]?.bar ?? "bg-stone-400" : "bg-stone-800"}`}
                    />
                  ))}
                </div>
                <span
                  className="cursor-help text-xs text-stone-500"
                  title="AI 汇总时的自评字段：数据缺块或信号互相矛盾时为低，信号一致且数据完整时为高，介于两者为中"
                >
                  置信度 {CONFIDENCE_CN[regime.confidence] ?? regime.confidence}
                </span>
              </div>
              <p className="mt-2 text-sm text-stone-200">{regime.core_logic}</p>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                <span className="rounded border border-teal-500/30 bg-teal-500/10 px-2 py-0.5 text-teal-200">
                  建议仓位 {regime.suggested_position_range}
                </span>
                <span className="rounded border border-stone-700 bg-stone-950 px-2 py-0.5 text-stone-300">
                  占优风格 {regime.dominant_style}
                </span>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {regime.drivers.map((d, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1 rounded border border-stone-700 bg-stone-950 px-2 py-1 text-xs text-stone-300"
                  >
                    {d.direction === "positive" ? (
                      <TrendingUp className="h-3 w-3 text-red-400" />
                    ) : d.direction === "negative" ? (
                      <TrendingDown className="h-3 w-3 text-emerald-400" />
                    ) : (
                      <Minus className="h-3 w-3 text-stone-500" />
                    )}
                    <span className="text-stone-500">{DIMENSION_CN[d.dimension] ?? d.dimension}</span>
                    {d.statement}
                  </span>
                ))}
              </div>
              {regime.risk_alerts.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {regime.risk_alerts.map((alert, i) => (
                    <span key={i} className="inline-flex items-center gap-1 text-xs text-amber-300">
                      <AlertTriangle className="h-3 w-3" />
                      {alert}
                    </span>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div>
              <p className="text-sm font-semibold text-stone-200">大盘 AI 汇总暂不可用</p>
              <p className="mt-1 text-xs text-stone-500">下方市场数据仍为 {asofDate} 快照，可点击重新生成</p>
            </div>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <button
            onClick={onRefresh}
            disabled={refreshing}
            className="inline-flex items-center gap-2 rounded-lg border border-teal-500/30 bg-teal-500/10 px-3 py-2 text-sm font-medium text-teal-200 transition hover:border-teal-400/60 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "生成中" : "重新生成"}
          </button>
          <div className="text-right text-xs text-stone-500">
            <div>数据日 {asofDate}</div>
            {generatedAt && <div>生成于 {generatedAt.replace("T", " ")}</div>}
            {isStale && <div className="text-amber-300">数据非最新交易日，建议刷新</div>}
          </div>
        </div>
      </div>
    </section>
  );
}
