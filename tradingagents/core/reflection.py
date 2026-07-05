"""Reflection engine for evaluating past trading decisions.

This module provides post-hoc analysis of decisions stored by daily_pipeline
and stock_analysis. It fetches actual price outcomes, evaluates accuracy,
and optionally generates LLM-assisted reflection text.
"""

from __future__ import annotations

import logging
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _convert_ticker_for_yahoo(ts_code: str) -> str:
    """Convert A-share ts_code format to Yahoo Finance format.

    Examples:
        600519.SH -> 600519.SS
        000858.SZ -> 000858.SZ (unchanged)
        300750.SZ -> 300750.SZ (unchanged)
    """
    if ts_code.upper().endswith(".SH"):
        return ts_code[:-3] + ".SS"
    return ts_code


class ReflectionEngine:
    """Orchestrates decision reflection by fetching outcomes and evaluating accuracy.

    Integrates with:
    - TradingMemoryLog for pending decision entries
    - MCP client or yfinance for actual price data
    - LLM (quick_think_llm) for reflection text generation
    - Database (persistence) for storing reflection records
    """

    def __init__(
        self,
        db: Any,
        config: dict[str, Any],
        memory_log: Any | None = None,
    ):
        self.db = db
        self.config = config
        self.memory_log = memory_log

    async def fetch_outcome(
        self,
        symbol: str,
        signal_date: str,
        horizon_days: int = 5,
    ) -> dict[str, Any] | None:
        """Fetch actual return for a symbol after signal_date.

        Strategy:
        1. Try MCP get_stock_daily (A-share ts_code format)
        2. Fallback to yfinance (requires ticker format conversion)

        Returns dict with actual_return, close_at_signal, close_at_horizon, or None.
        """
        from tradingagents.core.mcp_client import get_mcp_client

        try:
            start_date = signal_date.replace("-", "")
            end_dt = datetime.strptime(signal_date, "%Y-%m-%d") + timedelta(days=horizon_days + 5)
            end_date = end_dt.strftime("%Y%m%d")

            client = await get_mcp_client(self.config)
            if client is not None:
                result = await client.get_stock_daily(
                    ts_codes=[symbol],
                    start_date=start_date,
                    end_date=end_date,
                    adj_type="qfq",
                )
                if result and isinstance(result, dict):
                    rows = result.get("data") or result.get("rows") or []
                    if isinstance(rows, list) and len(rows) >= 2:
                        return self._compute_return_from_rows(rows, signal_date, horizon_days)

            # Fallback to yfinance
            return await self._fetch_outcome_yfinance(symbol, signal_date, horizon_days)
        except Exception as exc:
            logger.warning("fetch_outcome failed for %s: %s", symbol, exc)
            return None

    async def _fetch_outcome_yfinance(
        self,
        symbol: str,
        signal_date: str,
        horizon_days: int,
    ) -> dict[str, Any] | None:
        """Fallback: fetch price data via yfinance."""
        try:
            import asyncio

            import yfinance as yf

            yahoo_ticker = _convert_ticker_for_yahoo(symbol)
            start_dt = datetime.strptime(signal_date, "%Y-%m-%d")
            end_dt = start_dt + timedelta(days=horizon_days + 10)

            def _download():
                ticker = yf.Ticker(yahoo_ticker)
                return ticker.history(
                    start=start_dt.strftime("%Y-%m-%d"),
                    end=end_dt.strftime("%Y-%m-%d"),
                )

            df = await asyncio.to_thread(_download)
            if df is None or df.empty or len(df) < 2:
                return None

            close_at_signal = float(df.iloc[0]["Close"])
            horizon_idx = min(horizon_days, len(df) - 1)
            close_at_horizon = float(df.iloc[horizon_idx]["Close"])
            actual_return = (close_at_horizon - close_at_signal) / close_at_signal

            return {
                "actual_return": round(actual_return, 4),
                "close_at_signal": round(close_at_signal, 2),
                "close_at_horizon": round(close_at_horizon, 2),
                "horizon_days": horizon_days,
                "source": "yfinance",
            }
        except Exception as exc:
            logger.debug("yfinance fallback failed for %s: %s", symbol, exc)
            return None

    def _compute_return_from_rows(
        self,
        rows: list[dict[str, Any]],
        signal_date: str,
        horizon_days: int,
    ) -> dict[str, Any] | None:
        """Compute return from MCP daily data rows."""
        # Sort by trade_date
        sorted_rows = sorted(rows, key=lambda r: str(r.get("trade_date", "")))
        signal_date_compact = signal_date.replace("-", "")

        # Find signal date row
        signal_row = None
        signal_idx = -1
        for idx, row in enumerate(sorted_rows):
            td = str(row.get("trade_date", ""))
            if td >= signal_date_compact:
                signal_row = row
                signal_idx = idx
                break

        if signal_row is None or signal_idx < 0:
            return None

        # Find horizon row
        horizon_idx = min(signal_idx + horizon_days, len(sorted_rows) - 1)
        if horizon_idx <= signal_idx:
            return None

        horizon_row = sorted_rows[horizon_idx]
        close_at_signal = float(signal_row.get("close", 0))
        close_at_horizon = float(horizon_row.get("close", 0))

        if close_at_signal <= 0:
            return None

        actual_return = (close_at_horizon - close_at_signal) / close_at_signal
        return {
            "actual_return": round(actual_return, 4),
            "close_at_signal": round(close_at_signal, 2),
            "close_at_horizon": round(close_at_horizon, 2),
            "horizon_days": horizon_days,
            "source": "mcp",
        }

    def evaluate_accuracy(self, original_decision: str, actual_return: float) -> bool | None:
        """Determine if the original decision was directionally correct.

        BUY decisions are correct if actual_return > 0.
        SELL/AVOID decisions are correct if actual_return <= 0.
        WATCHLIST/HOLD are neutral — return ``None`` so callers can exclude
        them from the accuracy denominator instead of inflating it.
        """
        decision_upper = original_decision.upper()
        if "BUY" in decision_upper or "OVERWEIGHT" in decision_upper:
            return actual_return > 0
        if "SELL" in decision_upper or "AVOID" in decision_upper or "UNDERWEIGHT" in decision_upper:
            return actual_return <= 0
        # WATCHLIST, HOLD — neutral; excluded from accuracy denominator.
        return None

    async def fetch_post_signal_evidence(
        self,
        symbol: str,
        signal_date: str,
        horizon_days: int = 5,
    ) -> dict[str, Any]:
        """Fetch post-signal events used to distinguish ex-ante misses from new shocks."""
        evidence: dict[str, Any] = {
            "symbol": symbol,
            "signal_date": signal_date,
            "horizon_days": horizon_days,
            "announcements": [],
            "news": [],
            "market_context": {},
            "warnings": [],
        }
        try:
            from tradingagents.core.mcp_client import get_mcp_client

            client = await get_mcp_client(self.config)
            if client is None:
                evidence["warnings"].append("StockManager MCP unavailable; post-signal evidence is incomplete.")
                return evidence

            start_dt = datetime.strptime(signal_date, "%Y-%m-%d") + timedelta(days=1)
            end_dt = datetime.strptime(signal_date, "%Y-%m-%d") + timedelta(days=horizon_days + 5)
            if hasattr(client, "get_risk_announcements"):
                payload = await client.get_risk_announcements(
                    symbol,
                    start_dt.date().isoformat(),
                    end_dt.date().isoformat(),
                    keywords=["立案", "问询", "违规", "处罚", "减持", "业绩", "预亏", "退市"],
                )
                if isinstance(payload, dict):
                    rows = payload.get("rows") or payload.get("data") or []
                    evidence["announcements"] = rows if isinstance(rows, list) else []
                    if payload.get("warnings"):
                        evidence["warnings"].extend(payload.get("warnings") or [])
        except Exception as exc:
            logger.warning("Post-signal evidence fetch failed for %s: %s", symbol, exc)
            evidence["warnings"].append(f"post_signal_evidence_failed: {exc}")
        return evidence

    async def generate_attribution(
        self,
        case: dict[str, Any],
        outcome: dict[str, Any],
        post_signal_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Classify whether an outcome should feed strategy learning."""
        try:
            from tradingagents.llm_clients import create_llm_client

            client = create_llm_client(
                provider=self.config.get("llm_provider", "openai"),
                model=self.config.get("quick_think_llm", "gpt-5.4-mini"),
                base_url=self.config.get("backend_url"),
            )
            llm = client.get_llm()
            prompt = _build_attribution_prompt(case, outcome, post_signal_evidence)
            response = await llm.ainvoke(prompt)
            return _parse_attribution_payload(str(getattr(response, "content", response)))
        except Exception as exc:
            logger.warning("LLM attribution failed; using heuristic attribution: %s", exc)
            return _heuristic_attribution(case, outcome, post_signal_evidence)

    def maybe_create_strategy_lesson(
        self,
        case: dict[str, Any],
        attribution: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a reusable lesson only for actionable ex-ante misses."""
        if not case.get("eligible_for_strategy_learning"):
            return {}
        if attribution.get("attribution") != "ex_ante_miss":
            return {}
        if str(attribution.get("confidence") or "low") not in {"medium", "high"}:
            return {}

        snapshot = case.get("snapshot_payload") or {}
        symbol = case.get("symbol") or ""
        industry = snapshot.get("industry") or (snapshot.get("candidate") or {}).get("industry") or ""
        data_coverage = snapshot.get("data_coverage") or (snapshot.get("candidate") or {}).get("data_coverage") or {}
        missing = [
            key for key, value in data_coverage.items()
            if str(value).lower() == "missing"
        ] if isinstance(data_coverage, dict) else []

        scope = "industry" if industry else "global"
        target = str(industry or "")
        missed = attribution.get("missed_evidence") or []
        finding = attribution.get("strategy_lesson") or (
            f"{symbol} 的历史样本显示信号时点已有风险未被充分处理。"
        )
        if missing:
            finding += f" 数据缺失项: {', '.join(missing)}。"
        suggested = attribution.get("suggested_adjustment") or (
            "后续遇到类似候选时，要求 LLM 明确验证缺失数据和已知风险，再决定是否降级。"
        )
        lesson = {
            "id": str(uuid.uuid4()),
            "lesson_type": "signal_quality",
            "scope": scope,
            "target": target,
            "finding": finding,
            "suggested_adjustment": suggested,
            "evidence_count": 1,
            "confidence": str(attribution.get("confidence") or "medium"),
            "case_id": case.get("id"),
            "symbol": symbol,
            "missed_evidence": missed,
        }
        try:
            self.db.save_strategy_lesson(
                lesson_id=lesson["id"],
                lesson_type=lesson["lesson_type"],
                scope=lesson["scope"],
                target=lesson["target"],
                finding=lesson["finding"],
                suggested_adjustment=lesson["suggested_adjustment"],
                evidence_count=lesson["evidence_count"],
                confidence=lesson["confidence"],
                payload=lesson,
            )
        except Exception as exc:
            logger.warning("Failed to save strategy lesson for %s: %s", symbol, exc)
        return lesson

    async def generate_reflection(
        self,
        signal: dict[str, Any],
        outcome: dict[str, Any],
        evidence: str = "",
    ) -> str:
        """Generate a short reflection text using LLM."""
        try:
            from tradingagents.llm_clients import create_llm_client

            client = create_llm_client(
                provider=self.config.get("llm_provider", "openai"),
                model=self.config.get("quick_think_llm", "gpt-5.4-mini"),
                base_url=self.config.get("backend_url"),
            )
            llm = client.get_llm()
            prompt = (
                f"You are a trading reflection assistant. Given this past decision and its outcome, "
                f"write 2-3 sentences of actionable reflection.\n\n"
                f"Decision: {signal.get('original_decision', 'Unknown')}\n"
                f"Ticker: {signal.get('ticker', '?')}\n"
                f"Date: {signal.get('trade_date', '?')}\n"
                f"Actual return: {outcome.get('actual_return', 0):.2%} over {outcome.get('horizon_days', 5)} days\n"
                f"Was correct: {outcome.get('was_correct', '?')}\n"
                f"{f'Context: {evidence}' if evidence else ''}\n\n"
                f"Reflection (2-3 sentences):"
            )
            response = await llm.ainvoke(prompt)
            return str(response.content).strip()
        except Exception as exc:
            logger.warning("LLM reflection generation failed: %s", exc)
            was_correct = outcome.get("was_correct", None)
            ret = outcome.get("actual_return", 0)
            if was_correct:
                return f"Decision was directionally correct. Return: {ret:.2%}."
            return f"Decision was incorrect. Actual return: {ret:.2%}. Review entry signals."

    async def run_reflection_batch(
        self,
        lookback_days: int = 30,
        max_per_run: int = 20,
        horizon_days: int = 5,
    ) -> dict[str, Any]:
        """Run batch reflection on pending memory entries.

        Returns summary of how many were processed.
        """
        pending_cases = []
        if hasattr(self.db, "list_reflection_cases"):
            pending_cases = self.db.list_reflection_cases(
                status="pending",
                limit=max_per_run,
            )

        pending = self.memory_log.get_pending_entries() if self.memory_log is not None else []
        if not pending_cases and not pending:
            return {"processed": 0, "skipped": "no pending entries"}

        # Filter to entries within lookback window. TradingMemoryLog uses
        # "date"; newer callers may provide "trade_date".
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        eligible = [
            e for e in pending
            if _entry_trade_date(e) and _entry_trade_date(e) >= cutoff
        ][:max(0, max_per_run - len(pending_cases))]

        processed = 0
        cases_processed = 0
        lessons_created = 0
        errors = 0

        for case in pending_cases:
            try:
                symbol = str(case.get("symbol") or "")
                signal_date = str(case.get("signal_date") or "")
                case_horizon = int(case.get("horizon_days") or horizon_days)
                if not symbol or not signal_date:
                    continue
                outcome = await self.fetch_outcome(symbol, signal_date, case_horizon)
                if outcome is None:
                    continue
                original_decision = _case_original_decision(case)
                was_correct = self.evaluate_accuracy(original_decision, outcome["actual_return"])
                outcome["was_correct"] = was_correct
                evidence = await self.fetch_post_signal_evidence(symbol, signal_date, case_horizon)
                attribution = await self.generate_attribution(case, outcome, evidence)
                lesson = self.maybe_create_strategy_lesson(case, attribution)
                if lesson:
                    lessons_created += 1
                reflection_text = _reflection_text_from_attribution(attribution, outcome)
                self.db.update_reflection_case(
                    case["id"],
                    status="reflected",
                    outcome_payload=outcome,
                    post_signal_evidence_payload=evidence,
                    attribution_payload=attribution,
                    lesson_payload=lesson,
                )
                self.db.save_reflection({
                    "id": str(uuid.uuid4()),
                    "run_id": case.get("source_run_id", ""),
                    "ticker": symbol,
                    "trade_date": signal_date,
                    "original_decision": original_decision,
                    "actual_return": outcome["actual_return"],
                    "was_correct": was_correct,
                    "reflection_text": reflection_text,
                })
                cases_processed += 1
                processed += 1
            except Exception as exc:
                logger.warning("Reflection case failed for %s: %s", case.get("symbol"), exc)
                errors += 1

        for entry in eligible:
            try:
                ticker = entry.get("ticker", "")
                trade_date = _entry_trade_date(entry)
                if not ticker or not trade_date:
                    continue

                outcome = await self.fetch_outcome(ticker, trade_date, horizon_days)
                if outcome is None:
                    continue

                original_decision = entry.get("decision", "")
                was_correct = self.evaluate_accuracy(original_decision, outcome["actual_return"])
                outcome["was_correct"] = was_correct

                reflection_text = await self.generate_reflection(entry, outcome)

                # Update memory log
                self.memory_log.update_with_outcome(
                    ticker=ticker,
                    trade_date=trade_date,
                    raw_return=outcome["actual_return"],
                    alpha_return=outcome["actual_return"],  # simplified; no benchmark calc here
                    holding_days=horizon_days,
                    reflection=reflection_text,
                )

                # Save to DB
                self.db.save_reflection({
                    "id": str(uuid.uuid4()),
                    "run_id": entry.get("run_id", ""),
                    "ticker": ticker,
                    "trade_date": trade_date,
                    "original_decision": original_decision,
                    "actual_return": outcome["actual_return"],
                    "was_correct": was_correct,
                    "reflection_text": reflection_text,
                })
                processed += 1
            except Exception as exc:
                logger.warning("Reflection failed for %s: %s", entry.get("ticker"), exc)
                errors += 1

        result = {
            "processed": processed,
            "cases_processed": cases_processed,
            "legacy_memory_processed": processed - cases_processed,
            "lessons_created": lessons_created,
            "errors": errors,
            "eligible": len(eligible) + len(pending_cases),
            "total_pending": len(pending),
            "total_pending_cases": len(pending_cases),
        }
        if processed > 0:
            try:
                self.db.save_artifact(
                    artifact_id=str(uuid.uuid4()),
                    run_id="",
                    skill_id="reflection",
                    artifact_type="reflection_report",
                    title=f"反思批处理 {datetime.now(timezone.utc).date().isoformat()}",
                    subtitle=f"处理 {processed} 条决策",
                    subject_type="system",
                    subject_id="reflection",
                    subject_name="反思闭环",
                    status="success" if errors == 0 else "partial",
                    summary=f"处理 {processed} 条，生成经验 {lessons_created} 条，错误 {errors} 条",
                    content_markdown=_render_reflection_batch_report(result),
                    payload=result,
                    tags=["reflection", "batch"],
                )
            except Exception as exc:
                logger.warning("Failed to save reflection artifact: %s", exc)

        # Prune old reflected cases so the table does not grow without bound
        # (daily_pipeline creates many pending cases per run; only max_per_run
        # are resolved per day). Best-effort: never let cleanup failure break
        # the reflection batch.
        try:
            if hasattr(self.db, "prune_reflection_cases"):
                pruned = self.db.prune_reflection_cases(older_than_days=90, status="reflected")
                if pruned:
                    result["pruned_cases"] = pruned
                    logger.info("Pruned %d old reflected cases", pruned)
        except Exception as exc:
            logger.warning("Failed to prune reflection cases: %s", exc)

        return result


def _entry_trade_date(entry: dict[str, Any]) -> str:
    return str(entry.get("trade_date") or entry.get("date") or "")


def _render_reflection_batch_report(result: dict[str, Any]) -> str:
    return (
        "# Reflection Batch\n\n"
        f"- Processed: {result.get('processed', 0)}\n"
        f"- Cases processed: {result.get('cases_processed', 0)}\n"
        f"- Lessons created: {result.get('lessons_created', 0)}\n"
        f"- Errors: {result.get('errors', 0)}\n"
        f"- Eligible: {result.get('eligible', 0)}\n"
        f"- Total pending: {result.get('total_pending', 0)}\n"
        f"- Total pending cases: {result.get('total_pending_cases', 0)}\n"
    )


def _case_original_decision(case: dict[str, Any]) -> str:
    snapshot = case.get("snapshot_payload") or {}
    return str(
        snapshot.get("final_decision")
        or snapshot.get("signal")
        or snapshot.get("quant_decision")
        or snapshot.get("decision")
        or ""
    )


def _build_attribution_prompt(
    case: dict[str, Any],
    outcome: dict[str, Any],
    post_signal_evidence: dict[str, Any],
) -> str:
    snapshot = case.get("snapshot_payload") or {}
    return f"""你是交易系统的因果反思 Agent。请判断本次结果是否应该更新未来策略。

核心原则：
- 只有信号时点已经存在、且模型本应看到或处理的信息，才属于 ex_ante_miss。
- 信号后才出现的新公告、新政策、新利空，属于 ex_post_shock，不应惩罚原选股逻辑。
- 大盘或行业系统性变化属于 market_regime_shift。

Reflection case:
{json.dumps({k: case.get(k) for k in ['symbol', 'signal_date', 'reflection_scope', 'eligible_for_strategy_learning']}, ensure_ascii=False)}

Signal-time snapshot:
{json.dumps(snapshot, ensure_ascii=False)[:5000]}

Outcome:
{json.dumps(outcome, ensure_ascii=False)}

Post-signal evidence:
{json.dumps(post_signal_evidence, ensure_ascii=False)[:3000]}

请输出严格 JSON：
{{
  "attribution": "ex_ante_miss|ex_post_shock|market_regime_shift|noise|inconclusive",
  "confidence": "low|medium|high",
  "was_in_original_inputs": true,
  "missed_evidence": ["..."],
  "new_information": ["..."],
  "strategy_lesson": "...",
  "risk_monitor_lesson": "...",
  "suggested_adjustment": "..."
}}
"""


def _parse_attribution_payload(text: str) -> dict[str, Any]:
    try:
        stripped = text.strip()
        if not stripped.startswith("{"):
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start >= 0 and end > start:
                stripped = stripped[start : end + 1]
        parsed = json.loads(stripped)
    except Exception:
        return {
            "attribution": "inconclusive",
            "confidence": "low",
            "was_in_original_inputs": False,
            "missed_evidence": [],
            "new_information": [],
            "strategy_lesson": "",
            "risk_monitor_lesson": "",
            "suggested_adjustment": "",
        }
    attribution = str(parsed.get("attribution") or "inconclusive")
    if attribution not in {"ex_ante_miss", "ex_post_shock", "market_regime_shift", "noise", "inconclusive"}:
        attribution = "inconclusive"
    confidence = str(parsed.get("confidence") or "low")
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
    parsed["attribution"] = attribution
    parsed["confidence"] = confidence
    parsed["was_in_original_inputs"] = bool(parsed.get("was_in_original_inputs"))
    parsed["missed_evidence"] = parsed.get("missed_evidence") if isinstance(parsed.get("missed_evidence"), list) else []
    parsed["new_information"] = parsed.get("new_information") if isinstance(parsed.get("new_information"), list) else []
    return parsed


def _heuristic_attribution(
    case: dict[str, Any],
    outcome: dict[str, Any],
    post_signal_evidence: dict[str, Any],
) -> dict[str, Any]:
    actual_return = float(outcome.get("actual_return") or 0)
    was_correct = bool(outcome.get("was_correct"))
    snapshot = case.get("snapshot_payload") or {}
    announcements = post_signal_evidence.get("announcements") or []
    data_coverage = snapshot.get("data_coverage") or (snapshot.get("candidate") or {}).get("data_coverage") or {}
    missing = [
        key for key, value in data_coverage.items()
        if str(value).lower() == "missing"
    ] if isinstance(data_coverage, dict) else []
    gate_reasons = snapshot.get("gate_reasons") or snapshot.get("quant_gate_reasons") or []
    risk_flags = snapshot.get("risk_flags") or []
    if was_correct:
        attribution = "noise"
        confidence = "medium"
        lesson = "本次结果方向正确，无需调整策略。"
    elif announcements:
        attribution = "ex_post_shock"
        confidence = "medium"
        lesson = "信号后出现新增风险信息，应加强持仓后的风险监控，而不是惩罚信号时点判断。"
    elif actual_return <= -0.03 and (missing or gate_reasons or risk_flags):
        attribution = "ex_ante_miss"
        confidence = "medium"
        lesson = "信号时点已有数据缺失或风险标签，但最终决策未充分降级。"
    elif actual_return <= -0.05:
        attribution = "market_regime_shift"
        confidence = "low"
        lesson = "下跌幅度较大但缺少明确个股原因，先按市场环境变化处理。"
    else:
        attribution = "inconclusive"
        confidence = "low"
        lesson = "结果偏差较小或证据不足，不进入策略学习。"
    return {
        "attribution": attribution,
        "confidence": confidence,
        "was_in_original_inputs": attribution == "ex_ante_miss",
        "missed_evidence": [*missing, *[str(x) for x in gate_reasons], *[str(x) for x in risk_flags]],
        "new_information": [str(item.get("title") or item) for item in announcements[:5]] if isinstance(announcements, list) else [],
        "strategy_lesson": lesson if attribution == "ex_ante_miss" else "",
        "risk_monitor_lesson": lesson if attribution in {"ex_post_shock", "market_regime_shift"} else "",
        "suggested_adjustment": "类似候选必须解释数据缺失和已知风险是否足以否定量化信号。",
    }


def _reflection_text_from_attribution(attribution: dict[str, Any], outcome: dict[str, Any]) -> str:
    label = attribution.get("attribution", "inconclusive")
    ret = float(outcome.get("actual_return") or 0)
    if label == "ex_ante_miss":
        return f"归因为信号时点误判，{outcome.get('horizon_days', 5)}日收益 {ret:.2%}。{attribution.get('strategy_lesson') or '应复核当时已有证据。'}"
    if label == "ex_post_shock":
        return f"归因为信号后新增信息冲击，{outcome.get('horizon_days', 5)}日收益 {ret:.2%}。{attribution.get('risk_monitor_lesson') or '应加强持仓后风险监控。'}"
    if label == "market_regime_shift":
        return f"归因为市场或行业环境变化，{outcome.get('horizon_days', 5)}日收益 {ret:.2%}。"
    if label == "noise":
        return f"结果未显示需要调整策略，{outcome.get('horizon_days', 5)}日收益 {ret:.2%}。"
    return f"证据不足，暂不进入策略学习，{outcome.get('horizon_days', 5)}日收益 {ret:.2%}。"
