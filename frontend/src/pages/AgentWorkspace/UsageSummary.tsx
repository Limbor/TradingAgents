import type { UsageStats } from "@/api/agent";

const number = new Intl.NumberFormat("zh-CN");
const roles: Record<string, string> = {
  "Trading Coordinator": "任务协调", "Evidence Writer": "回复整理",
  "Market Analyst": "技术面分析", "Fundamentals Analyst": "基本面分析",
  "News Analyst": "新闻分析", "Sentiment Analyst": "情绪分析",
  "Bull Researcher": "多方研究", "Bear Researcher": "空方研究",
  "Research Manager": "研究裁决", Trader: "交易评估",
  "Aggressive Analyst": "激进风控", "Conservative Analyst": "保守风控",
  "Neutral Analyst": "中性风控", "Portfolio Manager": "最终裁决",
};
const money = (amount: number | null) => amount === null ? "暂无" : amount > 0 && amount < 0.001
  ? "<¥0.001" : `¥${amount.toFixed(3)}`;

export function UsageSummary({ usage, conversation = false }: { usage?: UsageStats; conversation?: boolean }) {
  if (!usage?.model_calls) return null;
  const label = conversation ? "会话累计用量" : "本轮用量";
  return <details aria-label={label} className="mt-3 text-[11px] leading-5 text-ui-muted">
    <summary aria-label={`展开${label}明细`} className="cursor-pointer rounded py-1 marker:text-ui-faint hover:text-ui-body">
      {conversation && <span className="mr-2 font-medium">会话累计</span>}
      {usage.reported_calls ? <span className="inline-flex flex-wrap gap-x-3 tabular-nums">
        <span className="whitespace-nowrap">输入 {number.format(usage.input_tokens)}</span>
        <span className="whitespace-nowrap">输出 {number.format(usage.output_tokens)}</span>
        <span className="whitespace-nowrap">缓存 {number.format(usage.cache_read_tokens)}{usage.cache_unknown_calls ? "（部分）" : ""}</span>
        <span className="whitespace-nowrap">{usage.cost_complete ? "估价" : "已知估价"} {money(usage.cost_cny)}</span>
      </span> : <span>{usage.pending_calls ? "等待模型返回用量" : "模型未返回用量"}</span>}
      {usage.incomplete && <span className="ml-2 whitespace-nowrap text-ui-warning">统计不完整</span>}
      {!!usage.pending_calls && <span className="ml-2 whitespace-nowrap">累计中</span>}
    </summary>
    <div className="mt-2 space-y-3 rounded-md border border-ui-line bg-ui-subtle p-3">
      <p>{usage.reported_calls}/{usage.model_calls} 次调用有用量记录{usage.cache_hit_rate !== null ? ` · 缓存命中 ${(usage.cache_hit_rate * 100).toFixed(1)}%` : ""}</p>
      <div className="space-y-3">{usage.groups.map((group) => <div key={`${group.role}:${group.provider}:${group.model}`}>
        <div className="flex flex-wrap items-baseline justify-between gap-x-3"><span className="text-ui-body">{roles[group.role] ?? group.role}</span><span className="tabular-nums">{group.model_calls} 次 · {money(group.unpriced_calls >= group.reported_calls ? null : group.cost_cny)}</span></div>
        <p className="truncate text-ui-faint" title={group.model}>{group.model}</p>
        <div className="flex flex-wrap gap-x-3 tabular-nums"><span className="whitespace-nowrap">输入 {number.format(group.input_tokens)}</span><span className="whitespace-nowrap">输出 {number.format(group.output_tokens)}</span><span className="whitespace-nowrap">缓存 {number.format(group.cache_read_tokens)}{group.cache_unknown_calls ? "（部分）" : ""}</span></div>
        {!!group.cache_creation_tokens && <p>缓存创建 {number.format(group.cache_creation_tokens)}</p>}
        {!!group.reasoning_tokens && <p>思考 {number.format(group.reasoning_tokens)}（已包含在输出中）</p>}
        {!!group.missing_calls && <p className="text-ui-warning">{group.missing_calls} 次调用未返回用量</p>}
        {!!group.unpriced_calls && <p>{group.unpriced_calls} 次调用缺少价格或缓存明细，未计入估价</p>}
      </div>)}</div>
      <p className="border-t border-ui-line pt-2 text-ui-faint">缓存已包含在输入中。思考明细未返回时不推定为零。估价仅含已记录的模型费用，免费额度、优惠和实际扣款以平台账单为准。价格基准 {usage.price_date}。</p>
    </div>
  </details>;
}
