import { useEffect, useMemo, useState } from "react";
import { Grid3X3, Table2, Search, Sparkles, X, TrendingUp, TrendingDown } from "lucide-react";
import type { IndustryStanceRow } from "../../api/client";

const RATING_CN: Record<string, string> = { bullish: "看多", neutral: "中性", bearish: "看空" };
const RATING_LEVEL_CN: Record<string, string> = {
  strong_bullish: "强多",
  bullish: "偏多",
  neutral: "中性",
  bearish: "偏空",
  strong_bearish: "强空",
};
const CONFIDENCE_CN: Record<string, string> = { low: "低置信", medium: "中置信", high: "高置信" };
const BOARD_TYPE_CN: Record<string, string> = {
  concept: "产业主题",
  industry: "行业",
  industry_group: "产业归并",
};

function boardLabel(row: IndustryStanceRow): string {
  if (row.board_type === "concept") return "产业主题";
  if (row.taxonomy === "CITICS") return `中信${row.industry_level ?? "L1"}`;
  if (row.taxonomy === "SW2021") return `申万${row.industry_level ?? "L1"}`;
  return BOARD_TYPE_CN[row.board_type ?? ""] ?? "行业";
}

function ratingLabel(row: IndustryStanceRow): string {
  return RATING_LEVEL_CN[row.rating_level ?? ""] ?? RATING_CN[row.rating ?? ""] ?? "未评级";
}

function ratingBadgeClass(rating: IndustryStanceRow["rating"]): string {
  if (rating === "bullish") return "border-red-500/40 bg-red-500/10 text-red-300";
  if (rating === "bearish") return "border-emerald-500/40 bg-emerald-500/10 text-emerald-300";
  if (rating === "neutral") return "border-stone-600 bg-stone-800 text-stone-300";
  return "border-stone-700 bg-stone-900 text-stone-500";
}

function heatTileClass(row: IndustryStanceRow): string {
  if (row.rating === "bullish") return "border-red-500/40 bg-red-500/15 hover:bg-red-500/25";
  if (row.rating === "bearish") return "border-emerald-500/40 bg-emerald-500/15 hover:bg-emerald-500/25";
  if (row.rating === "neutral") return "border-stone-600 bg-stone-800 hover:bg-stone-700";
  return "border-stone-800 bg-stone-900/70 hover:bg-stone-800";
}

function fmtPct(value: number | null): string {
  if (value === null || value === undefined) return "--";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function fmtInflow(value: number | null): string {
  if (value === null || value === undefined) return "--";
  const yi = value / 1e8;
  return `${yi >= 0 ? "+" : ""}${yi.toFixed(1)}亿`;
}

type SortKey = "rating" | "pct_change" | "main_inflow" | "score";

const RATING_ORDER: Record<string, number> = { bullish: 0, neutral: 1, bearish: 2 };

interface IndustryMatrixProps {
  stances: IndustryStanceRow[];
  onDeepDive: (industry: string) => void;
  onPickStocks: (row: IndustryStanceRow) => void;
}

function canPickStocks(row: IndustryStanceRow): boolean {
  if (row.selection_concept) return true;
  if (row.selection_mode === "unsupported") return false;
  if (row.selection_industries) return row.selection_industries.length > 0;
  // Backward compatibility for older artifacts: broad industries are already
  // MCP-compatible, but an old concept artifact has no trustworthy mapping.
  return row.board_type !== "concept";
}

function selectionDescription(row: IndustryStanceRow): string {
  if (!canPickStocks(row)) return "当前 MCP 暂无可靠行业映射，不能直接选股";
  const targets = row.selection_industries?.length ? row.selection_industries : [row.industry];
  if (row.selection_concept) {
    const fallback = row.selection_industries?.length
      ? `；解析失败时回退 ${targets.join(" / ")}`
      : "；解析失败时停止，不会放宽到全市场";
    return `MCP 选股口径：${row.industry}精确概念成分股${fallback}`;
  }
  if (row.selection_mode === "proxy") {
    return `MCP 选股口径：${row.industry} → ${targets.join(" / ")}（代理筛选）`;
  }
  if (row.selection_industry_codes?.length && row.selection_taxonomy) {
    return `MCP 选股口径：${row.selection_taxonomy} ${row.selection_level ?? "L1"} · ${row.industry}（${row.selection_industry_codes.join(" / ")}）`;
  }
  return `MCP 选股口径：${targets.join(" / ")}`;
}

export function IndustryMatrix({ stances, onDeepDive, onPickStocks }: IndustryMatrixProps) {
  const [view, setView] = useState<"heatmap" | "table">("heatmap");
  const [scope, setScope] = useState<"industry" | "theme">("industry");
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [detail, setDetail] = useState<IndustryStanceRow | null>(null);

  // Close the detail card with Escape.
  useEffect(() => {
    if (!detail) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDetail(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detail]);

  const sorted = useMemo(() => {
    const rows = stances.filter((row) =>
      scope === "theme" ? row.board_type === "concept" : row.board_type !== "concept",
    );
    if (sortKey === "rating") {
      rows.sort((a, b) => (RATING_ORDER[a.rating ?? ""] ?? 3) - (RATING_ORDER[b.rating ?? ""] ?? 3));
    } else {
      rows.sort((a, b) => (b[sortKey] ?? -Infinity) - (a[sortKey] ?? -Infinity));
    }
    return rows;
  }, [stances, sortKey, scope]);

  const industryCount = stances.filter((row) => row.board_type !== "concept").length;
  const themeCount = stances.filter((row) => row.board_type === "concept").length;

  useEffect(() => {
    if (scope === "industry" && industryCount === 0 && themeCount > 0) setScope("theme");
  }, [industryCount, scope, themeCount]);

  return (
    <section className="rounded-lg border border-stone-800 bg-stone-900 p-4 xl:col-span-2">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <Grid3X3 className="h-4 w-4 text-teal-300" />
            <h3 className="text-sm font-semibold text-stone-100">行业与热点矩阵</h3>
            <span className="text-xs text-stone-500">({sorted.length} 个)</span>
          </div>
          <p className="mt-1 text-[10px] text-stone-500">
            {scope === "industry"
              ? "标准机构行业口径；优先中信行业，数据不可用时降级申万"
              : "热点主题独立展示并归并同义项，不再混入标准行业排名"}
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <div className="flex items-center gap-1 rounded-lg border border-stone-700 p-0.5">
            <button
              onClick={() => setScope("industry")}
              className={`rounded px-2 py-1 text-xs transition ${scope === "industry" ? "bg-stone-800 text-stone-100" : "text-stone-500 hover:text-stone-300"}`}
            >
              机构行业 {industryCount}
            </button>
            <button
              onClick={() => setScope("theme")}
              className={`rounded px-2 py-1 text-xs transition ${scope === "theme" ? "bg-stone-800 text-stone-100" : "text-stone-500 hover:text-stone-300"}`}
            >
              热点主题 {themeCount}
            </button>
          </div>
          <div className="flex items-center gap-1 rounded-lg border border-stone-700 p-0.5">
          <button
            onClick={() => setView("heatmap")}
            className={`flex items-center gap-1 rounded px-2 py-1 text-xs transition ${view === "heatmap" ? "bg-stone-800 text-stone-100" : "text-stone-500 hover:text-stone-300"}`}
          >
            <Grid3X3 className="h-3 w-3" /> 热力图
          </button>
          <button
            onClick={() => setView("table")}
            className={`flex items-center gap-1 rounded px-2 py-1 text-xs transition ${view === "table" ? "bg-stone-800 text-stone-100" : "text-stone-500 hover:text-stone-300"}`}
          >
            <Table2 className="h-3 w-3" /> 表格
          </button>
          </div>
        </div>
      </div>

      {sorted.length === 0 ? (
        <div className="py-10 text-center text-sm text-stone-500">暂无板块数据</div>
      ) : view === "heatmap" ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {sorted.map((row) => (
            <button
              key={`${row.board_type ?? "industry"}:${row.industry_code ?? row.industry}`}
              type="button"
              onClick={() => setDetail(row)}
              className={`group relative rounded border p-2.5 text-left transition ${heatTileClass(row)}`}
              title={row.reason ?? "点击查看详情"}
            >
              <div className="flex items-center justify-between gap-1">
                <span className="truncate text-xs font-medium text-stone-100">{row.industry}</span>
                {row.board_type && (
                  <span className="shrink-0 rounded bg-stone-950/40 px-1 py-0.5 text-[9px] text-stone-400">
                    {boardLabel(row)}
                  </span>
                )}
                {row.rating && (
                  <span className={`shrink-0 rounded border px-1 py-0.5 text-[10px] ${ratingBadgeClass(row.rating)}`}>
                    {ratingLabel(row)}
                  </span>
                )}
              </div>
              <div className="mt-1 flex items-baseline justify-between gap-2">
                <span className={`font-mono text-sm ${(row.pct_change ?? 0) >= 0 ? "text-red-300" : "text-emerald-300"}`}>
                  {fmtPct(row.pct_change)}
                </span>
                {row.score !== undefined && (
                  <span className="font-mono text-[11px] text-stone-300">评分 {row.score > 0 ? "+" : ""}{row.score.toFixed(1)}</span>
                )}
              </div>
              {(row.phase || row.confidence) && (
                <p className="mt-1 truncate text-[10px] text-stone-400">
                  {[row.phase, row.confidence ? CONFIDENCE_CN[row.confidence] : undefined].filter(Boolean).join(" · ")}
                </p>
              )}
              {row.reason && (
                <p className="mt-1 truncate text-[10px] leading-tight text-stone-500">{row.reason}</p>
              )}
              <div className="absolute inset-x-0 bottom-0 hidden justify-end gap-1 rounded-b bg-stone-950/80 p-1 group-hover:flex">
                <span
                  role="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeepDive(row.industry);
                  }}
                  className="flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] text-teal-300 hover:bg-stone-800"
                >
                  <Search className="h-2.5 w-2.5" /> 深挖
                </span>
                <span
                  role="button"
                  aria-disabled={!canPickStocks(row)}
                  title={selectionDescription(row)}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (canPickStocks(row)) onPickStocks(row);
                  }}
                  className={`flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] ${canPickStocks(row) ? "text-teal-300 hover:bg-stone-800" : "cursor-not-allowed text-stone-600"}`}
                >
                  <Sparkles className="h-2.5 w-2.5" /> 选股
                </span>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-stone-800 text-stone-500">
                <th className="py-2 pr-2 font-medium">板块</th>
                <th className="cursor-pointer py-2 pr-2 font-medium hover:text-stone-300" onClick={() => setSortKey("pct_change")}>
                  涨跌%{sortKey === "pct_change" ? " ↓" : ""}
                </th>
                <th className="cursor-pointer py-2 pr-2 font-medium hover:text-stone-300" onClick={() => setSortKey("main_inflow")}>
                  主力资金{sortKey === "main_inflow" ? " ↓" : ""}
                </th>
                <th className="cursor-pointer py-2 pr-2 font-medium hover:text-stone-300" onClick={() => setSortKey("score")}>
                  多因子评分{sortKey === "score" ? " ↓" : ""}
                </th>
                <th className="cursor-pointer py-2 pr-2 font-medium hover:text-stone-300" onClick={() => setSortKey("rating")}>
                  评级{sortKey === "rating" ? " ↓" : ""}
                </th>
                <th className="py-2 pr-2 font-medium">理由</th>
                <th className="py-2 pr-2 font-medium">龙头股</th>
                <th className="py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => (
                <tr
                  key={`${row.board_type ?? "industry"}:${row.industry_code ?? row.industry}`}
                  onClick={() => setDetail(row)}
                  className="cursor-pointer border-b border-stone-800/60 hover:bg-stone-950/50"
                >
                  <td className="py-2 pr-2 text-stone-100">
                    {row.industry}
                    {row.board_type && (
                      <span className="ml-1 text-[10px] text-stone-500">{boardLabel(row)}</span>
                    )}
                  </td>
                  <td className={`py-2 pr-2 font-mono ${(row.pct_change ?? 0) >= 0 ? "text-red-300" : "text-emerald-300"}`}>
                    {fmtPct(row.pct_change)}
                  </td>
                  <td className={`py-2 pr-2 font-mono ${(row.main_inflow ?? 0) >= 0 ? "text-red-300" : "text-emerald-300"}`}>
                    {fmtInflow(row.main_inflow)}
                  </td>
                  <td className="py-2 pr-2 font-mono text-stone-200">
                    {row.score !== undefined ? `${row.score > 0 ? "+" : ""}${row.score.toFixed(1)}` : "--"}
                    {row.confidence && <span className="ml-1 text-[10px] text-stone-500">{CONFIDENCE_CN[row.confidence]}</span>}
                  </td>
                  <td className="py-2 pr-2">
                    {row.rating ? (
                      <span className={`rounded border px-1.5 py-0.5 ${ratingBadgeClass(row.rating)}`}>{ratingLabel(row)}</span>
                    ) : (
                      <span className="text-stone-600">未评级</span>
                    )}
                  </td>
                  <td className="max-w-[220px] truncate py-2 pr-2 text-stone-400" title={row.reason ?? undefined}>
                    {row.reason ?? "--"}
                  </td>
                  <td className="py-2 pr-2 text-stone-400">
                    {row.key_stocks.length > 0 ? row.key_stocks.join(" ") : row.leader_stock ?? "--"}
                  </td>
                  <td className="py-2">
                    <div className="flex gap-1">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onDeepDive(row.industry);
                        }}
                        className="rounded px-1.5 py-0.5 text-teal-300 hover:bg-stone-800"
                      >
                        深挖
                      </button>
                      <button
                        disabled={!canPickStocks(row)}
                        title={selectionDescription(row)}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (canPickStocks(row)) onPickStocks(row);
                        }}
                        className="rounded px-1.5 py-0.5 text-teal-300 hover:bg-stone-800 disabled:cursor-not-allowed disabled:text-stone-600 disabled:hover:bg-transparent"
                      >
                        选股
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Enlarged detail card: opened by clicking a heatmap tile or table row */}
      {detail && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onClick={() => setDetail(null)}
        >
          <div
            role="dialog"
            aria-label={`${detail.industry} 详情`}
            className="w-full max-w-lg rounded-xl border border-stone-700 bg-stone-900 p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <h4 className="text-lg font-semibold text-stone-50">{detail.industry}</h4>
                {detail.board_type && (
                  <span className="rounded border border-stone-700 bg-stone-950 px-2 py-0.5 text-xs text-stone-400">
                    {boardLabel(detail)}
                  </span>
                )}
                {detail.rating ? (
                  <span className={`rounded border px-2 py-0.5 text-xs ${ratingBadgeClass(detail.rating)}`}>
                    {ratingLabel(detail)}
                  </span>
                ) : (
                  <span className="rounded border border-stone-700 bg-stone-950 px-2 py-0.5 text-xs text-stone-500">
                    未评级（仅涨跌幅居前/居后的板块会送 AI 评级）
                  </span>
                )}
              </div>
              <button
                onClick={() => setDetail(null)}
                className="rounded p-1 text-stone-500 transition hover:bg-stone-800 hover:text-stone-200"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <div className="rounded-lg border border-stone-800 bg-stone-950 p-3">
                <div className="text-[10px] uppercase tracking-wide text-stone-500">今日涨跌</div>
                <div className={`mt-1 flex items-center gap-1 font-mono text-base ${(detail.pct_change ?? 0) >= 0 ? "text-red-300" : "text-emerald-300"}`}>
                  {(detail.pct_change ?? 0) >= 0 ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
                  {fmtPct(detail.pct_change)}
                </div>
              </div>
              <div className="rounded-lg border border-stone-800 bg-stone-950 p-3">
                <div className="text-[10px] uppercase tracking-wide text-stone-500">主力净流入</div>
                <div className={`mt-1 font-mono text-base ${(detail.main_inflow ?? 0) >= 0 ? "text-red-300" : "text-emerald-300"}`}>
                  {fmtInflow(detail.main_inflow)}
                </div>
              </div>
              <div className="rounded-lg border border-stone-800 bg-stone-950 p-3">
                <div className="text-[10px] uppercase tracking-wide text-stone-500">领涨股</div>
                <div className="mt-1 truncate text-base text-stone-200">{detail.leader_stock ?? "--"}</div>
              </div>
              <div className="rounded-lg border border-stone-800 bg-stone-950 p-3">
                <div className="text-[10px] uppercase tracking-wide text-stone-500">多因子评分</div>
                <div className="mt-1 font-mono text-base text-stone-200">
                  {detail.score !== undefined ? `${detail.score > 0 ? "+" : ""}${detail.score.toFixed(1)}` : "--"}
                </div>
                <div className="text-[10px] text-stone-500">
                  {[detail.phase, detail.confidence ? CONFIDENCE_CN[detail.confidence] : undefined].filter(Boolean).join(" · ")}
                </div>
              </div>
            </div>

            <div className="mt-3 rounded-lg border border-stone-800 bg-stone-950 p-3">
              <div className="text-[10px] uppercase tracking-wide text-stone-500">AI 评级理由</div>
              <p className="mt-1 text-sm leading-relaxed text-stone-200">
                {detail.reason ?? "该板块未进入本次 AI 评级样本，暂无理由。可用下方“深挖驱动逻辑”发起分析。"}
              </p>
            </div>

            {detail.source_name && detail.source_name !== detail.industry && (
              <p className="mt-2 text-[11px] text-stone-500">
                数据源原始名称：{detail.source_names?.length
                  ? detail.source_names.join(" / ")
                  : detail.source_name}
              </p>
            )}

            <p className={`mt-2 text-[11px] ${canPickStocks(detail) ? "text-teal-300/80" : "text-amber-300/80"}`}>
              {selectionDescription(detail)}
            </p>

            {detail.evidence && detail.evidence.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {detail.evidence.map((item) => (
                  <span key={item} className="rounded border border-stone-700 bg-stone-950 px-2 py-0.5 text-[11px] text-stone-300">
                    {item}
                  </span>
                ))}
              </div>
            )}

            {detail.ai_comment && (
              <div className="mt-3 rounded-lg border border-indigo-500/20 bg-indigo-500/5 p-3">
                <div className="text-[10px] uppercase tracking-wide text-indigo-300/70">AI 补充解读</div>
                <p className="mt-1 text-sm leading-relaxed text-stone-300">{detail.ai_comment}</p>
              </div>
            )}

            {detail.key_stocks.length > 0 && (
              <div className="mt-3">
                <div className="text-[10px] uppercase tracking-wide text-stone-500">关联个股</div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {detail.key_stocks.map((stock) => (
                    <span key={stock} className="rounded border border-stone-700 bg-stone-950 px-2 py-0.5 text-xs text-stone-300">
                      {stock}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => {
                  setDetail(null);
                  onDeepDive(detail.industry);
                }}
                className="inline-flex items-center gap-1.5 rounded-lg border border-stone-700 bg-stone-950 px-3 py-1.5 text-xs text-stone-200 transition hover:border-stone-500"
              >
                <Search className="h-3.5 w-3.5" /> 深挖驱动逻辑
              </button>
              <button
                disabled={!canPickStocks(detail)}
                title={selectionDescription(detail)}
                onClick={() => {
                  setDetail(null);
                  if (canPickStocks(detail)) onPickStocks(detail);
                }}
                className="inline-flex items-center gap-1.5 rounded-lg border border-teal-500/40 bg-teal-500/10 px-3 py-1.5 text-xs font-medium text-teal-200 transition hover:bg-teal-500/20 disabled:cursor-not-allowed disabled:border-stone-700 disabled:bg-stone-900 disabled:text-stone-600"
              >
                <Sparkles className="h-3.5 w-3.5" /> 该板块选股
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
