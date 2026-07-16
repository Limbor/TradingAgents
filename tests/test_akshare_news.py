import pandas as pd

from tradingagents.dataflows import akshare_news
from tradingagents.dataflows.config import set_config
from tradingagents.default_config import DEFAULT_CONFIG


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
