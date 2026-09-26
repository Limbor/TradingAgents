import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Wrench } from "lucide-react";
import type { ChatMessage } from "@/stores/useChatStore";
import { toolLabel } from "@/utils/eventLabels";

interface ToolCardProps {
  message: ChatMessage;
  /** Rendered inside a grouped container: drop own border/margin. */
  bare?: boolean;
}

// Max arg chips shown inline in the header; the rest collapse into "+N".
const HEADER_CHIP_COUNT = 3;

/**
 * Renders a lightweight tool result inline.
 *
 * Three display modes:
 * - "table": renders result as a simple key-value table
 * - "card": renders result as a structured card
 * - "text": markdown for strings, key-value grid for objects
 */
export function ToolCard({ message, bare = false }: ToolCardProps) {
  const tc = message.toolCall;
  if (!tc) return null;

  const { tool, args, result, display } = tc;
  const argEntries = args && typeof args === "object"
    ? Object.entries(args).filter(([, v]) => v !== undefined && v !== null && v !== "")
    : [];
  const headerChips = argEntries.slice(0, HEADER_CHIP_COUNT);
  const overflow = argEntries.length - headerChips.length;
  const hasCallDetails = argEntries.length > 0 || (message.citations?.length ?? 0) > 0;

  return (
    // Aligned with assistant bubbles (avatar width 8 + gap 3 = ml-11), no own avatar.
    <div
      className={
        bare
          ? "px-3 py-2 text-sm leading-6 text-ui-body"
          : "ml-11 max-w-[85%] rounded-lg border border-ui-line bg-ui-subtle/60 px-3 py-2 text-sm leading-6 text-ui-body"
      }
    >
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
        <Wrench className="h-3.5 w-3.5 shrink-0 text-indigo-300/80" />
        <span className="text-xs font-medium text-ui-body">{toolLabel(tool)}</span>
        {headerChips.map(([k, v]) => (
          <span
            key={k}
            className="rounded border border-ui-line bg-ui-panel/60 px-1.5 py-0.5 text-[11px] text-ui-muted"
          >
            <span className="text-ui-faint">{k}:</span> <span className="text-ui-body">{formatArg(v)}</span>
          </span>
        ))}
        {overflow > 0 && (
          <span className="rounded border border-ui-line bg-ui-panel/60 px-1.5 py-0.5 text-[11px] text-ui-faint">
            +{overflow}
          </span>
        )}
      </div>
      <ToolResult display={display} result={result} />
      {hasCallDetails && (
        <details className="mt-2 border-t border-ui-line pt-1.5">
          <summary className="cursor-pointer text-xs text-ui-faint hover:text-ui-body">调用详情</summary>
          <div className="mt-1.5 space-y-1.5">
            {argEntries.length > 0 && (
              <div className="flex flex-wrap gap-1 text-[11px] text-ui-muted">
                {argEntries.map(([k, v]) => (
                  <span key={k} className="rounded border border-ui-strong bg-ui-panel/60 px-1.5 py-0.5">
                    <span className="text-ui-faint">{k}:</span> <span className="text-ui-body">{formatArg(v)}</span>
                  </span>
                ))}
              </div>
            )}
            {message.citations && message.citations.length > 0 && (
              <Citations citations={message.citations} />
            )}
          </div>
        </details>
      )}
    </div>
  );
}

function ToolResult({ display, result }: { display: string; result: unknown }) {
  if (display === "table" && resultIsObject(result)) return <ToolTable result={result} />;
  if (display === "card" && resultIsObject(result)) return <ToolCardBody result={result} />;
  // "text" mode: strings render as markdown, objects as a key-value grid;
  // raw <pre> is the last resort only.
  if (typeof result === "string" && result.trim()) {
    return (
      <div className="prose prose-invert prose-sm max-w-none whitespace-pre-wrap break-words">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{result}</ReactMarkdown>
      </div>
    );
  }
  if (resultIsObject(result)) return <ToolTable result={result} />;
  return (
    <pre className="max-h-60 overflow-y-auto whitespace-pre-wrap break-all font-mono text-xs text-ui-body">
      {formatResult(result)}
    </pre>
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
      return <p className="text-ui-muted italic">{String(result.message ?? "No data")}</p>;
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
              <tr key={i} className="border-b border-ui-strong/30">
                {columns.map((col) => (
                  <td key={col} className="px-2 py-1 text-ui-body">
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
            <span className="text-ui-body">{formatCell(val)}</span>
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
            <span className="text-ui-body truncate">{formatCell(val)}</span>
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
    <div className="mt-2 pt-2 border-t border-ui-line space-y-1">
      <div className="text-xs text-ui-muted">
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
        <div className="text-xs text-ui-warning/80">
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
    <div className="mt-2 text-xs text-ui-warning/80">
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
