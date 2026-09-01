"""
대상 거래일 봉 절단 테스트 — 계약 C2

배경: 수집기가 시계열의 마지막 봉을 그대로 종가로 썼다(close_s.iloc[-1]).
      장이 열려 있으면 그 봉은 아직 끝나지 않은 세션의 미완성 봉이다.

      실측 사고 두 건:
        - 2026-09-01 11:48 발송 아침 브리핑 — 한국장 개장 중이라 9/1 장중가를
          담고 "9월 1일 종가 기준"이라 표기
        - 2026-09-02 00:36 발송 저녁 결산 — 미국 9/1 세션(22:30~05:00) 진행
          중이라 장중 수치를 "3대 지수 모두 하락 마감"이라 서술

      절단은 히스토리 단계에서 한 번만 한다. 그래야 종가·등락률·거래량·기술적
      지표·지지저항·캔들 패턴이 전부 같은 기준일 위에서 계산된다.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import patch

import pytest

pd = pytest.importorskip("pandas")

from app.collectors.price_collector import (  # noqa: E402
    PriceCollector,
    _market_of,
    _truncate_to_target,
)


def _hist(dates: list[str], closes: list[float]) -> "pd.DataFrame":
    idx = pd.to_datetime(dates)
    return pd.DataFrame(
        {
            "Open":   closes,
            "High":   [c * 1.01 for c in closes],
            "Low":    [c * 0.99 for c in closes],
            "Close":  closes,
            "Volume": [1_000_000] * len(closes),
        },
        index=idx,
    )


# ── 시장 판정 ────────────────────────────────────────────────────────────────

def test_market_of_splits_kr_and_us():
    assert _market_of("KR_005930") == "KR"
    assert _market_of("US_NVDA") == "US"


# ── 절단 ─────────────────────────────────────────────────────────────────────

def test_truncation_drops_bars_after_target():
    h = _truncate_to_target(_hist(["2026-08-31", "2026-09-01", "2026-09-02"], [100, 110, 120]),
                            "2026-09-01")
    assert len(h) == 2
    assert float(h["Close"].iloc[-1]) == 110


def test_truncation_keeps_everything_when_target_is_latest():
    h = _truncate_to_target(_hist(["2026-08-31", "2026-09-01"], [100, 110]), "2026-09-01")
    assert len(h) == 2


def test_truncation_leaves_nothing_when_target_precedes_all_bars():
    """대상일 데이터가 아예 없는 상태. 조용히 다른 날 값을 쓰지 않고 비어야 한다."""
    h = _truncate_to_target(_hist(["2026-09-01", "2026-09-02"], [110, 120]), "2026-08-28")
    assert len(h) == 0


# ── 수집 경로 통합 ───────────────────────────────────────────────────────────

def _stub_yfinance(hist):
    class _FastInfo:
        year_high = 200.0
        year_low = 50.0
        market_cap = 1e11

    class _Ticker:
        def __init__(self, sym): self.sym = sym
        def history(self, **kw): return hist
        @property
        def fast_info(self): return _FastInfo()
        def get_analyst_price_targets(self, *a, **k): raise RuntimeError("미사용")

    mod = types.ModuleType("yfinance")
    mod.Ticker = _Ticker
    return mod


def _collect_one(hist, target):
    with patch.dict(sys.modules, {"yfinance": _stub_yfinance(hist)}):
        c = PriceCollector()
        c.use_mock = False
        return c.collect(["KR_005930"], target=target)["KR_005930"]


def test_in_progress_bar_is_not_used_as_close():
    """9/2 장중 봉이 있어도 대상일이 9/1이면 9/1 종가를 써야 한다.
    이것이 "장중가가 종가로 둔갑"하던 사고를 직접 막는 검사다."""
    hist = _hist(["2026-08-28", "2026-08-31", "2026-09-01", "2026-09-02"],
                 [100.0, 200.0, 110.0, 999.0])   # 999 = 진행 중인 봉
    row = _collect_one(hist, {"KR": "2026-09-01", "US": "2026-09-01"})
    assert row["price"] == 110.0
    assert row["data_date"] == "2026-09-01"
    assert row["prev_close"] == 200.0            # 등락률 비교 대상도 함께 밀린다


def test_change_pct_is_computed_against_the_prior_completed_session():
    hist = _hist(["2026-08-31", "2026-09-01", "2026-09-02"], [100.0, 110.0, 999.0])
    row = _collect_one(hist, {"KR": "2026-09-01", "US": "2026-09-01"})
    assert row["change_pct"] == 10.0             # 999 봉이 섞이면 이 값이 달라진다


def test_technicals_exclude_the_in_progress_bar():
    """절단을 히스토리에서 하지 않고 종가만 골라내면 RSI·MA·MACD에 미완성
    봉이 남는다. 지표가 조용히 오염되는 경로를 막는다."""
    flat = [100.0] * 30
    with_spike = _hist([f"2026-07-{d:02d}" for d in range(1, 29)] + ["2026-08-03", "2026-08-04"],
                       flat)
    with_spike.iloc[-1, with_spike.columns.get_loc("Close")] = 500.0
    row = _collect_one(with_spike, {"KR": "2026-08-03", "US": "2026-08-03"})
    assert row["technical"]["ma5"] == 100.0


def test_no_target_keeps_legacy_behaviour():
    """대상일을 넘기지 않으면(대시보드 등 호출부) 기존처럼 최신 봉을 쓴다."""
    hist = _hist(["2026-09-01", "2026-09-02"], [110.0, 120.0])
    row = _collect_one(hist, None)
    assert row["price"] == 120.0
