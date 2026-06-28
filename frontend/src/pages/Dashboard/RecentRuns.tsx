import { Link } from "react-router-dom";
import { type RunResponse } from "../../api/client";
import { formatRelativeTime } from "../../lib/utils";
import { ArrowUpRight, Clock3 } from "lucide-react";

interface RecentRunsProps {
  runs: RunResponse[];
  loading: boolean;
}

const statusColors: Record<string, string> = {
  pending: "bg-amber-500/15 text-amber-300 ring-amber-500/20",
  running: "bg-teal-500/15 text-teal-300 ring-teal-500/20",
  completed: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/20",
  failed: "bg-red-500/15 text-red-300 ring-red-500/20",
  cancelled: "bg-stone-500/15 text-stone-300 ring-stone-500/20",
};

export function RecentRuns({ runs, loading }: RecentRunsProps) {
  if (loading) return <p className="text-sm text-stone-400">Loading...</p>;
  if (runs.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-stone-700 bg-stone-900 p-4 text-sm text-stone-500">
        No runs yet
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-stone-800 bg-stone-900">
      <table className="w-full text-sm">
        <thead className="bg-stone-950">
          <tr>
            <th className="px-3 py-3 text-left text-xs font-medium uppercase tracking-wide text-stone-500">Skill</th>
            <th className="px-3 py-3 text-left text-xs font-medium uppercase tracking-wide text-stone-500">Status</th>
            <th className="px-3 py-3 text-left text-xs font-medium uppercase tracking-wide text-stone-500">Time</th>
            <th className="px-3 py-3 text-right text-xs font-medium uppercase tracking-wide text-stone-500">Open</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-stone-800">
          {runs.map((run) => (
            <tr key={run.id} className="transition hover:bg-stone-800/60">
              <td className="px-3 py-3 font-mono text-xs text-stone-200">{run.skill_id}</td>
              <td className="px-3 py-3">
                <span
                  className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs ring-1 ${statusColors[run.status] ?? ""}`}
                >
                  {run.status}
                </span>
              </td>
              <td className="px-3 py-3 text-stone-400">
                <span className="flex items-center gap-1.5">
                  <Clock3 className="h-3.5 w-3.5 text-stone-600" />
                  {formatRelativeTime(run.created_at)}
                </span>
              </td>
              <td className="px-3 py-3 text-right">
                <Link
                  to={`/analysis/${run.id}`}
                  className="inline-flex items-center justify-end rounded-md p-1.5 text-teal-300 transition hover:bg-teal-500/10 hover:text-teal-200"
                  title="Open run"
                >
                  <ArrowUpRight className="h-4 w-4" />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
