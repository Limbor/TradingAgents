import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { UsageStats } from "@/api/agent";
import { UsageSummary } from "./UsageSummary";

afterEach(cleanup);

const group = {
  role: "Fundamentals Analyst", provider: "qianwen", model: "qwen3.8-max",
  model_calls: 2, reported_calls: 2, missing_calls: 0, pending_calls: 0,
  input_tokens: 10000, output_tokens: 1000, total_tokens: 11000,
  cache_read_tokens: 8000, cache_creation_tokens: 0, reasoning_tokens: 100,
  cache_unknown_calls: 0, reasoning_unknown_calls: 0, unpriced_calls: 0, cost_cny: 0.072,
};
const usage: UsageStats = { ...group, groups: [group], currency: "CNY", price_date: "2026-10-03",
  price_source: "https://www.qianwenai.com/models/", incomplete: false, cost_complete: true, cache_hit_rate: 0.8 };

describe("usage summary", () => {
  it("shows the turn total and expands the node breakdown on demand", () => {
    render(<UsageSummary usage={usage} />);
    const details = screen.getByLabelText("本轮用量");
    expect(details).not.toHaveAttribute("open");
    expect(screen.getByLabelText("展开本轮用量明细")).toHaveTextContent("输入 10,000");
    expect(screen.getByLabelText("展开本轮用量明细")).toHaveTextContent("估价 ¥0.072");
    fireEvent.click(screen.getByLabelText("展开本轮用量明细"));
    expect(details).toHaveAttribute("open");
    expect(screen.getByText("基本面分析")).toBeVisible();
    expect(screen.getByText(/思考 100（已包含在输出中）/)).toBeVisible();
  });
  it("marks missing receipts and unknown prices instead of reporting free calls", () => {
    render(<UsageSummary usage={{ ...usage, cost_cny: null, cost_complete: false, incomplete: true,
      missing_calls: 1, groups: [{ ...group, missing_calls: 1, unpriced_calls: 2 }] }} conversation />);
    const summary = screen.getByLabelText("展开会话累计用量明细");
    expect(summary).toHaveTextContent("已知估价 暂无");
    expect(summary).toHaveTextContent("统计不完整");
    fireEvent.click(summary);
    expect(screen.getByText("1 次调用未返回用量")).toBeVisible();
    expect(screen.queryByText("¥0.000")).not.toBeInTheDocument();
  });
  it("hides empty tasks and distinguishes pending receipts", () => {
    const { rerender } = render(<UsageSummary usage={{ ...usage, model_calls: 0 }} />);
    expect(screen.queryByLabelText("本轮用量")).not.toBeInTheDocument();
    rerender(<UsageSummary usage={{ ...usage, reported_calls: 0, pending_calls: 1 }} />);
    expect(screen.getByLabelText("展开本轮用量明细")).toHaveTextContent("等待模型返回用量");
  });
});
