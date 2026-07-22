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
    <main className="flex min-h-screen items-center justify-center bg-stone-950 p-8 text-stone-200">
      <div className="max-w-lg rounded border border-red-900 bg-red-950/30 p-6">
        <h1 className="font-semibold">TradingAgents 启动失败</h1>
        <p className="mt-2 text-sm text-red-200">{message}</p>
        <p className="mt-3 text-xs text-stone-400">请退出应用后重试；若仍失败，请检查桌面后端日志。</p>
      </div>
    </main>,
  );
});
