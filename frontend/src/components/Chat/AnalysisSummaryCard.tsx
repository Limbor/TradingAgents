import { Link } from "react-router-dom";
import { ExternalLink, Plus, Target, TrendingDown, TrendingUp } from "lucide-react";

export interface AnalysisSummary {
  rating?: string;
  target_price?: string;
  confidence?: number;
  reasons?: string[];
  runId?: string;
  symbol?: string;
}

interface AnalysisSummaryCardProps {
  summary: AnalysisSummary;
  onAddHolding?: (symbol: string) => void;
  onAnalyzeMore?: (symbol: string) => void;
}

function ratingColor(rating?: string) {
  if (!rating) return "text-stone-400";
  const r = rating.toLowerCase();
  if (r.includes("buy") || r.includes("strong buy")) return "text-emerald-300";
  if (r.includes("sell")) return "text-red-300";
  if (r.includes("hold") || r.includes("neutral")) return "text-amber-300";
  return "text-stone-300";
}

function RatingIcon({ rating }: { rating?: string }) {
  if (!rating) return null;
  const r = rating.toLowerCase();
  if (r.includes("buy")) return <TrendingUp className="h-5 w-5 text-emerald-300" />;
  if (r.includes("sell")) return <TrendingDown className="h-5 w-5 text-red-300" />;
  return <Target className="h-5 w-5 text-amber-300" />;
}

export function AnalysisSummaryCard({ summary, onAddHolding, onAnalyzeMore }: AnalysisSummaryCardProps) {
  const { rating, target_price, confidence, reasons, runId, symbol } = summary;

  return (
    <div className="space-y-3 rounded-lg border border-stone-800 bg-stone-900 p-4">
      {/* Header: Rating + Target + Confidence */}
      <div className="flex items-center gap-4">
        <RatingIcon rating={rating} />
        <div className="flex-1">
          <div className="flex items-baseline gap-3">
            <span className={`text-lg font-bold ${ratingColor(rating)}`}>
              {rating ?? "N/A"}
            </span>
            {target_price && (
              <span className="text-sm text-stone-400">
                目标价: <span className="font-mono text-stone-200">{target_price}</span>
              </span>
            )}
            {confidence !== undefined && (
              <span className="text-sm text-stone-400">
                信心: <span className="font-mono text-stone-200">{confidence}%</span>
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Key Reasons */}
      {reasons && reasons.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-stone-500">关键论据</p>
          <ol className="space-y-1 pl-4">
            {reasons.map((reason, i) => (
              <li key={i} className="text-sm text-stone-300 list-decimal">
                {reason}
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Action Buttons */}
      <div className="flex flex-wrap items-center gap-2 border-t border-stone-800 pt-3">
        {runId && (
          <Link
            to={`/analysis/${runId}`}
            className="inline-flex items-center gap-1.5 rounded-lg border border-teal-500/30 bg-teal-500/10 px-3 py-1.5 text-xs font-medium text-teal-300 transition hover:bg-teal-500/20"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            查看完整报告
          </Link>
        )}
        {symbol && onAddHolding && (
          <button
            onClick={() => onAddHolding(symbol)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-stone-700 px-3 py-1.5 text-xs font-medium text-stone-300 transition hover:bg-stone-800"
          >
            <Plus className="h-3.5 w-3.5" />
            加入持仓
          </button>
        )}
        {symbol && onAnalyzeMore && (
          <button
            onClick={() => onAnalyzeMore(symbol)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-stone-700 px-3 py-1.5 text-xs font-medium text-stone-300 transition hover:bg-stone-800"
          >
            <TrendingUp className="h-3.5 w-3.5" />
            对比同行业
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Try to extract a structured analysis summary from report_chunk content.
 * Falls back to undefined if no structured data detected.
 */
export function parseAnalysisSummary(content: string, runId?: string): AnalysisSummary | null {
  // Try to detect rating patterns in markdown content
  const ratingMatch = content.match(/(?:rating|评级|recommendation|建议)[：:\s]*([A-Za-z\s]+?)(?:\n|$|[|,;])/i);
  const targetMatch = content.match(/(?:target|目标价)[：:\s]*([¥$]?[\d,.]+)/i);
  const confidenceMatch = content.match(/(?:confidence|信心|把握)[：:\s]*(\d+)/i);

  if (!ratingMatch && !targetMatch) return null;

  // Extract reasons (numbered list items)
  const reasonMatches = content.match(/^\d+[.、]\s*(.+)$/gm);
  const reasons = reasonMatches?.slice(0, 5).map((r) => r.replace(/^\d+[.、]\s*/, ""));

  return {
    rating: ratingMatch?.[1]?.trim(),
    target_price: targetMatch?.[1]?.trim(),
    confidence: confidenceMatch ? Number(confidenceMatch[1]) : undefined,
    reasons,
    runId,
  };
}
