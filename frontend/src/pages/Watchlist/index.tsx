import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CalendarDays, Play, RefreshCw } from "lucide-react";
import { listRuns } from "../../api/client";
import { queryKeys } from "@/api/queryKeys";
import { dailyPipelineHint, useGoChat } from "@/lib/chatNav";

export default function Watchlist() {
  const navigate = useNavigate();
  const goChat = useGoChat();
  const runsQuery = useQuery({
    queryKey: queryKeys.runsWatchlist(),
    queryFn: () => listRuns(50),
    refetchInterval: 30000,
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });
  const [limit, setLimit] = useState("5");
  const [starting, setStarting] = useState(false);

  const runs = (runsQuery.data ?? []).filter((run) => run.skill_id === "daily_pipeline");

  const startDailyPipeline = async () => {
    setStarting(true);
    try {
      goChat({
        prompt: `每日选股 top ${Number(limit)}`,
        autoSend: true,
        intentHint: dailyPipelineHint(Number(limit)),
      });
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <div className="flex flex-col justify-between gap-4 border-b border-ui-line pb-5 lg:flex-row lg:items-end">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-ui-accent">
            Watchlist
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-ui-ink">Daily A-share queue</h2>
        </div>
        <div className="flex items-end gap-2">
          <label>
            <span className="mb-1 block text-xs text-ui-muted">Top N</span>
            <input
              type="number"
              min={1}
              max={20}
              value={limit}
              onChange={(event) => setLimit(event.target.value)}
              className="w-24 rounded-lg border border-ui-strong bg-ui-subtle px-3 py-2 text-sm text-ui-ink outline-none transition focus:border-ui-accent"
            />
          </label>
          <button
            onClick={startDailyPipeline}
            disabled={starting}
            className="inline-flex items-center gap-2 rounded-lg bg-ui-accent px-4 py-2 text-sm font-semibold text-ui-onAccent transition hover:bg-ui-accent disabled:opacity-50"
          >
            <Play className="h-4 w-4" />
            {starting ? "Starting..." : "Run Daily Pipeline"}
          </button>
        </div>
      </div>

      <section className="rounded-lg border border-ui-line bg-ui-panel p-4">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2 text-ui-ink">
            <CalendarDays className="h-4 w-4 text-ui-accent" />
            <h3 className="text-sm font-semibold">Recent DailyPipeline Runs</h3>
          </div>
          <button
            onClick={() => runsQuery.refetch()}
            className="rounded border border-ui-strong p-2 text-ui-muted hover:bg-ui-hover hover:text-ui-ink"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>
        {runsQuery.isLoading ? (
          <p className="text-sm text-ui-muted">Loading runs...</p>
        ) : runs.length === 0 ? (
          <p className="text-sm text-ui-faint">No daily pipeline runs yet.</p>
        ) : (
          <div className="grid gap-2">
            {runs.map((run) => (
              <button
                key={run.id}
                onClick={() => navigate(run.status === "completed" ? `/library?run_id=${run.id}` : `/analysis/${run.id}`)}
                className="flex items-center justify-between rounded-lg border border-ui-line bg-ui-subtle px-3 py-2 text-left transition hover:border-ui-accent/60"
              >
                <div>
                  <div className="font-mono text-sm text-ui-ink">{run.id.slice(0, 8)}</div>
                  <div className="text-xs text-ui-faint">{new Date(run.created_at).toLocaleString()}</div>
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
  if (status === "completed") return `${base} bg-ui-success/10 text-ui-success`;
  if (status === "failed") return `${base} bg-ui-danger/10 text-ui-danger`;
  if (status === "running") return `${base} bg-ui-accent/10 text-ui-accent`;
  return `${base} bg-ui-hover text-ui-body`;
}
