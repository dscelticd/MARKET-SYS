"""
휴장일 달력 테스트

배경: 거래일 판정이 주말만 봤다. 그래서 공휴일에는
  ① 대상 거래일이 휴장일로 잡혀 전 종목 결측이 되고 (리포트가 빈다)
  ② 또는 휴장일 직전 종가가 "당일 등락"으로 서술된다

      2026년은 KRX 17일 · 미국 10일이 휴장이고 겹치는 날은 4일뿐이다. 한쪽만
      쉬는 날이 훨씬 많아, 두 시장의 기준 거래일이 휴장 때문에 갈라진다 —
      아침 리포트에서도 한·미 날짜가 달라질 수 있다는 뜻이다.
      (출처: jangjeon.kr · glasswallet.com 교차 확인, 2026-09-02 조회)
"""
from __future__ import annotations

import json
import pathlib
from datetime import date, datetime

from app.reports.report_builder import _format_market_session_block
from app.utils.market_calendar import (
    holiday_name,
    is_market_holiday,
    is_trading_day,
    previous_trading_day,
    resolve_target_session,
)

_ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── 설정 파일 ────────────────────────────────────────────────────────────────

def test_calendar_has_both_markets_for_2026():
    cal = json.loads((_ROOT / "config" / "market_holidays.json").read_text(encoding="utf-8"))
    assert len(cal["markets"]["KR"]) == 17    # KRX 2026 공고 기준
    assert len(cal["markets"]["US"]) == 10
    assert 2026 in cal["covered_years"]


def test_chuseok_and_labor_day_are_registered():
    """가장 가까운 두 휴장 — 놓치면 리포트가 빈다."""
    assert is_market_holiday(date(2026, 9, 24), "KR")
    assert is_market_holiday(date(2026, 9, 25), "KR")
    assert is_market_holiday(date(2026, 9, 7), "US")


def test_holidays_are_market_specific():
    """추석에 미국장은 열리고, 미국 Labor Day에 한국장은 열린다."""
    assert is_trading_day(date(2026, 9, 25), "US") is True
    assert is_trading_day(date(2026, 9, 25), "KR") is False
    assert is_trading_day(date(2026, 9, 7), "KR") is True
    assert is_trading_day(date(2026, 9, 7), "US") is False


def test_market_omitted_keeps_weekend_only_behaviour():
    """시장을 특정하지 않는 호출부(요일 표기 등)의 기존 동작을 유지한다."""
    assert is_trading_day(date(2026, 9, 25)) is True
    assert is_trading_day(date(2026, 9, 26)) is False   # 토요일


def test_previous_trading_day_skips_the_holiday_run():
    assert previous_trading_day(date(2026, 9, 28), "KR") == date(2026, 9, 23)


# ── 대상 세션 ────────────────────────────────────────────────────────────────

def test_us_holiday_shifts_only_the_us_target():
    """9/8 아침 — 한국은 9/7 거래, 미국은 Labor Day 휴장이라 9/4."""
    t = resolve_target_session("morning", datetime(2026, 9, 8, 7, 10))
    assert (t["kr_date"], t["us_date"]) == ("2026-09-07", "2026-09-04")


def test_chuseok_break_shifts_only_the_korean_target():
    """9/28(월) 아침 — 한국은 9/24~25 추석 + 주말이라 9/23, 미국은 9/25."""
    t = resolve_target_session("morning", datetime(2026, 9, 28, 7, 10))
    assert (t["kr_date"], t["us_date"]) == ("2026-09-23", "2026-09-25")


def test_report_run_on_a_korean_holiday_falls_back_not_empty():
    """추석 당일에 실행돼도 대상은 직전 거래일이라 리포트가 비지 않는다."""
    t = resolve_target_session("morning", datetime(2026, 9, 25, 7, 10))
    assert t["kr_date"] == "2026-09-23"


def test_holiday_target_is_still_immune_to_run_time():
    on_time = resolve_target_session("morning", datetime(2026, 9, 8, 7, 10))
    delayed = resolve_target_session("morning", datetime(2026, 9, 8, 11, 40))
    assert (delayed["kr_date"], delayed["us_date"]) == (on_time["kr_date"], on_time["us_date"])


# ── 리포트 설명 ──────────────────────────────────────────────────────────────

def test_morning_gap_is_explained_by_holidays_not_by_market_hours():
    """휴장 때문에 갈린 날짜에 "미국장이 22:30에 열려서"라는 설명이 붙으면
    틀린 설명이다. 원인을 달력으로 짚어야 한다."""
    b = _format_market_session_block(
        {}, None, None, target=resolve_target_session("morning", datetime(2026, 9, 8, 7, 10))
    )
    assert "휴장일이 달라" in b
    assert "Labor Day" in b
    assert "22:30" not in b


def test_chuseok_gap_names_both_closed_days():
    b = _format_market_session_block(
        {}, None, None, target=resolve_target_session("morning", datetime(2026, 9, 28, 7, 10))
    )
    assert "추석" in b and "9/24" in b and "9/25" in b


def test_evening_gap_still_explained_by_market_hours():
    b = _format_market_session_block(
        {}, None, None, target=resolve_target_session("evening", datetime(2026, 9, 2, 20, 40))
    )
    assert "22:30" in b
    assert "휴장일이 달라" not in b


def test_holiday_name_lookup():
    assert holiday_name(date(2026, 9, 25), "KR") == "추석"
    assert holiday_name(date(2026, 9, 7), "US") == "Labor Day"
    assert holiday_name(date(2026, 9, 8), "US") is None
