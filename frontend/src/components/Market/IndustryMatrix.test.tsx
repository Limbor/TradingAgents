import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { IndustryStanceRow } from "../../api/client";
import { IndustryMatrix } from "./IndustryMatrix";

afterEach(cleanup);

const scoredRow: IndustryStanceRow = {
  industry: "半导体",
  pct_change: 3.21,
  main_inflow: null,
  leader_stock: "芯片龙头",
  score: 68.5,
  rating: "bullish",
  rating_level: "bullish",
  confidence: "medium",
  data_coverage: 0.65,
  phase: "领涨待确认",
  factor_scores: { momentum: 95, breadth: 42, funds: null, activity: 66 },
  evidence: ["日涨跌+3.21%（强度第2/49）", "上涨家数占71%"],
  reason: "日涨跌+3.21%（强度第2/49）；上涨家数占71%；缺资金数据",
  ai_comment: "相对强度和行业广度共振，但资金维度尚未覆盖。",
  key_stocks: ["芯片龙头"],
};

describe("IndustryMatrix multi-factor stance", () => {
  it("shows score, confidence and differentiated evidence", () => {
    render(
      <IndustryMatrix
        stances={[scoredRow]}
        onDeepDive={vi.fn()}
        onPickStocks={vi.fn()}
      />,
    );

    expect(screen.getByText("评分 +68.5")).toBeInTheDocument();
    expect(screen.getByText(/领涨待确认 · 中置信/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /半导体/ }));
    expect(screen.getByRole("dialog", { name: "半导体 详情" })).toBeInTheDocument();
    expect(screen.getByText("日涨跌+3.21%（强度第2/49）")).toBeInTheDocument();
    expect(screen.getByText(/相对强度和行业广度共振/)).toBeInTheDocument();
  });

  it("labels investor-facing concept boards and preserves the source name", () => {
    const onPickStocks = vi.fn();
    const themeRow: IndustryStanceRow = {
      ...scoredRow,
      industry: "CXO",
      source_name: "CXO概念",
      source_names: ["CXO概念", "CXO服务概念"],
      board_type: "concept",
      selection_industries: ["医药生物"],
      selection_concept: "CXO概念",
      selection_mode: "proxy",
    };
    render(
      <IndustryMatrix
        stances={[themeRow]}
        onDeepDive={vi.fn()}
        onPickStocks={onPickStocks}
      />,
    );

    expect(screen.getByText("行业与热点矩阵")).toBeInTheDocument();
    expect(screen.getByText("产业主题")).toBeInTheDocument();
    expect(screen.getByText(/热点主题独立展示并归并同义项/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /CXO/ }));
    expect(screen.getByRole("dialog", { name: "CXO 详情" })).toBeInTheDocument();
    expect(screen.getByText("数据源原始名称：CXO概念 / CXO服务概念")).toBeInTheDocument();
    expect(
      screen.getByText("MCP 选股口径：CXO精确概念成分股；解析失败时回退 医药生物"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "该板块选股" }));
    expect(onPickStocks).toHaveBeenCalledWith(themeRow);
  });

  it("keeps standard CITICS industries separate and exposes the exact MCP code", () => {
    const onPickStocks = vi.fn();
    const citicsRow: IndustryStanceRow = {
      ...scoredRow,
      industry: "电子",
      board_type: "industry",
      taxonomy: "CITICS",
      industry_code: "CI005009.CI",
      industry_level: "L1",
      selection_industries: ["电子"],
      selection_industry_codes: ["CI005009.CI"],
      selection_taxonomy: "CITICS",
      selection_level: "L1",
      selection_mode: "exact",
    };
    render(
      <IndustryMatrix
        stances={[citicsRow]}
        onDeepDive={vi.fn()}
        onPickStocks={onPickStocks}
      />,
    );

    expect(screen.getByText("中信L1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /电子/ }));
    expect(
      screen.getByText("MCP 选股口径：CITICS L1 · 电子（CI005009.CI）"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "该板块选股" }));
    expect(onPickStocks).toHaveBeenCalledWith(citicsRow);
  });

  it("disables stock selection when a concept has no reliable MCP mapping", () => {
    const unsupported: IndustryStanceRow = {
      ...scoredRow,
      industry: "跨行业主题",
      board_type: "concept",
      selection_industries: [],
      selection_mode: "unsupported",
    };
    render(
      <IndustryMatrix
        stances={[unsupported]}
        onDeepDive={vi.fn()}
        onPickStocks={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /跨行业主题/ }));
    expect(screen.getByText("当前 MCP 暂无可靠行业映射，不能直接选股")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "该板块选股" })).toBeDisabled();
  });
});
