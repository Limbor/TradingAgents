"""Shared coarse industry taxonomy.

A single source of truth for mapping raw A-share industry names (e.g.
``房地产``, ``房地产开发``, ``建筑``) onto coarse groups (e.g. ``地产``). Both
the cross-symbol pattern miner (which stores industry-scope lessons keyed by
the coarse group) and the candidate review runner (which matches those lessons
against a candidate's raw industry) MUST normalize through this function, or an
industry-scope lesson keyed by a coarse group would never match a candidate
carrying the raw industry string.
"""

from __future__ import annotations

# Coarse group -> substrings that map a raw industry name into it. First match
# wins (dict insertion order), so keep more specific groups earlier if they
# would otherwise overlap.
INDUSTRY_GROUPS: dict[str, list[str]] = {
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


def normalize_industry(industry: str) -> str:
    """Map a raw industry name to its coarse group, or return it unchanged.

    Case-insensitive substring match. Returns the original ``industry`` (with
    surrounding whitespace stripped) when no group matches, so callers can still
    compare unmapped industries by exact name.
    """
    text = (industry or "").strip()
    if not text:
        return text
    lowered = text.lower()
    for coarse, keywords in INDUSTRY_GROUPS.items():
        if any(kw in lowered for kw in keywords):
            return coarse
    return text
