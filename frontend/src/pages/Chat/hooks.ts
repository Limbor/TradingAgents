/**
 * Chat WebSocket message handling hook.
 * Extracted from Chat/index.tsx for separation of concerns.
 */

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { chatWsManager, type WSMessage } from "@/api/ws";
import { getRun } from "@/api/client";
import { useChatStore, type ChatMessage } from "@/stores/useChatStore";
import {
  eventStepLabel,
  formatParams,
  formatPayloadBrief,
  normalizeTaskStatus,
  progressStepDetail,
  progressStepLabel,
  skillTitle,
} from "@/utils/eventLabels";

/**
 * Hook that manages the WebSocket connection lifecycle and dispatches
 * incoming messages to the chat store.
 */
export function useChatWebSocket() {
  const queryClient = useQueryClient();
  const {
    setConnected,
    setRunning,
    setCurrentRunId,
    addMessage,
    createTask,
    addTaskStep,
    appendTaskResult,
    finishTask,
  } = useChatStore();

  useEffect(() => {
    const offOpen = chatWsManager.onOpen(async () => {
      setConnected(true);
      // Reconnect recovery: if a run was in-flight when the socket dropped,
      // it may have finished during the disconnect. The chat WS does not
      // replay events for an existing run, so poll the run's status via REST
      // and finalize the task card if it is already terminal.
      const { currentRunId } = useChatStore.getState();
      if (!currentRunId) return;
      try {
        const run = await getRun(currentRunId);
        const status = String(run.status || "").toLowerCase();
        if (status === "completed") {
          setRunning(false);
          setCurrentRunId(null);
          finishTask(currentRunId, "completed");
          queryClient.invalidateQueries({ queryKey: ["runs"] });
        } else if (status === "failed" || status === "cancelled") {
          setRunning(false);
          setCurrentRunId(null);
          finishTask(currentRunId, "failed", status === "cancelled" ? "任务已取消" : "任务失败");
          queryClient.invalidateQueries({ queryKey: ["runs"] });
        }
        // If still "running", leave it — the run continues server-side; the
        // task card stays open but won't get more chat-WS events. The user
        // can view progress on the Analysis page or Dashboard.
      } catch {
        // REST failed (auth/network) — leave state as-is; user can retry.
      }
    });
    const offClose = chatWsManager.onClose(() => setConnected(false));
    const offMessage = chatWsManager.onMessage((message: WSMessage) => {
      if (message.type === "chat_reply") {
        const payload = message.payload;
        const runId = payload.run_id as string | undefined;
        setRunning(Boolean(runId));
        setCurrentRunId(runId ?? null);
        if (runId) {
          createTask({
            runId,
            skillId: payload.skill_triggered as string | undefined,
            title: skillTitle(payload.skill_triggered as string | undefined),
            detail: payload.reason as string | undefined,
          });
          addTaskStep(runId, {
            label: "准备执行",
            detail: formatParams(payload.params),
            status: "running",
          });
        } else {
          addMessage({
            role: "assistant",
            content: String(payload.content ?? "我还没有找到合适的技能。"),
          });
        }
      } else if (message.type === "chat_answer") {
        const payload = message.payload;
        addMessage({
          role: "assistant",
          kind: "text",
          content: String(payload.content ?? ""),
          citations: payload.citations as ChatMessage["citations"] | undefined,
        });
      } else if (message.type === "tool_answer") {
        const payload = message.payload;
        useChatStore.getState().addToolMessage({
          content: String(payload.content ?? ""),
          tool: String(payload.tool ?? "unknown"),
          args: (payload.args as Record<string, unknown>) ?? {},
          result: payload.result,
          display: String(payload.display ?? "text"),
          citations: payload.citations as ChatMessage["citations"] | undefined,
        });
      } else if (message.type === "clarify") {
        const payload = message.payload;
        useChatStore.getState().addClarifyMessage({
          content: String(payload.question ?? "请问您需要什么帮助？"),
          options: Array.isArray(payload.options) ? (payload.options as string[]) : undefined,
        });
      } else if (message.type === "skill_progress" || message.type === "progress_update") {
        addTaskStep(message.run_id, {
          label: progressStepLabel(message.payload),
          detail: progressStepDetail(message.payload),
          status: normalizeTaskStatus(message.payload.status),
        });
      } else if (message.type === "report_chunk") {
        addTaskStep(message.run_id, {
          label: eventStepLabel(message),
          detail: "已生成一段可读结论",
          status: "completed",
        });
        appendTaskResult(message.run_id, String(message.payload.content ?? ""));
      } else if (message.type === "scanner_candidates") {
        const candidates = (message.payload.candidates as unknown[] | undefined) ?? [];
        const count = candidates.length;
        const warnings = (message.payload.warnings as string[] | undefined) ?? undefined;
        const asOfDate = message.payload.as_of_date ? String(message.payload.as_of_date) : undefined;
        const dataWindowNote = message.payload.data_window_note
          ? String(message.payload.data_window_note)
          : undefined;
        const sessionState = message.payload.session_state
          ? String(message.payload.session_state)
          : undefined;
        addTaskStep(message.run_id, {
          label: "完成候选池筛选",
          detail: `找到 ${count} 个候选标的${count === 0 ? "（无候选）" : ""}`,
          status: count === 0 ? "failed" : "completed",
        });
        appendTaskResult(
          message.run_id,
          JSON.stringify({
            __type: "candidates",
            data: candidates,
            warnings,
            asOfDate,
            dataWindowNote,
            sessionState,
          }),
        );
      } else if (message.type === "daily_pipeline_candidates") {
        const rows =
          (message.payload.reviewed_candidates as unknown[] | undefined) ??
          (message.payload.candidates as unknown[] | undefined) ??
          (message.payload.decision_pack as unknown[] | undefined);
        const count = (rows as unknown[] | undefined)?.length ?? 0;
        const warnings = (message.payload.warnings as string[] | undefined) ?? undefined;
        addTaskStep(message.run_id, {
          label: "完成每日选股打分",
          detail: `输出 ${count} 个候选标的${count === 0 ? "（无候选）" : ""}`,
          status: count === 0 ? "failed" : "completed",
        });
        appendTaskResult(
          message.run_id,
          JSON.stringify({ __type: "candidates", data: rows, warnings }),
        );
      } else if (message.type === "risk_monitor_results") {
        const rows = message.payload.risks as unknown[] | undefined;
        addTaskStep(message.run_id, {
          label: "完成持仓风险扫描",
          detail: `检查 ${rows?.length ?? 0} 个持仓风险项`,
          status: "completed",
        });
        appendTaskResult(message.run_id, JSON.stringify({ __type: "risks", data: rows }));
      } else if (message.type === "portfolio_update") {
        addTaskStep(message.run_id, {
          label: "更新持仓数据",
          detail: JSON.stringify(message.payload),
          status: "completed",
        });
      } else if (message.type === "skill_start") {
        addTaskStep(message.run_id, {
          label: "技能开始执行",
          status: "completed",
        });
      } else if (message.type === "skill_complete") {
        const payload = message.payload || {};
        // Capture the structured conclusion (e.g. stock_analysis trade plan) so
        // the frontend can render a plan card instead of regex-scraping the text.
        const structured = payload.structured_conclusion;
        if (structured && typeof structured === "object") {
          appendTaskResult(
            message.run_id,
            JSON.stringify({
              __type: "analysis_summary",
              data: structured,
              selectionContext: payload.selection_context ?? undefined,
              artifactId: typeof payload.artifact_id === "string" ? payload.artifact_id : undefined,
            }),
          );
        }
        addTaskStep(message.run_id, {
          label: "技能执行完成",
          status: "completed",
        });
        // Guard: only clear running state if this is still the active run.
        // skill_complete and run_complete are emitted back-to-back; without
        // this guard, a late terminal event from an old run can clobber a
        // newly-started run's running state.
        if (useChatStore.getState().currentRunId === message.run_id) {
          setRunning(false);
          setCurrentRunId(null);
        }
        finishTask(message.run_id, "completed");
      } else if (message.type === "run_complete") {
        if (useChatStore.getState().currentRunId === message.run_id) {
          setRunning(false);
          setCurrentRunId(null);
        }
        finishTask(message.run_id, "completed");
        // A run just finished — refresh the Dashboard's runs/artifacts/holdings
        // views on demand instead of waiting for their polling intervals.
        queryClient.invalidateQueries({ queryKey: ["runs"] });
        queryClient.invalidateQueries({ queryKey: ["dashboard-artifacts"] });
        queryClient.invalidateQueries({ queryKey: ["holdings"] });
      } else if (message.type === "run_cancelled") {
        // Terminal event: the run was cancelled (via Analysis page, scheduler,
        // or server restart). Without this branch the event fell into the
        // generic fallback, leaving running=true forever and the input locked.
        if (useChatStore.getState().currentRunId === message.run_id) {
          setRunning(false);
          setCurrentRunId(null);
        }
        finishTask(message.run_id, "failed", "任务已取消");
        queryClient.invalidateQueries({ queryKey: ["runs"] });
      } else if (message.type === "error") {
        if (useChatStore.getState().currentRunId === message.run_id) {
          setRunning(false);
          setCurrentRunId(null);
        }
        finishTask(message.run_id, "failed", String(message.payload.message ?? "Run failed"));
        queryClient.invalidateQueries({ queryKey: ["runs"] });
      } else if (message.type === "run_cancellation_ack") {
        // Acknowledgement of a cancel action — no UI state change needed; the
        // subsequent run_cancelled event will clear running state.
        return;
      } else if (message.type === "heartbeat") {
        return;
      } else if (message.run_id) {
        addTaskStep(message.run_id, {
          label: eventStepLabel(message),
          detail: formatPayloadBrief(message.payload),
          status: "running",
        });
      }
    });

    chatWsManager.connect();
    if (chatWsManager.isOpen()) setConnected(true);

    return () => {
      offOpen();
      offClose();
      offMessage();
    };
  }, [
    addMessage,
    addTaskStep,
    appendTaskResult,
    createTask,
    finishTask,
    queryClient,
    setConnected,
    setCurrentRunId,
    setRunning,
  ]);
}
