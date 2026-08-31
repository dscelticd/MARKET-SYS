"""
거시지표 수집 테스트

배경: macro_collector는 실패 시 raise로 파이프라인을 세우는 **필수 경로**인데
      테스트가 하나도 없었다. 동시에 하드코딩이 가장 많이 모인 파일이기도 하다
      (기준금리 4건, FOMC·금통위 일정 8건). 필수 경로 · 테스트 없음 · 수동 갱신
      의존이 한 파일에 겹쳐 있어 구조 검토에서 "가장 약한 고리"로 지목됐다.

      또한 이 파일에는 시장 전체 외국인·기관 순매수를
      `kospi_chg * random.uniform(150, 400)`으로 만들어 리포트가 사실처럼 서술하던
      난수 필드가 있었다(같은 데이터 3회 실행 시 -279억/-292억/-223억). 실측 대체가
      불가능해(KIS 모의투자는 해당 필드를 전부 0으로 반환) 필드를 제거했으며,
      아래 테스트가 재도입을 막는다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from app.collectors.macro_collector import (
    MacroCollector,
    _BOK_ALL,
    _FOMC_ALL,
    _next_meeting_date,
    get_upcoming_policy_meetings,
)

_SECTIONS = ("us_market", "kr_market", "currencies", "rates", "commodities", "sentiment")


# ── Mock 경로 (스키마 계약) ──────────────────────────────────────────────────

def test_mock_returns_all_expected_sections():
    m = MacroCollector()._collect_mock()
    for key in _SECTIONS:
        assert key in m, f"{key} 누락"
    assert m["_mock"] is True


def test_mock_and_real_share_section_schema():
    """Mock과 실데이터의 섹션 구조가 어긋나면 리포트 포맷터가 한쪽에서만 깨진다."""
    mock = MacroCollector()._collect_mock()
    # 실호출 없이 구조만 비교하기 위해 yfinance 임포트 실패를 유도 → Mock 폴백 경로
    with patch.dict("sys.modules", {"yfinance": None}):
        fallback = MacroCollector()._collect_real()
    assert set(mock.keys()) == set(fallback.keys())


def test_collect_respects_use_mock_env(monkeypatch):
    monkeypatch.setenv("USE_MOCK_DATA", "true")
    assert MacroCollector().collect()["_mock"] is True


def test_real_path_falls_back_to_mock_when_yfinance_missing():
    """필수 경로지만 라이브러리 부재는 예외가 아니라 Mock 폴백으로 처리한다."""
    with patch.dict("sys.modules", {"yfinance": None}):
        out = MacroCollector()._collect_real()
    assert out["_mock"] is True


# ── 난수 수급 필드 재도입 방지 ───────────────────────────────────────────────

@pytest.mark.parametrize("build", ["mock", "fallback"])
def test_fabricated_market_flow_fields_are_gone(build):
    """`kospi_chg * random.uniform(...)`으로 만든 시장 전체 순매수 필드는
    리포트가 사실로 서술해버려 제거했다 — 어떤 경로로도 되살아나면 안 된다."""
    if build == "mock":
        m = MacroCollector()._collect_mock()
    else:
        with patch.dict("sys.modules", {"yfinance": None}):
            m = MacroCollector()._collect_real()
    kr = m["kr_market"]
    assert "foreign_net_buy_bn" not in kr
    assert "institution_net_buy_bn" not in kr
    assert "_foreign_estimated" not in kr


def test_macro_block_no_longer_reports_market_wide_foreign_flow():
    from app.reports.report_builder import _format_macro_block
    block = _format_macro_block(MacroCollector()._collect_mock())
    assert "외국인 순매수" not in block


# ── 정책회의 캘린더 (수동 갱신 대상) ─────────────────────────────────────────

def test_policy_meeting_dates_are_well_formed_and_sorted():
    """매년 수동 갱신하는 값이라 형식이 깨지면 조용히 잘못된 날짜가 나간다."""
    for dates in (_FOMC_ALL, _BOK_ALL):
        assert dates == sorted(dates), "정렬되어 있어야 이분 탐색·최근일 계산이 맞는다"
        for d in dates:
            datetime.strptime(d, "%Y-%m-%d")  # 형식 오류면 예외


def test_policy_calendar_covers_current_year_forward():
    """올해 이후 일정이 남아 있어야 '다음 회의일' 계산이 의미를 가진다.
    이 테스트가 깨지면 연간 수동 갱신을 놓쳤다는 신호다."""
    today = date.today().isoformat()
    assert any(d >= today for d in _FOMC_ALL), "FOMC 일정 갱신 필요"
    assert any(d >= today for d in _BOK_ALL), "금통위 일정 갱신 필요"


def test_next_meeting_returns_first_future_date():
    dates = ["2020-01-01", "2099-06-01", "2099-12-01"]
    assert _next_meeting_date(dates) == "2099-06-01"


def test_next_meeting_falls_back_to_last_when_all_past():
    assert _next_meeting_date(["2020-01-01", "2020-06-01"]) == "2020-06-01"


def test_upcoming_policy_meetings_filters_to_window():
    fixed = datetime(2026, 9, 10)
    with patch("app.collectors.macro_collector.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        mock_dt.strptime = datetime.strptime
        events = get_upcoming_policy_meetings(days_ahead=14)
    assert events, "9/16 FOMC가 14일 창에 들어와야 한다"
    for e in events:
        assert fixed.date().isoformat() <= e["date"] <= (fixed + timedelta(days=14)).date().isoformat()
        assert e["category"] == "policy"
        assert e["country"] in ("US", "KR")


def test_upcoming_policy_meetings_empty_window_returns_empty():
    fixed = datetime(2026, 9, 1)
    with patch("app.collectors.macro_collector.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        mock_dt.strptime = datetime.strptime
        assert get_upcoming_policy_meetings(days_ahead=3) == []


# ── 하드코딩된 금리 값 ───────────────────────────────────────────────────────

def test_hardcoded_policy_rates_are_plausible():
    """기준금리는 실시간 조회가 아니라 코드에 박힌 last_known 값이다.
    갱신을 놓쳐도 오류가 나지 않으므로, 최소한 값이 상식적 범위인지는 지킨다."""
    with patch.dict("sys.modules", {"yfinance": None}):
        rates = MacroCollector()._collect_real()["rates"]
    for key in ("fed_funds_rate", "kr_base_rate"):
        val = rates.get(key, {}).get("value")
        assert isinstance(val, (int, float)), f"{key} 값이 숫자가 아님"
        assert 0.0 <= val <= 15.0, f"{key}={val} — 정상 범위를 벗어남"


def test_sentiment_derives_labels_from_vix():
    m = MacroCollector()._collect_mock()
    sent = m["sentiment"]
    assert "fear_greed_index" in sent
    assert "global_risk_appetite" in sent
