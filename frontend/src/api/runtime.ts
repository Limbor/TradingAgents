export interface BackendRuntime {
  api_origin: string;
  ws_origin: string;
  token: string;
}

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
    __TRADINGAGENTS_BACKEND__?: BackendRuntime;
  }
}

export function getBackendRuntime(): BackendRuntime | undefined {
  return window.__TRADINGAGENTS_BACKEND__;
}

/** Resolve the per-launch desktop connection and wait for the sidecar. */
export async function initializeBackendRuntime(): Promise<void> {
  if (!window.__TAURI_INTERNALS__) return;

  const { invoke } = await import("@tauri-apps/api/core");
  const runtime = await invoke<BackendRuntime>("backend_connection");
  if (!runtime.api_origin || !runtime.ws_origin || !runtime.token) {
    throw new Error("桌面后端返回了无效的连接配置");
  }
  window.__TRADINGAGENTS_BACKEND__ = runtime;

  const deadline = Date.now() + 20_000;
  let lastError = "尚未响应";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${runtime.api_origin}/api/v1/health`, {
        headers: { Authorization: `Bearer ${runtime.token}` },
      });
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => window.setTimeout(resolve, 200));
  }
  throw new Error(`桌面后端启动超时：${lastError}`);
}
