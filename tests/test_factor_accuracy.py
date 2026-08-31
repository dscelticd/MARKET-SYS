"""
요인별 신호 적중률 테스트

배경: 기존 적중률은 **등급 단위**(추천/안전 ↔ 주의/위험) 집계라 "어떤 신호가 실제로
      맞았는가"는 알 수 없었다. 총점만 이력에 저장하고 요인별 점수는 버렸기 때문에
      사후 계산 자체가 불가능했다. components 저장을 시작하면서 가능해진 집계다.

      목표 서술: "수급이 하락을 가리킵니다. 이 신호는 최근 20일 기준 68% 적중했습니다."

      가장 중요한 안전장치는 **표본 부족 시 수치를 내놓지 않는 것**이다. 3건짜리
      "80% 적중"은 근거가 아니라 착시이고, 리포트가 그걸 인용하면 오히려 신뢰를
      떨어뜨린다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.engine.history_tracker import HistoryTracker
from app.reports.report_builder import _format_factor_accuracy_block


def _tracker(entries: dict) -> HistoryTracker:
    t = HistoryTracker()
    t._data = entries
    return t


def _entry(days_ago: int, components: dict, prices: dict, confidence: float = 100.0) -> dict:
    d = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    return {
        "date": d,
        "report_type": "morning",
        "is_trading_day": True,   # 요일과 무관하게 거래일로 고정 (거래일 필터 영향 배제)
        "components": components,
        "closing_prices": prices,
        "grades": {},
        "data_quality": {"overall": {"confidence": confidence}},
    }


def _bulk(n: int, factor: str, score: float, rose: bool) -> tuple[dict, dict]:
    """n개 종목에 대해 같은 요인 점수를 주고, 가격이 오르거나 내린 상황을 만든다."""
    comps = {f"S{i}": {factor: score} for i in range(n)}
    past = {f"S{i}": 100.0 for i in range(n)}
    curr = {f"S{i}": {"price": 110.0 if rose else 90.0} for i in range(n)}
    return {"comps": comps, "past": past}, curr


# ── 기본 집계 ────────────────────────────────────────────────────────────────

def test_bullish_factor_counted_as_hit_when_price_rose():
    data, curr = _bulk(25, "technical_signal", 70.0, rose=True)
    t = _tracker({"k": _entry(20, data["comps"], data["past"])})
    out = t.compute_factor_accuracy(curr, lookback_days=20)
    f = out["factors"]["technical_signal"]
    assert f["count"] == 25 and f["hit"] == 25
    assert f["hit_rate"] == 100.0
    assert f["sufficient"] is True
    assert out["ready"] is True


def test_bullish_factor_counted_as_miss_when_price_fell():
    data, curr = _bulk(25, "technical_signal", 70.0, rose=False)
    t = _tracker({"k": _entry(20, data["comps"], data["past"])})
    f = t.compute_factor_accuracy(curr, lookback_days=20)["factors"]["technical_signal"]
    assert f["hit"] == 0 and f["hit_rate"] == 0.0


def test_bearish_factor_counted_as_hit_when_price_fell():
    data, curr = _bulk(25, "news_sentiment", 30.0, rose=False)
    t = _tracker({"k": _entry(20, data["comps"], data["past"])})
    f = t.compute_factor_accuracy(curr, lookback_days=20)["factors"]["news_sentiment"]
    assert f["hit"] == 25


def test_neutral_scores_are_excluded_from_judgment():
    """45~55 구간은 방향 신호가 아니므로 판정에서 빠져야 한다.
    애매한 값을 한쪽으로 몰면 통계가 의미를 잃는다."""
    data, curr = _bulk(25, "macro_alignment", 50.0, rose=True)
    t = _tracker({"k": _entry(20, data["comps"], data["past"])})
    out = t.compute_factor_accuracy(curr, lookback_days=20)
    assert "macro_alignment" not in out["factors"]


# ── 표본 부족 처리 (핵심 안전장치) ───────────────────────────────────────────

def test_thin_sample_is_marked_insufficient():
    """3건짜리 100% 적중은 통계가 아니다."""
    data, curr = _bulk(3, "volume_signal", 80.0, rose=True)
    t = _tracker({"k": _entry(20, data["comps"], data["past"])})
    out = t.compute_factor_accuracy(curr, lookback_days=20)
    f = out["factors"]["volume_signal"]
    assert f["count"] == 3
    assert f["sufficient"] is False
    assert out["ready"] is False


def test_report_block_hides_insufficient_factor_numbers():
    """표본 부족 축의 적중률 수치가 프롬프트에 노출되면 Claude가 인용해버린다."""
    fa = {
        "lookback_days": 20, "reference_date": "2026-08-11",
        "factors": {"volume_signal": {"count": 3, "hit": 3, "hit_rate": 100.0,
                                      "avg_return_pct": 5.0, "sufficient": False}},
        "ready": False,
    }
    block = _format_factor_accuracy_block(fa)
    assert "100.0%" not in block and "100%" not in block
    assert "표본 부족" in block
    assert "거래량 신호" in block


def test_report_block_shows_sufficient_factor_numbers():
    fa = {
        "lookback_days": 20, "reference_date": "2026-08-11",
        "factors": {"technical_signal": {"count": 40, "hit": 27, "hit_rate": 67.5,
                                         "avg_return_pct": 1.8, "sufficient": True}},
        "ready": True,
    }
    block = _format_factor_accuracy_block(fa)
    assert "67.5%" in block
    assert "기술적 신호" in block
    assert "27/40건" in block


def test_report_block_when_nothing_accumulated_yet():
    assert "통계 산출 전" in _format_factor_accuracy_block(None)
    assert "통계 산출 전" in _format_factor_accuracy_block({"factors": {}})


def test_report_block_states_when_no_factor_is_ready():
    fa = {
        "lookback_days": 20, "reference_date": "2026-08-11",
        "factors": {"news_sentiment": {"count": 5, "hit": 4, "hit_rate": 80.0,
                                       "avg_return_pct": 2.0, "sufficient": False}},
        "ready": False,
    }
    block = _format_factor_accuracy_block(fa)
    assert "표본이 쌓인 축이 없습니다" in block
    assert "80.0%" not in block


# ── 경계 조건 ────────────────────────────────────────────────────────────────

def test_returns_empty_when_no_components_in_history():
    """components 저장 이전의 과거 엔트리만 있으면 집계 불가."""
    t = _tracker({"k": {
        "date": (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d"),
        "report_type": "morning", "is_trading_day": True,
        "grades": {}, "closing_prices": {"A": 100.0},
        "data_quality": {"overall": {"confidence": 100.0}},
    }})
    out = t.compute_factor_accuracy({"A": {"price": 110.0}}, lookback_days=20)
    assert out["factors"] == {} and out["ready"] is False


def test_low_confidence_snapshot_is_excluded():
    """그 시점 데이터 신뢰도가 낮으면 스냅샷 전체를 통계에 쓰지 않는다."""
    data, curr = _bulk(25, "technical_signal", 70.0, rose=True)
    t = _tracker({"k": _entry(20, data["comps"], data["past"], confidence=30.0)})
    assert t.compute_factor_accuracy(curr, lookback_days=20)["factors"] == {}


def test_missing_current_price_skips_that_stock():
    data, curr = _bulk(25, "technical_signal", 70.0, rose=True)
    curr.pop("S0")
    t = _tracker({"k": _entry(20, data["comps"], data["past"])})
    assert t.compute_factor_accuracy(curr, lookback_days=20)["factors"]["technical_signal"]["count"] == 24


def test_non_numeric_component_score_is_ignored():
    comps = {"A": {"technical_signal": "N/A"}}
    t = _tracker({"k": _entry(20, comps, {"A": 100.0})})
    out = t.compute_factor_accuracy({"A": {"price": 110.0}}, lookback_days=20)
    assert out["factors"] == {}


def test_empty_history_returns_not_ready():
    out = _tracker({}).compute_factor_accuracy({"A": {"price": 100.0}}, lookback_days=20)
    assert out["ready"] is False and out["reference_date"] is None
