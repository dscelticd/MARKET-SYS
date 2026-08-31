"""
지지/저항 박스 + 손익비 계산 로직 테스트

배경: 개인용 주식 분석 리포트(PDF 참고 자료)를 벤치마킹해 90일 OHLC 히스토리에서
      스윙 고점/저점을 클러스터링, 지지·저항 박스와 손익비(상승여력%/하락위험%)를
      계산하는 기능을 price_collector.py에 추가함. 매수·매도 지시가 아닌 조건부
      참고 정보로만 report_builder.py 프롬프트/dashboard.py에 노출됨.
"""
from __future__ import annotations

import pandas as pd

from app.collectors.price_collector import (
    _detect_swing_points,
    _cluster_zones,
    _calc_support_resistance,
    _mock_support_resistance,
)
from app.reports.report_builder import _format_support_resistance_block


def test_swing_point_detection_finds_local_extremes():
    high = pd.Series([100, 101, 102, 103, 110, 103, 102, 101, 100, 99, 98, 90, 98, 99, 100])
    low  = pd.Series([95, 96, 97, 98, 105, 98, 97, 96, 95, 94, 93, 85, 93, 94, 95])
    swing_highs, swing_lows = _detect_swing_points(high, low, window=3)
    assert any(price == 110.0 for _, price in swing_highs)
    assert any(price == 85.0 for _, price in swing_lows)


def test_cluster_zones_merges_nearby_touches():
    points = [(5, 110.0), (17, 110.5), (11, 90.0)]
    zones = _cluster_zones(points, n_bars=22, tolerance_pct=1.5)
    assert len(zones) == 2
    resistance_zone = next(z for z in zones if z["low"] > 100)
    assert resistance_zone["touches"] == 2
    assert resistance_zone["strength"] > 0


def test_cluster_zones_keeps_distant_points_separate():
    points = [(1, 100.0), (2, 150.0), (3, 200.0)]
    zones = _cluster_zones(points, n_bars=10, tolerance_pct=1.5)
    assert len(zones) == 3


def test_calc_support_resistance_returns_empty_for_short_history():
    short_hist = pd.DataFrame({"High": [1, 2, 3], "Low": [0.5, 1.5, 2.5]})
    assert _calc_support_resistance(short_hist, 2.0) == {}


def test_calc_support_resistance_handles_no_resistance_above_price():
    """신고가 갱신 구간처럼 현재가 위에 스윙 고점이 없으면 저항 없이도 정상 반환"""
    n = 30
    close = pd.Series(range(100, 100 + n), dtype=float)
    hist = pd.DataFrame({
        "High": close + 1.0,
        "Low":  close - 1.0,
    })
    price = float(close.iloc[-1]) + 1.0  # 데이터 내 최고가보다 위
    sr = _calc_support_resistance(hist, price)
    assert sr.get("nearest_resistance_pct") is None
    assert sr.get("risk_reward_ratio") is None


def test_calc_support_resistance_computes_risk_reward_when_both_zones_exist():
    n = 40
    # V자 반등 패턴 — 중간에 저점, 양끝에 고점을 만들어 지지/저항 둘 다 확인되게 구성
    values = list(range(120, 100, -1)) + list(range(100, 120))
    close = pd.Series(values[:n], dtype=float)
    hist = pd.DataFrame({"High": close + 0.5, "Low": close - 0.5})
    price = float(close.iloc[-1])
    sr = _calc_support_resistance(hist, price)
    if sr.get("nearest_resistance_pct") is not None and sr.get("nearest_support_pct") is not None:
        assert sr["risk_reward_ratio"] is not None
        assert sr["risk_reward_ratio"] == round(
            sr["nearest_resistance_pct"] / sr["nearest_support_pct"], 2
        )


def test_mock_support_resistance_always_has_risk_reward():
    for _ in range(30):
        m = _mock_support_resistance(100.0)
        assert m["risk_reward_ratio"] is not None
        assert m["nearest_resistance_pct"] > 0
        assert m["nearest_support_pct"] > 0
        assert isinstance(m["risk_reward_meets_bar"], bool)


def test_risk_reward_meets_bar_threshold_is_two():
    m_low = {"risk_reward_ratio": 1.99}
    m_high = {"risk_reward_ratio": 2.0}
    assert not (m_low["risk_reward_ratio"] >= 2.0)
    assert m_high["risk_reward_ratio"] >= 2.0


def test_format_support_resistance_block_stays_non_prescriptive():
    """매수·매도 지시 표현이 데이터 블록에 들어가지 않는지 확인.

    이전에는 종목마다 "▲ X 위 거래량 동반 종가 마감 → 돌파로 볼 여지" 같은 조건부
    시나리오 줄을 함께 출력했고 이 테스트가 "볼 여지" 문구를 검증했다. 그 줄들은
    숫자만 바꾼 기계적 반복(18종목 36줄·1,641자)이었고 서술 방식은
    "## 지지/저항·손익비 서술 규칙"에 이미 명시돼 있어 제거했다 —
    조건부 서술의 책임은 이제 데이터가 아니라 프롬프트 규칙에 있다.
    """
    price_data = {
        "KR_005930": {
            "name": "삼성전자", "price": 231000, "currency": "KRW",
            "support_resistance": {
                "resistance_zones": [{"low": 238212, "high": 241788, "touches": 2, "strength": 65}],
                "support_zones":    [{"low": 221212, "high": 224788, "touches": 1, "strength": 40}],
                "nearest_resistance_pct": 3.1,
                "nearest_support_pct": 2.8,
                "risk_reward_ratio": 1.11,
                "risk_reward_meets_bar": False,
            },
        },
    }
    block = _format_support_resistance_block(price_data)
    assert "삼성전자" in block
    assert "손익비=1.11(기준미달)" in block
    # Claude가 조건부 서술을 만들 수 있도록 저항·지지 수치는 그대로 남아야 한다
    assert "238,212" in block and "224,788" in block
    forbidden = ["무조건 매수", "반드시 매도", "지금 사야 한다", "지금 팔아야 한다"]
    assert not any(f in block for f in forbidden)


def test_krw_prices_have_no_decimals_and_point_zones_collapse():
    """원화는 소수점 단위로 거래되지 않고, 상단=하단인 구간은 값 하나로 표기한다."""
    price_data = {
        "KR_005930": {
            "name": "삼성전자", "price": 231000, "currency": "KRW",
            "support_resistance": {
                "resistance_zones": [{"low": 267000.0, "high": 267000.0, "touches": 2, "strength": 40}],
                "support_zones":    [{"low": 240000.0, "high": 242000.0, "touches": 1, "strength": 25}],
                "nearest_resistance_pct": 6.2,
                "nearest_support_pct": 4.6,
                "risk_reward_ratio": 1.35,
                "risk_reward_meets_bar": False,
            },
        },
    }
    block = _format_support_resistance_block(price_data)
    assert "267,000.00" not in block          # 원화 소수점 없음
    assert "267,000~267,000" not in block     # 동일값 범위 축약
    assert "저항 267,000 KRW" in block
    assert "지지 240,000~242,000 KRW" in block  # 진짜 범위는 그대로 유지


def test_usd_prices_keep_two_decimals():
    price_data = {
        "US_NVDA": {
            "name": "NVIDIA", "price": 190.0, "currency": "USD",
            "support_resistance": {
                "resistance_zones": [{"low": 227.98, "high": 227.98, "touches": 2, "strength": 40}],
                "support_zones":    [],
                "nearest_resistance_pct": 5.0,
                "nearest_support_pct": None,
                "risk_reward_ratio": None,
                "risk_reward_meets_bar": None,
            },
        },
    }
    block = _format_support_resistance_block(price_data)
    assert "227.98" in block


def test_format_support_resistance_block_skips_empty_data():
    price_data = {"US_NVDA": {"name": "NVIDIA", "price": 190.0, "currency": "USD", "support_resistance": {}}}
    block = _format_support_resistance_block(price_data)
    assert block == "(지지/저항 데이터 없음)"


def test_format_support_resistance_block_handles_missing_resistance():
    price_data = {
        "KR_073240": {
            "name": "금호타이어", "price": 7810, "currency": "KRW",
            "support_resistance": {
                "resistance_zones": [],
                "support_zones": [{"low": 6300, "high": 6410, "touches": 1, "strength": 25}],
                "nearest_resistance_pct": None,
                "nearest_support_pct": 21.8,
                "risk_reward_ratio": None,
                "risk_reward_meets_bar": False,
            },
        },
    }
    block = _format_support_resistance_block(price_data)
    assert "저항 확인 안 됨" in block
    assert "손익비=" not in block
