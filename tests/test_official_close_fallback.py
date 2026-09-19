"""
공식 종가(info) 폴백 테스트 — 일봉 Close가 NaN일 때의 마지막 안전망

배경(실측 2026-09-19 09:37, 미국장 마감 4시간 반 뒤): 관심종목 미국 11종목
전부 yfinance 일봉에 9/18 행은 존재하는데 Close만 NaN이었다. 그 탓에 토요일
아침 브리핑이 "9월 17일(목) 기준"으로 나갔다 — 미국 금요일 종가를 처음 담아야
하는 회차인데 목적을 달성하지 못했고, SanDisk +10.99%, Coherent +7.22% 같은
큰 움직임이 통째로 빠졌다.

같은 시점 get_info()에는 확정 종가가 이미 들어 있었다. 다만 이 필드는 장중에는
실시간 현재가라, 그대로 믿으면 장중값을 종가로 둔갑시키는 사고(LG전자 건과 같은
성격)가 된다. 그래서 세 겹 검증을 모두 통과할 때만 채택한다.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

pd = pytest.importorskip("pandas")

from app.collectors.price_collector import (  # noqa: E402
    PriceCollector,
    _official_close_from_info,
)

# 2026-09-18 16:00 ET(= 20:00 UTC)의 epoch — 공식 종가 확정 시각
_CLOSE_TS = int(datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc).timestamp())


def _info(**over):
    base = {
        "marketState": "POSTPOST",
        "regularMarketPrice": 222.27,
        "regularMarketPreviousClose": 219.34,
        "regularMarketTime": _CLOSE_TS,
        "regularMarketOpen": 219.36,
        "regularMarketDayHigh": 222.73,
        "regularMarketDayLow": 218.04,
        "regularMarketVolume": 188_794_542,
        "exchangeTimezoneName": "America/New_York",
    }
    base.update(over)
    return base


class _Ticker:
    def __init__(self, info): self._info = info
    def get_info(self): return self._info


# ── 정상 채택 ────────────────────────────────────────────────────────────────

def test_adopts_official_close_with_full_ohlcv():
    bar = _official_close_from_info(_Ticker(_info()), "2026-09-18", prior_close=219.34)
    assert bar is not None
    assert bar["Close"] == 222.27
    assert bar["Open"] == 219.36
    assert bar["High"] == 222.73
    assert bar["Low"] == 218.04
    assert bar["Volume"] == 188_794_542


def test_ohlc_falls_back_to_close_when_absent():
    """시가·고가·저가가 없으면 종가로 메우되 값을 지어내지는 않는다."""
    info = _info()
    for k in ("regularMarketOpen", "regularMarketDayHigh", "regularMarketDayLow"):
        info.pop(k)
    bar = _official_close_from_info(_Ticker(info), "2026-09-18", prior_close=219.34)
    assert bar["Open"] == bar["High"] == bar["Low"] == 222.27


# ── 검증 ① 정규장 진행 중이면 거부 ──────────────────────────────────────────

@pytest.mark.parametrize("state", ["REGULAR", "PRE"])
def test_rejects_while_the_regular_session_is_live(state):
    """장중 regularMarketPrice는 실시간 현재가다. 확정 종가로 받아들이면
    LG전자 사고와 같은 성격의 오염이 된다."""
    assert _official_close_from_info(
        _Ticker(_info(marketState=state)), "2026-09-18", prior_close=219.34
    ) is None


# ── 검증 ② 가격이 가리키는 세션이 대상 거래일이어야 함 ──────────────────────

def test_rejects_when_the_price_belongs_to_another_session():
    """regularMarketTime을 거래소 현지 시각으로 환산해 대상일과 대조한다.
    세션 계산을 다시 하지 않고 데이터 자체에 물어보는 방식이다."""
    assert _official_close_from_info(
        _Ticker(_info()), "2026-09-17", prior_close=219.34
    ) is None


def test_uses_the_exchange_timezone_not_the_runner_timezone():
    """러너는 UTC다. 16:00 ET를 UTC로 읽으면 날짜가 하루 밀린다
    (20:00 UTC 9/18이라 이 경우는 같지만, 마감이 자정을 넘기는 거래소에서
    어긋난다). 거래소 타임존을 써야 한다."""
    bar = _official_close_from_info(
        _Ticker(_info(exchangeTimezoneName="America/New_York")),
        "2026-09-18", prior_close=219.34,
    )
    assert bar is not None


# ── 검증 ③ 직전 확정 종가와의 자기검증 ──────────────────────────────────────

def test_rejects_when_previous_close_does_not_match_settled_data():
    """필드 의미가 바뀌거나 다른 세션을 가리키면 여기서 걸린다.
    실측으로 11종목 전부 직전 확정 종가와 정확히 일치함을 확인했다."""
    assert _official_close_from_info(
        _Ticker(_info()), "2026-09-18", prior_close=200.00
    ) is None


def test_tolerates_rounding_level_differences():
    """소수점 반올림 수준(0.05% 미만)의 차이는 통과시킨다."""
    assert _official_close_from_info(
        _Ticker(_info()), "2026-09-18", prior_close=219.36
    ) is not None


def test_returns_none_when_info_call_fails():
    class _Broken:
        def get_info(self): raise RuntimeError("network error")
    assert _official_close_from_info(_Broken(), "2026-09-18", prior_close=219.34) is None


# ── 수집 경로 통합 ───────────────────────────────────────────────────────────

def _hist_nan_close(dates, closes):
    """마지막 행의 Close가 NaN — 실측된 미국 종목의 마감 직후 상태."""
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes,
         "Volume": [1_000_000] * len(closes)},
        index=pd.to_datetime(dates),
    )


def _stub_yf(hist, info):
    class _T:
        def __init__(self, sym): pass
        def history(self, **kw): return hist
        def get_info(self): return info
        @property
        def fast_info(self):
            return types.SimpleNamespace(year_high=1.0, year_low=1.0, market_cap=1.0)
        def get_analyst_price_targets(self, *a, **k): raise RuntimeError("미사용")
    m = types.ModuleType("yfinance"); m.Ticker = _T
    return m


def test_us_stock_with_nan_close_recovers_the_target_day():
    hist = _hist_nan_close(["2026-09-16", "2026-09-17", "2026-09-18"],
                           [213.90, 219.34, float("nan")])
    with patch.dict(sys.modules, {"yfinance": _stub_yf(hist, _info())}):
        c = PriceCollector(); c.use_mock = False
        row = c.collect(["US_NVDA"], target={"KR": "2026-09-18", "US": "2026-09-18"})["US_NVDA"]
    assert not row.get("missing")
    assert row["data_date"] == "2026-09-18"      # 9/17로 물러나지 않는다
    assert row["price"] == 222.27
    assert row["change_pct"] == 1.34


def test_falls_back_to_honest_stale_when_info_is_not_trustworthy():
    """세 겹 검증 중 하나라도 실패하면 억지로 쓰지 않고, 기존의 정직한
    지연 처리로 물러난다 — 실제 날짜를 그대로 남긴다."""
    hist = _hist_nan_close(["2026-09-16", "2026-09-17", "2026-09-18"],
                           [213.90, 219.34, float("nan")])
    live = _info(marketState="REGULAR")   # 장중 — 검증 ①에서 거부
    with patch.dict(sys.modules, {"yfinance": _stub_yf(hist, live)}):
        c = PriceCollector(); c.use_mock = False
        row = c.collect(["US_NVDA"], target={"KR": "2026-09-18", "US": "2026-09-18"})["US_NVDA"]
    assert not row.get("missing")
    assert row["data_date"] == "2026-09-17"      # 정직하게 실제 날짜
    assert row["price"] == 219.34


def test_candle_pattern_is_not_degenerate_after_info_recovery():
    """info 경로로 복구한 봉도 시가·고가·저가를 갖춰야 한다. 종가로만
    메우면 캔들 판정이 매번 도지(십자형)로 잘못 나온다(KIS 경로에서
    실제로 겪은 결함)."""
    hist = _hist_nan_close(["2026-09-16", "2026-09-17", "2026-09-18"],
                           [213.90, 219.34, float("nan")])
    with patch.dict(sys.modules, {"yfinance": _stub_yf(hist, _info())}):
        c = PriceCollector(); c.use_mock = False
        row = c.collect(["US_NVDA"], target={"KR": "2026-09-18", "US": "2026-09-18"})["US_NVDA"]
    candle = row.get("candle_pattern") or {}
    assert candle.get("body_ratio", 0) > 0
