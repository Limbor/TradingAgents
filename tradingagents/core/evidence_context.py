"""Bounded access to preserved tool evidence; no second model summary call."""

from __future__ import annotations

import json


def evidence_slice(result: dict, path: list[str], offset: int = 0, limit: int = 10) -> dict:
    if len(path) > 6 or any(not isinstance(key, str) or len(key) > 80 for key in path):
        raise ValueError("字段路径无效")
    if not 0 <= offset <= 100_000 or not 1 <= limit <= 20:
        raise ValueError("分页范围无效")
    value = result
    for key in path:
        if isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
            continue
        if not isinstance(value, dict) or key not in value:
            raise ValueError("证据中不存在该字段")
        value = value[key]
    if isinstance(value, list):
        payload = {"kind": "list", "total": len(value), "offset": offset,
                   "items": value[offset:offset + limit]}
    elif isinstance(value, dict):
        keys = list(value)
        payload = {"kind": "object", "keys": keys, "offset": offset,
                   "fields": {key: value[key] for key in keys[offset:offset + limit]}}
    elif isinstance(value, str):
        payload = {"kind": "text", "total_chars": len(value), "offset": offset,
                   "text": value[offset:offset + limit * 500]}
    else:
        payload = {"kind": "scalar", "value": value}
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    if len(raw) > 12_000:
        return {"kind": "index", "path": path, "keys": list(value)[:100] if isinstance(value, dict) else [],
                "total": len(value) if isinstance(value, (dict, list, str)) else None,
                "message": "此字段仍较大，请缩小字段路径或分页数量；原始证据已完整保存"}
    return {"path": path, **payload}
