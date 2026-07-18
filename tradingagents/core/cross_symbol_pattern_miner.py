"""Cross-symbol pattern miner — discovers statistical patterns across symbols.

Runs after the daily reflection batch to mine statistically significant
patterns from reflected cases. Pure statistics do the heavy lifting
(grouping, significance testing); LLM is only used for human-readable
explanation of significant findings.

Architecture: Statistics first, LLM last. The LLM explains patterns the
statistics found — it never invents patterns from thin air.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PatternBucket:
    """A statistically significant group of cases sharing the same features."""

    dimension: str  # Composite key like "decision=BUY+data_coverage.flow=missing"
    samples: list[dict[str, Any]] = field(default_factory=list)
    n: int = 0  # total cases in the bucket (including neutral/unscored)
    scored_n: int = 0  # cases with was_correct != None (the win_rate denominator)
    correct_count: int = 0  # cases with was_correct == True
    win_rate: float = 0.0  # correct_count / scored_n (0.0 when scored_n == 0)
    avg_return: float = 0.0
    baseline_win_rate: float = 0.0
    lift: float = 0.0
    # Neutral channel (WATCHLIST/HOLD/MONITOR cases; was_correct is None).
    # These accumulate excess-over-benchmark returns instead of a win_rate.
    neutral_excesses: list[float] = field(default_factory=list)
    neutral_avg_excess: float = 0.0
    neutral_consistency: float = 0.0
    # Injection scope derived from the bucket dimension (neutral channel only;
    # directional lessons stay scope="global").
    scope: str = "global"
    target: str = ""


class CrossSymbolPatternMiner:
    """Mine statistically significant cross-symbol patterns from reflected cases.

    The miner loads reflected cases with a configurable lookback window, extracts
    features, groups them into buckets, filters for statistically significant
    deviations, and optionally uses LLM to explain the findings.

    Usage::

        miner = CrossSymbolPatternMiner(db, config)
        result = await miner.mine(lookback_days=30, min_samples=5, min_lift=0.15)
        # result["new_lessons"] → list of strategy_lesson dicts created
    """

    def __init__(
        self,
        db: Any,
        config: dict[str, Any],
        llm: Any = None,
    ):
        self._db = db
        self._config = config
        self._llm = llm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def mine(
        self,
        lookback_days: int = 30,
        min_samples: int = 5,
        min_lift: float = 0.15,
    ) -> dict[str, Any]:
        """Run the full cross-symbol mining pipeline.

        Returns a dict with:
        - ``new_lessons``: list of strategy_lesson dicts that were created/updated
        - ``total_cases``: number of reflected cases loaded
        - ``buckets_evaluated``: number of feature buckets evaluated
        - ``significant_buckets``: number of statistically significant buckets
        - ``neutral_significant_buckets``: significant neutral (excess-return)
          buckets (present only when the neutral channel ran)
        - ``lessons_created``: count of new lessons
        - ``lessons_updated``: count of merged/updated lessons
        """
        result = {
            "new_lessons": [],
            "total_cases": 0,
            "buckets_evaluated": 0,
            "significant_buckets": 0,
            "lessons_created": 0,
            "lessons_updated": 0,
        }

        neutral_enabled = bool(self._config.get("cross_symbol_miner_neutral_enabled", True))
        active_dimensions: set[str] = set()

        # ---- Directional channel: win-rate lift over eligible cases ----
        cases = self._load_reflected_cases(lookback_days)
        result["total_cases"] = len(cases)
        significant: list[PatternBucket] = []
        if len(cases) >= min_samples:
            features = [self._extract_features(c) for c in cases]
            baseline = self._compute_baseline(cases)
            buckets = self._aggregate(cases, features)
            result["buckets_evaluated"] = len(buckets)
            significant = self._filter_significant(buckets, min_samples, min_lift, baseline)
            result["significant_buckets"] = len(significant)
            for bucket in significant:
                finding, adjustment = await self._explain_bucket(bucket)
                lesson = self._save_or_update_lesson(bucket, finding, adjustment, result)
                if lesson:
                    result["new_lessons"].append(lesson)
                active_dimensions.add(bucket.dimension)
        else:
            logger.info(
                "CrossSymbolMiner: only %d eligible reflected cases in last %d days "
                "(need >=%d); skipping directional channel",
                len(cases), lookback_days, min_samples,
            )

        # ---- Neutral channel: excess-return consistency over WATCHLIST/HOLD/
        # MONITOR cases (was_correct is None), which the win-rate gate cannot
        # see. Loaded independently so it works even when eligible directional
        # cases are too few for the directional channel.
        neutral_sig: list[PatternBucket] = []
        if neutral_enabled:
            neutral_sig = self._mine_neutral(lookback_days, min_samples, result)
            for bucket in neutral_sig:
                finding, adjustment = self._template_explain_neutral(bucket)
                lesson = self._save_or_update_neutral_lesson(bucket, finding, adjustment, result)
                if lesson:
                    result["new_lessons"].append(lesson)
                active_dimensions.add(bucket.dimension)

        # ---- Deactivate patterns no longer significant this run, so stale
        # lessons stop being injected into the daily_pipeline LLM review prompt.
        # Skip deactivation on a fully empty run to avoid nuking everything on a
        # transient data gap.
        if significant or neutral_sig:
            self._deactivate_stale_lessons(
                active_dimensions, result, include_neutral=neutral_enabled
            )

        return result

    def _deactivate_stale_lessons(
        self, active_dimensions: set[str], result: dict[str, Any], *, include_neutral: bool = True
    ) -> None:
        """Deactivate cross_symbol_pattern lessons not in ``active_dimensions``.

        Best-effort: a lesson that was significant in a prior run but did not
        recur this run is marked ``active=0`` so it stops being injected into
        the candidate review prompt. Lessons whose dimension still appears in
        this run are left active (they were just updated).

        Neutral-channel lessons carry a ``neutral:`` dimension prefix. When the
        neutral channel did not run this call (``include_neutral=False``), those
        lessons are left untouched so a disabled neutral channel does not
        silently deactivate them.
        """
        try:
            existing = self._db.list_strategy_lessons(
                limit=200, active_only=True, lesson_type="cross_symbol_pattern"
            )
        except Exception as exc:
            logger.debug("CrossSymbolMiner: could not load active lessons for deactivation: %s", exc)
            return

        deactivated = 0
        for lesson in existing:
            payload = lesson.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            dimension = payload.get("dimension")
            if not dimension:
                continue
            if str(dimension).startswith("neutral:") and not include_neutral:
                continue
            if dimension not in active_dimensions:
                try:
                    self._db.deactivate_strategy_lesson(lesson["id"])
                    deactivated += 1
                except Exception as exc:
                    logger.debug("CrossSymbolMiner: failed to deactivate %s: %s", lesson.get("id"), exc)
        if deactivated:
            result["lessons_deactivated"] = deactivated
            logger.info("CrossSymbolMiner deactivated %d stale lessons", deactivated)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_reflected_cases(self, lookback_days: int) -> list[dict[str, Any]]:
        """Load reflected cases within the lookback window.

        ``eligible_only=True`` restricts to cases flagged for strategy learning
        (final_decision=BUY that the reflection engine marked eligible). This
        keeps the miner focused on actionable BUY signals; widen to False if
        sample volume is too low for a given lookback window.
        """
        try:
            return self._db.list_reflection_cases(
                limit=500,
                status="reflected",
                eligible_only=True,
                lookback_days=lookback_days,
            )
        except Exception as exc:
            logger.warning("CrossSymbolMiner: failed to load reflected cases: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------

    def _extract_features(self, case: dict[str, Any]) -> dict[str, str]:
        """Extract aggregatable features from a reflected case's JSON payloads.

        Returns a dict of ``dimension_key → value`` pairs. Each key is a
        composite dimension name like ``"decision"``, ``"quant_score"``,
        ``"data_coverage.flow"``, etc.
        """
        feats: dict[str, str] = {}

        snapshot = self._safe_json(case.get("snapshot_payload"))
        attribution = self._safe_json(case.get("attribution_payload"))
        outcome = self._safe_json(case.get("outcome_payload"))

        # Decision type
        final_decision = str(snapshot.get("final_decision", "")).strip().upper()
        if final_decision:
            feats["final_decision"] = final_decision

        # Quant score bucket
        quant_score = snapshot.get("quant_score")
        if quant_score is not None:
            try:
                qs = float(quant_score)
                if qs < 60:
                    feats["quant_score"] = "<60"
                elif qs <= 75:
                    feats["quant_score"] = "60-75"
                else:
                    feats["quant_score"] = "75+"
            except (TypeError, ValueError):
                pass

        # Industry
        industry = str(snapshot.get("industry", "")).strip()
        if industry:
            feats["industry"] = self._normalize_industry(industry)

        # Board
        board = str(snapshot.get("board", "")).strip().lower()
        if board:
            feats["board"] = board

        # Data coverage dimensions
        data_cov = snapshot.get("data_coverage")
        if isinstance(data_cov, dict):
            for dim in ("valuation", "flow", "quality", "liquidity", "momentum", "risk_control"):
                status = str(data_cov.get(dim, "")).strip().lower()
                if status:
                    feats[f"data_coverage.{dim}"] = status

        # Risk flags (flatten list)
        risk_flags = snapshot.get("risk_flags")
        if isinstance(risk_flags, list):
            for flag in risk_flags:
                flag_str = str(flag).strip().lower()
                if flag_str:
                    feats[f"risk_flag.{flag_str}"] = "present"

        # Catalyst strength
        catalyst = str(snapshot.get("catalyst_strength", "")).strip().lower()
        if catalyst:
            feats["catalyst_strength"] = catalyst

        # LLM view
        llm_view = str(snapshot.get("llm_view", "")).strip().lower()
        if llm_view:
            feats["llm_view"] = llm_view

        # Risk assessment
        risk_assess = str(snapshot.get("risk_assessment", "")).strip().lower()
        if risk_assess:
            feats["risk_assessment"] = risk_assess

        # Attribution type
        attr_type = str(attribution.get("attribution", "")).strip().lower()
        if attr_type:
            feats["attribution"] = attr_type

        # Attribution confidence
        attr_conf = str(attribution.get("confidence", "")).strip().lower()
        if attr_conf:
            feats["attribution_confidence"] = attr_conf

        # Return bucket
        actual_return = outcome.get("actual_return")
        if actual_return is not None:
            try:
                ret = float(actual_return)
                if ret < -0.05:
                    feats["return"] = "<-5%"
                elif ret <= 0.03:
                    # Covers the -5%..+3% band (the <-5% branch above already
                    # captured returns below -5%).
                    feats["return"] = "-5~+3%"
                else:
                    feats["return"] = ">+3%"
            except (TypeError, ValueError):
                pass

        return feats

    def _normalize_industry(self, industry: str) -> str:
        """Normalize industry names to a coarse grouping."""
        industry_lower = industry.lower()
        # Map to coarse groups
        for coarse, keywords in _INDUSTRY_GROUPS.items():
            if any(kw in industry_lower for kw in keywords):
                return coarse
        return industry

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def _compute_baseline(self, cases: list[dict[str, Any]]) -> float:
        """Compute the baseline win_rate across all cases."""
        correct = 0
        total = 0
        for case in cases:
            outcome = self._safe_json(case.get("outcome_payload"))
            was_correct = outcome.get("was_correct")
            if was_correct is not None:
                total += 1
                if was_correct:
                    correct += 1
        return correct / total if total > 0 else 0.5

    def _aggregate(
        self,
        cases: list[dict[str, Any]],
        features: list[dict[str, str]],
    ) -> list[PatternBucket]:
        """Group cases by shared feature dimensions and compute statistics.

        Each case is assigned to multiple buckets — one per feature dimension.
        For cross-dimension combinations (v1 priorities), we also build composite
        keys.
        """
        buckets: dict[str, PatternBucket] = {}

        for case, feats in zip(cases, features, strict=False):
            outcome = self._safe_json(case.get("outcome_payload"))
            was_correct = outcome.get("was_correct")
            actual_return = outcome.get("actual_return")
            try:
                ret = float(actual_return) if actual_return is not None else 0.0
            except (TypeError, ValueError):
                ret = 0.0

            # Single-dimension buckets
            for dim_key, dim_value in feats.items():
                composite = f"{dim_key}={dim_value}"
                self._add_to_bucket(buckets, composite, case, was_correct, ret)

            # v1 priority cross-dimension combinations
            self._add_cross_dimension_buckets(buckets, feats, case, was_correct, ret)

        # Sort by sample size descending
        result = sorted(buckets.values(), key=lambda b: b.n, reverse=True)
        return result

    def _add_to_bucket(
        self,
        buckets: dict[str, PatternBucket],
        key: str,
        case: dict[str, Any],
        was_correct: bool | None,
        ret: float,
    ) -> None:
        if key not in buckets:
            buckets[key] = PatternBucket(dimension=key)
        bucket = buckets[key]
        bucket.samples.append({"symbol": case.get("symbol", ""), "id": case.get("id", "")})
        bucket.n += 1
        if was_correct is not None:
            bucket.scored_n += 1
            if was_correct:
                bucket.correct_count += 1
            # Recompute win_rate exactly from counts (no incremental float drift).
            bucket.win_rate = bucket.correct_count / bucket.scored_n
        bucket.avg_return = (
            (bucket.avg_return * (bucket.n - 1) + ret) / bucket.n
        )

    def _add_cross_dimension_buckets(
        self,
        buckets: dict[str, PatternBucket],
        feats: dict[str, str],
        case: dict[str, Any],
        was_correct: bool | None,
        ret: float,
    ) -> None:
        """Add v1 priority cross-dimension combinations."""
        # Combo 1: data_coverage.valuation=available + data_coverage.flow=missing + final_decision=BUY
        v_f = feats.get("data_coverage.valuation", "")
        f_f = feats.get("data_coverage.flow", "")
        d_f = feats.get("final_decision", "")
        if v_f == "available" and f_f == "missing" and d_f == "BUY":
            key = "valuation_avail+flow_missing+BUY"
            self._add_to_bucket(buckets, key, case, was_correct, ret)

        # Combo 2: quant_score>=75 + catalyst_strength∈{speculative,none} + risk∈{high,critical}
        qs_f = feats.get("quant_score", "")
        cs_f = feats.get("catalyst_strength", "")
        ra_f = feats.get("risk_assessment", "")
        if qs_f == "75+" and cs_f in ("speculative", "none") and ra_f in ("high", "critical"):
            key = "high_quant+weak_catalyst+high_risk"
            self._add_to_bucket(buckets, key, case, was_correct, ret)

        # Combo 3: attribution=ex_ante_miss + any decision
        at_f = feats.get("attribution", "")
        if at_f == "ex_ante_miss":
            key = f"ex_ante_miss+decision={d_f}" if d_f else "ex_ante_miss"
            self._add_to_bucket(buckets, key, case, was_correct, ret)

    # ------------------------------------------------------------------
    # Significance filtering
    # ------------------------------------------------------------------

    def _filter_significant(
        self,
        buckets: list[PatternBucket],
        min_samples: int,
        min_lift: float,
        baseline_win_rate: float,
    ) -> list[PatternBucket]:
        """Filter buckets: scored_n >= min_samples AND |win_rate - baseline| >= min_lift.

        ``scored_n`` (not total ``n``) is the threshold because win_rate is
        computed only from scored cases (was_correct != None). A bucket with
        many neutral/unscored cases but few scored ones does not have a
        reliable win_rate.
        """
        significant: list[PatternBucket] = []
        for bucket in buckets:
            if bucket.scored_n < min_samples:
                continue
            bucket.baseline_win_rate = baseline_win_rate
            bucket.lift = bucket.win_rate - baseline_win_rate
            if abs(bucket.lift) >= min_lift:
                significant.append(bucket)
        return significant

    # ------------------------------------------------------------------
    # LLM explanation + template fallback
    # ------------------------------------------------------------------

    async def _explain_bucket(self, bucket: PatternBucket) -> tuple[str, str]:
        """Explain a significant bucket using LLM, falling back to template."""
        # Try LLM if available
        if self._llm is not None:
            try:
                finding, adjustment = await self._llm_explain(bucket)
                if finding:
                    return finding, adjustment
            except Exception as exc:
                logger.debug("CrossSymbolMiner LLM explanation failed: %s", exc)

        # Template fallback
        return self._template_explain(bucket)

    async def _llm_explain(self, bucket: PatternBucket) -> tuple[str, str]:
        """Use LLM to generate a natural-language finding + adjustment.

        Reuses the LLM client passed at construction (``self._llm``) so callers
        can inject a pre-configured client; falls back to building one from
        config when none was injected.
        """
        llm = self._llm
        if llm is None:
            from tradingagents.llm_clients import create_llm_client

            client = create_llm_client(
                provider=self._config.get("llm_provider", "openai"),
                model=self._config.get("quick_think_llm", "gpt-5.4-mini"),
                base_url=self._config.get("backend_url"),
            )
            llm = client.get_llm()

        prompt = (
            "你是 A 股量化策略分析师。下方是统计发现的跨标的显著模式，请仅基于统计量解释，不要发明新模式。\n\n"
            "请输出：\n"
            "1. finding（1-2 句中文，解释可能因果，用资金流/换手/板块轮动/估值分位/数据质量等 A 股因子语义）\n"
            "2. suggested_adjustment（1-2 句中文，必须可执行，如'该类候选 quant_score 上调/下调 X 分'或'要求额外验证 Y'，不要泛泛说'注意风险'）\n\n"
            f"模式维度: {bucket.dimension}\n"
            f"样本数: {bucket.n}（可评分: {bucket.scored_n}）\n"
            f"胜率: {bucket.win_rate:.1%}\n"
            f"基准胜率: {bucket.baseline_win_rate:.1%}\n"
            f"偏离: {bucket.lift:+.1%}\n"
            f"平均收益: {bucket.avg_return:+.2%}\n\n"
            "严格 JSON 输出: {\"finding\": \"...\", \"suggested_adjustment\": \"...\"}"
        )

        from langchain_core.messages import HumanMessage

        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = getattr(response, "content", "")
        if not content:
            return "", ""

        try:
            data = json.loads(content)
            return data.get("finding", ""), data.get("suggested_adjustment", "")
        except (json.JSONDecodeError, TypeError):
            return content, ""

    def _template_explain(self, bucket: PatternBucket) -> tuple[str, str]:
        """Generate a template-based finding when LLM is unavailable."""
        direction = "高于" if bucket.lift > 0 else "低于"
        finding = (
            f"近30天，{bucket.dimension} 的候选胜率 {bucket.win_rate:.0%}"
            f"（基准 {bucket.baseline_win_rate:.0%}，{direction}基准 {abs(bucket.lift):.0%}，"
            f"n={bucket.scored_n}）"
        )
        adjustment = (
            f"对于 {bucket.dimension} 的候选，"
            f"{'可适当提高置信度' if bucket.lift > 0 else '建议降低权重或要求额外验证'}。"
        )
        return finding, adjustment

    # ------------------------------------------------------------------
    # Persistence: dedup, merge, save
    # ------------------------------------------------------------------

    def _save_or_update_lesson(
        self,
        bucket: PatternBucket,
        finding: str,
        adjustment: str,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Save a new lesson or merge with an existing one (accumulate evidence).

        The lesson ID is derived from ``bucket.dimension`` (not the finding
        text), so it stays stable across runs even when the embedded win_rate /
        sample numbers fluctuate. Dedup matches on the dimension — different
        dimensions are NEVER merged, even if their template findings share many
        common words.
        """
        # Stable ID keyed by dimension, not by the volatile finding text.
        lesson_id = _lesson_id_from_finding(bucket.dimension)
        payload = {
            "dimension": bucket.dimension,
            "n": bucket.n,
            "scored_n": bucket.scored_n,
            "correct_count": bucket.correct_count,
            "win_rate": bucket.win_rate,
            "baseline": bucket.baseline_win_rate,
            "lift": bucket.lift,
            "avg_return": bucket.avg_return,
        }
        confidence = self._lift_to_confidence(bucket.lift)

        # Check existing lessons for a same-dimension match.
        try:
            existing = self._db.list_strategy_lessons(
                limit=100, active_only=False, lesson_type="cross_symbol_pattern"
            )
        except Exception:
            existing = []

        merged = self._find_and_merge(existing, lesson_id, bucket.dimension)
        if merged:
            try:
                updated = self._db.update_strategy_lesson(
                    merged["id"],
                    finding=finding,
                    suggested_adjustment=adjustment,
                    evidence_count=bucket.n,
                    confidence=confidence,
                    payload=payload,
                )
                result["lessons_updated"] += 1
                return updated
            except Exception as exc:
                logger.warning("CrossSymbolMiner: failed to update lesson: %s", exc)
                return None

        # Create new lesson
        try:
            self._db.save_strategy_lesson(
                lesson_id=lesson_id,
                lesson_type="cross_symbol_pattern",
                scope="global",
                finding=finding,
                suggested_adjustment=adjustment,
                target="",
                evidence_count=bucket.n,
                confidence=confidence,
                active=True,
                payload=payload,
            )
            result["lessons_created"] += 1
            return {"id": lesson_id, "finding": finding, "adjustment": adjustment}
        except Exception as exc:
            logger.warning("CrossSymbolMiner: failed to save lesson: %s", exc)
            return None

    def _find_and_merge(
        self,
        existing: list[dict[str, Any]],
        lesson_id: str,
        dimension: str,
    ) -> dict[str, Any] | None:
        """Find an existing lesson for the SAME dimension only.

        Matches by exact lesson ID (dimension-derived), or by the ``dimension``
        field stored in the lesson payload (covers lessons created before the
        ID scheme was dimension-based). Different dimensions are never merged.
        """
        for e in existing:
            if e.get("id") == lesson_id:
                return e
        for e in existing:
            payload = e.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            if payload.get("dimension") == dimension:
                return e
        return None

    # ------------------------------------------------------------------
    # Neutral channel (WATCHLIST/HOLD/MONITOR excess-return patterns)
    # ------------------------------------------------------------------

    def _load_neutral_cases(self, lookback_days: int) -> list[dict[str, Any]]:
        """Load reflected neutral cases (was_correct is None) with excess return.

        Neutral decisions are ``eligible_for_strategy_learning=False``, so the
        directional loader (``eligible_only=True``) skips them entirely. This
        loads with ``eligible_only=False`` and keeps only neutral cases that
        carry a numeric ``excess_return`` in their outcome payload.
        """
        try:
            cases = self._db.list_reflection_cases(
                limit=500,
                status="reflected",
                eligible_only=False,
                lookback_days=lookback_days,
            )
        except Exception as exc:
            logger.warning("CrossSymbolMiner: failed to load neutral cases: %s", exc)
            return []
        neutral: list[dict[str, Any]] = []
        for case in cases:
            outcome = self._safe_json(case.get("outcome_payload"))
            if outcome.get("was_correct") is not None:
                continue
            if not isinstance(outcome.get("excess_return"), (int, float)):
                continue
            neutral.append(case)
        return neutral

    @staticmethod
    def _neutral_scope_target(dim_key: str, dim_value: str) -> tuple[str | None, str]:
        """Map a feature dimension to an injectable (scope, target).

        Only dimensions the daily-pipeline injection can precisely match are
        kept, so neutral lessons target the right candidates instead of being
        broadcast globally:
        - ``industry=X``            -> ("industry", X)
        - ``board=X``               -> ("board", X)
        - ``data_coverage.f=missing`` -> ("factor", f)
        Any other dimension returns ``(None, "")`` and is skipped.
        """
        if dim_key == "industry":
            return "industry", dim_value
        if dim_key == "board":
            return "board", dim_value
        if dim_key.startswith("data_coverage.") and dim_value == "missing":
            return "factor", dim_key.split(".", 1)[1]
        return None, ""

    def _aggregate_neutral(self, cases: list[dict[str, Any]]) -> list[PatternBucket]:
        """Group neutral cases by injectable feature dimensions, tracking excess.

        Each bucket dimension is prefixed with ``neutral:`` to keep a separate
        lesson-ID namespace from the directional channel (a directional and a
        neutral bucket may share the same base feature, e.g. ``industry=有色``).
        """
        buckets: dict[str, PatternBucket] = {}
        for case in cases:
            outcome = self._safe_json(case.get("outcome_payload"))
            try:
                excess = float(outcome.get("excess_return"))
            except (TypeError, ValueError):
                continue
            feats = self._extract_features(case)
            for dim_key, dim_value in feats.items():
                scope, target = self._neutral_scope_target(dim_key, dim_value)
                if scope is None:
                    continue
                dimension = f"neutral:{dim_key}={dim_value}"
                bucket = buckets.get(dimension)
                if bucket is None:
                    bucket = PatternBucket(dimension=dimension)
                    bucket.scope = scope
                    bucket.target = target
                    buckets[dimension] = bucket
                bucket.neutral_excesses.append(excess)
                bucket.samples.append({"symbol": case.get("symbol", ""), "id": case.get("id", "")})
        return sorted(buckets.values(), key=lambda b: len(b.neutral_excesses), reverse=True)

    def _mine_neutral(
        self, lookback_days: int, min_samples: int, result: dict[str, Any]
    ) -> list[PatternBucket]:
        """Find neutral buckets with a consistent, material excess-over-benchmark.

        A bucket qualifies when it has ``>= cross_symbol_miner_neutral_min_samples``
        cases, an absolute mean excess ``>= cross_symbol_miner_min_excess``, and a
        same-sign consistency ``>= cross_symbol_miner_min_consistency``. The sign
        of the mean excess decides the pattern: positive => missed_upside (filter
        too strict), negative => validated_avoidance (caution paid off).
        """
        result["neutral_significant_buckets"] = 0
        neutral_min_samples = int(
            self._config.get("cross_symbol_miner_neutral_min_samples", min_samples)
        )
        cases = self._load_neutral_cases(lookback_days)
        if len(cases) < neutral_min_samples:
            return []
        min_excess = float(self._config.get("cross_symbol_miner_min_excess", 0.05))
        min_consistency = float(self._config.get("cross_symbol_miner_min_consistency", 0.6))
        significant: list[PatternBucket] = []
        for bucket in self._aggregate_neutral(cases):
            excesses = bucket.neutral_excesses
            n = len(excesses)
            if n < neutral_min_samples:
                continue
            avg = sum(excesses) / n
            if avg >= 0:
                same = sum(1 for e in excesses if e > 0)
            else:
                same = sum(1 for e in excesses if e < 0)
            consistency = same / n
            bucket.n = n
            bucket.neutral_avg_excess = avg
            bucket.neutral_consistency = consistency
            if abs(avg) >= min_excess and consistency >= min_consistency:
                significant.append(bucket)
        result["neutral_significant_buckets"] = len(significant)
        return significant

    def _template_explain_neutral(self, bucket: PatternBucket) -> tuple[str, str]:
        """Human-readable finding/adjustment for a significant neutral bucket."""
        base = bucket.dimension.replace("neutral:", "", 1)
        avg = bucket.neutral_avg_excess
        n = len(bucket.neutral_excesses)
        cons = bucket.neutral_consistency
        if avg > 0:
            finding = (
                f"样本期内，{base} 的观望决策平均跑赢基准 {avg:.1%}"
                f"（n={n}，同向 {cons:.0%}），疑似过滤/降级过严错过机会。"
            )
            adjustment = (
                f"复核 {base} 类候选被降级为观望的 gate_reasons/阈值，评估是否放宽。"
            )
        else:
            finding = (
                f"样本期内，{base} 的观望决策平均跑输基准 {abs(avg):.1%}"
                f"（n={n}，同向 {cons:.0%}），观望规避有效。"
            )
            adjustment = (
                f"沉淀 {base} 作为同类候选优先降级/加强风控的依据。"
            )
        return finding, adjustment

    def _save_or_update_neutral_lesson(
        self,
        bucket: PatternBucket,
        finding: str,
        adjustment: str,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Persist a neutral pattern lesson with a precise injection scope.

        Shares ``lesson_type="cross_symbol_pattern"`` (so it flows through the
        same injection/deactivation machinery) but uses the bucket's derived
        scope/target and an excess-based confidence. The ``neutral:`` dimension
        prefix keeps its lesson ID from colliding with directional lessons.
        """
        lesson_id = _lesson_id_from_finding(bucket.dimension)
        n = len(bucket.neutral_excesses)
        pattern = "missed_upside" if bucket.neutral_avg_excess > 0 else "validated_avoidance"
        payload = {
            "dimension": bucket.dimension,
            "kind": "neutral",
            "pattern": pattern,
            "n": n,
            "avg_excess": round(bucket.neutral_avg_excess, 4),
            "consistency": round(bucket.neutral_consistency, 4),
            "scope": bucket.scope,
            "target": bucket.target,
        }
        confidence = self._excess_to_confidence(bucket.neutral_avg_excess)

        try:
            existing = self._db.list_strategy_lessons(
                limit=100, active_only=False, lesson_type="cross_symbol_pattern"
            )
        except Exception:
            existing = []

        merged = self._find_and_merge(existing, lesson_id, bucket.dimension)
        if merged:
            try:
                updated = self._db.update_strategy_lesson(
                    merged["id"],
                    finding=finding,
                    suggested_adjustment=adjustment,
                    evidence_count=n,
                    confidence=confidence,
                    payload=payload,
                )
                result["lessons_updated"] += 1
                return updated
            except Exception as exc:
                logger.warning("CrossSymbolMiner: failed to update neutral lesson: %s", exc)
                return None

        try:
            self._db.save_strategy_lesson(
                lesson_id=lesson_id,
                lesson_type="cross_symbol_pattern",
                scope=bucket.scope,
                finding=finding,
                suggested_adjustment=adjustment,
                target=bucket.target,
                evidence_count=n,
                confidence=confidence,
                active=True,
                payload=payload,
            )
            result["lessons_created"] += 1
            return {"id": lesson_id, "finding": finding, "adjustment": adjustment}
        except Exception as exc:
            logger.warning("CrossSymbolMiner: failed to save neutral lesson: %s", exc)
            return None

    def _excess_to_confidence(self, avg_excess: float) -> str:
        """Map mean excess-return magnitude to a confidence level."""
        a = abs(avg_excess)
        if a >= 0.10:
            return "high"
        if a >= 0.05:
            return "medium"
        return "low"

    def _lift_to_confidence(self, lift: float) -> str:
        """Map lift magnitude to confidence level."""
        al = abs(lift)
        if al >= 0.30:
            return "high"
        elif al >= 0.15:
            return "medium"
        return "low"

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_json(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}


# ---------------------------------------------------------------------------
# Industry normalization groups
# ---------------------------------------------------------------------------

_INDUSTRY_GROUPS: dict[str, list[str]] = {
    "白酒": ["白酒", "酒"],
    "新能源": ["新能源", "电池", "锂电", "光伏", "风电", "储能"],
    "半导体": ["半导体", "芯片", "集成电路"],
    "金融": ["银行", "证券", "保险", "金融"],
    "有色": ["有色", "黄金", "铜", "铝", "稀土", "矿业"],
    "医药": ["医药", "生物", "医疗", "制药"],
    "消费": ["消费", "食品", "饮料", "家电", "零售"],
    "制造": ["制造", "机械", "重工", "装备"],
    "科技": ["科技", "软件", "计算机", "通信", "电子"],
    "地产": ["地产", "房地产", "建筑"],
    "化工": ["化工", "化学"],
    "汽车": ["汽车", "整车", "零部件"],
}


# ---------------------------------------------------------------------------
# Lesson ID and dedup helpers
# ---------------------------------------------------------------------------


def _lesson_id_from_finding(finding: str) -> str:
    """Generate a stable lesson ID from the finding text."""
    h = hashlib.sha256(finding.encode("utf-8")).hexdigest()[:16]
    return f"csp_{h}"
