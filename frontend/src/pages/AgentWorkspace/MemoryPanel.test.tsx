import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { MemoryDetails, MemoryProgress, taskMemory } from "./MemoryPanel";
import type { AgentTask } from "@/api/agent";

afterEach(cleanup);
const task = (status: string, event: string, payload: Record<string, unknown>): AgentTask => ({
  id: "t", conversation_id: "c", goal: "板块分析", status, result: {}, error: null,
  created_at: "2026-01-01", updated_at: "2026-01-01", evidence: [],
  events: [{ task_id: "t", seq: 1, event_type: event, payload, created_at: "2026-01-01" }],
});

it("shows retrieval and comparing progress without claiming reference", () => {
  const { rerender } = render(<MemoryProgress task={task("running", "memory_retrieving", {})} />);
  expect(screen.getByRole("status")).toHaveTextContent("正在检索");
  rerender(<MemoryProgress task={task("reviewing", "memory_comparing", { lesson_ids: ["a", "b"] })} />);
  expect(screen.getByRole("status")).toHaveTextContent("核对 2 条历史经验");
  expect(screen.getByRole("status")).not.toHaveTextContent("模型报告参考");
});

it("a receipt without usage keeps the reporting gap visible", () => {
  render(<MemoryProgress task={task("completed", "memory_injected", { lesson_ids: ["a"], snapshots: [{ id: "a" }] })} />);
  expect(screen.getByRole("status")).toHaveTextContent("模型未报告具体参考情况");
});

it("shows no-match and failed retrieval honestly", () => {
  const { rerender } = render(<MemoryProgress task={task("running", "memory_retrieved", { lesson_ids: [] })} />);
  expect(screen.getByRole("status")).toHaveTextContent("未找到适用的已批准经验");
  rerender(<MemoryProgress task={task("completed", "memory_unavailable", { message: "历史经验检索暂不可用" })} />);
  expect(screen.getByRole("status")).toHaveTextContent("历史经验检索暂不可用");
});

it("renders model-reported reasons and dated numerical examples", () => {
  const trace = { injected_ids: ["a", "b"], snapshots: [{ id: "a", scope: "industry", target: "医药生物",
    finding: "检查量价持续性", evidence_count: 5, examples: [{ id: "case", symbol: "600000.SH",
      signal_date: "2026-01-01", outcome: "incorrect", actual_return: -0.032, excess_return: -0.012 }] }],
    usage: [{ lesson_id: "a", status: "not_applicable", reason: "当前周期不同" },
      { lesson_id: "b", status: "referenced", reason: "同行业" }] };
  render(<MemoryDetails trace={trace} />);
  expect(screen.getByText("模型报告参考 1 条，1 条不适用")).toBeInTheDocument();
  expect(screen.getByText("模型报告不适用：当前周期不同")).toBeInTheDocument();
  expect(screen.getByText(/收益 -3.20% · 超额 -1.20%/)).toBeInTheDocument();
  expect(taskMemory(task("completed", "task_completed", {}))).toBeNull();
});
