"""
예정 이벤트 캘린더 수집 테스트 — 실제 네트워크 호출 없이 검증

배경: "오늘/내일 주요 모니터링 포인트" 섹션이 전용 데이터 없이 Claude의 자유 추론에
      의존하던 문제를 실측 데이터로 대체. FRED(release_id는 이름으로 런타임 조회,
      하드코딩 안 함) + FOMC/한국은행 정적 캘린더 + 국내 법정 공시기한(순수 날짜 계산)
      + DART IR 관련 공시(기존 disclosure_data 재사용)를 통합.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.collectors.calendar_collector import (
    CalendarCollector,
    build_event_calendar,
    get_dart_ir_events,
    get_kr_filing_deadlines,
)
from app.reports.report_builder import _format_event_calendar_block


def _make_collector(tmp_path, monkeypatch, api_key="test-fred-key"):
    monkeypatch.setenv("FRED_API_KEY", api_key)
    import app.collectors.calendar_collector as mod
    monkeypatch.setattr(mod, "_RELEASES_CACHE_FILE", tmp_path / "fred_releases.json")
    return CalendarCollector()


def test_is_configured_reflects_env_var(tmp_path, monkeypatch):
    collector = _make_collector(tmp_path, monkeypatch)
    assert collector.is_configured() is True

    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert CalendarCollector().is_configured() is False


def test_resolve_release_ids_matches_by_name_and_caches(tmp_path, monkeypatch):
    collector = _make_collector(tmp_path, monkeypatch)
    releases_response = MagicMock()
    releases_response.raise_for_status.return_value = None
    releases_response.json.return_value = {
        "releases": [
            {"id": 10, "name": "Consumer Price Index"},
            {"id": 46, "name": "Producer Price Index"},
            {"id": 50, "name": "Employment Situation"},
            {"id": 999, "name": "Some Unrelated Release"},
        ]
    }

    with patch("app.collectors.calendar_collector.requests.get", return_value=releases_response) as mock_get:
        resolved = collector._resolve_release_ids()
        assert resolved["cpi"] == 10
        assert resolved["ppi"] == 46
        assert resolved["employment"] == 50
        assert "gdp" not in resolved  # 매칭 안 되면 조용히 제외
        mock_get.assert_called_once()

        # 캐시 재사용 — 두 번째 호출은 네트워크 요청 없음
        collector._resolve_release_ids()
        mock_get.assert_called_once()


def test_resolve_release_ids_cache_expires_after_ttl(tmp_path, monkeypatch):
    collector = _make_collector(tmp_path, monkeypatch)
    import app.collectors.calendar_collector as mod
    mod._RELEASES_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    mod._RELEASES_CACHE_FILE.write_text(
        json.dumps({"cached_at": time.time() - 40 * 24 * 3600, "releases": [{"id": 10, "name": "Consumer Price Index"}]}),
        encoding="utf-8",
    )
    fresh_response = MagicMock()
    fresh_response.raise_for_status.return_value = None
    fresh_response.json.return_value = {"releases": [{"id": 10, "name": "Consumer Price Index"}]}

    with patch("app.collectors.calendar_collector.requests.get", return_value=fresh_response) as mock_get:
        collector._resolve_release_ids()
        mock_get.assert_called_once()  # 캐시 TTL(30일) 만료로 재조회


def test_fetch_fred_events_returns_empty_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    collector = CalendarCollector()
    assert collector.fetch_fred_events() == []


def test_fetch_fred_events_builds_macro_events(tmp_path, monkeypatch):
    collector = _make_collector(tmp_path, monkeypatch)
    releases_response = MagicMock()
    releases_response.raise_for_status.return_value = None
    releases_response.json.return_value = {"releases": [{"id": 10, "name": "Consumer Price Index"}]}

    today = datetime.now().date()
    dates_response = MagicMock()
    dates_response.raise_for_status.return_value = None
    dates_response.json.return_value = {
        "release_dates": [{"release_id": 10, "date": (today + timedelta(days=3)).isoformat()}]
    }

    with patch("app.collectors.calendar_collector.requests.get", side_effect=[releases_response, dates_response]):
        events = collector.fetch_fred_events(days_ahead=14)

    assert len(events) == 1
    assert events[0]["category"] == "macro"
    assert events[0]["country"] == "US"
    assert events[0]["source"] == "fred"


def test_fetch_fred_events_skips_indicator_on_failure(tmp_path, monkeypatch):
    collector = _make_collector(tmp_path, monkeypatch)
    releases_response = MagicMock()
    releases_response.raise_for_status.return_value = None
    releases_response.json.return_value = {
        "releases": [
            {"id": 10, "name": "Consumer Price Index"},
            {"id": 46, "name": "Producer Price Index"},
        ]
    }

    empty_dates_response = MagicMock()
    empty_dates_response.raise_for_status.return_value = None
    empty_dates_response.json.return_value = {"release_dates": []}

    with patch(
        "app.collectors.calendar_collector.requests.get",
        side_effect=[releases_response, RuntimeError("network error"), empty_dates_response],
    ):
        events = collector.fetch_fred_events(days_ahead=14)

    assert events == []  # 둘 다 실패/빈값이어도 예외 없이 빈 리스트


def test_get_kr_filing_deadlines_finds_deadline_in_window():
    # 오늘이 사업보고서 법정기한(전년 12/31 + 90일) 직전이 되도록 today를 고정
    fixed_today = datetime(2026, 3, 25)
    with patch("app.collectors.calendar_collector.now_kst", return_value=fixed_today):
        events = get_kr_filing_deadlines(days_ahead=14)

    assert any(e["category"] == "filing_deadline" and "사업보고서" in e["title"] for e in events)
    for e in events:
        assert fixed_today.date().isoformat() <= e["date"] <= (fixed_today + timedelta(days=14)).date().isoformat()


def test_get_kr_filing_deadlines_returns_empty_when_nothing_in_window():
    fixed_today = datetime(2026, 1, 5)  # 어떤 마감일과도 멀리 떨어진 날짜
    with patch("app.collectors.calendar_collector.now_kst", return_value=fixed_today):
        events = get_kr_filing_deadlines(days_ahead=3)

    assert events == []


def test_get_dart_ir_events_filters_by_keyword():
    disclosure_data = {
        "KR_015760": [
            {"title": "기업설명회(IR) 개최(안내공시)", "corp_name": "한국전력공사", "rcept_dt": "20260807"},
            {"title": "주요사항보고서(자기주식취득결정)", "corp_name": "한국전력공사", "rcept_dt": "20260805"},
        ],
        "KR_005930": [],
    }
    events = get_dart_ir_events(disclosure_data)
    assert len(events) == 1
    assert events[0]["date"] == "2026-08-07"
    assert events[0]["category"] == "disclosure"
    assert "공시 접수" in events[0]["title"]


def test_get_dart_ir_events_handles_empty_input():
    assert get_dart_ir_events(None) == []
    assert get_dart_ir_events({}) == []


def test_build_event_calendar_merges_sources_and_sorts(tmp_path, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)  # FRED 없이도 나머지 소스는 동작해야 함
    disclosure_data = {
        "KR_015760": [{"title": "IR 개최 안내공시", "corp_name": "한국전력공사", "rcept_dt": datetime.now().strftime("%Y%m%d")}],
    }
    events = build_event_calendar(disclosure_data, days_ahead=14)
    dates = [e["date"] for e in events]
    assert dates == sorted(dates)
    assert any(e["source"] == "dart" for e in events)


def test_build_event_calendar_non_fatal_on_source_failure(monkeypatch):
    with patch("app.collectors.calendar_collector.get_kr_filing_deadlines", side_effect=RuntimeError("boom")):
        events = build_event_calendar(disclosure_data=None, days_ahead=14)
    assert isinstance(events, list)  # 한 소스가 죽어도 예외 없이 리스트 반환


def test_format_event_calendar_block_handles_empty():
    assert "이벤트 캘린더 데이터 없음" in _format_event_calendar_block(None)
    assert "이벤트 캘린더 데이터 없음" in _format_event_calendar_block([])


def test_format_event_calendar_block_shows_date_category_and_title():
    events = [
        {"date": "2026-08-13", "category": "macro", "country": "US", "title": "美 CPI 발표 예정", "source": "fred"},
        {"date": "2026-08-14", "category": "disclosure", "country": "KR", "title": "한국전력공사: IR 개최 (공시 접수)", "source": "dart"},
    ]
    block = _format_event_calendar_block(events)
    assert "2026-08-13" in block
    assert "🇺🇸 매크로" in block
    assert "美 CPI 발표 예정" in block
    assert "📄 공시" in block
