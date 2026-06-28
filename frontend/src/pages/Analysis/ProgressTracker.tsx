interface ProgressTrackerProps {
  status: "idle" | "running" | "completed" | "failed" | "cancelled";
  error: string | null;
}

export function ProgressTracker({ status, error }: ProgressTrackerProps) {
  const percent =
    status === "completed" ? 100 : status === "running" ? 58 : status === "idle" ? 0 : 100;
  const tone =
    status === "completed"
      ? "bg-emerald-400"
      : status === "failed"
        ? "bg-red-400"
        : status === "cancelled"
          ? "bg-amber-400"
          : "bg-teal-400";

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-stone-200">Run State</h3>
        <span className="font-mono text-xs text-stone-500">{percent}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-stone-800">
        <div className={`h-full ${tone} transition-all`} style={{ width: `${percent}%` }} />
      </div>
      <div className="flex items-center gap-2">
        <span
          className={`h-2 w-2 rounded-full ${
            status === "running"
              ? "animate-pulse bg-teal-400"
              : status === "completed"
                ? "bg-emerald-400"
                : status === "failed"
                  ? "bg-red-400"
                  : status === "cancelled"
                    ? "bg-amber-400"
                    : "bg-stone-600"
          }`}
        />
        <span className="text-sm capitalize text-stone-300">{status}</span>
      </div>
      {error && (
        <p className="rounded-lg border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-300">{error}</p>
      )}
    </div>
  );
}
