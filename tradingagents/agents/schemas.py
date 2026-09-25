"""Pydantic schemas used by agents that produce structured output.

The framework's primary artifact is still prose: each agent's natural-language
reasoning is what users read in the saved markdown reports and what the
downstream agents read as context.  Structured output is layered onto the
three decision-making agents (Research Manager, Trader, Portfolio Manager)
so that:

- Their outputs follow consistent section headers across runs and providers
- Each provider's native structured-output mode is used (json_schema for
  OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic)
- Schema field descriptions become the model's output instructions, freeing
  the prompt body to focus on context and the rating-scale guidance
- A render helper turns the parsed Pydantic instance back into the same
  markdown shape the rest of the system already consumes, so display,
  memory log, and saved reports keep working unchanged
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


class PortfolioRating(str, Enum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class TraderAction(str, Enum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan
    into a concrete transaction proposal: should the desk execute a Buy, a
    Sell, or sit on Hold this round.  Position sizing and the nuanced
    Overweight / Underweight calls happen later at the Portfolio Manager.
    """

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view,
    the rationale captures which side of the bull/bear debate carried the
    argument, and the strategic actions translate that into concrete
    instructions the trader can execute against.
    """

    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation. Exactly one of Buy / Overweight / "
            "Hold / Underweight / Sell. Reserve Hold for situations where the "
            "evidence on both sides is genuinely balanced; otherwise commit to "
            "the side with the stronger arguments."
        ),
    )
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including position sizing guidance consistent with the rating."
        ),
    )


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join([
        f"**Recommendation**: {plan.recommendation.value}",
        "",
        f"**Rationale**: {plan.rationale}",
        "",
        f"**Strategic Actions**: {plan.strategic_actions}",
    ])


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst
    reports, then turns them into a concrete transaction: what action to
    take, the reasoning that justifies it, and the practical levels for
    entry, stop-loss, and sizing.
    """

    action: TraderAction = Field(
        description="The transaction direction. Exactly one of Buy / Hold / Sell.",
    )
    reasoning: str = Field(
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    entry_price: float | None = Field(
        default=None,
        description="Optional entry price target in the instrument's quote currency.",
    )
    stop_loss: float | None = Field(
        default=None,
        description="Optional stop-loss price in the instrument's quote currency.",
    )
    position_sizing: str | None = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )
    entry_zone: list[float] | None = Field(
        default=None,
        description="Optional entry price zone [low, high]. For A-shares, use "
        "集合竞价 reference ± band rather than a single price.",
    )
    targets: list[float] | None = Field(
        default=None,
        description="Optional ordered profit-target prices.",
    )
    time_horizon: str | None = Field(
        default=None,
        description="Optional holding horizon, e.g. '3-10 trading days'.",
    )


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown.

    The trailing ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line is
    preserved for backward compatibility with the analyst stop-signal text
    and any external code that greps for it.
    """
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    if proposal.entry_price is not None:
        parts.extend(["", f"**Entry Price**: {proposal.entry_price}"])
    if proposal.entry_zone and len(proposal.entry_zone) >= 2:
        parts.extend(["", f"**Entry Zone**: {proposal.entry_zone[0]} – {proposal.entry_zone[1]}"])
    if proposal.stop_loss is not None:
        parts.extend(["", f"**Stop Loss**: {proposal.stop_loss}"])
    if proposal.targets:
        parts.extend(["", f"**Targets**: {', '.join(str(t) for t in proposal.targets)}"])
    if proposal.position_sizing:
        parts.extend(["", f"**Position Sizing**: {proposal.position_sizing}"])
    if proposal.time_horizon:
        parts.extend(["", f"**Time Horizon**: {proposal.time_horizon}"])
    parts.extend([
        "",
        f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
    ])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


class PlanAction(str, Enum):
    """Canonical action vocabulary shared by plans, conditions and clients."""

    ENTER = "ENTER"
    ADD = "ADD"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"


class TradeCondition(BaseModel):
    """A discrete condition that triggers a plan action.

    Captures composite signals the user wants monitored — e.g. a golden cross
    plus positive operating-cash-flow confirmation as the full-position
    trigger, or a death cross plus northbound outflow as the stop trigger.
    """

    kind: Literal["entry", "full", "stop", "take_profit"] = Field(
        description=(
            "What this condition triggers: 'entry' = open the position, "
            "'full' = scale to full position, 'stop' = exit for loss protection, "
            "'take_profit' = exit for profit."
        ),
    )
    trigger_action: PlanAction | None = Field(
        default=None,
        description=(
            "Explicit portfolio action when this condition is satisfied. Use "
            "ENTER/ADD/HOLD/REDUCE/EXIT; never infer it from `kind`."
        ),
    )
    description: str = Field(
        description=(
            "Concrete, monitorable description, e.g. "
            "'5日均线上穿20日均线（金叉）且经营现金流连续两季为正' or '收盘跌破前低 9.00'."
        ),
    )
    source: str | None = Field(
        default=None,
        description="Optional indicator basis, e.g. 'MA金叉+现金流', '价格', 'MACD死叉+北向资金'.",
    )
    order_quantity: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Exact share quantity to trade when this condition fires. Required for "
            "ENTER/ADD/REDUCE/EXIT when a holding context is available; prose percentages "
            "are not executable quantities."
        ),
    )
    target_quantity: int | None = Field(
        default=None,
        ge=0,
        description="Exact shares expected to remain after the condition executes.",
    )


class TradePlan(BaseModel):
    """Structured, monitorable trade plan produced by the Portfolio Manager.

    Complements the prose ``executive_summary`` with machine-readable levels
    and conditions so the frontend can render a plan card and (later) a
    scheduler can evaluate the conditions at close.
    """

    plan_action: PlanAction = Field(
        default=PlanAction.HOLD,
        description="Primary action of this plan: ENTER/ADD/HOLD/REDUCE/EXIT.",
    )
    action_zone: list[float] | None = Field(
        default=None,
        description="Price zone where `plan_action` should be executed.",
    )
    invalidation_level: float | None = Field(
        default=None,
        description="Price level that invalidates the plan thesis.",
    )
    objective_levels: list[float] | None = Field(
        default=None,
        description="Ordered reference/objective price levels for this plan.",
    )
    entry_zone: list[float] | None = Field(
        default=None,
        description=(
            "Deprecated compatibility alias for action_zone. New consumers must "
            "use plan_action + action_zone."
        ),
    )
    stop_loss: float | None = Field(
        default=None,
        description=(
            "Deprecated compatibility alias for invalidation_level."
        ),
    )
    targets: list[float] | None = Field(
        default=None,
        description=(
            "Deprecated compatibility alias for objective_levels."
        ),
    )
    position_pct: float | None = Field(
        default=None,
        description="Suggested position size as a percentage of portfolio (0-100).",
    )
    order_quantity: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Exact shares for the primary action. Required for executable holding "
            "adjustments; never derive this from a prose percentage."
        ),
    )
    target_quantity: int | None = Field(
        default=None,
        ge=0,
        description="Exact shares that should remain after the primary action.",
    )
    conditions: list[TradeCondition] = Field(
        default_factory=list,
        description=(
            "Monitorable conditions for entry / full position / stop / take profit "
            "(e.g. golden cross + cash flow, death cross + northbound outflow)."
        ),
    )

    @model_validator(mode="after")
    def sync_legacy_level_aliases(self) -> TradePlan:
        """Keep old persisted/report shapes readable during the schema migration."""
        if self.action_zone is None:
            self.action_zone = self.entry_zone
        if self.entry_zone is None:
            self.entry_zone = self.action_zone
        if self.invalidation_level is None:
            self.invalidation_level = self.stop_loss
        if self.stop_loss is None:
            self.stop_loss = self.invalidation_level
        if self.objective_levels is None:
            self.objective_levels = self.targets
        if self.targets is None:
            self.targets = self.objective_levels
        return self


class SelectionReconciliation(BaseModel):
    """Portfolio Manager's explicit comparison with a prior stock selection."""

    prior_decision: str = Field(
        description="The scanner/daily-pipeline decision being compared, e.g. BUY or WATCHLIST.",
    )
    alignment: Literal["aligned", "compatible", "downgrade", "upgrade", "reversal"] = Field(
        description=(
            "Relationship between the prior selection and this analysis. Use reversal only "
            "when the directions oppose; downgrade/upgrade for a one-way conviction change."
        ),
    )
    decision_changed: bool = Field(
        description="Whether this analysis changes the actionable direction of the prior selection.",
    )
    explanation: str = Field(
        description="Concrete explanation of why the conclusions agree or differ.",
    )
    new_evidence: list[str] = Field(
        default_factory=list,
        description="Evidence found in deep analysis that was absent from the selection snapshot.",
    )


class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate
    extraction pass is required. Field descriptions double as the model's
    output instructions, so the prompt body only needs to convey context and
    the rating-scale guidance.
    """

    rating: PortfolioRating = Field(
        description=(
            "The final position rating. Exactly one of Buy / Overweight / Hold / "
            "Underweight / Sell, picked based on the analysts' debate."
        ),
    )
    executive_summary: str = Field(
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. If prior lessons are referenced in the prompt context, "
            "incorporate them; otherwise rely solely on the current analysis."
        ),
    )
    price_target: float | None = Field(
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )
    confidence: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Optional confidence in the final decision on a 0-100 scale.",
    )
    time_horizon: str | None = Field(
        default=None,
        description="Optional recommended holding period, e.g. '3-6 months'.",
    )
    trade_plan: TradePlan | None = Field(
        default=None,
        description=(
            "Structured, monitorable trade plan: entry zone, stop loss, targets, "
            "position sizing, and the conditions (e.g. golden cross + cash flow) "
            "that trigger entry / full position / stop / take profit. Fill this "
            "even when the rating is Hold, so the user can see the levels they "
            "would act on."
        ),
    )
    selection_reconciliation: SelectionReconciliation | None = Field(
        default=None,
        description=(
            "When the prompt contains a 'Prior selection conclusion', explicitly compare it "
            "with this decision and fill this object. Leave null when no prior selection exists."
        ),
    )

    @model_validator(mode="after")
    def normalize_plan_actions(self) -> PortfolioDecision:
        """Make direction explicit even when an older provider omits new fields."""
        if self.trade_plan is None:
            return self
        plan = self.trade_plan
        if "plan_action" not in plan.model_fields_set:
            plan.plan_action = {
                PortfolioRating.BUY: PlanAction.ENTER,
                PortfolioRating.OVERWEIGHT: PlanAction.ADD,
                PortfolioRating.HOLD: PlanAction.HOLD,
                PortfolioRating.UNDERWEIGHT: PlanAction.REDUCE,
                PortfolioRating.SELL: PlanAction.EXIT,
            }[self.rating]
        long_actions = {
            "entry": PlanAction.ENTER,
            "full": PlanAction.ADD,
            "stop": PlanAction.EXIT,
            "take_profit": PlanAction.REDUCE,
        }
        exit_actions = {
            "entry": PlanAction.REDUCE,
            "full": PlanAction.EXIT,
            "stop": PlanAction.HOLD,
            "take_profit": PlanAction.EXIT,
        }
        defaults = exit_actions if plan.plan_action in {PlanAction.REDUCE, PlanAction.EXIT} else long_actions
        for condition in plan.conditions:
            if condition.trigger_action is None:
                condition.trigger_action = defaults[condition.kind]
        return self


def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision back to the markdown shape the rest of the system expects.

    Memory log, CLI display, and saved report files all read this markdown,
    so the rendered output preserves the exact section headers (``**Rating**``,
    ``**Executive Summary**``, ``**Investment Thesis**``) that downstream
    parsers and the report writers already handle.
    """
    parts = [
        f"**Rating**: {decision.rating.value}",
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    if decision.confidence is not None:
        parts.extend(["", f"**Confidence**: {decision.confidence}"])
    if decision.time_horizon:
        parts.extend(["", f"**Time Horizon**: {decision.time_horizon}"])
    tp = decision.trade_plan
    if tp is not None:
        parts.extend(["", "**Trade Plan**", f"- Action: {tp.plan_action.value}"])
        if tp.action_zone:
            parts.append(f"- Action Zone: {tp.action_zone}")
            parts.append(f"- Entry Zone: {tp.action_zone}")
        if tp.invalidation_level is not None:
            parts.append(f"- Invalidation Level: {tp.invalidation_level}")
            parts.append(f"- Stop Loss: {tp.invalidation_level}")
        if tp.objective_levels:
            parts.append(f"- Objective Levels: {tp.objective_levels}")
            parts.append(f"- Targets: {tp.objective_levels}")
        if tp.position_pct is not None:
            parts.append(f"- Position Sizing: {tp.position_pct}%")
        if tp.order_quantity is not None:
            parts.append(f"- Order Quantity: {tp.order_quantity} shares")
        if tp.target_quantity is not None:
            parts.append(f"- Target Quantity: {tp.target_quantity} shares")
        for cond in tp.conditions:
            src = f" ({cond.source})" if cond.source else ""
            action = cond.trigger_action.value if cond.trigger_action else cond.kind
            parts.append(f"- Condition [{cond.kind}]{src}: {cond.description}")
            parts.append(f"- Trigger Action [{action}]: {cond.description}")
            if cond.order_quantity is not None:
                parts.append(f"- Trigger Order Quantity [{action}]: {cond.order_quantity} shares")
            if cond.target_quantity is not None:
                parts.append(f"- Trigger Target Quantity [{action}]: {cond.target_quantity} shares")
    reconciliation = decision.selection_reconciliation
    if reconciliation is not None:
        parts.extend(
            [
                "",
                "**Selection Reconciliation**",
                f"- Prior Decision: {reconciliation.prior_decision}",
                f"- Alignment: {reconciliation.alignment}",
                f"- Decision Changed: {str(reconciliation.decision_changed).lower()}",
                f"- Explanation: {reconciliation.explanation}",
            ]
        )
        for evidence in reconciliation.new_evidence:
            parts.append(f"- New Evidence: {evidence}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sentiment Analyst
# ---------------------------------------------------------------------------


class SentimentBand(str, Enum):
    """Discrete sentiment direction produced by the Sentiment Analyst.

    Six tiers keep the signal granular enough to be actionable while remaining
    small enough for every provider to map reliably from its JSON output.
    """

    BULLISH = "Bullish"
    MILDLY_BULLISH = "Mildly Bullish"
    NEUTRAL = "Neutral"
    MIXED = "Mixed"
    MILDLY_BEARISH = "Mildly Bearish"
    BEARISH = "Bearish"


class SentimentReport(BaseModel):
    """Structured sentiment report produced by the Sentiment Analyst.

    Replaces the previous free-form prose output so downstream consumers
    (dashboards, audit logs, PDF renderers, other agents) can read
    ``overall_band`` and ``overall_score`` without maintaining fragile regex
    fallbacks that drift with every model release. ``narrative`` preserves the
    rich source-by-source analysis; ``render_sentiment_report`` prepends a
    deterministic header so the saved report stays human-readable.
    """

    overall_band: SentimentBand = Field(
        description=(
            "Overall sentiment direction. Exactly one of: "
            "Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. "
            "Use Mixed when sources point in clearly different directions. "
            "Use Neutral only when all sources are genuinely silent or non-committal."
        ),
    )
    overall_score: float = Field(
        ge=0.0,
        le=10.0,
        description=(
            "Numeric sentiment intensity on a 0–10 scale. "
            "0 = maximally bearish, 5 = neutral, 10 = maximally bullish. "
            "Guideline for consistency with overall_band: "
            "Bullish ~6.5–10, Mildly Bullish ~5.5–6.4, Neutral/Mixed ~4.5–5.5, "
            "Mildly Bearish ~3.5–4.4, Bearish ~0–3.4. "
            "Only the 0–10 bounds are enforced."
        ),
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description=(
            "Confidence in the assessment based on data quality and sample size. "
            "Use 'low' when one or more sources returned a placeholder or fewer "
            "than 5 data points; 'medium' when data is present but sparse; "
            "'high' when all three sources returned substantive data."
        ),
    )
    narrative: str = Field(
        description=(
            "Full sentiment report covering, in order: "
            "(1) source-by-source breakdown with specific evidence (cite message "
            "counts, ratios, notable posts); "
            "(2) cross-source divergences and alignments; "
            "(3) dominant narrative themes; "
            "(4) catalysts and risks surfaced by the data; "
            "(5) a markdown table summarising key sentiment signals, their "
            "direction, source, and supporting evidence. "
            "Keep it informative and substantive: develop each section thoroughly "
            "with concrete evidence so every point adds new signal for the trader."
        ),
    )


def render_sentiment_report(report: SentimentReport) -> str:
    """Render a SentimentReport to the markdown shape the rest of the system expects.

    The structured header (band + score + confidence) is prepended to the
    narrative so the saved report is both human-readable and machine-parseable
    without regex.
    """
    return "\n".join([
        f"**Overall Sentiment:** **{report.overall_band.value}** "
        f"(Score: {report.overall_score:.1f}/10)",
        f"**Confidence:** {report.confidence.capitalize()}",
        "",
        report.narrative,
    ])


# ---------------------------------------------------------------------------
# Market Overview (market_overview skill)
# ---------------------------------------------------------------------------


class MarketTrendBand(str, Enum):
    """5-tier market regime trend rating produced by the market_overview skill."""

    STRONG_BULLISH = "Strong Bullish"   # 强多
    MILDLY_BULLISH = "Mildly Bullish"   # 偏多
    SIDEWAYS = "Sideways"               # 震荡
    MILDLY_BEARISH = "Mildly Bearish"   # 偏空
    STRONG_BEARISH = "Strong Bearish"   # 强空


class MarketDriver(BaseModel):
    """A single driver behind the market regime judgment."""

    dimension: Literal["funds", "sentiment", "policy", "macro"] = Field(
        description="驱动因子维度：funds=资金面 sentiment=情绪面 policy=政策面 macro=宏观面",
    )
    direction: Literal["positive", "negative", "neutral"] = Field(
        description="该因子对大盘的方向性影响。",
    )
    statement: str = Field(
        description="一句话论据，必须引用输入数据中的具体数字（如北向净流入额、涨停家数），≤40字",
    )


class MarketRegimeReport(BaseModel):
    """Structured whole-market regime judgment for the /market page banner."""

    trend_band: MarketTrendBand = Field(
        description=(
            "大盘趋势评级。Strong Bullish=强多 / Mildly Bullish=偏多 / "
            "Sideways=震荡 / Mildly Bearish=偏空 / Strong Bearish=强空。"
            "仅当证据明显一边倒时才使用 Strong 档位。"
        ),
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description="判断置信度：数据缺块或信号互相矛盾时用 low，信号一致且数据完整时用 high。",
    )
    core_logic: str = Field(
        description="核心逻辑一句话，≤60字，需引用输入数据中的具体证据",
    )
    drivers: list[MarketDriver] = Field(
        description="2-4 条关键驱动因子，覆盖不同维度",
    )
    suggested_position_range: str = Field(
        description="建议整体仓位区间，如 '5-7成'",
    )
    dominant_style: str = Field(
        description="当前占优风格，如 '大盘价值' / '小盘成长' / '题材轮动'",
    )
    risk_alerts: list[str] = Field(
        default_factory=list,
        description="0-3 条风险提示，每条≤30字",
    )


def render_market_regime_report(report: MarketRegimeReport) -> str:
    """Render a MarketRegimeReport as the banner section of the markdown report."""
    band_cn = {
        MarketTrendBand.STRONG_BULLISH: "强多",
        MarketTrendBand.MILDLY_BULLISH: "偏多",
        MarketTrendBand.SIDEWAYS: "震荡",
        MarketTrendBand.MILDLY_BEARISH: "偏空",
        MarketTrendBand.STRONG_BEARISH: "强空",
    }[report.trend_band]
    lines = [
        f"**大盘趋势评级**: {band_cn} ({report.trend_band.value}) · 置信度 {report.confidence}",
        "",
        f"**核心逻辑**: {report.core_logic}",
        "",
        f"**建议仓位**: {report.suggested_position_range} · **占优风格**: {report.dominant_style}",
        "",
        "**关键驱动**:",
    ]
    for driver in report.drivers:
        sign = {"positive": "+", "negative": "-", "neutral": "·"}[driver.direction]
        lines.append(f"- [{sign}] {driver.dimension}: {driver.statement}")
    if report.risk_alerts:
        lines.append("")
        lines.append("**风险提示**:")
        lines.extend(f"- {alert}" for alert in report.risk_alerts)
    return "\n".join(lines)


class IndustryStance(BaseModel):
    """Per-industry bullish/bearish stance produced by the market_overview skill."""

    industry: str = Field(
        description="行业板块名，必须与输入数据中的板块名完全一致",
    )
    rating: Literal["bullish", "neutral", "bearish"] = Field(
        description="复制输入中确定性多因子模型给出的看多/中性/看空评级，不得自行修改。",
    )
    reason: str = Field(
        description="差异化解释一句话，≤60字，引用至少两个可用因子并指出确认项或分歧项。",
    )
    key_stocks: list[str] = Field(
        default_factory=list,
        description="该板块领涨/代表个股名称，≤3只，来自输入数据，不得编造",
    )


class IndustryStanceList(BaseModel):
    """Batch container so all industry stances come back in one structured call."""

    stances: list[IndustryStance] = Field(
        description="输入数据中每个候选板块各一条评级，顺序不限",
    )


class TaggedNews(BaseModel):
    """A single market news item tagged with polarity and impact metadata."""

    title: str = Field(
        description="新闻标题原文（可截断至50字）",
    )
    polarity: Literal["bullish", "bearish", "neutral"] = Field(
        description="利好=bullish 利空=bearish 中性=neutral（对A股整体或涉及标的而言）",
    )
    impact_scope: Literal["market", "industry", "stock"] = Field(
        description="影响范围：market=全市场 industry=特定行业 stock=特定个股",
    )
    impact_level: Literal["high", "medium", "low"] = Field(
        description="影响级别。high 仅用于重大政策/宏观数据/系统性事件。",
    )
    industries: list[str] = Field(
        default_factory=list,
        description="受影响行业名，无则为空",
    )
    symbols: list[str] = Field(
        default_factory=list,
        description="受影响个股代码（6位数字），无则为空",
    )
    interpretation: str = Field(
        description="一句话解读，≤30字",
    )


class TaggedNewsList(BaseModel):
    """Batch container so all news items are tagged in one structured call."""

    items: list[TaggedNews] = Field(
        description="输入新闻逐条打标结果，保持输入顺序",
    )
