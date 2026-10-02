import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Database, FileCheck2, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { getPaperStatus } from "@/api/paper";
import type { ChatMessage } from "@/stores/useChatStore";

interface CaseInspectorProps {
  messages: ChatMessage[];
  paperSessionId?: string | null;
  running: boolean;
}

export function CaseInspector({ messages, paperSessionId, running }: CaseInspectorProps) {
  const latestUser = [...messages].reverse().find((message) => message.role === "user");
  const latestTask = [...messages].reverse().find((message) => message.kind === "task");
  const latestEvidence = [...messages].reverse().find((message) => message.citations?.length);
  const citations = latestEvidence?.citations ?? [];
  const paper = useQuery({
    queryKey: ["agent-paper-inspector", paperSessionId],
    queryFn: () => getPaperStatus(paperSessionId!),
    enabled: Boolean(paperSessionId),
    retry: false,
  });
  const snapshot = paper.data?.snapshot;

  return (
    <aside aria-label="任务证据与方案" className="flex min-h-0 flex-col rounded-2xl border border-ui-line bg-ui-panel/80">
      <div className="flex items-center justify-between border-b border-ui-line px-4 py-4">
        <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-ui-accent">Case file</p><h3 className="mt-1 font-semibold">任务档案</h3></div>
        <span className={`rounded-full px-2.5 py-1 text-xs ${running ? "bg-ui-warning/10 text-ui-warning" : "bg-ui-accent/10 text-ui-accent"}`}>{running ? "分析中" : "待命"}</span>
      </div>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
        <section>
          <h4 className="text-xs font-medium text-ui-body">当前目标</h4>
          <p className="mt-2 rounded-xl border border-ui-line bg-ui-subtle/60 p-3 text-sm leading-6 text-ui-body">{latestUser?.content ?? "输入交易问题后，在这里跟踪目标与证据。"}</p>
          {paperSessionId && <p className="mt-2 break-all text-xs text-ui-accent">模拟盘会话 · {paperSessionId}</p>}
        </section>
        {latestTask && <section className="border-t border-ui-line pt-4">
          <h4 className="text-xs font-medium text-ui-body">执行计划</h4>
          <ol className="mt-3 space-y-3">{(latestTask.steps ?? []).slice(-6).map((step) => <li key={step.id} className="flex gap-2.5 text-xs"><span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${step.status === "failed" ? "bg-ui-danger" : step.status === "running" ? "bg-ui-warning" : "bg-ui-accent"}`} /><span><strong className="block font-medium text-ui-body">{step.label}</strong>{step.detail && <span className="mt-0.5 block text-ui-faint">{step.detail}</span>}</span></li>)}</ol>
        </section>}
        <section className="border-t border-ui-line pt-4">
          <div className="flex items-center justify-between"><h4 className="text-xs font-medium text-ui-body">证据快照</h4><span className="text-xs text-ui-faint">{citations.length} 项</span></div>
          {citations.length ? <div className="mt-3 space-y-2">{citations.map((item, index) => <div key={`${item.tool}-${index}`} className="rounded-xl border border-ui-line bg-ui-subtle/50 p-3"><div className="flex items-center gap-2 text-xs font-medium text-ui-body"><Database className="h-3.5 w-3.5 text-ui-accent" />{item.summary || item.tool}</div><p className="mt-1 text-xs text-ui-faint">{item.source || item.tool}{item.as_of_date ? ` · ${item.as_of_date}` : ""}</p>{item.warnings?.length ? <p className="mt-2 text-xs text-ui-warning">{item.warnings.join("；")}</p> : null}</div>)}</div> : <p className="mt-2 text-xs leading-5 text-ui-faint">本轮还没有可引用的数据。Agent 回答会标出实际调用的来源。</p>}
        </section>
        {paperSessionId && <section className="border-t border-ui-line pt-4">
          <h4 className="text-xs font-medium text-ui-body">模拟盘账本</h4>
          {paper.isError ? <p className="mt-2 text-xs text-ui-warning">账本暂时不可用，请检查 StockManager 连接。</p> : <div className="mt-2 space-y-2 text-xs text-ui-muted"><div className="flex justify-between"><span>基准日</span><strong className="text-ui-body">{snapshot?.as_of_date ?? "尚未推进"}</strong></div><div className="flex justify-between"><span>账户权益</span><strong className="text-ui-body">{snapshot?.equity == null ? "—" : `¥${snapshot.equity.toLocaleString("zh-CN")}`}</strong></div><div className="flex justify-between"><span>成交笔数</span><strong className="text-ui-body">{paper.data?.trades_count ?? "—"}</strong></div></div>}
          <Link className="mt-3 inline-flex items-center gap-1 text-xs text-ui-accent hover:text-ui-accent" to={`/paper?session=${encodeURIComponent(paperSessionId)}`}>打开完整账本 <ArrowUpRight className="h-3.5 w-3.5" /></Link>
        </section>}
        <section className="border-t border-ui-line pt-4">
          <div className="flex items-start gap-2"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-ui-accent" /><p className="text-xs leading-5 text-ui-muted">交易判断与策略信号分开呈现；修改持仓或推进模拟盘需要在对应页面确认。</p></div>
        </section>
      </div>
      <div className="flex items-center gap-2 border-t border-ui-line px-4 py-3 text-xs text-ui-faint"><FileCheck2 className="h-3.5 w-3.5" />对话、工具来源与任务结果可追溯</div>
    </aside>
  );
}
