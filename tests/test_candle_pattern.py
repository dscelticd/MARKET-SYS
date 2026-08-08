"""
당일 캔들 패턴 자동 판별 테스트 (장대양봉/장대음봉/도지/망치형/유성형/일반형)
"""
from __future__ import annotations

from app.collectors.price_collector import _classify_candle, _mock_candle_ohlc


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
