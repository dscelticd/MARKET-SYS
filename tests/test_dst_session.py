"""
서머타임 전환 테스트 — 미국 정규장 시각을 시차 고정값이 아니라 시간대 규칙으로 계산

배경: 미국 시차가 KST 대비 -13시간(서머타임 기준)으로 고정돼 있었다. 2026-11-01
서머타임이 끝나면 미국장 마감은 05:00 KST가 아니라 06:00 KST다. 고정값을 쓰면
05:00~06:00 KST 사이에 진행 중인 미국 정규장을 "마감"으로 판정한다.

이 구간은 가상의 시나리오가 아니다. 2026-10 저녁 결산 예약 실행은 실측
03:29·04:43·05:24에 돌았다. 장중 행은 Close가 NaN이 아니라 공식 종가(info)
경로의 세 겹 검증도 거치지 않고 그대로 종가로 채택된다.
"""
from __future__ import annotations

from datetime import date, datetime

from app.reports.report_builder import _format_market_session_block
from app.utils.market_calendar import (
    market_session_state,
    resolve_target_session,
    session_close_kst,
    session_open_kst,
)


# ── 정규장 시각 환산 ─────────────────────────────────────────────────────────

def test_us_close_is_0500_kst_during_daylight_saving():
    assert session_close_kst("US", date(2026, 10, 15)) == datetime(2026, 10, 16, 5, 0)


def test_us_close_moves_to_0600_kst_after_daylight_saving_ends():
    assert session_close_kst("US", date(2026, 11, 2)) == datetime(2026, 11, 3, 6, 0)


def test_us_open_moves_to_2330_kst_after_daylight_saving_ends():
    assert session_open_kst("US", date(2026, 10, 15)) == datetime(2026, 10, 15, 22, 30)
    assert session_open_kst("US", date(2026, 11, 2)) == datetime(2026, 11, 2, 23, 30)


def test_spring_forward_switches_back_to_0500():
    """3월 둘째 일요일(2026-03-08)에 서머타임이 시작된다 — 그 전 금요일과 후 월요일."""
    assert session_close_kst("US", date(2026, 3, 6)) == datetime(2026, 3, 7, 6, 0)
    assert session_close_kst("US", date(2026, 3, 9)) == datetime(2026, 3, 10, 5, 0)


def test_kr_session_is_unaffected():
    assert session_close_kst("KR", date(2026, 11, 2)) == datetime(2026, 11, 2, 15, 30)


# ── 세션 상태 ────────────────────────────────────────────────────────────────

def test_last_winter_hour_is_still_live():
    """11/3 05:30 KST — 미국 11/2 정규장 마지막 한 시간. 고정값은 '마감'이라 했다."""
    assert market_session_state("US", datetime(2026, 11, 3, 5, 30)) == "개장중"


def test_2300_kst_in_winter_is_before_open():
    assert market_session_state("US", datetime(2026, 11, 2, 23, 0)) == "개장전"


def test_summer_states_are_unchanged():
    assert market_session_state("US", datetime(2026, 10, 16, 5, 30)) == "마감"
    assert market_session_state("US", datetime(2026, 10, 15, 23, 0)) == "개장중"


# ── 대상 거래일 ──────────────────────────────────────────────────────────────

def test_delayed_evening_in_winter_does_not_target_the_live_us_session():
    """11/2(월) 저녁 결산이 05:30까지 밀려도 미국 대상은 진행 중인 11/2가 아니라
    직전 완료 세션(10/30 금)이어야 한다 (계약 C2)."""
    t = resolve_target_session("evening", datetime(2026, 11, 3, 5, 30))
    assert t["kr_date"] == "2026-11-02"
    assert t["us_date"] == "2026-10-30"


def test_delayed_evening_after_winter_close_targets_that_session():
    t = resolve_target_session("evening", datetime(2026, 11, 3, 6, 5))
    assert t["us_date"] == "2026-11-02"


def test_morning_target_is_the_same_in_both_seasons():
    """아침 브리핑 기준선은 09:00 KST — 미국장은 여름·겨울 모두 그 전에 끝난다."""
    t = resolve_target_session("morning", datetime(2026, 11, 3, 7, 10))
    assert (t["kr_date"], t["us_date"]) == ("2026-11-02", "2026-11-02")


# ── 리포트 문구 ──────────────────────────────────────────────────────────────

def test_evening_explanation_uses_the_actual_open_time():
    summer = _format_market_session_block(
        {}, None, None, target=resolve_target_session("evening", datetime(2026, 10, 15, 20, 40))
    )
    winter = _format_market_session_block(
        {}, None, None, target=resolve_target_session("evening", datetime(2026, 11, 2, 20, 40))
    )
    assert "22:30" in summer
    assert "23:30" in winter and "22:30" not in winter
