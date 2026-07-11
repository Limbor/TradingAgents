import { AlertTriangle, ShieldAlert, ShieldCheck, ShieldX } from "lucide-react";
import { displayNameOf } from "@/components/common/StockName";

export interface RiskRow {
  symbol: string;
  name?: string;
  level: string;
  summary: string;
}

interface RiskAlertListProps {
  risks: RiskRow[];
  onAnalyze?: (symbol: string) => void;
}

const levelConfig: Record<string, { icon: typeof ShieldAlert; color: string; bg: string }> = {
  critical: { icon: ShieldX, color: "text-red-300", bg: "bg-red-500/10 border-red-500/30" },
  high: { icon: ShieldAlert, color: "text-orange-300", bg: "bg-orange-500/10 border-orange-500/30" },
  medium: { icon: AlertTriangle, color: "text-amber-300", bg: "bg-amber-500/10 border-amber-500/30" },
  low: { icon: ShieldCheck, color: "text-emerald-300", bg: "bg-emerald-500/10 border-emerald-500/30" },
};

function getLevelConfig(level: string) {
  const normalized = level.toLowerCase();
  if (normalized.includes("critical") || normalized.includes("红")) return levelConfig.critical!;
  if (normalized.includes("high") || normalized.includes("橙")) return levelConfig.high!;
  if (normalized.includes("medium") || normalized.includes("黄")) return levelConfig.medium!;
  return levelConfig.low!;
}

export function RiskAlertList({ risks, onAnalyze }: RiskAlertListProps) {
  if (risks.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/10 p-4 text-sm text-emerald-300">
        <ShieldCheck className="h-4 w-4" />
        当前没有发现明确风险事件。
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {risks.map((risk, i) => {
        const cfg = getLevelConfig(risk.level);
        const Icon = cfg.icon;
        return (
          <div
            key={`${risk.symbol}-${i}`}
            className={`flex items-start gap-3 rounded-lg border p-3 ${cfg.bg}`}
          >
            <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${cfg.color}`} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-stone-100">
                  {displayNameOf(risk.name, risk.symbol)}
                  {risk.name && risk.name !== risk.symbol && (
                    <span className="ml-1 font-mono text-xs text-stone-500">{risk.symbol}</span>
                  )}
                </span>
                <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${cfg.color}`}>
                  {risk.level}
                </span>
              </div>
              <p className="mt-1 text-sm text-stone-300">{risk.summary}</p>
            </div>
            {onAnalyze && (
              <button
                onClick={() => onAnalyze(risk.symbol)}
                className="shrink-0 rounded border border-stone-700 px-2 py-1 text-xs text-stone-300 transition hover:bg-stone-800"
              >
                详情
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

/**
 * Parse raw risk data from WebSocket into typed rows.
 */
export function parseRisks(raw: unknown): RiskRow[] {
  if (!Array.isArray(raw) || raw.length === 0) return [];
  return raw.slice(0, 10).map((item, i) => {
    const row = item as Record<string, unknown>;
    const symbol = String(row.symbol ?? row.ts_code ?? `#${i + 1}`);
    return {
      symbol,
      name: row.name ? String(row.name) : undefined,
      level: String(row.risk_level ?? row.level ?? "unknown"),
      summary: String(row.summary ?? row.title ?? row.message ?? ""),
    };
  });
}
