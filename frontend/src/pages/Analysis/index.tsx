import { useEffect } from "react";
import { Link, useParams } from "react-router-dom";
import { useRunStore } from "@/stores/useRunStore";
import { wsManager } from "@/api/ws";
import { type WSMessage } from "@/api/ws";
import { cancelRun as cancelRunRequest } from "@/api/client";
import { AgentGraph } from "./AgentGraph";
import { ReportPanel } from "./ReportPanel";
import { ProgressTracker } from "./ProgressTracker";
import { CircleSlash2, Layers3, RadioTower, ScrollText } from "lucide-react";

import { ActivityTimeline, taskActivities } from "@/pages/AgentWorkspace/ActivityTimeline";

export default function Analysis() {
  const { runId } = useParams<{ runId: string }>();
  const {
    status,
    agentStatuses,
    reportSections,
    toolCalls,
    progressEvents,
    addProgressEvent,
    error,
    startRun,
    updateAgentStatus,
    updateReportSection,
    addToolCall,
    completeRun,
    cancelRun,
    failRun,
  } = useRunStore();

  useEffect(() => {
    if (!runId) return;

    startRun(runId);

    const relay = (msg: WSMessage) => addProgressEvent({
      task_id: runId, seq: 0, event_type: "skill_progress", created_at: msg.timestamp,
      payload: { run_id: runId, event_type: msg.type, payload: msg.payload },
    });

    const unsubStatus = wsManager.on("agent_status", (msg: WSMessage) => {
      relay(msg);
      if (!["running", "completed", "failed"].includes(String(msg.payload.status))) return;
      updateAgentStatus({
        agent: msg.payload.agent as string,
        status: msg.payload.status as "running" | "completed" | "failed",
        duration_ms: msg.payload.duration_ms as number | undefined,
      });
    });

    const unsubReport = wsManager.on("report_chunk", (msg: WSMessage) => {
      updateReportSection({
        section: msg.payload.section as string,
        content: msg.payload.content as string,
        is_final: msg.payload.is_final as boolean,
      });
    });

    const unsubReportComplete = wsManager.on("report_complete", (msg: WSMessage) => {
      const sections = msg.payload.sections as Record<string, string>;
      for (const [key, content] of Object.entries(sections)) {
        updateReportSection({
          section: key,
          content,
          is_final: key === "final_trade_decision",
        });
      }
    });

    const unsubProgress = wsManager.on("skill_progress", relay);
    const unsubRuntime = wsManager.on("agent_runtime", (msg: WSMessage) => addProgressEvent({
      task_id: runId, seq: 0, event_type: "agent_runtime", payload: msg.payload, created_at: msg.timestamp,
    }));
    const unsubLegacyProgress = wsManager.on("progress_update", relay);

    const unsubTool = wsManager.on("tool_call", (msg: WSMessage) => {
      relay(msg);
      addToolCall({
        activity_id: msg.payload.activity_id as string | undefined,
        status: msg.payload.status as string | undefined,
        tool: msg.payload.tool as string,
        args: msg.payload.args as Record<string, unknown>,
        timestamp: msg.timestamp,
      });
    });

    const unsubRunComplete = wsManager.on("run_complete", () => {
      completeRun();
    });

    const unsubCancelled = wsManager.on("run_cancelled", () => {
      cancelRun();
    });

    const unsubError = wsManager.on("error", (msg: WSMessage) => {
      failRun(msg.payload.message as string);
    });

    // Register every handler before connecting: the server replays persisted
    // events immediately after the handshake, so connecting first can drop the
    // opening status/report frames on fast local or reconnected sessions.
    wsManager.connect(runId);

    return () => {
      unsubStatus();
      unsubProgress();
      unsubRuntime();
      unsubLegacyProgress();
      unsubReport();
      unsubReportComplete();
      unsubTool();
      unsubRunComplete();
      unsubCancelled();
      unsubError();
      wsManager.disconnect();
    };
  }, [
    addToolCall,
    addProgressEvent,
    cancelRun,
    completeRun,
    failRun,
    runId,
    startRun,
    updateAgentStatus,
    updateReportSection,
  ]);

  if (!runId) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-ui-faint">
          从工作台开始分析后，可在这里查看执行过程。
        </p>
      </div>
    );
  }

  const completedAgents = Object.values(agentStatuses).filter(
    (agent) => agent.status === "completed"
  ).length;
  const sectionCount = Object.keys(reportSections).length;

  const handleCancel = async () => {
    if (!runId || status !== "running") return;
    await cancelRunRequest(runId);
    cancelRun();
  };

  return (
    <div className="mx-auto flex h-full max-w-7xl flex-col gap-4">
      <div className="flex flex-col justify-between gap-3 border-b border-ui-line pb-4 lg:flex-row lg:items-center">
        <div>
          <p className="font-mono text-xs text-ui-faint">Run {runId.slice(0, 8)}</p>
          <h2 className="mt-1 text-xl font-semibold text-ui-ink">Agent 分析过程</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Kpi icon={Layers3} label="已完成 Agent" value={completedAgents} />
          <Kpi icon={ScrollText} label="报告章节" value={sectionCount} />
          <Kpi icon={RadioTower} label="数据查询" value={toolCalls.length} />
          <Link
            to={`/library?run_id=${runId}`}
            className="inline-flex items-center gap-2 rounded-lg border border-ui-accent/30 bg-ui-accent/10 px-3 py-2 text-xs font-medium text-ui-accent transition hover:border-ui-accent/60"
          >
            <ScrollText className="h-4 w-4" />
            研究产物
          </Link>
          <button
            onClick={handleCancel}
            disabled={status !== "running"}
            className="inline-flex items-center gap-2 rounded-lg border border-ui-strong px-3 py-2 text-xs font-medium text-ui-body transition hover:bg-ui-hover disabled:cursor-not-allowed disabled:opacity-45"
          >
            <CircleSlash2 className="h-4 w-4" />
            Cancel
          </button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto">
          <section className="shrink-0 rounded-lg border border-ui-line bg-ui-panel p-4">
          <h3 className="mb-3 text-sm font-semibold text-ui-body">分析流程</h3>
          <AgentGraph agentStatuses={agentStatuses} />
          </section>
          <section className="rounded-lg border border-ui-line bg-ui-panel p-4">
          <ProgressTracker status={status} error={error} />
          <div className="mt-3"><ActivityTimeline activities={taskActivities({
            id: runId, conversation_id: "", goal: "", status, result: {}, error,
            created_at: "", updated_at: "", evidence: [], events: progressEvents,
          })} /></div>
          </section>
        </div>

        <section className="min-h-0 rounded-lg border border-ui-line bg-ui-panel p-4">
          <ReportPanel sections={reportSections} toolCalls={toolCalls} />
        </section>
      </div>
    </div>
  );
}

function Kpi({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Layers3;
  label: string;
  value: number;
}) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-ui-line bg-ui-panel px-3 py-2 text-xs">
      <Icon className="h-4 w-4 text-ui-accent" />
      <span className="text-ui-faint">{label}</span>
      <span className="font-mono font-semibold text-ui-ink">{value}</span>
    </div>
  );
}
