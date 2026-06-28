interface AgentStatus {
  agent: string;
  status: "pending" | "running" | "completed" | "failed";
}

interface AgentGraphProps {
  agentStatuses: Record<string, AgentStatus>;
}

const PHASES = [
  {
    label: "Analysts",
    agents: ["Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst"],
  },
  {
    label: "Research",
    agents: ["Bull Researcher", "Bear Researcher", "Research Manager"],
  },
  { label: "Trading", agents: ["Trader"] },
  {
    label: "Risk",
    agents: ["Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"],
  },
  { label: "Decision", agents: ["Portfolio Manager"] },
];

const statusStyle: Record<string, string> = {
  pending: "border-stone-700 text-stone-500 bg-stone-950",
  running: "border-teal-400 text-teal-200 bg-teal-500/10",
  completed: "border-emerald-500/70 text-emerald-200 bg-emerald-500/10",
  failed: "border-red-500/70 text-red-200 bg-red-500/10",
};

export function AgentGraph({ agentStatuses }: AgentGraphProps) {
  return (
    <div className="h-full space-y-3 overflow-y-auto pr-1">
      {PHASES.map((phase, index) => {
        const completed = phase.agents.filter(
          (agent) => agentStatuses[agent]?.status === "completed"
        ).length;
        return (
        <div key={phase.label} className="relative rounded-lg border border-stone-800 bg-stone-950 p-3">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-md bg-stone-800 font-mono text-xs text-stone-300">
                {index + 1}
              </span>
              <p className="text-xs font-semibold uppercase tracking-wide text-stone-400">
                {phase.label}
              </p>
            </div>
            <span className="font-mono text-xs text-stone-600">
              {completed}/{phase.agents.length}
            </span>
          </div>
          <div className="grid gap-1.5">
            {phase.agents.map((agent) => {
              const status = agentStatuses[agent]?.status ?? "pending";
              return (
                <div
                  key={agent}
                  className={`flex min-h-8 items-center justify-between rounded-md border px-2.5 py-1.5 text-xs ${statusStyle[status]}`}
                >
                  <span className="truncate">{agent}</span>
                  <span className={`h-1.5 w-1.5 rounded-full ${
                    status === "running"
                      ? "animate-pulse bg-teal-300"
                      : status === "completed"
                        ? "bg-emerald-300"
                        : status === "failed"
                          ? "bg-red-300"
                          : "bg-stone-700"
                  }`} />
                </div>
              );
            })}
          </div>
        </div>
      );
      })}
    </div>
  );
}
