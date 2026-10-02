import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  createRun: vi.fn(),
  getRun: vi.fn(),
  getMarketOverview: vi.fn(),
  listHoldings: vi.fn(),
}));

vi.mock("../../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api/client")>();
  return { ...original, ...api };
});

vi.mock("@/lib/chatNav", () => ({
  dailyPipelineHint: vi.fn(() => ({ skill_id: "daily_pipeline", params: {} })),
  useGoChat: vi.fn(() => vi.fn()),
}));

import Market from "./index";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function renderMarket() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Market />
    </QueryClientProvider>,
  );
}

describe("Market overview refresh", () => {
  it("starts polling after createRun returns and stops on completion", async () => {
    api.getMarketOverview.mockResolvedValue({
      available: false,
      artifact: null,
      is_stale: false,
      current_asof_date: "2026-08-11",
    });
    api.listHoldings.mockResolvedValue([]);
    api.createRun.mockResolvedValue({ id: "market-run-1", status: "running" });
    api.getRun.mockResolvedValue({ id: "market-run-1", status: "completed" });

    renderMarket();
    const button = await screen.findByRole("button", { name: "立即生成" });
    vi.useFakeTimers();

    fireEvent.click(button);
    await act(async () => Promise.resolve());
    expect(screen.getByRole("button", { name: "生成中，请稍候" })).toBeDisabled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });

    expect(api.getRun).toHaveBeenCalledWith("market-run-1");
    expect(screen.getByRole("button", { name: "立即生成" })).toBeEnabled();
  });

  it("stops spinning after three consecutive polling failures", async () => {
    api.getMarketOverview.mockResolvedValue({
      available: false,
      artifact: null,
      is_stale: false,
      current_asof_date: "2026-08-11",
    });
    api.listHoldings.mockResolvedValue([]);
    api.createRun.mockResolvedValue({ id: "market-run-2", status: "running" });
    api.getRun.mockRejectedValue(new Error("backend offline"));

    renderMarket();
    const button = await screen.findByRole("button", { name: "立即生成" });
    vi.useFakeTimers();
    fireEvent.click(button);
    await act(async () => Promise.resolve());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });

    expect(screen.getByText(/连续 3 次无法获取生成状态/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "立即生成" })).toBeEnabled();
  });
});
