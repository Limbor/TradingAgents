import react from "@vitejs/plugin-react";
import path from "path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_BACKEND_PROXY_TARGET || "http://127.0.0.1:8422",
        changeOrigin: true,
      },
      "/ws": {
        target: (process.env.VITE_BACKEND_PROXY_TARGET || "http://127.0.0.1:8422").replace(/^http/, "ws"),
        changeOrigin: true,
        ws: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    clearMocks: true,
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
  },
});
