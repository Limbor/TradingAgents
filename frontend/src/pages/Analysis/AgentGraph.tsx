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
  pending: "border-ui-strong text-ui-faint bg-ui-subtle",
  running: "border-ui-accent text-ui-accent bg-ui-accent/10",
  completed: "border-ui-success/70 text-ui-success bg-ui-success/10",
  failed: "border-ui-danger/70 text-ui-danger bg-ui-danger/10",
};

export function AgentGraph({ agentStatuses }: AgentGraphProps) {
  return (
    <div className="h-full space-y-3 overflow-y-auto pr-1">
      {PHASES.map((phase, index) => {
        const completed = phase.agents.filter(
          (agent) => agentStatuses[agent]?.status === "completed"
        ).length;
        return (
        <div key={phase.label} className="relative rounded-lg border border-ui-line bg-ui-subtle p-3">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-md bg-ui-hover font-mono text-xs text-ui-body">
                {index + 1}
              </span>
              <p className="text-xs font-semibold uppercase tracking-wide text-ui-muted">
                {phase.label}
              </p>
            </div>
            <span className="font-mono text-xs text-ui-faint">
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
                      ? "animate-pulse bg-ui-accent"
                      : status === "completed"
                        ? "bg-ui-success"
                        : status === "failed"
                          ? "bg-ui-danger"
                          : "bg-ui-hover"
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
