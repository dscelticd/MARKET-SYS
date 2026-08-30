"""
등급 적중률 추적 테스트 (HistoryTracker.compute_accuracy_report)

배경: 우선순위 로드맵 2순위 — 시스템이 순수 forward-looking("오늘의 판단")에
      머물지 않고, 과거 등급이 실제로 방향성을 맞혔는지 스스로 검증하는 기능.
      history_tracker.py가 이미 일별 등급·종가 스냅샷을 저장하고 있어 그 데이터를
      재사용한다. 실제 파일(data/history/ratings_history.json)을 건드리지 않도록
      HistoryTracker._data를 직접 주입해 테스트한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.engine.history_tracker import HistoryTracker
from app.reports.report_builder import _format_accuracy_block


def _make_tracker(entries: dict) -> HistoryTracker:
    tracker = HistoryTracker()
    tracker._data = entries  # 실제 파일은 건드리지 않고 메모리 데이터만 주입
    return tracker


def _entry(date: str, grades: dict, prices: dict, confidence: float = 100.0,
           report_type: str = "morning", is_trading_day: bool = True) -> dict:
    # is_trading_day를 명시적으로 넣는 이유: 적중률 계산이 주말 엔트리를 기준점에서
    # 제외하도록 바뀌었는데, 테스트가 datetime.now() 기준 상대 날짜를 쓰다 보니
    # 실행 요일에 따라 5일/20일 전이 주말에 걸려(예: 목요일 실행 시 5일 전=토요일)
    # 테스트가 요일에 따라 깨지는 문제가 있었다. 여기서는 거래일 필터가 아니라
    # 적중률 산식 자체를 검증하므로 항상 거래일로 고정한다.
    return {
        "date": date,
        "report_type": report_type,
        "is_trading_day": is_trading_day,
        "grades": grades,
        "closing_prices": prices,
        "data_quality": {"overall": {"confidence": confidence}},
    }


def test_bullish_grade_hit_when_price_rose():
    past_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    tracker = _make_tracker({
        "k1": _entry(past_date, {"A": "추천"}, {"A": 100.0}),
    })
    result = tracker.compute_accuracy_report({"A": {"price": 110.0}})
    stats = result[20]
    assert stats["sample_count"] == 1
    assert stats["overall_hit_rate"] == 100.0
    assert stats["grade_stats"]["추천"]["hit"] == 1


def test_bullish_grade_miss_when_price_fell_significantly():
    past_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    tracker = _make_tracker({
        "k1": _entry(past_date, {"A": "안전"}, {"A": 100.0}),
    })
    result = tracker.compute_accuracy_report({"A": {"price": 90.0}})  # -10%
    stats = result[20]
    assert stats["sample_count"] == 1
    assert stats["overall_hit_rate"] == 0.0


def test_bearish_grade_hit_when_price_fell():
    past_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    tracker = _make_tracker({
        "k1": _entry(past_date, {"B": "위험"}, {"B": 100.0}),
    })
    result = tracker.compute_accuracy_report({"B": {"price": 90.0}})
    stats = result[5]
    assert stats["overall_hit_rate"] == 100.0


def test_tolerance_band_treats_small_dip_as_hit_for_bullish():
    """±2%p 허용 오차 — 추천 등급인데 -1.5%면 여전히 '적중'으로 처리"""
    past_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    tracker = _make_tracker({
        "k1": _entry(past_date, {"A": "추천"}, {"A": 100.0}),
    })
    result = tracker.compute_accuracy_report({"A": {"price": 98.5}})  # -1.5%
    assert result[20]["overall_hit_rate"] == 100.0


def test_neutral_and_pending_grades_excluded_from_stats():
    past_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    tracker = _make_tracker({
        "k1": _entry(past_date, {"A": "보통", "B": "판단보류", "C": "추천"}, {"A": 100.0, "B": 100.0, "C": 100.0}),
    })
    result = tracker.compute_accuracy_report({"A": {"price": 50.0}, "B": {"price": 50.0}, "C": {"price": 105.0}})
    stats = result[20]
    assert stats["sample_count"] == 1  # C(추천)만 집계, A/B는 제외
    assert "보통" not in stats["grade_stats"]
    assert "판단보류" not in stats["grade_stats"]


def test_low_confidence_snapshot_excluded_entirely():
    """그 시점 데이터 신뢰도가 낮으면(<50) 등급·주가 자체를 신뢰할 수 없어 전체 제외"""
    past_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    tracker = _make_tracker({
        "k1": _entry(past_date, {"A": "추천"}, {"A": 100.0}, confidence=30.0),
    })
    result = tracker.compute_accuracy_report({"A": {"price": 150.0}})
    assert result[20]["sample_count"] == 0


def test_insufficient_history_returns_empty_stats():
    tracker = _make_tracker({})
    result = tracker.compute_accuracy_report({"A": {"price": 100.0}})
    assert result[5]["sample_count"] == 0
    assert result[20]["sample_count"] == 0
    assert result[5]["overall_hit_rate"] is None


def test_picks_morning_entry_when_both_available_on_same_day():
    past_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    tracker = _make_tracker({
        "k1": _entry(past_date, {"A": "추천"}, {"A": 100.0}, report_type="evening"),
        "k2": _entry(past_date, {"A": "추천"}, {"A": 80.0}, report_type="morning"),
    })
    result = tracker.compute_accuracy_report({"A": {"price": 90.0}})
    # morning(80.0) 기준이면 +12.5%(적중), evening(100.0) 기준이면 -10%(불일치) — morning이 선택되어야 함
    assert result[20]["overall_hit_rate"] == 100.0


def test_missing_current_price_skips_stock_without_crashing():
    past_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    tracker = _make_tracker({
        "k1": _entry(past_date, {"A": "추천"}, {"A": 100.0}),
    })
    result = tracker.compute_accuracy_report({})  # A의 현재가 없음
    assert result[20]["sample_count"] == 0


def test_format_accuracy_block_shows_stats_when_available():
    accuracy_report = {
        5: {"sample_count": 0, "overall_hit_rate": None, "reference_date": None, "grade_stats": {}},
        20: {
            "sample_count": 3, "overall_hit_rate": 66.7, "reference_date": "2026-07-20",
            "grade_stats": {"추천": {"count": 3, "hit": 2, "hit_rate": 66.7, "avg_return_pct": 1.2}},
        },
    }
    block = _format_accuracy_block(accuracy_report)
    assert "누적 이력 부족" in block  # 5일 항목
    assert "66.7%" in block          # 20일 항목
    assert "추천" in block


def test_format_accuracy_block_handles_none():
    assert "등급 적중률 데이터 없음" in _format_accuracy_block(None)
    assert "등급 적중률 데이터 없음" in _format_accuracy_block({})
