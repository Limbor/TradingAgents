import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Brain } from "lucide-react";
import { getMemoryOverview, getMemoryEvaluationPreview, startMemoryEvaluation, getMemoryEvaluationJob } from "@/api/client";
import { UsageSummary } from "@/pages/AgentWorkspace/UsageSummary";

const percent = (value: number | null | undefined) => value == null ? "—" : `${(value * 100).toFixed(2)}%`;

export default function MemoryOverview() {
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const preview = useQuery({ queryKey: ["memory-evaluation-preview"], queryFn: getMemoryEvaluationPreview, refetchInterval: 15000 });
  const activeId = jobId || preview.data?.active_job_id;
  const job = useQuery({ queryKey: ["memory-evaluation-job", activeId], queryFn: () => getMemoryEvaluationJob(activeId!), enabled: !!activeId,
    refetchInterval: query => query.state.data?.status === "running" ? 3000 : false });
  const start = useMutation({ mutationFn: startMemoryEvaluation, onSuccess: data => {
    setJobId(data.id); void qc.invalidateQueries({ queryKey: ["memory-evaluation-preview"] });
  } });
  const jobStatus = job.data?.status;
  useEffect(() => {
    if (jobStatus && jobStatus !== "running") {
      void qc.invalidateQueries({ queryKey: ["strategy-memory-overview"] });
      void qc.invalidateQueries({ queryKey: ["memory-evaluation-preview"] });
    }
  }, [jobStatus, qc]);
  const query = useQuery({ queryKey: ["strategy-memory-overview"], queryFn: getMemoryOverview, refetchInterval: 15000 });
  const data = query.data;
  const report = data?.evaluation;
  return <section aria-label="记忆使用概览" className="min-w-0 rounded-lg border border-ui-line bg-ui-panel p-4">
    <h3 className="flex items-center gap-2 text-sm font-medium text-ui-ink"><Brain className="h-4 w-4 text-ui-accent" /> 经验如何参与判断</h3>
    <p className="mt-2 text-xs leading-5 text-ui-muted">到期复盘 → 生成候选经验 → 人工批准 → 按问题检索 → 核对适用条件 → 记录参考理由</p>
    {query.isError && <p role="alert" className="mt-3 text-xs text-ui-warning">使用记录暂不可用，请稍后刷新。</p>}
    {query.isPending && <p className="mt-3 text-xs text-ui-faint">正在读取经验和参考记录…</p>}
    {data?.inventory && <>
      <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {[["可用经验", data.inventory.available], ["等待批准", data.inventory.pending], ["已停用", data.inventory.retired], ["已过期", data.inventory.expired]].map(([label, value]) => <div key={label} className="min-w-0"><dt className="whitespace-nowrap text-xs text-ui-faint">{label}</dt><dd className="mt-1 text-xl font-medium tabular-nums text-ui-ink">{value}</dd></div>)}
      </dl>
      <p className="mt-4 border-t border-ui-line pt-3 text-xs leading-5 text-ui-muted">最近 {data.usage.sampled_tasks} 个工作台任务中，{data.usage.injected_tasks} 个提供了经验，{data.usage.reported_tasks} 个返回了具体参考理由。</p>
    </>}
    <details className="mt-3 border-t border-ui-line pt-3 text-xs">
      <summary className="cursor-pointer text-ui-body">有记忆 / 无记忆对照评测{report ? ` · ${report.memory_pairs} 对适用样本` : " · 尚无报告"}</summary>
      <div className="mt-3 space-y-2 leading-6 text-ui-muted">
        <p>可用历史样本 {preview.data?.available_pairs ?? "—"} 对 · 需要至少 20 对。仅使用当时已批准的经验与原始事实。</p>
        <button onClick={() => start.mutate()} disabled={!preview.data?.can_run || start.isPending || job.data?.status === "running"}
          className="rounded border border-ui-line px-3 py-1.5 text-ui-accent disabled:cursor-not-allowed disabled:opacity-50">
          {start.isPending ? "正在准备评测…" : "运行 20 对评测（最多 40 次模型调用）"}
        </button>
        <p className="text-ui-faint">点击后消耗当前统一模型的额度；最多 200,000 token 预算，免费额度和实际扣费以平台为准。不自动重试或批准经验。</p>
        {start.isError && <p role="alert" className="text-ui-warning">{start.error.message}</p>}
        {preview.isError && <p role="alert" className="text-ui-warning">历史样本预检暂不可用。</p>}
        {job.data && <p role="status">{job.data.status === "running" ? "正在比较两组判断" : job.data.status === "completed" ? "对照评测完成" : "对照评测未完成"} · {job.data.completed_pairs}/{job.data.total_pairs} 对{job.data.error && ` · ${job.data.error}`}</p>}
        {job.data?.usage_stats && <UsageSummary usage={job.data.usage_stats} />}
      </div>
      {report ? <div className="mt-3 space-y-3 text-ui-muted">
        <p>{report.model} · {report.total_pairs} 对总样本 · {report.changed_directions} 对判断方向变化</p>
        <div className="overflow-x-auto"><table className="w-full whitespace-nowrap text-left tabular-nums"><thead><tr className="border-b border-ui-line"><th className="py-2 pr-3 font-normal">指标</th><th className="px-3 font-normal">无记忆</th><th className="pl-3 font-normal">有记忆</th></tr></thead><tbody>
          <tr><td className="py-2 pr-3">方向判断覆盖率</td><td className="px-3">{percent(report.arms.without_memory.coverage)}</td><td className="pl-3">{percent(report.arms.with_memory.coverage)}</td></tr>
          <tr><td className="py-2 pr-3">方向判断命中率</td><td className="px-3">{percent(report.arms.without_memory.hit_rate)}</td><td className="pl-3">{percent(report.arms.with_memory.hit_rate)}</td></tr>
        </tbody></table></div>
        {!report.sufficient_samples && <p className="text-ui-warning">适用样本不足 {report.min_samples} 对，暂不判断记忆是否有效。</p>}
        <p>使用相同的信号时点数据做成对比较；结果不等于账户收益，仍需检查样本相关性和模型随机性。</p>
        {report.usage_stats && <UsageSummary usage={report.usage_stats} />}
        <Link to={`/library?artifact_type=memory_evaluation`} className="inline-block text-ui-accent underline underline-offset-2">查看评测报告</Link>
      </div> : <p className="mt-3 leading-5 text-ui-faint">尚未运行成对模型评测。经验参考次数只说明使用情况，不能证明效果提升；评测工具已支持按历史日期比较两组判断。</p>}
    </details>
  </section>;
}
