"""Provider usage receipts and reference prices; never infer a cash debit."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

# CNY per million tokens, checked against the channel's model market.
# Unknown channels/models deliberately have no price instead of a zero price.
PRICE_DATE = "2026-10-03"
PRICES = {
    ("qianwen", "qwen3.8-max"): (12.0, 1.5, 36.0, 15.0, 1.0),
    ("qianwen", "qwen3.8-flash"): (0.8, 0.1, 2.7, 1.25, 0.1),
}
PRICE_SOURCE = "https://www.qianwenai.com/models/"


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if math.isfinite(value) and value >= 0 and value == int(value) else None


def normalize_usage(value: Any) -> dict | None:
    """Normalize an AIMessage or serialized receipt, including genuine zeros."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("raw"), (dict,)) or hasattr(value.get("raw"), "model_dump"):
        return normalize_usage(value["raw"])
    metadata = value.get("response_metadata") or {}
    usage = value.get("usage_metadata") or {}
    raw = metadata.get("token_usage") or metadata.get("usage") or value.get("token_usage") or {}
    input_tokens = _count(usage.get("input_tokens", raw.get("prompt_tokens", raw.get("input_tokens"))))
    output_tokens = _count(usage.get("output_tokens", raw.get("completion_tokens", raw.get("output_tokens"))))
    if input_tokens is None or output_tokens is None:
        return None
    input_details = usage.get("input_token_details") or {}
    output_details = usage.get("output_token_details") or {}
    prompt_details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}
    completion_details = raw.get("completion_tokens_details") or raw.get("output_tokens_details") or {}
    cached = _count(input_details.get("cache_read", prompt_details.get("cached_tokens",
                    raw.get("cache_read_input_tokens", raw.get("prompt_cache_hit_tokens")))))
    created = _count(input_details.get("cache_creation", prompt_details.get("cache_creation_input_tokens", raw.get("cache_creation_input_tokens", 0))))
    if not usage and "cache_read_input_tokens" in raw:
        # Anthropic's raw input excludes cached inputs; LangChain's unified
        # usage already includes them, so adjust only the native receipt.
        input_tokens += (cached or 0) + (created or 0)
    if cached is not None and cached + (created or 0) > input_tokens:
        return None
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens, "cache_read_tokens": cached,
            "cache_creation_tokens": created or 0,
            "reasoning_tokens": _count(output_details.get("reasoning", completion_details.get("reasoning_tokens"))),
            "cache_mode": "explicit" if "cache_creation_input_tokens" in prompt_details else "implicit",
            "request_id": metadata.get("id") or metadata.get("request_id") or value.get("id")}


def estimate_tokens(value: Any) -> int:
    """Conservative text estimate for the execution guard, not API usage."""
    import json
    if hasattr(value, "to_messages"):
        value = value.to_messages()
    if isinstance(value, list):
        value = [item.model_dump() if hasattr(item, "model_dump") else item for item in value]
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    cjk = sum("\u3400" <= char <= "\u9fff" for char in text)
    return math.ceil(cjk * 1.2 + (len(text) - cjk) / 3) + 256


def summarize_usage(records: list[dict]) -> dict:
    """Sum model calls only; deduplicate host links and raw response receipts."""
    rows = defaultdict(list)
    seen = set()
    for record in records:
        if record.get("kind") != "model" or record.get("request_started") is False:
            continue
        receipt = record.get("usage") or normalize_usage(record.get("output"))
        provider, model = record.get("provider", ""), record.get("model", "")
        identity = (provider, model, (receipt or {}).get("request_id") or record["run_id"])
        if identity in seen:
            continue
        seen.add(identity)
        rows[(record.get("role", "模型"), provider, model)].append((receipt, record.get("status")))
    groups = []
    for (role, provider, model), calls in rows.items():
        group = {"role": role, "provider": provider, "model": model, "model_calls": len(calls),
                 "reported_calls": 0, "missing_calls": 0, "pending_calls": 0,
                 "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                 "cache_read_tokens": 0, "cache_creation_tokens": 0,
                 "reasoning_tokens": 0, "cache_unknown_calls": 0,
                 "reasoning_unknown_calls": 0, "unpriced_calls": 0, "cost_cny": 0.0}
        for receipt, status in calls:
            if receipt is None:
                group["pending_calls" if status == "running" else "missing_calls"] += 1
                continue
            group["reported_calls"] += 1
            for field in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens",
                          "cache_creation_tokens", "reasoning_tokens"):
                group[field] += receipt.get(field) or 0
            if receipt.get("cache_read_tokens") is None:
                group["cache_unknown_calls"] += 1
            if receipt.get("reasoning_tokens") is None:
                group["reasoning_unknown_calls"] += 1
            price = PRICES.get((provider, model))
            if price is None or receipt.get("cache_read_tokens") is None:
                group["unpriced_calls"] += 1
            else:
                regular = receipt["input_tokens"] - receipt["cache_read_tokens"] - receipt["cache_creation_tokens"]
                cache_price = price[4] if receipt.get("cache_mode") == "explicit" else price[1]
                group["cost_cny"] += (regular * price[0] + receipt["cache_read_tokens"] * cache_price
                                      + receipt["output_tokens"] * price[2] + receipt["cache_creation_tokens"] * price[3]) / 1_000_000
        group["cost_cny"] = round(group["cost_cny"], 8)
        groups.append(group)
    numeric = [key for key in groups[0] if key not in {"role", "provider", "model"}] if groups else [
        "model_calls", "reported_calls", "missing_calls", "pending_calls", "input_tokens", "output_tokens",
        "total_tokens", "cache_read_tokens", "cache_creation_tokens", "reasoning_tokens",
        "cache_unknown_calls", "reasoning_unknown_calls", "unpriced_calls", "cost_cny"]
    total = {key: sum(group[key] for group in groups) for key in numeric}
    total.update(groups=groups, currency="CNY", price_date=PRICE_DATE, price_source=PRICE_SOURCE,
                 incomplete=bool(total["missing_calls"] or total["cache_unknown_calls"]),
                 cost_complete=bool(total["reported_calls"] and not (total["missing_calls"] or
                                    total["pending_calls"] or total["unpriced_calls"])),
                 cache_hit_rate=(total["cache_read_tokens"] / total["input_tokens"]
                                 if total["input_tokens"] and not total["cache_unknown_calls"] else None))
    total["cost_cny"] = round(total["cost_cny"], 8) if total["reported_calls"] > total["unpriced_calls"] else None
    return total
