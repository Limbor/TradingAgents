import { useRunStore } from "@/stores/useRunStore";
import { Activity, CheckCircle2, CircleSlash2, Clock3, Server } from "lucide-react";

export function Header() {
  const status = useRunStore((s) => s.status);

  const statusMeta = {
    idle: { label: "Ready", icon: Clock3, color: "text-stone-400", dot: "bg-stone-500" },
    running: { label: "Analysis running", icon: Activity, color: "text-teal-300", dot: "bg-teal-300" },
    completed: { label: "Analysis complete", icon: CheckCircle2, color: "text-emerald-300", dot: "bg-emerald-300" },
    failed: { label: "Run failed", icon: CircleSlash2, color: "text-red-300", dot: "bg-red-300" },
    cancelled: { label: "Run cancelled", icon: CircleSlash2, color: "text-amber-300", dot: "bg-amber-300" },
  }[status];
  const StatusIcon = statusMeta.icon;

  return (
    <header className="flex h-16 items-center justify-between border-b border-stone-800 bg-stone-900 px-5">
      <div>
        <h1 className="text-sm font-semibold text-stone-100">
          Multi-Agent Equity Research Console
        </h1>
        <p className="text-xs text-stone-500">A-share and US market workflows</p>
      </div>
      <div className="flex items-center gap-3">
        <div className="hidden items-center gap-2 rounded-lg border border-stone-800 bg-stone-950 px-3 py-2 text-xs text-stone-400 sm:flex">
          <Server className="h-3.5 w-3.5 text-stone-500" />
          Local API
        </div>
        <div className={`flex items-center gap-2 rounded-lg border border-stone-800 bg-stone-950 px-3 py-2 text-xs ${statusMeta.color}`}>
          <span className={`h-2 w-2 rounded-full ${statusMeta.dot} ${status === "running" ? "animate-pulse" : ""}`} />
          <StatusIcon className="h-3.5 w-3.5" />
          {statusMeta.label}
        </div>
      </div>
    </header>
  );
}
