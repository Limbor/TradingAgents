import { useQuery } from "@tanstack/react-query";
import { listReflectionCases } from "@/api/client";

function daysUntil(iso: string | null): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  return Math.ceil((t - Date.now()) / 86400000);
}

function scopeLabel(scope: string): string {
  if (scope === "decision_grade") return "强反思";
  if (scope === "candidate_pool") return "观察池";
  if (scope === "user_private") return "私人";
  return scope;
}

export function ReflectionQueueCard() {
  const { data } = useQuery({
    queryKey: ["reflection-cases", "pending"],
    queryFn: () => listReflectionCases({ status: "pending", limit: 15 }),
    refetchInterval: 60000,
  });
  const cases = data ?? [];

  return (
    <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-stone-100">反思队列</h3>
        <span className="text-xs text-stone-500">{cases.length} 个待反思</span>
      </div>
      {cases.length === 0 ? (
        <p className="text-xs text-stone-500">
          暂无等待反思的决策。选股与个股分析会自动纳入此处，到期后由 16:30 反思任务复盘。
        </p>
      ) : (
        <ul className="space-y-1.5 text-xs">
          {cases.map((c) => {
            const d = daysUntil(c.due_date);
            const dueText =
              d === null ? `${c.horizon_days} 个交易日后` :
              d > 0 ? `${d} 天后反思` :
              d === 0 ? "今日到期" :
              `已逾期 ${-d} 天`;
            const dueTone = d !== null && d <= 0 ? "text-amber-300" : "text-stone-400";
            return (
              <li key={c.id} className="flex items-center justify-between gap-2 rounded border border-stone-800 bg-stone-950 px-2 py-1.5">
                <div className="min-w-0">
                  <div className="font-mono text-stone-200">{c.symbol}</div>
                  <div className="text-[10px] text-stone-500">
                    {c.signal_date} · {scopeLabel(c.reflection_scope)} · {c.source_type}
                  </div>
                </div>
                <span className={`shrink-0 font-mono text-[11px] ${dueTone}`} title={c.due_date ?? undefined}>
                  {dueText}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
