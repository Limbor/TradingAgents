import { AlertCircle, ArrowRight, Plus, TrendingUp } from "lucide-react";

export interface CandidateRow {
  rank: number;
  symbol: string;
  name?: string;
  industry?: string;
  board?: string;
  decision?: string;
  quantDecision?: string;
  llmView?: string;
  catalystStrength?: string;
  riskAssessment?: string;
  score?: number;
  quantScore?: number;
  gate_reasons?: string[];
  dataCoverageWarnings?: string[];
}

interface CandidateTableProps {
  candidates: CandidateRow[];
  warnings?: string[];
  onAnalyze?: (symbol: string) => void;
  onAddWatchlist?: (symbols: string[]) => void;
}

export function CandidateTable({ candidates, warnings, onAnalyze, onAddWatchlist }: CandidateTableProps) {
  if (candidates.length === 0) {
    return (
      <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-4 text-sm text-amber-200 space-y-2">
        <div className="flex items-center gap-2">
          <AlertCircle className="h-4 w-4 shrink-0 text-amber-300" />
          <span className="font-medium">没有找到候选标的</span>
        </div>
        {warnings && warnings.length > 0 && (
          <ul className="ml-6 list-disc space-y-1 text-xs text-amber-300/80">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        )}
        <p className="text-xs text-stone-500 mt-1">
          可能原因：非交易日、MCP 数据未更新、板块过滤过严或指数成分股缺失
        </p>
      </div>
    );
  }

  const decisionColor = (decision?: string) => {
    if (!decision) return "text-stone-400";
    const d = decision.toLowerCase();
    if (d.includes("buy") || d.includes("strong")) return "text-emerald-300";
    if (d.includes("sell") || d.includes("avoid")) return "text-red-300";
    if (d.includes("hold") || d.includes("neutral")) return "text-amber-300";
    return "text-stone-300";
  };

  return (
    <div className="space-y-3">
      <div className="overflow-hidden rounded-lg border border-stone-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-stone-900 text-xs uppercase text-stone-500">
            <tr>
              <th className="px-3 py-2 w-8">#</th>
              <th className="px-3 py-2">标的</th>
              <th className="px-3 py-2 text-center">最终</th>
              <th className="px-3 py-2 text-center">量化</th>
              <th className="px-3 py-2 text-center">LLM</th>
              <th className="px-3 py-2 text-center">风险</th>
              <th className="px-3 py-2 text-right">分数</th>
              <th className="px-3 py-2 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((row) => (
              <tr key={row.symbol} className="border-t border-stone-800 hover:bg-stone-900/50">
                <td className="px-3 py-2 text-stone-500">{row.rank}</td>
                <td className="px-3 py-2">
                  <span className="font-mono text-stone-100">{row.symbol}</span>
                  {row.name && <span className="ml-2 text-stone-400">{row.name}</span>}
                  <div className="mt-1 flex flex-wrap gap-1 text-xs text-stone-500">
                    {row.board && <span>{row.board}</span>}
                    {row.industry && <span>{row.industry}</span>}
                    {row.gate_reasons?.slice(0, 2).map((reason) => (
                      <span key={reason} className="rounded bg-stone-800 px-1.5 py-0.5">{reason}</span>
                    ))}
                    {row.dataCoverageWarnings?.map((warning) => (
                      <span key={warning} className="rounded bg-amber-500/10 px-1.5 py-0.5 text-amber-300">{warning}</span>
                    ))}
                  </div>
                </td>
                <td className={`px-3 py-2 text-center font-medium ${decisionColor(row.decision)}`}>
                  {row.decision ?? "-"}
                </td>
                <td className="px-3 py-2 text-center text-stone-300">
                  {row.quantDecision ?? "-"}
                </td>
                <td className="px-3 py-2 text-center text-stone-300">
                  <div>{row.llmView ?? "-"}</div>
                  {row.catalystStrength && (
                    <div className="text-xs text-stone-500">{row.catalystStrength}</div>
                  )}
                </td>
                <td className="px-3 py-2 text-center text-stone-300">
                  {row.riskAssessment ?? "-"}
                </td>
                <td className="px-3 py-2 text-right font-mono text-stone-200">
                  {row.score !== undefined ? row.score : "-"}
                  {row.quantScore !== undefined && (
                    <div className="text-xs text-stone-500">Q {row.quantScore}</div>
                  )}
                </td>
                <td className="px-3 py-2 text-right">
                  {onAnalyze && (
                    <button
                      onClick={() => onAnalyze(row.symbol)}
                      className="inline-flex items-center gap-1 rounded border border-teal-500/30 px-2 py-1 text-xs text-teal-300 transition hover:bg-teal-500/10"
                    >
                      <TrendingUp className="h-3 w-3" />
                      分析
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {onAddWatchlist && candidates.length > 0 && (
        <div className="flex gap-2">
          <button
            onClick={() => onAnalyze?.(candidates[0]!.symbol)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-teal-500/30 bg-teal-500/10 px-3 py-1.5 text-xs font-medium text-teal-300 transition hover:bg-teal-500/20"
          >
            <ArrowRight className="h-3.5 w-3.5" />
            深度分析第1名
          </button>
          <button
            onClick={() => onAddWatchlist(candidates.map((c) => c.symbol))}
            className="inline-flex items-center gap-1.5 rounded-lg border border-stone-700 px-3 py-1.5 text-xs font-medium text-stone-300 transition hover:bg-stone-800"
          >
            <Plus className="h-3.5 w-3.5" />
            全部加入关注
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * Parse raw candidate data from WebSocket into typed rows.
 */
export function parseCandidates(raw: unknown): CandidateRow[] {
  if (!Array.isArray(raw) || raw.length === 0) return [];
  return raw.slice(0, 10).map((item, index) => {
    const row = item as Record<string, unknown>;
    const symbol = String(row.symbol ?? row.ticker ?? row.ts_code ?? `#${index + 1}`);
    const name = row.name ? String(row.name) : undefined;
    const industry = row.industry ? String(row.industry) : undefined;
    const board = row.board ? String(row.board) : undefined;
    const decision = row.final_decision ?? row.signal ?? row.quant_decision;
    const score = row.display_score ?? row.final_score ?? row.score ?? row.total_score;
    const quantScore = row.quant_score;
    const dataCoverage = row.data_coverage as Record<string, unknown> | undefined;
    const dataCoverageWarnings = dataCoverage
      ? Object.entries(dataCoverage)
          .filter(([, value]) => String(value).toLowerCase() === "missing")
          .map(([key]) => `${key} missing`)
      : undefined;
    return {
      rank: index + 1,
      symbol,
      name,
      industry,
      board,
      decision: decision ? String(decision) : undefined,
      quantDecision: row.quant_decision ? String(row.quant_decision) : undefined,
      llmView: row.llm_view ? String(row.llm_view) : undefined,
      catalystStrength: row.catalyst_strength ? String(row.catalyst_strength) : undefined,
      riskAssessment: row.risk_assessment ? String(row.risk_assessment) : undefined,
      score: score !== undefined ? Number(score) : undefined,
      quantScore: quantScore !== undefined ? Number(quantScore) : undefined,
      gate_reasons: Array.isArray(row.gate_reasons) ? row.gate_reasons.map(String) : undefined,
      dataCoverageWarnings,
    };
  });
}
