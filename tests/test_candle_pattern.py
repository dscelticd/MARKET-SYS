"""
당일 캔들 패턴 자동 판별 테스트 (장대양봉/장대음봉/도지/망치형/유성형/일반형)
"""
from __future__ import annotations

from app.collectors.price_collector import _classify_candle, _mock_candle_ohlc
from app.reports.report_builder import _format_technical_block


def test_long_bullish_candle():
    result = _classify_candle(100, 109, 99, 108)
    assert result["pattern"] == "장대양봉"
    assert result["direction"] == "양봉"


def test_long_bearish_candle():
    result = _classify_candle(108, 109, 99, 100)
    assert result["pattern"] == "장대음봉"
    assert result["direction"] == "음봉"


def test_doji_small_body():
    result = _classify_candle(100, 110, 95, 100.5)
    assert result["pattern"] == "도지"


def test_hammer_long_lower_shadow():
    result = _classify_candle(100, 102.5, 94, 102)
    assert result["pattern"] == "망치형"


def test_shooting_star_long_upper_shadow():
    result = _classify_candle(100, 115, 99.5, 103)
    assert result["pattern"] == "유성형"


def test_normal_candle_falls_through_to_default():
    result = _classify_candle(100, 106, 97, 103)
    assert result["pattern"] == "일반형"


def test_zero_range_returns_flat():
    result = _classify_candle(100, 100, 100, 100)
    assert result["pattern"] == "보합"


def test_mock_candle_ohlc_is_internally_consistent():
    open_, high, low, close = _mock_candle_ohlc(price=105.0, prev_close=100.0)
    assert high >= max(open_, close)
    assert low <= min(open_, close)
    assert close == 105.0
    assert open_ == 100.0


def test_candle_pattern_reaches_report_prompt():
    """price_collector가 계산한 candle_pattern이 report_builder 프롬프트 블록에
    실제로 노출되는지 확인 — 계산만 하고 프롬프트에 연결하지 않는 회귀 방지"""
    price_data = {
        "KR_005930": {
            "name": "삼성전자",
            "price": 250000,
            "technical": {"rsi_14": 55},
            "candle_pattern": {"pattern": "장대양봉", "direction": "양봉", "body_ratio": 0.75},
        }
    }
    block = _format_technical_block(price_data)
    assert "당일캔들=장대양봉(양봉)" in block
