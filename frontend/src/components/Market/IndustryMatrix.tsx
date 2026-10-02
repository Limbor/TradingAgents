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
  if (rating === "bullish") return "border-ui-danger/40 bg-ui-danger/10 text-ui-danger";
  if (rating === "bearish") return "border-ui-success/40 bg-ui-success/10 text-ui-success";
  if (rating === "neutral") return "border-ui-strong bg-ui-hover text-ui-body";
  return "border-ui-strong bg-ui-panel text-ui-faint";
}

function heatTileClass(row: IndustryStanceRow): string {
  if (row.rating === "bullish") return "border-ui-danger/40 bg-ui-danger/15 hover:bg-ui-danger/25";
  if (row.rating === "bearish") return "border-ui-success/40 bg-ui-success/15 hover:bg-ui-success/25";
  if (row.rating === "neutral") return "border-ui-strong bg-ui-hover hover:bg-ui-hover";
  return "border-ui-line bg-ui-panel/70 hover:bg-ui-hover";
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
    <section className="rounded-lg border border-ui-line bg-ui-panel p-4 xl:col-span-2">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <Grid3X3 className="h-4 w-4 text-ui-accent" />
            <h3 className="text-sm font-semibold text-ui-ink">行业与热点矩阵</h3>
            <span className="text-xs text-ui-faint">({sorted.length} 个)</span>
          </div>
          <p className="mt-1 text-xs text-ui-faint">
            {scope === "industry"
              ? "标准机构行业口径；优先中信行业，数据不可用时降级申万"
              : "热点主题独立展示并归并同义项，不再混入标准行业排名"}
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <div className="flex items-center gap-1 rounded-lg border border-ui-strong p-0.5">
            <button
              onClick={() => setScope("industry")}
              className={`rounded px-2 py-1 text-xs transition ${scope === "industry" ? "bg-ui-hover text-ui-ink" : "text-ui-faint hover:text-ui-body"}`}
            >
              机构行业 {industryCount}
            </button>
            <button
              onClick={() => setScope("theme")}
              className={`rounded px-2 py-1 text-xs transition ${scope === "theme" ? "bg-ui-hover text-ui-ink" : "text-ui-faint hover:text-ui-body"}`}
            >
              热点主题 {themeCount}
            </button>
          </div>
          <div className="flex items-center gap-1 rounded-lg border border-ui-strong p-0.5">
          <button
            onClick={() => setView("heatmap")}
            className={`flex items-center gap-1 rounded px-2 py-1 text-xs transition ${view === "heatmap" ? "bg-ui-hover text-ui-ink" : "text-ui-faint hover:text-ui-body"}`}
          >
            <Grid3X3 className="h-3 w-3" /> 热力图
          </button>
          <button
            onClick={() => setView("table")}
            className={`flex items-center gap-1 rounded px-2 py-1 text-xs transition ${view === "table" ? "bg-ui-hover text-ui-ink" : "text-ui-faint hover:text-ui-body"}`}
          >
            <Table2 className="h-3 w-3" /> 表格
          </button>
          </div>
        </div>
      </div>

      {sorted.length === 0 ? (
        <div className="py-10 text-center text-sm text-ui-faint">暂无板块数据</div>
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
                <span className="truncate text-xs font-medium text-ui-ink">{row.industry}</span>
                {row.board_type && (
                  <span className="shrink-0 rounded bg-ui-subtle/40 px-1 py-0.5 text-xs text-ui-muted">
                    {boardLabel(row)}
                  </span>
                )}
                {row.rating && (
                  <span className={`shrink-0 rounded border px-1 py-0.5 text-xs ${ratingBadgeClass(row.rating)}`}>
                    {ratingLabel(row)}
                  </span>
                )}
              </div>
              <div className="mt-1 flex items-baseline justify-between gap-2">
                <span className={`font-mono text-sm ${(row.pct_change ?? 0) >= 0 ? "text-ui-danger" : "text-ui-success"}`}>
                  {fmtPct(row.pct_change)}
                </span>
                {row.score !== undefined && (
                  <span className="font-mono text-xs text-ui-body">评分 {row.score > 0 ? "+" : ""}{row.score.toFixed(1)}</span>
                )}
              </div>
              {(row.phase || row.confidence) && (
                <p className="mt-1 truncate text-xs text-ui-muted">
                  {[row.phase, row.confidence ? CONFIDENCE_CN[row.confidence] : undefined].filter(Boolean).join(" · ")}
                </p>
              )}
              {row.reason && (
                <p className="mt-1 truncate text-xs leading-tight text-ui-faint">{row.reason}</p>
              )}
              <div className="absolute inset-x-0 bottom-0 hidden justify-end gap-1 rounded-b bg-ui-subtle/80 p-1 group-hover:flex">
                <span
                  role="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeepDive(row.industry);
                  }}
                  className="flex items-center gap-0.5 rounded px-1.5 py-0.5 text-xs text-ui-accent hover:bg-ui-hover"
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
                  className={`flex items-center gap-0.5 rounded px-1.5 py-0.5 text-xs ${canPickStocks(row) ? "text-ui-accent hover:bg-ui-hover" : "cursor-not-allowed text-ui-faint"}`}
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
              <tr className="border-b border-ui-line text-ui-faint">
                <th className="py-2 pr-2 font-medium">板块</th>
                <th className="cursor-pointer py-2 pr-2 font-medium hover:text-ui-body" onClick={() => setSortKey("pct_change")}>
                  涨跌%{sortKey === "pct_change" ? " ↓" : ""}
                </th>
                <th className="cursor-pointer py-2 pr-2 font-medium hover:text-ui-body" onClick={() => setSortKey("main_inflow")}>
                  主力资金{sortKey === "main_inflow" ? " ↓" : ""}
                </th>
                <th className="cursor-pointer py-2 pr-2 font-medium hover:text-ui-body" onClick={() => setSortKey("score")}>
                  多因子评分{sortKey === "score" ? " ↓" : ""}
                </th>
                <th className="cursor-pointer py-2 pr-2 font-medium hover:text-ui-body" onClick={() => setSortKey("rating")}>
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
                  className="cursor-pointer border-b border-ui-line/60 hover:bg-ui-subtle/50"
                >
                  <td className="py-2 pr-2 text-ui-ink">
                    {row.industry}
                    {row.board_type && (
                      <span className="ml-1 text-xs text-ui-faint">{boardLabel(row)}</span>
                    )}
                  </td>
                  <td className={`py-2 pr-2 font-mono ${(row.pct_change ?? 0) >= 0 ? "text-ui-danger" : "text-ui-success"}`}>
                    {fmtPct(row.pct_change)}
                  </td>
                  <td className={`py-2 pr-2 font-mono ${(row.main_inflow ?? 0) >= 0 ? "text-ui-danger" : "text-ui-success"}`}>
                    {fmtInflow(row.main_inflow)}
                  </td>
                  <td className="py-2 pr-2 font-mono text-ui-body">
                    {row.score !== undefined ? `${row.score > 0 ? "+" : ""}${row.score.toFixed(1)}` : "--"}
                    {row.confidence && <span className="ml-1 text-xs text-ui-faint">{CONFIDENCE_CN[row.confidence]}</span>}
                  </td>
                  <td className="py-2 pr-2">
                    {row.rating ? (
                      <span className={`rounded border px-1.5 py-0.5 ${ratingBadgeClass(row.rating)}`}>{ratingLabel(row)}</span>
                    ) : (
                      <span className="text-ui-faint">未评级</span>
                    )}
                  </td>
                  <td className="max-w-[220px] truncate py-2 pr-2 text-ui-muted" title={row.reason ?? undefined}>
                    {row.reason ?? "--"}
                  </td>
                  <td className="py-2 pr-2 text-ui-muted">
                    {row.key_stocks.length > 0 ? row.key_stocks.join(" ") : row.leader_stock ?? "--"}
                  </td>
                  <td className="py-2">
                    <div className="flex gap-1">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onDeepDive(row.industry);
                        }}
                        className="rounded px-1.5 py-0.5 text-ui-accent hover:bg-ui-hover"
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
                        className="rounded px-1.5 py-0.5 text-ui-accent hover:bg-ui-hover disabled:cursor-not-allowed disabled:text-ui-faint disabled:hover:bg-transparent"
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
            className="w-full max-w-lg rounded-xl border border-ui-strong bg-ui-panel p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <h4 className="text-lg font-semibold text-ui-ink">{detail.industry}</h4>
                {detail.board_type && (
                  <span className="rounded border border-ui-strong bg-ui-subtle px-2 py-0.5 text-xs text-ui-muted">
                    {boardLabel(detail)}
                  </span>
                )}
                {detail.rating ? (
                  <span className={`rounded border px-2 py-0.5 text-xs ${ratingBadgeClass(detail.rating)}`}>
                    {ratingLabel(detail)}
                  </span>
                ) : (
                  <span className="rounded border border-ui-strong bg-ui-subtle px-2 py-0.5 text-xs text-ui-faint">
                    未评级（仅涨跌幅居前/居后的板块会送 AI 评级）
                  </span>
                )}
              </div>
              <button
                onClick={() => setDetail(null)}
                className="rounded p-1 text-ui-faint transition hover:bg-ui-hover hover:text-ui-body"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <div className="rounded-lg border border-ui-line bg-ui-subtle p-3">
                <div className="text-xs uppercase tracking-wide text-ui-faint">今日涨跌</div>
                <div className={`mt-1 flex items-center gap-1 font-mono text-base ${(detail.pct_change ?? 0) >= 0 ? "text-ui-danger" : "text-ui-success"}`}>
                  {(detail.pct_change ?? 0) >= 0 ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
                  {fmtPct(detail.pct_change)}
                </div>
              </div>
              <div className="rounded-lg border border-ui-line bg-ui-subtle p-3">
                <div className="text-xs uppercase tracking-wide text-ui-faint">主力净流入</div>
                <div className={`mt-1 font-mono text-base ${(detail.main_inflow ?? 0) >= 0 ? "text-ui-danger" : "text-ui-success"}`}>
                  {fmtInflow(detail.main_inflow)}
                </div>
              </div>
              <div className="rounded-lg border border-ui-line bg-ui-subtle p-3">
                <div className="text-xs uppercase tracking-wide text-ui-faint">领涨股</div>
                <div className="mt-1 truncate text-base text-ui-body">{detail.leader_stock ?? "--"}</div>
              </div>
              <div className="rounded-lg border border-ui-line bg-ui-subtle p-3">
                <div className="text-xs uppercase tracking-wide text-ui-faint">多因子评分</div>
                <div className="mt-1 font-mono text-base text-ui-body">
                  {detail.score !== undefined ? `${detail.score > 0 ? "+" : ""}${detail.score.toFixed(1)}` : "--"}
                </div>
                <div className="text-xs text-ui-faint">
                  {[detail.phase, detail.confidence ? CONFIDENCE_CN[detail.confidence] : undefined].filter(Boolean).join(" · ")}
                </div>
              </div>
            </div>

            <div className="mt-3 rounded-lg border border-ui-line bg-ui-subtle p-3">
              <div className="text-xs uppercase tracking-wide text-ui-faint">AI 评级理由</div>
              <p className="mt-1 text-sm leading-relaxed text-ui-body">
                {detail.reason ?? "该板块未进入本次 AI 评级样本，暂无理由。可用下方“深挖驱动逻辑”发起分析。"}
              </p>
            </div>

            {detail.source_name && detail.source_name !== detail.industry && (
              <p className="mt-2 text-xs text-ui-faint">
                数据源原始名称：{detail.source_names?.length
                  ? detail.source_names.join(" / ")
                  : detail.source_name}
              </p>
            )}

            <p className={`mt-2 text-xs ${canPickStocks(detail) ? "text-ui-accent/80" : "text-ui-warning/80"}`}>
              {selectionDescription(detail)}
            </p>

            {detail.evidence && detail.evidence.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {detail.evidence.map((item) => (
                  <span key={item} className="rounded border border-ui-strong bg-ui-subtle px-2 py-0.5 text-xs text-ui-body">
                    {item}
                  </span>
                ))}
              </div>
            )}

            {detail.ai_comment && (
              <div className="mt-3 rounded-lg border border-ui-info/20 bg-ui-info/5 p-3">
                <div className="text-xs uppercase tracking-wide text-ui-info">AI 补充解读</div>
                <p className="mt-1 text-sm leading-relaxed text-ui-body">{detail.ai_comment}</p>
              </div>
            )}

            {detail.key_stocks.length > 0 && (
              <div className="mt-3">
                <div className="text-xs uppercase tracking-wide text-ui-faint">关联个股</div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {detail.key_stocks.map((stock) => (
                    <span key={stock} className="rounded border border-ui-strong bg-ui-subtle px-2 py-0.5 text-xs text-ui-body">
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
                className="inline-flex items-center gap-1.5 rounded-lg border border-ui-strong bg-ui-subtle px-3 py-1.5 text-xs text-ui-body transition hover:border-ui-strong"
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
                className="inline-flex items-center gap-1.5 rounded-lg border border-ui-accent/40 bg-ui-accent/10 px-3 py-1.5 text-xs font-medium text-ui-accent transition hover:bg-ui-accent/20 disabled:cursor-not-allowed disabled:border-ui-strong disabled:bg-ui-panel disabled:text-ui-faint"
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
