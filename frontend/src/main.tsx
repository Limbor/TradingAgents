import React from "react";
import ReactDOM from "react-dom/client";
import { initializeBackendRuntime } from "./api/runtime";
import "./index.css";

async function bootstrap(): Promise<void> {
  await initializeBackendRuntime();
  // Import the application only after the desktop runtime is available so API
  // modules never capture an obsolete fixed origin during module evaluation.
  const { default: App } = await import("./App");
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

void bootstrap().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <main className="flex min-h-screen items-center justify-center bg-ui-canvas p-8 text-ui-ink">
      <div className="max-w-lg rounded border border-ui-danger/30 bg-ui-panel p-6">
        <h1 className="font-semibold">TradingAgents 启动失败</h1>
        <p className="mt-2 text-sm text-ui-danger">{message}</p>
        <p className="mt-3 text-xs text-ui-muted">请退出应用后重试；若仍失败，请检查桌面后端日志。</p>
      </div>
    </main>,
  );
});
