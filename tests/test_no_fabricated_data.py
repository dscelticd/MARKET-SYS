"""
실제 데이터 모드에서 지어낸 데이터가 리포트에 들어가지 않는지 — 2026-11-25 점검

실측: 클라우드 리포트의 뉴스가 한국 종목은 9월부터 100%, 미국 종목은 10월부터
98~100% 수집기의 예시 기사(_NEWS_POOL)였다. 리포트는 "NVIDIA 가이던스 상회, Blackwell
B300 출하 가속…이 뉴스에서 확인된다"처럼 지어낸 기사를 사실로 서술했다.
  - yfinance 뉴스 기능이 2026-10부터 "YAHOO! FINANCE IS CURRENTLY DOWN!"으로 실패
  - 클라우드에는 네이버 키가 없어 한국 종목이 늘 예시 기사로 대체됨
수급도 같은 구조였다 — 실패하면 random 값으로 채운 수치가 리포트 블록에 실렸다.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import patch

import pytest

from app.collectors import news_collector as nc


@pytest.fixture
def real_mode(monkeypatch):
    monkeypatch.setenv("USE_MOCK_DATA", "false")
    monkeypatch.setenv("NAVER_CLIENT_ID", "")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "")


def _item(headline, mock=False):
    return {"headline": headline, "sentiment": 0.8, "relevance": 0.9, "source": "X",
            "link": "https://example.com", "_mock": mock}


# ── 수집기 ────────────────────────────────────────────────────────────────────

def test_total_failure_leaves_news_empty_instead_of_inventing_it(real_mode):
    c = nc.NewsCollector()
    with patch.object(c, "_collect_google_news", return_value=[]), \
         patch.object(c, "_collect_yfinance", return_value={}):
        data = c.collect(["KR_005930", "US_NVDA"])
    assert data == {"KR_005930": [], "US_NVDA": []}
    assert c.sources == {"KR_005930": "none", "US_NVDA": "none"}


def test_korean_stocks_fall_back_to_google_news_without_naver_keys(real_mode):
    c = nc.NewsCollector()
    with patch.object(c, "_collect_google_news", return_value=[_item("삼성전자 실제 기사")]) as g, \
         patch.object(c, "_collect_naver_single") as naver:
        data = c.collect(["KR_005930"])
    naver.assert_not_called()
    assert g.call_args.kwargs.get("lang") == "ko"
    assert data["KR_005930"][0]["headline"] == "삼성전자 실제 기사"
    assert c.sources["KR_005930"] == "google_news"


def test_us_stocks_fall_back_to_google_news_when_yfinance_fails(real_mode):
    c = nc.NewsCollector()
    with patch.object(c, "_collect_yfinance", return_value={"US_NVDA": []}), \
         patch.object(c, "_collect_google_news", return_value=[_item("Nvidia real headline")]) as g:
        data = c.collect(["US_NVDA"])
    assert g.call_args.args[1] == "NVIDIA stock" and g.call_args.kwargs.get("lang") == "en"
    assert not any(n.get("_mock") for n in data["US_NVDA"])


def _rss(*items):
    body = "".join(
        f"<item><title>{t} - {src}</title><link>https://n/{i}</link>"
        f"<pubDate>{format_datetime(d)}</pubDate><source url='https://s'>{src}</source></item>"
        for i, (t, src, d) in enumerate(items))
    return f"<rss><channel>{body}</channel></rss>".encode("utf-8")


def test_google_news_parsing_keeps_recent_unique_items_and_splits_the_source():
    now = datetime.now(timezone.utc)
    feed = _rss(("Why Sandisk Stock Plunged Today", "Yahoo Finance", now - timedelta(hours=5)),
                ("Why Sandisk Stock Plunged Today", "Yahoo Finance", now - timedelta(hours=6)),
                ("Old story", "Reuters", now - timedelta(days=9)))
    with patch("app.collectors.news_collector.urllib.request.urlopen",
               return_value=io.BytesIO(feed)) as op:
        op.return_value.__enter__ = lambda s: s
        op.return_value.__exit__ = lambda s, *a: None
        items = nc.NewsCollector()._collect_google_news("US_SNDK", "SanDisk stock", 5, lang="en")
    assert [i["headline"] for i in items] == ["Why Sandisk Stock Plunged Today"]
    assert items[0]["source"] == "Yahoo Finance" and items[0]["_mock"] is False


def test_queries_come_from_the_watchlist_symbols():
    """한미반도체·피에스케이는 예전 뉴스 검색어 목록에서 빠져 예시 기사로 대체됐다."""
    assert nc._kr_query("KR_042700") == "한미반도체 주가"
    assert "-홀딩스" in nc._kr_query("KR_319660")       # 피에스케이홀딩스 기사 혼입 방지
    assert nc._intl_query("US_QQQ") == "QQQ ETF"
    assert nc._intl_query("US_SNDK") == "SanDisk stock"


# ── 리포트·메일 ───────────────────────────────────────────────────────────────

def test_report_news_block_never_contains_mock_items_in_real_mode(monkeypatch):
    from app.reports.report_builder import _format_news_block
    monkeypatch.setenv("USE_MOCK_DATA", "false")
    block = _format_news_block({"US_NVDA": [_item("NVIDIA, Blackwell B300 출하 가속", mock=True),
                                             _item("Nvidia stock cools")]},
                               {"US_NVDA": {"name": "NVIDIA"}})
    assert "Blackwell B300" not in block and "Nvidia stock cools" in block
    only_mock = _format_news_block({"US_NVDA": [_item("가짜", mock=True)]}, {})
    assert only_mock == "(뉴스 데이터 없음)"


def test_email_news_cards_skip_mock_items_in_real_mode(monkeypatch):
    from app.delivery.email_sender import _build_news_html
    monkeypatch.setenv("USE_MOCK_DATA", "false")
    html = _build_news_html({"US_NVDA": [_item("NVIDIA 분기 매출 가이던스 상회", mock=True)]})
    assert "가이던스" not in html


# ── 품질 표시 ─────────────────────────────────────────────────────────────────

def test_validator_counts_only_real_news():
    from app.utils.data_validator import DataValidator
    q = DataValidator._check_news({"US_NVDA": [_item("a", mock=True)], "KR_005930": [_item("b")]})
    assert q["total"] == 1 and q["covered_stocks"] == 1


def test_quality_line_flags_a_market_with_no_real_news():
    from app.main import summarize_run_quality
    pdata = {"KR_005930": {"data_date": "2026-11-24", "price_source": "kis"},
             "US_NVDA": {"data_date": "2026-11-24", "price_source": "yfinance"}}
    news = {"KR_005930": [_item("실제")], "US_NVDA": [_item("가짜", mock=True)]}
    line, degraded = summarize_run_quality(pdata, {}, {"kr_date": "2026-11-24", "us_date": "2026-11-24"},
                                           news_data=news)
    assert "뉴스 KR 1/1" in line and "뉴스 US 0/1" in line and degraded


# ── 수급 ──────────────────────────────────────────────────────────────────────

def test_failed_investor_flow_is_not_printed_as_numbers(monkeypatch):
    from app.reports.report_builder import _format_investor_flow_block
    monkeypatch.setenv("USE_MOCK_DATA", "false")
    block = _format_investor_flow_block({
        "KR_005930": {"name": "삼성전자", "investor_flow": {"_mock": True, "_failed": True}},
        "KR_000660": {"name": "SK하이닉스", "investor_flow": {
            "_mock": True, "foreign_net_5d": 123, "institution_net_5d": 1, "individual_net_5d_est": -124}},
    })
    assert "삼성전자" not in block and "SK하이닉스" not in block
