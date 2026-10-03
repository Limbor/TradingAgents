"""Auditable research reuse and extractive, token-bounded agent handoffs.

Full reports and debate transcripts remain in state/storage. These helpers
build model-facing views only, without another model call or invented facts.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from tradingagents.core.llm_usage import estimate_tokens
from tradingagents.core.model_policy import resolve_model

REPORT_ROLES = {
    "market_report": ("market", "Market Analyst", "技术面"),
    "fundamentals_report": ("fundamentals", "Fundamentals Analyst", "基本面"),
    "news_report": ("news", "News Analyst", "新闻与公告"),
    "sentiment_report": ("social", "Sentiment Analyst", "情绪"),
}
REPORT_KEYS = (*REPORT_ROLES, "investment_plan", "trader_investment_plan", "final_trade_decision")
REUSE_TTL_SECONDS = 1800
_GAP = re.compile(r"缺失|缺少|未获取|未取得|未提供|不可用|无法核验|无法验证|证据不足|待核验|missing|unavailable|not available|not verified|insufficient", re.I)
_RISK = re.compile(r"风险|回撤|止损|警示|退市|停牌|处罚|诉讼|质押|减持|不能|不得|限制|risk|drawdown|stop.loss|must not|cannot|halt|penalt", re.I)
_CONCLUSION = re.compile(r"结论|判断|建议|观点|前提|条件|展望|conclusion|verdict|recommend|thesis|outlook|condition", re.I)
_SOURCE = re.compile(r"来源|截至|基准日|报告期|公告日|source|as.of|cutoff|https?://|\d{4}-\d{2}-\d{2}", re.I)
_METRIC = re.compile(r"\d(?:[\d.,+-]*)(?:\s*(?:%|亿元|万元|元|股|倍|million|billion|USD|CNY)|\s*[\u4e00-\u9fff]{0,4}(?:营收|利润|现金流))", re.I)


def research_policy_key(config: dict, asset_type: str = "stock", memory_prompt: str = "") -> str:
    policy = {"provider": config.get("llm_provider"), "model": resolve_model(config),
              "backend_url": config.get("backend_url"), "data_vendors": config.get("data_vendors"),
              "tool_vendors": config.get("tool_vendors"),
              "style": config.get("investment_style", "medium_term"), "asset_type": asset_type,
              "language": config.get("output_language"), "thinking": config.get("qianwen_thinking", False)}
    policy["memory"] = hashlib.sha256(memory_prompt.encode()).hexdigest()
    policy["skill_document"] = config.get("skill_document_hash")
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()


def _timestamp(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else None
    except (TypeError, ValueError):
        return None


def select_reusable_research(records: list[dict], *, symbol: str, as_of_date: str,
                             info_cutoff: str, policy_key: str, now=None) -> dict:
    """Newest role attempt wins: failures must never expose an older success."""
    now = now or datetime.now(timezone.utc)
    cutoff = _timestamp(info_cutoff)
    selected, attempted = {}, set()
    for record in reversed(records):
        key = next((key for key, (_, role, _) in REPORT_ROLES.items() if record.get("role") == role), None)
        if not key or record.get("kind") != "agent" or symbol not in record.get("symbols", []):
            continue
        if key in attempted:
            continue
        attempted.add(key)
        provenance = record.get("reused_from") or {}
        completed = _timestamp(provenance.get("completed_at") or record.get("completed_at"))
        original_cutoff = _timestamp(provenance.get("info_cutoff") or record.get("info_cutoff"))
        report = (record.get("output") or {}).get(key)
        observations = record.get("tool_observations") or []
        if (record.get("status") != "completed" or not record.get("source_task_completed") or
                record.get("as_of_date") != as_of_date or record.get("research_policy_key") != policy_key or
                not completed or not cutoff or not original_cutoff or original_cutoff > cutoff or
                original_cutoff.date() != cutoff.date() or not 0 <= (now - completed).total_seconds() <= REUSE_TTL_SECONDS or
                not isinstance(report, str) or not report.strip() or _GAP.search(report) or
                any(row.get("status") != "completed" for row in observations) or
                not observations or not record.get("evidence_refs")):
            continue
        selected[key] = {"report": report, "role": record["role"],
                         "tool_observations": observations, "evidence_refs": record["evidence_refs"],
                         "source": {"run_id": provenance.get("run_id") or record["run_id"],
                                    "task_id": provenance.get("task_id") or record["root_id"],
                                    "symbol": symbol, "policy_key": policy_key,
                                    "completed_at": completed.isoformat(), "info_cutoff": original_cutoff.isoformat(),
                                    "as_of_date": as_of_date}}
    return selected


def research_still_valid(cached, context, state) -> bool:
    source = cached["source"]
    completed = _timestamp(source.get("completed_at"))
    return bool(context.research_reuse_allowed and completed and 0 <= (datetime.now(timezone.utc) - completed).total_seconds() <= REUSE_TTL_SECONDS
                and source.get("as_of_date") == context.as_of_date
                and source.get("symbol") == str(state.get("company_of_interest", "")).upper().replace(".SS", ".SH")
                and source.get("policy_key") == research_policy_key(context.config, state.get("asset_type", "stock"), context.memory_prompt))


def load_reusable_research(db, conversation_id, task_id, **scope) -> dict:
    if db is None or not conversation_id or not task_id:
        return {}
    with db._conn() as conn:
        tasks = conn.execute(
            "SELECT id,status FROM agent_tasks WHERE conversation_id=? AND rowid < "
            "(SELECT rowid FROM agent_tasks WHERE id=? AND conversation_id=?) "
            "ORDER BY rowid DESC LIMIT 12", (conversation_id, task_id, conversation_id),
        ).fetchall()
    records = [{**record, "source_task_completed": task["status"] == "completed"}
               for task in reversed(tasks) for record in db.list_agent_runtime(task["id"])]
    return select_reusable_research(records, **scope)


def report_digest(text: str, *, as_of_date=None, source=None, observations=(), budget=1000) -> dict:
    """Extract complete source lines/sentences, prioritizing gaps and risks.

    No fragment is rewritten. Omitted material is explicitly marked; a digest
    is not evidence of complete coverage or of an absent risk.
    """
    buckets = {"gaps": [], "risks": [], "conclusions": [], "facts": [], "sources": []}
    units = list(dict.fromkeys(part.strip() for line in text.splitlines()
                              for part in re.split(r"(?<=[。！？])|(?<=[.!?])\s+(?=[A-Za-z])", line) if part.strip()))
    for unit in units:
        category = ("gaps" if _GAP.search(unit) else "risks" if _RISK.search(unit) else
                    "conclusions" if _CONCLUSION.search(unit) else "sources" if _SOURCE.search(unit) else "facts")
        buckets[category].append(unit)
    digest = {"as_of_date": as_of_date, "full_report_ref": source, **{key: [] for key in buckets},
              "partial": False, "omitted_units": 0,
              "notice": "原文摘取；未摘入的内容不代表不存在。只引用已列事实，保留数据缺口和判断前提。"}
    # Actual retrieval failures survive even when prose omits them.
    for row in observations:
        if row.get("status") != "completed":
            buckets["gaps"].insert(0, f"取证未完成：{row.get('tool')}（{row.get('status')}，{row.get('error_type') or '返回异常'}）")
    for category, candidates in buckets.items():
        candidates = sorted(candidates, key=lambda unit: not bool(_METRIC.search(unit)))
        for unit in candidates:
            digest[category].append(unit)
            if estimate_tokens(digest) > budget:
                digest[category].pop()
                digest["omitted_units"] += 1
    digest["partial"] = bool(digest["omitted_units"])
    if digest["partial"]:
        digest["notice"] += "摘要有省略，不能声称已核对全部风险或数据完整。"
    return digest


def compact_text(text: str, budget=1000, **metadata) -> str:
    if estimate_tokens(text) <= budget:
        return text
    return json.dumps(report_digest(text, budget=budget, **metadata), ensure_ascii=False)


def compact_research_state(state: dict) -> tuple[dict, dict]:
    """Return a model-facing copy; never mutate or shorten the stored state."""
    view = dict(state)
    before, after = 0, 0
    receipts = {row.get("role"): row for row in state.get("specialist_results", [])}
    for key in REPORT_KEYS:
        text = state.get(key)
        if not isinstance(text, str) or not text:
            continue
        row = receipts.get((REPORT_ROLES.get(key) or (None, None))[1], {})
        view[key] = compact_text(text, as_of_date=state.get("trade_date"),
                                 source={"run_id": (row.get("reused_from") or {}).get("run_id") or row.get("run_id"), "field": key},
                                 observations=row.get("tool_observations", []))
        before += estimate_tokens(text)
        after += estimate_tokens(view[key])
    for key in ("investment_debate_state", "risk_debate_state"):
        debate = state.get(key)
        if not isinstance(debate, dict):
            continue
        view[key] = dict(debate)
        for field, text in debate.items():
            if isinstance(text, str) and text:
                view[key][field] = compact_text(text, budget=1000 if field == "history" else 600,
                                                 as_of_date=state.get("trade_date"), source={"field": f"{key}.{field}"})
                before += estimate_tokens(text)
                after += estimate_tokens(view[key][field])
    return view, {"input_tokens_before": before, "input_tokens_after": after,
                  "saved_tokens_estimate": max(0, before - after), "method": "extractive"}


def restore_debate_history(output: dict, original: dict, view: dict) -> dict:
    """Nodes append to the bounded view; restore full history plus new text."""
    output = dict(output)
    for key in ("investment_debate_state", "risk_debate_state"):
        if not isinstance(output.get(key), dict):
            continue
        output[key] = dict(output[key])
        for field, text in output[key].items():
            if field != "history" and not field.endswith("_history"):
                continue
            prefix = (view.get(key) or {}).get(field)
            full = (original.get(key) or {}).get(field)
            if isinstance(text, str) and isinstance(prefix, str) and isinstance(full, str) and text.startswith(prefix):
                output[key][field] = full + text[len(prefix):]
    return output
