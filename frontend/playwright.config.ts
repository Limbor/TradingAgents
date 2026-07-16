import { existsSync } from "node:fs";
import { defineConfig } from "@playwright/test";

const localChrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

export default defineConfig({
  testDir: "./e2e",
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
  webServer: {
    command: "npm run dev -- --host 127.0.0.1",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
