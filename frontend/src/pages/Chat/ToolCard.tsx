import { Wrench } from "lucide-react";
import type { ChatMessage } from "@/stores/useChatStore";

interface ToolCardProps {
  message: ChatMessage;
}

/**
 * Renders a lightweight tool result inline.
 *
 * Three display modes:
 * - "table": renders result as a simple key-value table
 * - "card": renders result as a structured card
 * - "text": renders result as plain text
 */
export function ToolCard({ message }: ToolCardProps) {
  const tc = message.toolCall;
  if (!tc) return null;

  const { tool, args, result, display } = tc;
  const argEntries = args && typeof args === "object"
    ? Object.entries(args).filter(([, v]) => v !== undefined && v !== null && v !== "")
    : [];

  return (
    <div className="flex gap-3 justify-start">
      <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-indigo-500/30 bg-indigo-500/10">
        <Wrench className="h-4 w-4 text-indigo-300" />
      </div>
      <div className="max-w-[80%] rounded-lg border border-indigo-500/20 bg-indigo-500/5 px-3 py-2 text-sm leading-6 text-stone-200">
        <div className="mb-1 text-xs font-medium uppercase tracking-wide text-indigo-300/80">
          {tool}
        </div>
        {argEntries.length > 0 && (
          <div className="mb-1.5 flex flex-wrap gap-1 text-[11px] text-stone-400">
            {argEntries.map(([k, v]) => (
              <span key={k} className="rounded border border-stone-700 bg-stone-900/60 px-1.5 py-0.5">
                <span className="text-stone-500">{k}:</span> <span className="text-stone-300">{formatArg(v)}</span>
              </span>
            ))}
          </div>
        )}
        {display === "table" && resultIsObject(result) ? (
          <ToolTable result={result} />
        ) : display === "card" && resultIsObject(result) ? (
          <ToolCardBody result={result} />
        ) : (
          <pre className="whitespace-pre-wrap break-all font-mono text-xs text-stone-300">
            {formatResult(result)}
          </pre>
        )}
        {message.citations && message.citations.length > 0 && (
          <Citations citations={message.citations} />
        )}
      </div>
    </div>
  );
}

function formatArg(v: unknown): string {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function resultIsObject(val: unknown): val is Record<string, unknown> {
  return val !== null && typeof val === "object" && !Array.isArray(val);
}

function formatResult(val: unknown): string {
  if (val === null || val === undefined) return "";
  if (typeof val === "string") return val;
  try {
    return JSON.stringify(val, null, 2);
  } catch {
    return String(val);
  }
}

function ToolTable({ result }: { result: Record<string, unknown> }) {
  // Find a likely list property (holdings, runs, lessons, results, snapshot)
  const listKey = ["holdings", "runs", "lessons", "results", "snapshot"].find(
    (k) => Array.isArray(result[k])
  );
  if (listKey) {
    const rows = result[listKey] as Record<string, unknown>[];
    if (rows.length === 0) {
      return <p className="text-stone-400 italic">{String(result.message ?? "No data")}</p>;
    }
    const columns = Object.keys(rows[0] ?? {}).filter(
      (k) => k !== "warnings" && k !== "errors"
    );
    return (
      <div className="overflow-x-auto">
        <table className="min-w-full text-xs">
          <thead>
            <tr className="border-b border-indigo-500/20">
              {columns.map((col) => (
                <th
                  key={col}
                  className="px-2 py-1 text-left font-medium text-indigo-300/80"
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-stone-700/30">
                {columns.map((col) => (
                  <td key={col} className="px-2 py-1 text-stone-300">
                    {formatCell(row[col])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {renderWarnings(result)}
      </div>
    );
  }

  // No recognized list — show key-value pairs
  return (
    <div className="space-y-1">
      {Object.entries(result)
        .filter(([k]) => k !== "warnings")
        .map(([key, val]) => (
          <div key={key} className="flex gap-2 text-xs">
            <span className="font-medium text-indigo-300/70">{key}:</span>
            <span className="text-stone-300">{formatCell(val)}</span>
          </div>
        ))}
      {renderWarnings(result)}
    </div>
  );
}

function ToolCardBody({ result }: { result: Record<string, unknown> }) {
  // Card display: show key fields in a compact layout
  return (
    <div className="space-y-1.5">
      {Object.entries(result)
        .filter(
          ([k]) =>
            !["warnings", "total", "_internal"].includes(k) &&
            !Array.isArray(result[k])
        )
        .slice(0, 10)
        .map(([key, val]) => (
          <div key={key} className="flex gap-2 text-xs">
            <span className="font-medium text-indigo-300/70 shrink-0">{key}:</span>
            <span className="text-stone-300 truncate">{formatCell(val)}</span>
          </div>
        ))}
      {renderWarnings(result)}
    </div>
  );
}

function Citations({
  citations,
}: {
  citations: Array<{
    tool: string;
    args: Record<string, unknown>;
    summary: string;
    as_of_date?: string;
    source?: string;
    warnings?: string[];
  }>;
}) {
  return (
    <div className="mt-2 pt-2 border-t border-indigo-500/15 space-y-1">
      <div className="text-xs text-stone-400">
        数据来源：
        {citations.map((c, i) => (
          <span key={i} className="ml-1 text-indigo-400/70">
            {c.source || c.tool}
            {c.as_of_date ? ` · 基准日 ${c.as_of_date}` : ""}
            {c.summary ? ` (${c.summary})` : ""}
            {i < citations.length - 1 ? "," : ""}
          </span>
        ))}
      </div>
      {citations.some((c) => c.warnings && c.warnings.length > 0) && (
        <div className="text-xs text-amber-300/80">
          {citations
            .flatMap((c) => c.warnings || [])
            .map((w, i) => (
              <span key={i} className="mr-2">⚠ {w}</span>
            ))}
        </div>
      )}
    </div>
  );
}

function renderWarnings(result: Record<string, unknown> | null | undefined) {
  if (!result) return null;
  const warnings: unknown = result.warnings;
  if (!Array.isArray(warnings) || warnings.length === 0) return null;
  return (
    <div className="mt-2 text-xs text-amber-400/80">
      {warnings.map((w: unknown, i: number) => (
        <div key={i}>⚠ {String(w)}</div>
      ))}
    </div>
  );
}

function formatCell(val: unknown): string {
  if (val === null || val === undefined) return "-";
  if (typeof val === "number") {
    return Number.isFinite(val) ? String(Number(val.toFixed(2))) : String(val);
  }
  if (typeof val === "boolean") return val ? "✓" : "✗";
  if (typeof val === "object") {
    try {
      return JSON.stringify(val);
    } catch {
      return String(val);
    }
  }
  return String(val);
}
