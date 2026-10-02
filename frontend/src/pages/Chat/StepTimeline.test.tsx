import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { StepTimeline } from "@/pages/Chat/TaskCard";
import type { ChatTaskStep } from "@/stores/useChatStore";

function makeSteps(labels: string[]): ChatTaskStep[] {
  return labels.map((label, index) => ({
    id: `s${index}`,
    label,
    status: "completed",
    timestamp: new Date(Date.UTC(2026, 0, 1, 0, 0, index * 30)).toISOString(),
  }));
}

afterEach(() => {
  // vitest globals are off, so RTL auto-cleanup never registers.
  cleanup();
});

describe("StepTimeline", () => {
  it("shows only the latest 3 steps while running, with an expand-all toggle", () => {
    const steps = makeSteps(["步骤一", "步骤二", "步骤三", "步骤四", "步骤五"]);
    render(<StepTimeline steps={steps} taskStatus="running" />);

    // Older steps are folded away by default.
    expect(screen.queryByText("步骤一")).not.toBeInTheDocument();
    expect(screen.queryByText("步骤二")).not.toBeInTheDocument();
    expect(screen.getByText("步骤三")).toBeInTheDocument();
    // Latest step appears twice: pinned current-step row + timeline node.
    expect(screen.getAllByText("步骤五")).toHaveLength(2);
    expect(screen.getByText("第 5 步")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "展开全部 5 步" }));
    expect(screen.getByText("步骤一")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收起步骤" })).toBeInTheDocument();
  });

  it("collapses into a summary row when terminal and expands on click", () => {
    const steps = makeSteps(["步骤一", "步骤二", "步骤三", "步骤四"]);
    render(<StepTimeline steps={steps} taskStatus="completed" />);

    // Folded by default: only the summary line, no step rows.
    const summary = screen.getByRole("button", { name: /共 4 步 · 耗时 1m 30s/ });
    expect(summary).toBeInTheDocument();
    expect(screen.queryByText("步骤一")).not.toBeInTheDocument();

    fireEvent.click(summary);
    expect(screen.getByText("步骤一")).toBeInTheDocument();
    expect(screen.getByText("步骤四")).toBeInTheDocument();
  });

  it("truncates long step details and keeps the full text in the title attribute", () => {
    const longDetail = "x".repeat(120);
    const steps: ChatTaskStep[] = [
      {
        id: "s0",
        label: "准备执行",
        detail: longDetail,
        status: "completed",
        timestamp: "2026-01-01T00:00:00Z",
      },
    ];
    render(<StepTimeline steps={steps} taskStatus="running" />);

    const detailNode = screen.getByTitle(longDetail);
    expect(detailNode.textContent).toHaveLength(91); // 90 chars + ellipsis
  });
});
