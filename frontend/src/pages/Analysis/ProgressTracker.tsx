interface ProgressTrackerProps {
  status: 'idle' | 'running' | 'completed' | 'failed' | 'cancelled';
  error: string | null;
}
const labels = { idle: '等待开始', running: '正在分析', completed: '分析已完成', failed: '分析失败', cancelled: '已取消' };
export function ProgressTracker({ status, error }: ProgressTrackerProps) {
  return <div className="space-y-3">
    <div className="flex items-center justify-between gap-2">
      <h3 className="text-sm font-semibold text-ui-body">执行状态</h3>
      <span className="text-xs text-ui-muted">{labels[status]}</span>
    </div>
    {status === 'running' && <p className="text-xs leading-5 text-ui-faint">下方实时显示各个分析师与数据查询的进度。</p>}
    {error && <p role="alert" className="rounded-lg border border-ui-danger/30 bg-ui-danger/10 p-2 text-xs text-ui-danger">{error}</p>}
  </div>;
}
