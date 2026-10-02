import type { LegacyArchiveMessage } from "@/api/agent";

export const LEGACY_CHAT_IMPORT_MARKER = "tradingagents-legacy-chat-imported-v1";

export interface LegacyChatBatch {
  paperSessionId: string | null;
  messages: LegacyArchiveMessage[];
}

/** Convert the old browser snapshot into inert text archives grouped by account. */
export function readLegacyChatBatches(storage: Pick<Storage, "getItem">): LegacyChatBatch[] {
  let parsed: unknown;
  try {
    const raw = storage.getItem("tradingagents-chat");
    parsed = raw ? JSON.parse(raw) : null;
  } catch {
    return [];
  }
  const state = parsed && typeof parsed === "object" ? (parsed as { state?: unknown }).state : null;
  const rawMessages = state && typeof state === "object"
    ? (state as { messages?: unknown }).messages : null;
  if (!Array.isArray(rawMessages)) return [];

  const grouped = new Map<string, LegacyArchiveMessage[]>();
  for (const raw of rawMessages.slice(-100)) {
    if (!raw || typeof raw !== "object") continue;
    const message = raw as Record<string, unknown>;
    if (message.id === "welcome" || (message.role !== "user" && message.role !== "assistant")) continue;
    if (typeof message.content !== "string" || !message.content.trim()) continue;
    const timestamp = typeof message.timestamp === "string" ? new Date(message.timestamp) : null;
    if (!timestamp || Number.isNaN(timestamp.getTime())) continue;
    const scope = typeof message.scope === "string" ? message.scope : "general";
    if (scope !== "general" && (!scope.startsWith("paper:") ||
        !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(scope.slice(6)) ||
        scope.slice(6).includes(".."))) continue;
    const extra = message.kind === "task" && typeof message.result === "string"
      ? `\n\n${message.result}` : "";
    const content = `${message.content}${extra}`.slice(0, 20_000).trim();
    if (!content) continue;
    const entries = grouped.get(scope) ?? [];
    entries.push({ role: message.role, content, created_at: timestamp.toISOString() });
    grouped.set(scope, entries);
  }
  return [...grouped].map(([scope, messages]) => ({
    paperSessionId: scope === "general" ? null : scope.slice(6), messages,
  }));
}
