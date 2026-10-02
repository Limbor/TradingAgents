import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getConfig: vi.fn(),
  getMarketOverview: vi.fn(),
  healthCheck: vi.fn(),
  listArtifacts: vi.fn(),
  listHoldings: vi.fn(),
  listReflectionCases: vi.fn(),
  listRiskEvents: vi.fn(),
  listRuns: vi.fn(),
  listStrategyLessons: vi.fn(),
}));

const chat = vi.hoisted(() => ({ go: vi.fn() }));

vi.mock("../../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api/client")>();
  return { ...original, ...api };
});

vi.mock("@/lib/chatNav", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/chatNav")>();
  return { ...original, useGoChat: () => chat.go };
});

import Dashboard from "./index";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderDashboard() {
  api.getConfig.mockResolvedValue({ daily_pipeline_filters: {} });
  api.getMarketOverview.mockResolvedValue({ available: false, artifact: null });
  api.healthCheck.mockResolvedValue({ stockmanager_mcp: { connected: true } });
  api.listArtifacts.mockResolvedValue([]);
  api.listHoldings.mockResolvedValue([]);
  api.listReflectionCases.mockResolvedValue([]);
  api.listRiskEvents.mockResolvedValue([]);
  api.listRuns.mockResolvedValue([]);
  api.listStrategyLessons.mockResolvedValue([]);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => null }));

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <Dashboard />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("Dashboard daily review navigation", () => {
  it("opens Chat and auto-sends the deterministic daily_review workflow", async () => {
    renderDashboard();

    fireEvent.click(await screen.findByRole("button", { name: "开始收盘复盘" }));

    expect(chat.go).toHaveBeenCalledWith({
      prompt: "执行今日收盘复盘并生成次日计划",
      autoSend: true,
      intentHint: {
        skill_id: "daily_review",
        params: { daily_limit: 5, candidate_limit: 120 },
      },
    });
  });
});
