interface ProgressTrackerProps {
  status: "idle" | "running" | "completed" | "failed" | "cancelled";
  error: string | null;
}

export function ProgressTracker({ status, error }: ProgressTrackerProps) {
  const percent =
    status === "completed" ? 100 : status === "running" ? 58 : status === "idle" ? 0 : 100;
  const tone =
    status === "completed"
      ? "bg-ui-success"
      : status === "failed"
        ? "bg-ui-danger"
        : status === "cancelled"
          ? "bg-ui-warning"
          : "bg-ui-accent";

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ui-body">Run State</h3>
        <span className="font-mono text-xs text-ui-faint">{percent}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-ui-hover">
        <div className={`h-full ${tone} transition-all`} style={{ width: `${percent}%` }} />
      </div>
      <div className="flex items-center gap-2">
        <span
          className={`h-2 w-2 rounded-full ${
            status === "running"
              ? "animate-pulse bg-ui-accent"
              : status === "completed"
                ? "bg-ui-success"
                : status === "failed"
                  ? "bg-ui-danger"
                  : status === "cancelled"
                    ? "bg-ui-warning"
                    : "bg-ui-faint"
          }`}
        />
        <span className="text-sm capitalize text-ui-body">{status}</span>
      </div>
      {error && (
        <p className="rounded-lg border border-ui-danger/30 bg-ui-danger/10 p-2 text-xs text-ui-danger">{error}</p>
      )}
    </div>
  );
}
