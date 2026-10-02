/** Plain-language labels shared by the workbench and live analysis. */
export const AGENT_ROLES: Record<string, string> = {
  'Trading Coordinator': '交易助手', 'Task Planner': '任务规划', 'Evidence Writer': '结论整理',
  'Candidate Reviewer': '候选复核', 'Market Researcher': '市场研究', 'Reflection Agent': '复盘分析', 'Pattern Researcher': '经验研究',
  'Market Analyst': '行情分析师', 'Sentiment Analyst': '情绪分析师',
  'News Analyst': '新闻分析师', 'Fundamentals Analyst': '基本面分析师',
  'Bull Researcher': '多方研究员', 'Bear Researcher': '空方研究员',
  'Research Manager': '研究经理', Trader: '交易规划师',
  'Aggressive Analyst': '进攻风控', 'Conservative Analyst': '防守风控',
  'Neutral Analyst': '综合风控', 'Portfolio Manager': '组合经理',
  'Market Overview': '市场分析师', 'Market Regime': '市场分析师',
  'Industry Analyst': '板块分析师', 'News Tagger': '新闻分析师',
  'Market Scanner': '市场扫描', 'Factor Scorer': '量化分析师',
  'LLM Reviewer': '候选复核', 'Deep Analyst': '深度分析师',
  'Report Writer': '报告整理', 'Risk Monitor': '风险监控',
};
export const AGENT_ACTIONS: Record<string, string> = {
  'Market Analyst': '分析价格走势与技术指标', 'Sentiment Analyst': '分析市场情绪',
  'News Analyst': '核对新闻与公司公告', 'Fundamentals Analyst': '分析财报与估值',
  'Bull Researcher': '评估上涨逻辑与催化剂', 'Bear Researcher': '检查下行风险与反面证据',
  'Research Manager': '综合多空观点', Trader: '制定交易计划',
  'Aggressive Analyst': '评估进攻方案的风险', 'Conservative Analyst': '检查防守与回撤约束',
  'Neutral Analyst': '平衡收益与风险', 'Portfolio Manager': '核对仓位并形成最终判断',
};
export const SKILL_ACTIONS: Record<string, string> = {
  stock_analysis: '分析个股', market_overview: '研究市场与板块', market_scanner: '筛选候选股票',
  daily_pipeline: '执行每日研究流程', portfolio_management: '诊断持仓', daily_review: '复盘交易',
  risk_monitor: '核对风险事件', strategy_backtest: '回测策略',
};
const TOOL_ACTIONS: Record<string, string> = {
  get_paper_session: '读取模拟盘账本与持仓', get_paper_job: '查询模拟盘推进进度',
  get_portfolio_summary: '读取持仓概览', get_recent_runs: '查询近期任务记录',
  search_artifacts: '查找已有研究报告', get_strategy_lessons: '检索适用的历史经验',
  get_mcp_factor_snapshot: '查询价格与量化因子', risk_announcements: '核对风险公告',
  announcements: '查询公司公告', get_stock_data: '查询股票价格',
  get_verified_market_snapshot: '核对市场行情快照', get_market_structure_snapshot: '查询市场结构',
  get_indicators: '计算技术指标', get_theme_heat: '查询板块热度',
  get_global_news: '查询市场新闻', get_news: '查询相关新闻',
  get_balance_sheet: '查询资产负债表', get_cashflow: '查询现金流',
  get_income_statement: '查询利润表', get_fundamentals: '查询基本面与估值',
  get_northbound_flow: '查询北向资金', get_institutional_flow: '查询机构资金流',
};
export function agentRole(agent: unknown): string | undefined {
  return typeof agent === 'string' ? AGENT_ROLES[agent] ?? (/\p{Script=Han}/u.test(agent) ? agent : '分析助手') : undefined;
}
export function toolAction(name: string): string {
  return Object.entries(TOOL_ACTIONS).find(([key]) => name.toLowerCase().includes(key))?.[1] ?? '查询分析所需数据';
}
export function activityMessage(action: string, status: string): string {
  if (status === 'completed') return `${action}完成`;
  if (status === 'failed') return `${action}失败`;
  if (status === 'queued') return `等待${action}`;
  return `正在${action}`;
}
export function activityDetail(args: unknown): string | undefined {
  if (!args || typeof args !== 'object' || Array.isArray(args)) return undefined;
  const obj = args as Record<string, unknown>;
  const parts = [
    [['ticker', 'symbol', 'ts_code'], '标的'],
    [['curr_date', 'date', 'analysis_date', 'trade_date'], '基准日'],
    [['start_date'], '起始日'], [['end_date'], '截止日'], [['indicator'], '指标'],
  ].flatMap(([keys, label]) => {
    const value = (keys as string[]).map((key) => obj[key]).find((v) => typeof v === 'string' || typeof v === 'number');
    return value === undefined ? [] : [`${label} ${String(value).slice(0, 40)}`];
  });
  return parts.length ? parts.join(' · ') : undefined;
}
