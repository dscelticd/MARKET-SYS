"""
방향성 근거(상승/하락) 강화 테스트

배경: 리포트가 제시하는 근거가 6가지 임계값(당일 등락 ±2%, 거래량 1.8배, 뉴스 건수,
      사이클 문자열, VIX)뿐이었다. 문제는 세 가지였다:
      ① 점수에는 반영되는데 근거로는 설명되지 않음 — 기술적 신호 15%, 애널리스트 10%
         가중치를 차지하면서 근거 목록에는 한 줄도 없었다("점수는 올랐는데 왜인지 모름")
      ② 수급이 점수·근거 어디에도 없음 — KIS 공식 API로 외국인·기관·개인 실측
         순매수를 3/5/10/20일 누적으로 받아오면서 등급 기여도는 0%였다
      ③ 모순 신호가 가중평균에 상쇄돼 사라짐 — "강한 상승 3 + 약한 하락 4"와
         "전부 미지근한 중립"이 같은 점수가 됐다

      점수 산식(가중치)은 의도적으로 건드리지 않았다 — 바꾸면 기존 등급 적중률
      이력과 비교가 불가능해진다. 근거 노출만 강화한다.
"""
from __future__ import annotations

from app.engine.signal_scorer import SignalScorer
from app.reports.report_builder import _format_rating_block

_STOCK = {"id": "KR_005930", "name": "삼성전자", "sector": "반도체",
          "themes": ["AI"], "country": "KR"}
_MACRO = {"sentiment": {}, "us_market": {}, "currencies": {}}


def _score(price: dict, news=None):
    return SignalScorer().score(_STOCK, price, news or [], _MACRO)


def _flow(foreign_5d, inst_5d, source="kis"):
    return {"_mock": False, "_source": source,
            "foreign_net_5d": foreign_5d, "institution_net_5d": inst_5d}


# ── 기술적 지표가 근거로 노출되는가 ──────────────────────────────────────────

def test_oversold_rsi_becomes_positive_evidence():
    r = _score({"change_pct": 0.0, "technical": {"rsi_14": 28}})
    assert any("RSI 28" in f and "과매도" in f for f in r["positive_factors"])


def test_overbought_rsi_becomes_negative_evidence():
    r = _score({"change_pct": 0.0, "technical": {"rsi_14": 76}})
    assert any("RSI 76" in f and "과매수" in f for f in r["negative_factors"])


def test_missing_rsi_is_not_treated_as_oversold():
    """RSI가 없을 때 0.0으로 떨어져 '과매도'로 오판되면 안 된다."""
    r = _score({"change_pct": 0.0, "technical": {}})
    assert not any("과매도" in f for f in r["positive_factors"])


def test_moving_average_alignment_is_evidence():
    up = _score({"change_pct": 0.0, "technical": {"ma5": 110, "ma20": 100}})
    assert any("정배열" in f for f in up["positive_factors"])
    down = _score({"change_pct": 0.0, "technical": {"ma5": 90, "ma20": 100}})
    assert any("역배열" in f for f in down["negative_factors"])


def test_macd_direction_reported_without_scale_dependent_magnitude():
    """MACD 히스토그램 절대값은 주가 스케일에 비례해 종목 간 비교가 불가능하다
    (삼성전자 +1331.98 vs NVIDIA -0.47). 방향만 제시해야 한다."""
    r = _score({"change_pct": 0.0, "technical": {"macd_histogram": 1331.98}})
    macd = [f for f in r["positive_factors"] if "MACD" in f]
    assert macd and "1331" not in macd[0]


# ── 수급이 근거로 노출되는가 ─────────────────────────────────────────────────

def test_foreign_and_institution_selling_becomes_negative_evidence():
    r = _score({"change_pct": 0.0, "investor_flow": _flow(-9_912_739, -7_312_094)})
    neg = [f for f in r["negative_factors"] if "순매도" in f]
    assert neg
    assert "실측" in neg[0]          # KIS 출처 구분
    assert "-9,912,739" in neg[0]    # 수치 그대로 노출


def test_foreign_and_institution_buying_becomes_positive_evidence():
    r = _score({"change_pct": 0.0, "investor_flow": _flow(5_000_000, 2_000_000)})
    assert any("순매수" in f for f in r["positive_factors"])


def test_naver_sourced_flow_is_labeled_as_estimate():
    r = _score({"change_pct": 0.0, "investor_flow": _flow(-1000, -1000, source="naver")})
    neg = [f for f in r["negative_factors"] if "순매도" in f]
    assert neg and "추정" in neg[0]


def test_mock_flow_is_not_used_as_evidence():
    """Mock 폴백 수급을 실제 근거로 제시하면 안 된다."""
    r = _score({"change_pct": 0.0,
                "investor_flow": {"_mock": True, "foreign_net_5d": -1, "institution_net_5d": -1}})
    assert not any("순매" in f for f in r["negative_factors"] + r["positive_factors"])


def test_mixed_flow_direction_is_not_claimed_either_way():
    """외국인 매수·기관 매도처럼 엇갈리면 한쪽으로 단정하지 않는다."""
    r = _score({"change_pct": 0.0, "investor_flow": _flow(5_000_000, -5_000_000)})
    assert not any("순매수" in f or "순매도" in f
                   for f in r["positive_factors"] + r["negative_factors"])


# ── 애널리스트·손익비 ────────────────────────────────────────────────────────

def test_analyst_upside_becomes_evidence():
    r = _score({"change_pct": 0.0,
                "analyst": {"upside_pct": 41.9, "num_analysts": 58}})
    assert any("상승여력" in f and "58명" in f for f in r["positive_factors"])


def test_risk_reward_below_bar_goes_to_check_required():
    r = _score({"change_pct": 0.0,
                "support_resistance": {"risk_reward_ratio": 0.73,
                                       "risk_reward_meets_bar": False}})
    assert any("손익비" in f for f in r["check_required"])


# ── 다이버전스 (신호 간 불일치) ──────────────────────────────────────────────

def test_rally_without_volume_is_flagged():
    r = _score({"change_pct": 3.5, "volume_ratio": 0.6})
    assert any("거래량이 실리지 않음" in f for f in r["check_required"])


def test_rally_against_foreign_selling_is_flagged():
    r = _score({"change_pct": 3.5, "volume_ratio": 1.0,
                "investor_flow": _flow(-1_000_000, -1_000_000)})
    assert any("수급 미확인 상승" in f for f in r["check_required"])


def test_decline_with_foreign_buying_is_flagged():
    r = _score({"change_pct": -3.5, "volume_ratio": 1.0,
                "investor_flow": _flow(1_000_000, 1_000_000)})
    assert any("수급은 지지" in f for f in r["check_required"])


def test_overbought_with_weakening_momentum_is_flagged():
    r = _score({"change_pct": 0.0,
                "technical": {"rsi_14": 75, "macd_histogram": -0.4}})
    assert any("되돌림" in f for f in r["check_required"])


# ── 리포트 블록: 신호 균형 노출 ──────────────────────────────────────────────

def test_rating_block_shows_signal_balance_and_checks():
    """가중평균 점수만으로는 '3대3 팽팽함'과 '전부 중립'이 구분되지 않는다.
    방향별 신호 개수와 다이버전스를 함께 보여줘야 한다."""
    ratings = [{
        "emoji": "🔵", "name": "삼성전자", "grade": "안전",
        "total_score": 54.6, "risk_score": 30.0, "data_confidence": 100.0,
        "positive_factors": ["RSI 28 — 과매도 구간", "5일선이 20일선 위 — 단기 정배열"],
        "negative_factors": ["가격 하락 압력 (-2.2%)"],
        "check_required": ["상승에 거래량이 실리지 않음 — 상승 지속력 확인 필요"],
    }]
    block = _format_rating_block(ratings)
    assert "신호 균형: 상승 2 / 하락 1 / 확인필요 1" in block
    assert "상승 근거:" in block and "하락 근거:" in block
    assert "확인 필요:" in block
    assert "거래량이 실리지 않음" in block   # 이전엔 프롬프트에 전달조차 안 됐다


def test_rating_block_handles_stock_with_no_factors():
    ratings = [{
        "emoji": "🟡", "name": "무신호주", "grade": "보통",
        "total_score": 50.0, "risk_score": 40.0, "data_confidence": 100.0,
        "positive_factors": [], "negative_factors": [], "check_required": [],
    }]
    block = _format_rating_block(ratings)
    assert "신호 균형: 상승 0 / 하락 0 / 확인필요 0" in block
    assert "없음" in block


def test_technical_block_omits_scale_dependent_macd_magnitude():
    """기술적 상세 블록도 MACD 절대값을 노출하면 안 된다.

    실측: 삼성전자 +1,411 / SK하이닉스 +29,518은 주가 스케일 차이일 뿐인데,
    리포트가 "+29518.45(↑강한 개선)"처럼 크기를 모멘텀 강도로 해석했다.
    """
    from app.reports.report_builder import _format_technical_block
    block = _format_technical_block({
        "KR_000660": {
            "name": "SK하이닉스", "price": 1_618_000, "currency": "KRW",
            "technical": {"macd_histogram": 29518.45, "rsi_14": 62},
        },
    })
    assert "MACD히스토그램" in block
    assert "29518" not in block and "29,518" not in block
    assert "양(개선방향)" in block
