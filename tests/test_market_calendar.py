"""
거래일/데이터 기준일 처리 테스트

배경: 파이프라인 전체에 요일 개념이 없어 주말에 실행하면 금요일 종가를 그대로
      "당일 등락률"로 보고했다. 실측 사고 사례:
        - 삼성전자 281,500원 +3.87%가 금(21)저녁·토(22)아침·토(22)저녁·일(23)아침
          4개 리포트에 동일하게 "당일 등락"으로 등장
        - 토요일 아침 KOSDAQ +1.99%(목요일치) → 같은 토요일 저녁 -4.63%(금요일치)로
          하루 안에 방향이 정반대 (yfinance 심볼별 반영 지연)
      요일 계산만으로는 공휴일을 못 잡으므로, 데이터에 실제로 박힌 기준일(data_date)을
      집계하는 방식이 핵심 방어선이다.
"""
from __future__ import annotations

from datetime import date, datetime

from app.engine.history_tracker import HistoryTracker
from app.reports.report_builder import _format_market_session_block
from app.utils.market_calendar import (
    is_trading_day,
    previous_trading_day,
    summarize_data_freshness,
    weekday_kr,
)


# ── 거래일 판정 ──────────────────────────────────────────────────────────────

def test_weekday_is_trading_day_and_weekend_is_not():
    assert is_trading_day(date(2026, 8, 28)) is True   # 금
    assert is_trading_day(date(2026, 8, 29)) is False  # 토
    assert is_trading_day(date(2026, 8, 30)) is False  # 일
    assert is_trading_day(date(2026, 8, 31)) is True   # 월


def test_previous_trading_day_skips_weekend():
    # 월요일의 직전 거래일은 (일·토를 건너뛴) 금요일
    assert previous_trading_day(date(2026, 8, 31)) == date(2026, 8, 28)
    # 토요일의 직전 거래일은 금요일
    assert previous_trading_day(date(2026, 8, 29)) == date(2026, 8, 28)


def test_weekday_kr_labels():
    assert weekday_kr(date(2026, 8, 31)) == "월"
    assert weekday_kr(date(2026, 8, 30)) == "일"


# ── 데이터 기준일 집계 ────────────────────────────────────────────────────────

def _price(data_date: str) -> dict:
    return {"price": 100.0, "change_pct": 1.0, "data_date": data_date}


def test_freshness_flags_weekend_run_with_stale_friday_data():
    # 일요일에 실행했는데 데이터는 금요일 종가인 실제 사고 상황
    freshness = summarize_data_freshness(
        {"A": _price("2026-08-28"), "B": _price("2026-08-28")},
        now=datetime(2026, 8, 30, 7, 0),  # 일요일 아침
    )
    assert freshness["run_is_trading_day"] is False
    assert freshness["latest_data_date"] == "2026-08-28"
    assert freshness["stale_days"] == 2
    assert freshness["has_fresh_data"] is False
    assert freshness["mixed_dates"] is False


def test_freshness_detects_mixed_data_dates_across_symbols():
    # 삼성전자=8/28, NVDA=8/27로 기준일이 갈리는 실측 상황
    freshness = summarize_data_freshness(
        {"KR": _price("2026-08-28"), "US": _price("2026-08-27")},
        now=datetime(2026, 8, 31, 7, 0),
    )
    assert freshness["mixed_dates"] is True
    assert freshness["date_counts"] == {"2026-08-28": 1, "2026-08-27": 1}
    assert freshness["latest_data_date"] == "2026-08-28"
    assert freshness["oldest_data_date"] == "2026-08-27"


def test_freshness_marks_fresh_when_data_is_same_day():
    freshness = summarize_data_freshness(
        {"A": _price("2026-08-31")}, now=datetime(2026, 8, 31, 20, 30)
    )
    assert freshness["has_fresh_data"] is True
    assert freshness["stale_days"] == 0
    assert freshness["run_is_trading_day"] is True


def test_freshness_handles_missing_data_date_gracefully():
    freshness = summarize_data_freshness({"A": {"price": 1.0}}, now=datetime(2026, 8, 31))
    assert freshness["latest_data_date"] is None
    assert freshness["has_fresh_data"] is False


# ── 리포트 세션 블록 ─────────────────────────────────────────────────────────

def test_session_block_warns_on_holiday_and_states_actual_date():
    freshness = summarize_data_freshness(
        {"A": _price("2026-08-28")}, now=datetime(2026, 8, 30, 7, 0)
    )
    block = _format_market_session_block(freshness)
    assert "휴장일" in block
    assert "2026-08-28" in block


def test_session_block_flags_no_new_trading_since_previous_report():
    freshness = summarize_data_freshness(
        {"A": _price("2026-08-28")}, now=datetime(2026, 8, 30, 7, 0)
    )
    block = _format_market_session_block(freshness, prev_report_data_date="2026-08-28")
    assert "새로운 거래가 없" in block


def test_session_block_warns_when_symbol_dates_differ():
    freshness = summarize_data_freshness(
        {"KR": _price("2026-08-28"), "US": _price("2026-08-27")},
        now=datetime(2026, 8, 31, 7, 0),
    )
    block = _format_market_session_block(freshness)
    assert "기준일이 서로 다릅니다" in block


def test_session_block_handles_empty_input():
    assert "세션 정보 없음" in _format_market_session_block(None)


# ── 이력 오염 방지 ───────────────────────────────────────────────────────────

def _hist_entry(date_str: str, is_trading_day_flag: bool | None, price: float) -> dict:
    entry = {
        "date": date_str,
        "report_type": "morning",
        "grades": {"A": "안전"},
        "closing_prices": {"A": price},
        "data_quality": {"overall": {"confidence": 100.0}},
    }
    if is_trading_day_flag is not None:
        entry["is_trading_day"] = is_trading_day_flag
    return entry


def _tracker(entries: dict) -> HistoryTracker:
    t = HistoryTracker()
    t._data = entries
    return t


def test_accuracy_skips_weekend_entry_and_uses_trading_day_instead():
    """주말 엔트리는 금요일 종가 복사본이라 기준점으로 쓰면 수익률 구간이 어긋난다.
    더 오래된 거래일 엔트리가 있으면 그쪽을 기준으로 삼아야 한다."""
    t = _tracker({
        "weekend": _hist_entry("2026-08-29", False, 200.0),  # 토 — 제외돼야 함
        "friday":  _hist_entry("2026-08-28", True, 100.0),   # 금 — 이게 선택돼야 함
    })
    entry = t._find_closest_entry_before(1)
    assert entry is not None
    assert entry["date"] == "2026-08-28"


def test_find_previous_skips_weekend_entries():
    t = _tracker({
        "sat": _hist_entry("2026-08-29", False, 200.0),
        "fri": _hist_entry("2026-08-28", True, 100.0),
    })
    prev = t._find_previous("morning")
    assert prev is not None
    assert prev["date"] == "2026-08-28"


def test_entry_trading_day_derived_from_weekday_when_flag_absent():
    """플래그가 없는 과거(레거시) 엔트리도 날짜 요일로 올바르게 판정돼야 한다."""
    assert HistoryTracker._entry_is_trading_day({"date": "2026-08-29"}) is False  # 토
    assert HistoryTracker._entry_is_trading_day({"date": "2026-08-28"}) is True   # 금
    # 날짜가 깨진 엔트리는 기존 동작 유지를 위해 배제하지 않음
    assert HistoryTracker._entry_is_trading_day({"date": "bogus"}) is True


def test_explicit_flag_overrides_weekday_inference():
    # 임시 휴장(공휴일) 등 요일만으로 판정 불가한 경우를 위해 명시 플래그가 우선
    assert HistoryTracker._entry_is_trading_day(
        {"date": "2026-08-28", "is_trading_day": False}
    ) is False
