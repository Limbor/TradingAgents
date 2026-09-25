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


# StockManager MCP 的 ``filters.include_industries`` 只接受申万 2021 一级
# 行业全名。Market 页面展示的则既有一级行业，也有投资者常用主题概念。
# 这里作为两条链路共享的唯一翻译源，避免 Market 能展示、pipeline 却无法消费。
SW_L1_INDUSTRIES: frozenset[str] = frozenset({
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "汽车", "家用电器",
    "食品饮料", "纺织服饰", "轻工制造", "医药生物", "公用事业", "交通运输",
    "房地产", "商贸零售", "社会服务", "银行", "非银金融", "综合", "建筑材料",
    "建筑装饰", "电力设备", "机械设备", "国防军工", "计算机", "传媒", "通信",
    "煤炭", "石油石化", "环保", "美容护理",
})

STANDARD_INDUSTRY_TAXONOMIES: frozenset[str] = frozenset({"CITICS", "SW2021"})


def normalize_industry_taxonomy(value: str | None) -> str | None:
    """Normalize user/vendor aliases to the wire-level taxonomy identifier."""
    text = str(value or "").strip().upper().replace("_", "")
    aliases = {
        "CITIC": "CITICS",
        "CITICS": "CITICS",
        "中信": "CITICS",
        "中信行业": "CITICS",
        "SW": "SW2021",
        "SW2021": "SW2021",
        "申万": "SW2021",
        "申万2021": "SW2021",
    }
    return aliases.get(text)


# More-specific aliases must come first. The resolver intentionally returns the
# first matching rule: accumulating every substring hit would map “生物育种” to
# both 农林牧渔 and 医药生物, which is worse than having no mapping at all.
INDUSTRY_SELECTION_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("生物育种", ("农林牧渔",)),
    ("生态农业", ("农林牧渔",)),
    ("水产品", ("农林牧渔",)),
    ("水产养殖", ("农林牧渔",)),
    ("猪肉", ("农林牧渔",)),
    ("乡村振兴", ("农林牧渔",)),
    ("BC电池", ("电力设备",)),
    ("HIT电池", ("电力设备",)),
    ("TOPCON电池", ("电力设备",)),
    ("固态电池", ("电力设备",)),
    ("电解液", ("电力设备", "基础化工")),
    ("智能电网", ("电力设备",)),
    ("钙钛矿", ("电力设备",)),
    ("充电桩", ("电力设备",)),
    ("存储芯片", ("电子",)),
    ("华为海思", ("电子",)),
    ("消费电子", ("电子",)),
    ("无线耳机", ("电子",)),
    ("智能穿戴", ("电子",)),
    ("集成电路", ("电子",)),
    ("创新药", ("医药生物",)),
    ("医疗服务", ("医药生物",)),
    ("CXO", ("医药生物",)),
    ("CRO", ("医药生物",)),
    ("CDMO", ("医药生物",)),
    ("工业母机", ("机械设备",)),
    ("工程机械", ("机械设备",)),
    ("机器人", ("机械设备",)),
    ("智能汽车", ("汽车",)),
    ("碳纤维", ("基础化工",)),
    ("聚氨酯", ("基础化工",)),
    ("石墨烯", ("基础化工", "电子")),
    ("草甘膦", ("基础化工",)),
    ("生物燃料", ("基础化工",)),
    ("碳交易", ("环保",)),
    ("建筑节能", ("建筑材料", "建筑装饰")),
    ("商业航天", ("国防军工",)),
    ("低空经济", ("国防军工",)),
    ("国产软件", ("计算机",)),
    ("人工智能", ("计算机",)),
    ("数据中心", ("计算机",)),
    ("云计算", ("计算机",)),
    ("光通信", ("通信",)),
    ("光模块", ("通信",)),
    ("大金融", ("银行", "非银金融")),
    ("CPO", ("通信",)),
    ("鸿蒙", ("计算机",)),
    ("软件", ("计算机",)),
    ("信创", ("计算机",)),
    ("算力", ("计算机",)),
    ("AI", ("计算机",)),
    ("半导体", ("电子",)),
    ("芯片", ("电子",)),
    ("元件", ("电子",)),
    ("医药", ("医药生物",)),
    ("医疗", ("医药生物",)),
    ("制药", ("医药生物",)),
    ("生物", ("医药生物",)),
    ("锂电", ("电力设备",)),
    ("光伏", ("电力设备",)),
    ("风电", ("电力设备",)),
    ("储能", ("电力设备",)),
    ("电池", ("电力设备",)),
    ("新能源", ("电力设备",)),
    ("军工", ("国防军工",)),
    ("国防", ("国防军工",)),
    ("黄金", ("有色金属",)),
    ("铜", ("有色金属",)),
    ("铝", ("有色金属",)),
    ("稀土", ("有色金属",)),
    ("锂矿", ("有色金属",)),
    ("有色", ("有色金属",)),
    ("白酒", ("食品饮料",)),
    ("饮料", ("食品饮料",)),
    ("食品", ("食品饮料",)),
    ("券商", ("非银金融",)),
    ("证券", ("非银金融",)),
    ("保险", ("非银金融",)),
    ("地产", ("房地产",)),
    ("化工", ("基础化工",)),
    ("游戏", ("传媒",)),
    ("影视", ("传媒",)),
    ("家电", ("家用电器",)),
    ("机械", ("机械设备",)),
    ("整车", ("汽车",)),
    ("石油", ("石油石化",)),
    ("养殖", ("农林牧渔",)),
    ("种业", ("农林牧渔",)),
    ("农业", ("农林牧渔",)),
    ("交通", ("交通运输",)),
    ("社服", ("社会服务",)),
    ("商贸", ("商贸零售",)),
    ("轻工", ("轻工制造",)),
    ("纺服", ("纺织服饰",)),
    ("建材", ("建筑材料",)),
    ("公用", ("公用事业",)),
    ("电力", ("公用事业",)),
    ("美容", ("美容护理",)),
    ("5G", ("通信",)),
)


# These are provider-defined baskets, corporate ecosystems, regional labels,
# or historical/capital-market events rather than durable industry chains.
# They may be useful in an event scanner, but mixing them into an industry heat
# map makes the matrix impossible to use for sector rotation or stock picking.
NON_INDUSTRIAL_CONCEPT_TERMS: tuple[str, ...] = (
    "未股改", "股改", "出口退税", "内贸规划", "三沙", "百度",
    "昨日涨停", "昨日连板", "融资融券", "沪股通", "深股通",
    "机构重仓", "基金重仓", "QFII重仓", "MSCI中国", "富时罗素",
    "标准普尔", "转债标的", "预亏预减", "预盈预增", "破净股", "百元股",
    "股权转让", "定向增发", "高送转", "解禁", "回购", "员工持股",
    "证金持股", "社保重仓", "举牌", "壳资源", "重组", "摘帽",
    "国企改革", "央企改革", "地方国资改革", "区域改革", "水域改革",
)

NON_INDUSTRIAL_CONCEPT_NAMES: frozenset[str] = frozenset({
    "华为", "百度",
})

INVESTOR_THEME_CANONICAL_ALIASES: dict[str, str] = {
    "鸿蒙": "鸿蒙生态",
    "华为鸿蒙": "鸿蒙生态",
    "harmonyos": "鸿蒙生态",
    "华为汽车": "智能汽车",
}


def _selection_name(value: str) -> str:
    text = (value or "").strip()
    for suffix in ("概念板块", "行业板块", "概念", "板块", "行业"):
        if text.endswith(suffix):
            text = text.removesuffix(suffix).strip()
            break
    return text


def canonicalize_investor_theme(value: str) -> str:
    """Collapse provider synonyms onto one investor-facing theme label."""
    text = _selection_name(value)
    return INVESTOR_THEME_CANONICAL_ALIASES.get(text.casefold(), text)


def resolve_sw_l1_industries(value: str) -> list[str]:
    """Translate an investor-facing sector/theme into MCP-supported SW L1 names.

    An empty result means that no honest industry proxy is known. Callers must
    not push the original theme into MCP's exact-match industry filter.
    """
    text = _selection_name(value)
    if not text:
        return []
    if text in SW_L1_INDUSTRIES:
        return [text]
    folded = text.casefold()
    for alias, industries in INDUSTRY_SELECTION_ALIASES:
        alias_folded = alias.casefold()
        if alias_folded in folded:
            return list(industries)
    return []


def expand_industry_selection_terms(value: str) -> list[str]:
    """Return the display keyword followed by its MCP-compatible proxies."""
    text = _selection_name(value)
    if not text:
        return []
    return list(dict.fromkeys([text, *resolve_sw_l1_industries(text)]))


def industry_selection_metadata(
    value: str,
    board_type: str | None = None,
    *,
    taxonomy: str | None = None,
    industry_code: str | None = None,
    industry_level: str | None = None,
) -> dict[str, object]:
    """Describe how one Market board can be consumed by DailyPipeline."""
    text = _selection_name(value)
    normalized_taxonomy = normalize_industry_taxonomy(taxonomy)
    code = str(industry_code or "").strip()
    level = str(industry_level or "").strip().upper()
    if board_type != "concept" and normalized_taxonomy and code:
        return {
            "selection_industries": [text] if text else [],
            "selection_industry_codes": [code],
            "selection_taxonomy": normalized_taxonomy,
            "selection_level": level if level in {"L1", "L2", "L3"} else "L1",
            "selection_concept": None,
            "selection_mode": "exact",
        }
    industries = resolve_sw_l1_industries(text)
    is_exact = (
        board_type != "concept"
        and text in SW_L1_INDUSTRIES
        and industries == [text]
    )
    return {
        "selection_industries": industries,
        "selection_industry_codes": [],
        "selection_taxonomy": "SW2021" if is_exact else None,
        "selection_level": "L1" if is_exact else None,
        "selection_concept": text if board_type == "concept" else None,
        "selection_mode": (
            "exact" if is_exact else "proxy" if industries else "unsupported"
        ),
    }


def is_investable_industry_concept(value: str) -> bool:
    """Whether a provider concept is suitable for the industry rotation matrix.

    A concept must map to at least one MCP-consumable SW L1 industry and must
    not be an event/ownership/region basket. Exact concept constituents remain
    the preferred selection scope; the L1 mapping is the safe fallback.
    """
    text = canonicalize_investor_theme(value)
    if (
        not text
        or text in NON_INDUSTRIAL_CONCEPT_NAMES
        or any(term.casefold() in text.casefold() for term in NON_INDUSTRIAL_CONCEPT_TERMS)
    ):
        return False
    return bool(resolve_sw_l1_industries(text))


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
