import pandas as pd

from tradingagents.dataflows import akshare_news
from tradingagents.dataflows.config import set_config
from tradingagents.default_config import DEFAULT_CONFIG


def test_market_news_flash_falls_back_to_eastmoney(monkeypatch):
    """CLS timeout → eastmoney flash feed is used before the CCTV fallback."""
    fake_ak = type("FakeAk", (), {
        "stock_info_global_cls": object(),
        "stock_info_global_em": object(),
        "stock_info_global_ths": object(),
        "news_cctv": object(),
    })()

    em_df = pd.DataFrame({
        "标题": ["央行开展逆回购操作", "某行业政策落地"],
        "摘要": ["央行今日开展逆回购3000亿元", "行业利好政策细则发布"],
        "发布时间": ["2026-07-27 21:00:00", "2026-07-27 20:30:00"],
        "链接": ["https://e/1", "https://e/2"],
    })

    def fake_call(fn, *args, **kwargs):
        if fn is fake_ak.stock_info_global_cls:
            raise TimeoutError("cls timed out")
        if fn is fake_ak.stock_info_global_em:
            return em_df
        raise AssertionError("should not reach THS/CCTV when eastmoney succeeds")

    monkeypatch.setattr(akshare_news, "ak_lazy_import", lambda: fake_ak)
    monkeypatch.setattr(akshare_news, "akshare_call", fake_call)

    items = akshare_news.get_market_news_flash("2026-07-27", limit=10)
    assert len(items) == 2
    assert items[0]["title"] == "央行开展逆回购操作"
    # 摘要 column is picked up as the content body.
    assert items[0]["content"] == "央行今日开展逆回购3000亿元"
    assert items[0]["datetime"] == "2026-07-27 21:00:00"


def test_topic_news_filters_keyword_search_by_date(monkeypatch):
    raw = pd.DataFrame({
        "新闻标题": ["钨价继续上涨", "过期新闻"],
        "新闻内容": ["供给扰动", "旧消息"],
        "发布时间": ["2026-08-04 09:30:00", "2026-06-01 09:30:00"],
    })
    monkeypatch.setattr(
        akshare_news,
        "_eastmoney_stock_news_fallback",
        lambda query: raw if query == "钨" else None,
    )

    items = akshare_news.get_topic_news("钨", "2026-08-05", look_back_days=14, limit=10)

    assert [item["title"] for item in items] == ["钨价继续上涨"]
    assert items[0]["content"] == "供给扰动"


def test_market_news_flash_falls_back_to_ths_then_cctv_last(monkeypatch):
    """CLS and eastmoney both fail → THS is used; CCTV stays untouched."""
    fake_ak = type("FakeAk", (), {
        "stock_info_global_cls": object(),
        "stock_info_global_em": object(),
        "stock_info_global_ths": object(),
        "news_cctv": object(),
    })()

    ths_df = pd.DataFrame({
        "标题": ["北向资金净流入超百亿"],
        "内容": ["今日北向资金净流入120亿元"],
        "发布时间": ["2026-07-27 15:30:00"],
        "链接": ["https://t/1"],
    })

    def fake_call(fn, *args, **kwargs):
        if fn is fake_ak.stock_info_global_cls or fn is fake_ak.stock_info_global_em:
            raise RuntimeError("feed down")
        if fn is fake_ak.stock_info_global_ths:
            return ths_df
        raise AssertionError("CCTV must be the last resort only")

    monkeypatch.setattr(akshare_news, "ak_lazy_import", lambda: fake_ak)
    monkeypatch.setattr(akshare_news, "akshare_call", fake_call)

    items = akshare_news.get_market_news_flash("2026-07-27", limit=10)
    assert len(items) == 1
    assert items[0]["title"] == "北向资金净流入超百亿"
    assert items[0]["datetime"] == "2026-07-27 15:30:00"


def test_a_share_news_includes_capped_body_snippet(monkeypatch):
    class DummyAk:
        def stock_news_em(self, symbol):
            return None

    raw = pd.DataFrame(
        [
            {
                "发布时间": "2026-06-26 09:30:00",
                "新闻标题": "测试新闻",
                "新闻内容": "这是一段很长的正文内容，用于验证新闻正文摘录会被截断。",
                "文章来源": "测试来源",
                "新闻链接": "https://example.com/news/1",
            },
            {
                "发布时间": "2026-06-26 10:30:00",
                "新闻标题": "第二条新闻",
                "新闻内容": "第二条正文不应出现，因为配置只允许一条摘录。",
                "文章来源": "测试来源",
                "新闻链接": "https://example.com/news/2",
            },
        ]
    )

    monkeypatch.setattr(akshare_news, "ak_lazy_import", lambda: DummyAk())
    monkeypatch.setattr(akshare_news, "akshare_call", lambda func, **kwargs: raw)
    set_config({"news_body_snippet_items": 1, "news_body_snippet_chars": 12})

    try:
        report = akshare_news.get_news("600519.SH", "2026-06-26", "2026-06-26")

        assert "摘录: 这是一段很长的正文内容，..." in report
        assert "第二条正文不应出现" not in report
    finally:
        set_config(
            {
                "news_body_snippet_items": DEFAULT_CONFIG["news_body_snippet_items"],
                "news_body_snippet_chars": DEFAULT_CONFIG["news_body_snippet_chars"],
            }
        )


def test_a_share_news_uses_direct_fallback_when_akshare_fails(monkeypatch):
    """stock_news_em failure (e.g. proxy rejects eastmoney) → direct search API fallback."""
    class DummyAk:
        def stock_news_em(self, symbol):
            raise AssertionError("unused")

    fallback_df = pd.DataFrame(
        [
            {
                "发布时间": "2026-07-27 09:30:00",
                "新闻标题": "直连降级新闻",
                "新闻内容": "正文",
                "文章来源": "东财",
                "新闻链接": "http://finance.eastmoney.com/a/1.html",
            }
        ]
    )

    def raising_call(func, **kwargs):
        raise RuntimeError("访问失败")

    monkeypatch.setattr(akshare_news, "ak_lazy_import", lambda: DummyAk())
    monkeypatch.setattr(akshare_news, "akshare_call", raising_call)
    monkeypatch.setattr(akshare_news, "_eastmoney_stock_news_fallback", lambda code: fallback_df)

    report = akshare_news.get_news("600519.SH", "2026-07-27", "2026-07-27")

    assert "直连降级新闻" in report
    assert "Eastmoney search API (direct fallback)" in report
    assert "Failed to fetch" not in report


def test_a_share_news_reports_error_when_fallback_also_fails(monkeypatch):
    class DummyAk:
        def stock_news_em(self, symbol):
            raise AssertionError("unused")

    def raising_call(func, **kwargs):
        raise RuntimeError("proxy refused")

    monkeypatch.setattr(akshare_news, "ak_lazy_import", lambda: DummyAk())
    monkeypatch.setattr(akshare_news, "akshare_call", raising_call)
    monkeypatch.setattr(akshare_news, "_eastmoney_stock_news_fallback", lambda code: None)

    report = akshare_news.get_news("600519.SH", "2026-07-27", "2026-07-27")

    assert "Failed to fetch A-share news for 600519.SH" in report
    assert "proxy refused" in report


def test_em_news_df_strips_highlight_tags_and_builds_url():
    items = [
        {
            "title": "<em>600519</em>贵州茅台新闻",
            "content": "正文<em>关键词</em>片段",
            "date": "2026-07-27 10:00:00",
            "mediaName": "证券时报",
            "code": "202607271000",
        },
        {"title": "", "content": "", "date": "", "mediaName": "", "code": ""},
    ]

    df = akshare_news._em_news_df(items)

    assert df is not None and len(df) == 1
    assert df.iloc[0]["新闻标题"] == "600519贵州茅台新闻"
    assert df.iloc[0]["新闻内容"] == "正文关键词片段"
    assert df.iloc[0]["新闻链接"] == "http://finance.eastmoney.com/a/202607271000.html"


def test_em_news_df_returns_none_for_empty_items():
    assert akshare_news._em_news_df([]) is None
