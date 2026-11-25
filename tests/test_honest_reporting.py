"""
정직한 표시 테스트 — 신뢰도 점수, 등급 예측력 점검, 참고 가격대 검증 줄, 등급 정의

2026-11-25 점검:
  - 신뢰도는 실행 116회 모두 91 또는 100점이었다(뉴스 중복 제거에만 감점).
  - 리포트 적중률은 1거래일 69%(±2%p 허용)였지만 시장 대비·허용오차 없이 47%였다.
  - 청산 신호 적중 85%는 같은 기간 아무 날이나 골랐을 때의 85%와 같았다.
  - '안전' 등급 설명("리스크가 낮고 변동성이 제한적")이 계산 방식과 맞지 않았다.
"""
from __future__ import annotations

import pytest

from app.engine.accuracy_audit import audit, verdict
from app.reports import report_builder as rb
from app.utils.data_validator import DataValidator

_TARGET = {"kr_date": "2026-11-24", "us_date": "2026-11-24"}
_MACRO = {"_mock": False, "us_market": {}, "kr_market": {}, "currencies": {}, "rates": {},
          "sentiment": {}, "data_dates": {"KOSPI": "2026-11-24", "SP500": "2026-11-24"}}


def _stocks(n=20):
    return [{"id": f"KR_{i:03d}"} for i in range(n)]


def _prices(n=20, date="2026-11-24"):
    return {f"KR_{i:03d}": {"_mock": False, "data_date": date} for i in range(n)}


def _news(n=20):
    return {f"KR_{i:03d}": [{"headline": f"기사 {i}"}, {"headline": "공통 기사"}] for i in range(n)}


def _conf(**kw):
    args = dict(price_data=_prices(), news_data=_news(), macro_data=_MACRO, stocks=_stocks(),
                target=_TARGET, missing_stocks={})
    args.update(kw)
    return DataValidator().validate(**args)["overall"]


# ── 신뢰도 ────────────────────────────────────────────────────────────────────

def test_clean_run_scores_full_marks_despite_shared_news():
    """관심종목끼리 같은 기사를 공유하는 건 정상 — 예전엔 이것 하나로 91점에 고정됐다."""
    o = _conf()
    assert o["confidence"] == 100 and o["issues"] == []


def test_stale_missing_gaps_and_index_dates_now_cost_points():
    prices = _prices()
    prices["KR_000"]["data_date"] = "2026-11-23"                  # 지연
    prices["KR_001"]["prev_gap_sessions"] = 1                      # 등락률 불연속
    missing = {"KR_019": {"missing": True}}
    stocks = _stocks(19)                                           # 파이프라인은 결측을 빼고 넘긴다
    prices.pop("KR_019")
    macro = dict(_MACRO, data_dates={"KOSPI": "2026-11-21", "SP500": "2026-11-24"})
    o = _conf(price_data=prices, stocks=stocks, missing_stocks=missing, macro_data=macro)
    # 결측 1/20×60 + 지연 1/20×40 + 불연속 1/20×30 + 지수 5 = 3 + 2 + 1.5 + 5
    assert o["confidence"] == pytest.approx(88.5)
    joined = " ".join(o["issues"])
    assert "결측" in joined and "지연" in joined and "누적" in joined and "KOSPI" in joined


def test_half_missing_drops_below_the_grade_cap_line():
    prices = _prices(10)
    missing = {f"KR_{i:03d}": {"missing": True} for i in range(10, 20)}
    o = _conf(price_data=prices, stocks=_stocks(10), missing_stocks=missing)
    assert o["confidence"] == 70 and o["status"] == "보통"


def test_without_target_behaves_like_before_for_the_dashboard():
    o = DataValidator().validate(_prices(), _news(), _MACRO, _stocks())["overall"]
    assert o["confidence"] == 100


# ── 등급 예측력 점검: 분할 · 시장 정렬 · 판정 ─────────────────────────────────

def _snap(date, prices, grades=None, scores=None, us_date=None):
    return {"date": date, "report_type": "morning", "is_trading_day": True,
            "closing_prices": prices, "grades": grades or {}, "scores": scores or {},
            "us_date": us_date or date}


def test_split_is_converted_before_computing_returns():
    a = _snap("2026-11-19", {"US_SNDK": 755.12, "US_NVDA": 100.0}, grades={"US_SNDK": "안전"})
    b = _snap("2026-11-20", {"US_SNDK": 94.39, "US_NVDA": 100.0})
    raw = audit({"a": a, "b": b})["horizons"][1]["by_grade"]["안전"]["avg_excess"]
    fixed = audit({"a": a, "b": b}, factor=lambda s, x, y: 8.0 if s == "US_SNDK" else 1.0)
    assert raw < -40                                              # 분할을 모르면 −87.5% 하락처럼 보인다
    assert fixed["horizons"][1]["by_grade"]["안전"]["avg_excess"] == pytest.approx(0.0, abs=0.01)


def test_us_prices_are_compared_only_between_unified_mornings():
    """한국만 쉰 날 다음 아침 칸의 미국 가격은 하루 뒤 것이다 — 섞으면 날짜가 어긋난다."""
    a = _snap("2026-10-08", {"US_NVDA": 100.0, "US_QQQ": 100.0}, us_date="2026-10-09")
    b = _snap("2026-10-12", {"US_NVDA": 110.0, "US_QQQ": 100.0})
    assert audit({"a": a, "b": b})["horizons"][1]["pairs"] == 0


def test_overlap_adjusted_t_is_used_for_significance():
    h20 = {"score_ic": {"mean": -0.17, "n": 37, "significant": False, "adj_t": -0.7}}
    assert verdict({"horizons": {20: h20}}).endswith("근거는 없습니다.")


def test_verdict_wording_for_each_case():
    def hz(mean, sig, n=40):
        return {"score_ic": {"mean": mean, "n": n, "significant": sig}}
    assert "근거는 없습니다" in verdict({"horizons": {1: hz(0.01, False), 5: hz(-0.02, False)}})
    assert "역방향" in verdict({"horizons": {1: hz(-0.2, True)}})
    assert "통계적으로 확인" in verdict({"horizons": {1: hz(0.2, True)}})
    assert "엇갈려" in verdict({"horizons": {1: hz(0.2, True), 5: hz(-0.2, True)}})
    assert "이력이 쌓이지" in verdict({"horizons": {1: hz(0.2, True, n=5)}})


# ── 리포트 블록 ───────────────────────────────────────────────────────────────

_AUDIT = {
    "snapshots": 57, "first": "2026-09-01", "last": "2026-11-24",
    "horizons": {
        1: {"pairs": 56, "directional_n": 272, "strict_hit_rate": 49, "legacy_hit_rate": 70,
            "by_grade": {"안전": {"n": 250, "avg_excess": -0.10}, "보통": {"n": 584, "avg_excess": 0.09}},
            "score_ic": {"mean": -0.024, "n": 56, "positive_share": 45, "t": -0.53, "adj_t": -0.53,
                         "significant": False},
            "factor_ic": {"news_sentiment": {"mean": 0.35, "n": 52, "significant": True},
                          "price_momentum": {"mean": -0.04, "n": 52, "significant": False}}},
    },
}


def test_accuracy_block_states_the_method_and_the_verdict():
    b = rb._format_accuracy_block(_AUDIT)
    assert "시장 관심종목 평균 대비 초과수익" in b and "허용오차 없음" in b
    assert "0과 구분되지 않음" in b and "적중 49%" in b
    assert "판정:" in b and "근거는 없습니다" in b
    assert "70%" not in b                                         # 부풀려진 옛 수치는 싣지 않는다


def test_factor_block_stars_only_significant_factors():
    b = rb._format_factor_accuracy_block(_AUDIT)
    assert "뉴스 감성" in b and "+0.35★" in b
    assert "-0.04★" not in b


def test_factor_block_says_when_nothing_is_significant():
    quiet = {"horizons": {1: {"factor_ic": {"price_momentum": {"mean": -0.04, "n": 52, "significant": False}}}}}
    assert "의미 있는 요인은 없습니다" in rb._format_factor_accuracy_block(quiet)


def test_verification_line_compares_exit_signals_with_the_base_rate():
    line = rb._verification_line({
        "void": 2,
        "entry": {"decided": 28, "wins": 5, "losses": 23, "win_rate": 18, "avg_r": -0.60, "enough": True},
        "exit": {"decided": 40, "hits": 34, "enough": True, "base_rate": {"n": 119, "hits": 102, "rate": 86}},
    })
    assert line.startswith("> **검증 결과**")
    assert "승률 18%" in line and "-0.60R" in line and "손실이 더 컸습니다" in line
    assert "아무 날이나 골랐을 때 86%" in line and "확인되지 않았습니다" in line
    assert "무효 처리한 2건" in line


def test_gap_stocks_are_called_out_in_the_report():
    pdata = {"US_SNDK": {"name": "SanDisk", "data_date": "2026-11-23", "prev_data_date": "2026-11-19",
                         "prev_gap_sessions": 1, "change_pct": -7.08}}
    b = rb._format_stale_block(pdata, {"kr_date": "2026-11-23", "us_date": "2026-11-23"})
    assert "등락률 불연속" in b and "2026-11-19 → 2026-11-23" in b and "2거래일" in b


def test_grade_definitions_no_longer_promise_low_risk():
    assert "앞날 예측이 아님" in rb.SYSTEM_PROMPT
    assert "변동성이 제한적" not in rb.SYSTEM_PROMPT
    from app.engine.rating_analyzer import GRADE_META
    assert "낮다는 뜻은 아님" in GRADE_META["안전"]["description"]


def test_quality_line_counts_multi_day_changes():
    from app.main import summarize_run_quality
    line, degraded = summarize_run_quality(
        {"US_SNDK": {"data_date": "2026-11-23", "price_source": "yfinance", "prev_gap_sessions": 1}},
        {}, {"kr_date": "2026-11-23", "us_date": "2026-11-23"})
    assert "불연속 1" in line and degraded
