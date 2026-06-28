import { useQuery } from "@tanstack/react-query";
import { listSkills, listRuns } from "../../api/client";
import { SkillCard } from "./SkillCard";
import { RecentRuns } from "./RecentRuns";
import { Activity, BarChart3, Database, ShieldCheck } from "lucide-react";

export default function Dashboard() {
  const skillsQuery = useQuery({
    queryKey: ["skills"],
    queryFn: listSkills,
  });

  const runsQuery = useQuery({
    queryKey: ["runs"],
    queryFn: () => listRuns(10),
    refetchInterval: 5000,
  });

  const runs = runsQuery.data ?? [];
  const runningRuns = runs.filter((run) => run.status === "running").length;
  const completedRuns = runs.filter((run) => run.status === "completed").length;

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5">
      <div className="flex flex-col justify-between gap-4 border-b border-stone-800 pb-5 lg:flex-row lg:items-end">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-teal-300">
            Research cockpit
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-stone-50">
            Equity agent workspace
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-stone-400">
            Live multi-agent analysis with market, news, sentiment, fundamentals, debate, risk, and portfolio decision stages.
          </p>
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <Metric icon={Activity} label="Active" value={runningRuns} tone="teal" />
          <Metric icon={ShieldCheck} label="Complete" value={completedRuns} tone="emerald" />
          <Metric icon={Database} label="Runs" value={runs.length} tone="amber" />
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="min-w-0">
          <div className="mb-3 flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-stone-100">Skills</h3>
              <p className="text-xs text-stone-500">Registered execution graphs</p>
            </div>
            <BarChart3 className="h-4 w-4 text-stone-500" />
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            {skillsQuery.isLoading && (
              <p className="text-sm text-stone-400">Loading skills...</p>
            )}
            {skillsQuery.data?.map((skill) => (
              <SkillCard key={skill.id} skill={skill} />
            ))}
            {skillsQuery.error && (
              <p className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
                Failed to load skills: {(skillsQuery.error as Error).message}
              </p>
            )}
          </div>
        </section>

        <section className="min-w-0">
          <div className="mb-3">
            <h3 className="text-sm font-semibold text-stone-100">Recent Runs</h3>
            <p className="text-xs text-stone-500">Execution ledger</p>
          </div>
          <RecentRuns runs={runs} loading={runsQuery.isLoading} />
        </section>
      </div>
    </div>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: typeof Activity;
  label: string;
  value: number;
  tone: "teal" | "emerald" | "amber";
}) {
  const tones = {
    teal: "text-teal-300",
    emerald: "text-emerald-300",
    amber: "text-amber-300",
  };
  return (
    <div className="min-w-24 rounded-lg border border-stone-800 bg-stone-900 px-3 py-2">
      <div className={`mb-1 flex items-center gap-1.5 ${tones[tone]}`}>
        <Icon className="h-3.5 w-3.5" />
        <span>{label}</span>
      </div>
      <div className="font-mono text-lg font-semibold text-stone-50">{value}</div>
    </div>
  );
}
