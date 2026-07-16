import { AlertTriangle, CheckCircle2, MinusCircle, TrendingUp } from "lucide-react";
import { useNavigate } from "react-router-dom";

type Advice = Record<string, unknown>;

const actionStyle: Record<string, string> = {
  ADD: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  HOLD: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  REDUCE: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  EXIT: "border-red-500/30 bg-red-500/10 text-red-300",
};

export function PositionAdviceCard({ advice }: { advice: Advice }) {
  const navigate = useNavigate();
  const action = String(advice.action ?? "HOLD");
  const metrics = objectOf(advice.metrics);
  const risk = objectOf(advice.risk);
  const execution = objectOf(advice.execution);
  const reasons = Array.isArray(advice.reasons) ? advice.reasons.map(String) : [];
  const warnings = Array.isArray(advice.warnings) ? advice.warnings.map(String) : [];
  const quantityChange = number(execution.quantity_change);
  const canAdjust = action !== "HOLD" && quantityChange !== 0;
  const Icon = action === "ADD" ? TrendingUp : action === "HOLD" ? CheckCircle2 : action === "REDUCE" ? MinusCircle : AlertTriangle;

  return (
    <div className="rounded-lg border border-stone-700 bg-stone-900/80 p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="font-mono text-xs text-stone-500">{String(advice.symbol ?? "")}</div>
          <div className="mt-1 text-sm text-stone-300">
            浮盈亏 {number(metrics.pnl_pct).toFixed(2)}% · 仓位 {number(metrics.position_pct).toFixed(2)}%
          </div>
        </div>
        <div className={`flex items-center gap-1.5 rounded border px-3 py-1.5 text-sm font-semibold ${actionStyle[action] ?? actionStyle.HOLD}`}>
          <Icon className="h-4 w-4" /> {action}
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <Metric label="现价" value={number(metrics.current_price).toFixed(2)} />
        <Metric label="风险等级" value={String(risk.level ?? "unknown")} />
        <Metric label="建议变化" value={`${signed(number(execution.quantity_change))} 股`} />
        <Metric label="目标仓位" value={`${number(execution.target_position_pct).toFixed(2)}%`} />
      </div>
      {reasons.length > 0 && <ul className="mt-3 space-y-1 text-xs text-stone-300">{reasons.map((reason) => <li key={reason}>• {reason}</li>)}</ul>}
      {warnings.length > 0 && <div className="mt-3 rounded border border-amber-500/20 bg-amber-500/5 p-2 text-xs text-amber-200">{warnings.join("；")}</div>}
      {Boolean(execution.requires_user_confirmation) && <p className="mt-3 text-xs text-stone-500">该建议不会自动修改持仓，执行前需要用户确认。</p>}
      {canAdjust && (
        <button
          type="button"
          onClick={() => navigate("/portfolio", {
            state: {
              adjustment: {
                symbol: String(advice.symbol ?? ""),
                action: action === "ADD" ? "add" : "reduce",
                quantity: Math.abs(quantityChange),
                price: number(metrics.current_price),
                source: "position_advisor",
                decision_id: String(advice.decision_id ?? ""),
              },
            },
          })}
          className="mt-3 rounded border border-indigo-500/30 bg-indigo-500/10 px-3 py-2 text-xs font-medium text-indigo-200 transition hover:bg-indigo-500/20"
        >
          前往 Portfolio 确认{action === "ADD" ? "加仓" : action === "EXIT" ? "清仓" : "减仓"}
        </button>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="rounded border border-stone-800 bg-stone-950 p-2"><div className="text-stone-500">{label}</div><div className="mt-1 font-mono text-stone-200">{value}</div></div>;
}

function objectOf(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function number(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function signed(value: number): string {
  return value > 0 ? `+${value}` : String(value);
}
