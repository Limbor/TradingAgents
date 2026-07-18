import { existsSync } from "node:fs";
import { defineConfig } from "@playwright/test";

const localChrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

// Pre-release smoke mode: boot the real FastAPI backend alongside the vite dev
// server and run only *.smoke.ts. The default run ignores smoke specs and keeps
// the mocked *.spec.ts contract as the stable frontend-flow verifier.
const realBackend = process.env.E2E_REAL_BACKEND === "1";

const frontendServer = {
  command: "npm run dev -- --host 127.0.0.1",
  url: "http://127.0.0.1:5173",
  reuseExistingServer: !process.env.CI,
  timeout: 60_000,
};

const backendServer = {
  // Runs from the repo root; the vite dev proxy forwards /api and /ws to :8422.
  command: "python -m tradingagents.api.server",
  cwd: "..",
  url: "http://127.0.0.1:8422/api/v1/health",
  reuseExistingServer: !process.env.CI,
  // MCP init waits up to 10s before degrading, plus import/startup overhead.
  timeout: 120_000,
  env: {
    TRADINGAGENTS_API_HOST: "127.0.0.1",
    TRADINGAGENTS_API_PORT: "8422",
    // A throwaway DB file (the connect-per-call layer can't use :memory:) keeps
    // the smoke off the developer's real ~/.tradingagents/app.db. Assertions are
    // data-independent (shell + health + static header), so stale rows are fine.
    TRADINGAGENTS_APP_DB: "/tmp/tradingagents-e2e-smoke.db",
  },
};

export default defineConfig({
  testDir: "./e2e",
  testMatch: realBackend ? "**/*.smoke.ts" : "**/*.spec.ts",
  timeout: 30_000,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    launchOptions: !process.env.CI && existsSync(localChrome)
      ? { executablePath: localChrome }
      : undefined,
  },
  webServer: realBackend ? [backendServer, frontendServer] : frontendServer,
});
