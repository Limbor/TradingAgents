import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig } from "@playwright/test";

const localChrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const localVenvPython = resolve(process.cwd(), "../.venv/bin/python");
const backendPython = existsSync(localVenvPython)
  ? `"${localVenvPython}"`
  : process.platform === "win32" ? "python" : "python3";

// Pre-release smoke mode: boot the real FastAPI backend alongside the vite dev
// server and run only *.smoke.ts. The default run ignores smoke specs and keeps
// the mocked *.spec.ts contract as the stable frontend-flow verifier.
const realBackend = process.env.E2E_REAL_BACKEND === "1";
const frontendPort = process.env.E2E_FRONTEND_PORT || "5173";

const frontendServer = {
  command: `npm run dev -- --host 127.0.0.1 --port ${frontendPort} --strictPort`,
  url: `http://127.0.0.1:${frontendPort}`,
  reuseExistingServer: !process.env.CI && !realBackend,
  timeout: 60_000,
  env: realBackend ? { VITE_BACKEND_PROXY_TARGET: "http://127.0.0.1:8423" } : {},
};

const backendServer = {
  // Runs from the repo root; the smoke Vite proxy forwards /api and /ws to :8423.
  command: `${backendPython} -m tradingagents.api.server`,
  cwd: "..",
  url: "http://127.0.0.1:8423/api/v1/health",
  reuseExistingServer: false,
  // Leave room for backend import/startup overhead.
  timeout: 120_000,
  env: {
    TRADINGAGENTS_API_HOST: "127.0.0.1",
    TRADINGAGENTS_API_PORT: "8423",
    // A throwaway DB file (the connect-per-call layer can't use :memory:) keeps
    // the smoke off the developer's real ~/.tradingagents/app.db. Tests create
    // their own conversations, so earlier smoke rows do not affect assertions.
    TRADINGAGENTS_APP_DB: "/tmp/tradingagents-e2e-smoke.db",
    STOCKMANAGER_MCP_ENABLED: "false",
    TRADINGAGENTS_AGENT_MODEL_PLANNING_ENABLED: "false",
    TRADINGAGENTS_SCHEDULER_ENABLED: "false",
    TRADINGAGENTS_TICKER_NAME_BACKFILL_ENABLED: "false",
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
    baseURL: `http://127.0.0.1:${frontendPort}`,
    trace: "retain-on-failure",
    launchOptions: !process.env.CI && existsSync(localChrome)
      ? { executablePath: localChrome }
      : undefined,
  },
  webServer: realBackend ? [backendServer, frontendServer] : frontendServer,
});
