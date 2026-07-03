import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CalendarDays, Play, RefreshCw } from "lucide-react";
import { listRuns } from "../../api/client";

export default function Watchlist() {
  const navigate = useNavigate();
  const runsQuery = useQuery({
    queryKey: ["runs", "watchlist"],
    queryFn: () => listRuns(50),
    refetchInterval: 5000,
  });
  const [limit, setLimit] = useState("5");
  const [starting, setStarting] = useState(false);

  const runs = (runsQuery.data ?? []).filter((run) => run.skill_id === "daily_pipeline");

  const startDailyPipeline = async () => {
    setStarting(true);
    try {
      navigate("/chat", {
        state: {
          prompt: `每日选股 top ${Number(limit)}`,
          autoSend: true,
        },
      });
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <div className="flex flex-col justify-between gap-4 border-b border-stone-800 pb-5 lg:flex-row lg:items-end">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-teal-300">
            Watchlist
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-stone-50">Daily A-share queue</h2>
        </div>
        <div className="flex items-end gap-2">
          <label>
            <span className="mb-1 block text-xs text-stone-400">Top N</span>
            <input
              type="number"
              min={1}
              max={20}
              value={limit}
              onChange={(event) => setLimit(event.target.value)}
              className="w-24 rounded-lg border border-stone-700 bg-stone-950 px-3 py-2 text-sm text-stone-100 outline-none transition focus:border-teal-400"
            />
          </label>
          <button
            onClick={startDailyPipeline}
            disabled={starting}
            className="inline-flex items-center gap-2 rounded-lg bg-teal-500 px-4 py-2 text-sm font-semibold text-stone-950 transition hover:bg-teal-400 disabled:opacity-50"
          >
            <Play className="h-4 w-4" />
            {starting ? "Starting..." : "Run Daily Pipeline"}
          </button>
        </div>
      </div>

      <section className="rounded-lg border border-stone-800 bg-stone-900 p-4">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2 text-stone-100">
            <CalendarDays className="h-4 w-4 text-teal-300" />
            <h3 className="text-sm font-semibold">Recent DailyPipeline Runs</h3>
          </div>
          <button
            onClick={() => runsQuery.refetch()}
            className="rounded border border-stone-700 p-2 text-stone-400 hover:bg-stone-800 hover:text-stone-100"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>
        {runsQuery.isLoading ? (
          <p className="text-sm text-stone-400">Loading runs...</p>
        ) : runs.length === 0 ? (
          <p className="text-sm text-stone-500">No daily pipeline runs yet.</p>
        ) : (
          <div className="grid gap-2">
            {runs.map((run) => (
              <button
                key={run.id}
                onClick={() => navigate(`/analysis/${run.id}`)}
                className="flex items-center justify-between rounded-lg border border-stone-800 bg-stone-950 px-3 py-2 text-left transition hover:border-teal-500/60"
              >
                <div>
                  <div className="font-mono text-sm text-stone-100">{run.id.slice(0, 8)}</div>
                  <div className="text-xs text-stone-500">{new Date(run.created_at).toLocaleString()}</div>
                </div>
                <span className={statusClass(run.status)}>{run.status}</span>
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function statusClass(status: string) {
  const base = "rounded px-2 py-1 text-xs font-medium";
  if (status === "completed") return `${base} bg-emerald-500/10 text-emerald-300`;
  if (status === "failed") return `${base} bg-red-500/10 text-red-300`;
  if (status === "running") return `${base} bg-teal-500/10 text-teal-300`;
  return `${base} bg-stone-800 text-stone-300`;
}
