/**
 * Chat WebSocket message handling hook.
 * Extracted from Chat/index.tsx for separation of concerns.
 */

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { chatWsManager, type WSMessage } from "@/api/ws";
import { useChatStore } from "@/stores/useChatStore";
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
    const offOpen = chatWsManager.onOpen(() => setConnected(true));
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
        const count = (message.payload.candidates as unknown[] | undefined)?.length ?? 0;
        addTaskStep(message.run_id, {
          label: "完成候选池筛选",
          detail: `找到 ${count} 个候选标的`,
          status: "completed",
        });
        appendTaskResult(message.run_id, JSON.stringify({ __type: "candidates", data: message.payload.candidates }));
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
        addTaskStep(message.run_id, {
          label: "技能执行完成",
          status: "completed",
        });
        setRunning(false);
        setCurrentRunId(null);
        finishTask(message.run_id, "completed");
      } else if (message.type === "run_complete") {
        setRunning(false);
        setCurrentRunId(null);
        finishTask(message.run_id, "completed");
        // A run just finished — refresh the Dashboard's runs/artifacts/holdings
        // views on demand instead of waiting for their polling intervals.
        queryClient.invalidateQueries({ queryKey: ["runs"] });
        queryClient.invalidateQueries({ queryKey: ["dashboard-artifacts"] });
        queryClient.invalidateQueries({ queryKey: ["holdings"] });
      } else if (message.type === "error") {
        setRunning(false);
        setCurrentRunId(null);
        finishTask(message.run_id, "failed", String(message.payload.message ?? "Run failed"));
        queryClient.invalidateQueries({ queryKey: ["runs"] });
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
