"""
국내 종목 최근 일봉을 KIS 확정치로 교체 — 수집 경로와 차트 경로

실측(2026-10-17, 10/16 종가를 KIS 실전 30거래일과 대조):
  삼성전자   KIS 217,000   vs yfinance 215,500  (-0.69%)
  SK하이닉스 KIS 1,265,000 vs yfinance 1,272,000 (+0.55%)
  LG전자     KIS 170,700   vs yfinance 169,700  (-0.59%)
  LS ELECTRIC KIS 159,000  vs yfinance 158,600  (-0.25%)
그 이전 29일과 고가·저가는 모두 일치했다. yfinance가 최근 종가를 장 마감
동시호가 전 체결가로 잡아 두고 나중에 고치는 패턴이다.

대상일 한 봉만 KIS로 바꾸던 방식에서는 (1) 대상일 직전 봉이 아직 고쳐지기 전
값이면 이동평균·RSI·전일 대비가 그 값을 썼고, (2) 차트는 통째로 yfinance라
본문 종가와 차트 마지막 봉이 달랐다.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

pd = pytest.importorskip("pandas")

from app.collectors.kis_collector import KISCollector  # noqa: E402
from app.collectors.price_collector import PriceCollector, overlay_kis_bars  # noqa: E402


def _bar(date, close, change_pct=0.0, volume=1_000_000):
    return {"date": date, "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": volume, "change_pct": change_pct}


def _hist(dates, closes, tz="Asia/Seoul"):
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    if tz:
        idx = idx.tz_localize(tz)
    return pd.DataFrame(
        {"Open": closes, "High": [c * 1.01 for c in closes], "Low": [c * 0.99 for c in closes],
         "Close": closes, "Volume": [1_000_000] * len(closes)},
        index=idx,
    )


# ── 덮어쓰기 ─────────────────────────────────────────────────────────────────

def test_overlay_replaces_matching_dates_and_keeps_older_ones():
    h = _hist(["2026-10-14", "2026-10-15", "2026-10-16"], [205_000.0, 210_000.0, 215_500.0])
    out = overlay_kis_bars(h, [_bar("2026-10-15", 210_000.0), _bar("2026-10-16", 217_000.0)])
    assert list(out["Close"]) == [205_000.0, 210_000.0, 217_000.0]
    assert len(out) == 3


def test_overlay_adds_dates_yfinance_does_not_have_yet():
    h = _hist(["2026-10-14", "2026-10-15"], [205_000.0, 210_000.0])
    out = overlay_kis_bars(h, [_bar("2026-10-16", 217_000.0)])
    assert out["Close"].iloc[-1] == 217_000.0 and len(out) == 3


def test_overlay_keeps_the_exchange_timezone_so_sorting_works():
    """yfinance 인덱스에는 시간대가 붙어 있다. 시간대 없는 날짜를 섞으면
    정렬이 TypeError로 실패한다."""
    h = _hist(["2026-10-15", "2026-10-16"], [210_000.0, 215_500.0])
    out = overlay_kis_bars(h, [_bar("2026-10-14", 205_000.0), _bar("2026-10-16", 217_000.0)])
    assert str(out.index.tz) == "Asia/Seoul"
    assert out.index.is_monotonic_increasing


# ── 수집 경로 ────────────────────────────────────────────────────────────────

def _stub_yf(hist, seen_kwargs):
    class _T:
        def __init__(self, sym): pass
        def history(self, **kw):
            seen_kwargs.append(kw)
            return hist
        @property
        def fast_info(self):
            return types.SimpleNamespace(year_high=1.0, year_low=1.0, market_cap=1.0)
        def get_analyst_price_targets(self, *a, **k): raise RuntimeError("미사용")
    m = types.ModuleType("yfinance"); m.Ticker = _T
    return m


def _collect(hist, bars, sid="KR_005930", target="2026-10-16"):
    seen: list[dict] = []
    with patch.dict(sys.modules, {"yfinance": _stub_yf(hist, seen)}), \
         patch("app.collectors.price_collector._kis_collector.is_configured", return_value=True), \
         patch("app.collectors.price_collector._kis_collector.fetch_daily_bars", return_value=bars), \
         patch("app.collectors.price_collector._kis_collector.fetch_investor_flow",
               side_effect=RuntimeError("미사용")), \
         patch("app.collectors.price_collector._fetch_investor_flow", side_effect=RuntimeError("미사용")), \
         patch("app.collectors.price_collector.market_session_state", return_value="마감"):
        c = PriceCollector(); c.use_mock = False
        row = c.collect([sid], target={"KR": target, "US": target})[sid]
    return row, seen


def test_bars_before_the_target_are_corrected_too():
    """대상일 직전 봉이 yfinance의 고쳐지기 전 값이면, 한 봉만 바꾸는 방식에서는
    전일 대비·이동평균이 그 값을 썼다."""
    closes = [200_000.0] * 20 + [205_000.0, 999_999.0, 215_500.0]     # 10/15가 틀린 값
    dates = pd.bdate_range(end="2026-10-16", periods=len(closes)).strftime("%Y-%m-%d").tolist()
    bars = [_bar("2026-10-15", 210_000.0, 2.44), _bar("2026-10-16", 217_000.0, 3.33)]
    row, _ = _collect(_hist(dates, closes), bars)
    assert row["price"] == 217_000.0
    assert row["prev_close"] == 210_000.0
    assert row["change_pct"] == 3.33
    assert row["technical"]["ma5"] < 300_000          # 999,999가 섞이면 크게 튄다
    assert row["price_source"] == "kis"


def test_kis_without_the_target_bar_still_corrects_older_bars():
    """KIS에 대상일 봉이 아직 없으면 대상일은 yfinance 값을 쓰되, 그 앞의
    봉은 KIS 확정치로 바뀌어 있어야 한다."""
    dates = ["2026-10-14", "2026-10-15", "2026-10-16"]
    row, _ = _collect(_hist(dates, [205_000.0, 999_999.0, 215_500.0]),
                      [_bar("2026-10-14", 205_000.0), _bar("2026-10-15", 210_000.0)])
    assert row["price"] == 215_500.0
    assert row["prev_close"] == 210_000.0
    assert row["price_source"] == "yfinance"


def test_korean_history_is_requested_without_dividend_adjustment():
    """KIS 일봉은 배당 미반영 수정주가다. yfinance도 같은 기준이어야 이음매에
    단차가 생기지 않는다."""
    _, seen = _collect(_hist(["2026-10-15", "2026-10-16"], [210_000.0, 215_500.0]),
                       [_bar("2026-10-16", 217_000.0)])
    assert seen and seen[0].get("auto_adjust") is False


def test_us_history_keeps_dividend_adjustment():
    seen: list[dict] = []
    h = _hist(["2026-10-15", "2026-10-16"], [100.0, 101.0], tz="America/New_York")
    with patch.dict(sys.modules, {"yfinance": _stub_yf(h, seen)}):
        c = PriceCollector(); c.use_mock = False
        c.collect(["US_NVDA"], target={"KR": "2026-10-16", "US": "2026-10-16"})
    assert seen[0].get("auto_adjust") is True


# ── KIS 일봉 파싱 ────────────────────────────────────────────────────────────

def _row(d, o, h, l, c, v="100", ctrt="0.00"):
    return {"stck_bsop_date": d, "stck_oprc": o, "stck_hgpr": h, "stck_lwpr": l,
            "stck_clpr": c, "acml_vol": v, "prdy_ctrt": ctrt}


def test_fetch_daily_bars_drops_sessions_after_the_target_and_placeholders(tmp_path, monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "k"); monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ENV", "real")
    import app.collectors.kis_collector as mod
    monkeypatch.setattr(mod, "_TOKEN_CACHE_FILE", tmp_path / "t.json")
    k = KISCollector()
    rows = [   # 최신순
        _row("20261019", "0", "0", "0", "0"),                               # 개장 전 자리표시자
        _row("20261016", "215000", "222500", "210500", "217000", ctrt="3.33"),
        _row("20261015", "209000", "211000", "207000", "210000"),
    ]
    resp = MagicMock(); resp.raise_for_status.return_value = None
    resp.json.return_value = {"rt_cd": "0", "output": rows}
    with patch.object(k, "get_token", return_value="t"), \
         patch("app.collectors.kis_collector.requests.get", return_value=resp):
        bars = k.fetch_daily_bars("005930", through_date="2026-10-16")
    assert [b["date"] for b in bars] == ["2026-10-15", "2026-10-16"]    # 오래된 것부터
    assert bars[-1]["close"] == 217_000.0 and bars[-1]["change_pct"] == 3.33

    with patch.object(k, "get_token", return_value="t"), \
         patch("app.collectors.kis_collector.requests.get", return_value=resp):
        bars = k.fetch_daily_bars("005930", through_date="2026-10-15")
    assert [b["date"] for b in bars] == ["2026-10-15"]                  # 대상일 이후는 버림


def test_fetch_daily_bars_drops_a_placeholder_row_on_the_target_date(tmp_path, monkeypatch):
    """개장 전에는 당일 자리에 0으로 채운 행이 먼저 생긴다. 대상일이 그날로
    잘못 넘어와도 이 행을 확정 일봉으로 받아들이면 안 된다."""
    monkeypatch.setenv("KIS_APP_KEY", "k"); monkeypatch.setenv("KIS_APP_SECRET", "s")
    import app.collectors.kis_collector as mod
    monkeypatch.setattr(mod, "_TOKEN_CACHE_FILE", tmp_path / "t.json")
    k = KISCollector()
    rows = [_row("20261019", "0", "0", "0", "217000"),
            _row("20261016", "215000", "222500", "210500", "217000")]
    resp = MagicMock(); resp.raise_for_status.return_value = None
    resp.json.return_value = {"rt_cd": "0", "output": rows}
    with patch.object(k, "get_token", return_value="t"),          patch("app.collectors.kis_collector.requests.get", return_value=resp):
        bars = k.fetch_daily_bars("005930", through_date="2026-10-19")
    assert [b["date"] for b in bars] == ["2026-10-16"]


# ── 차트 경로 ────────────────────────────────────────────────────────────────

def _chart_hist(sym_hist):
    seen: list[dict] = []
    return _stub_yf(sym_hist, seen), seen


def test_chart_last_candle_matches_the_report_close():
    from app.reports import chart_generator as cg
    dates = pd.bdate_range(end="2026-10-16", periods=30).strftime("%Y-%m-%d").tolist()
    stub, seen = _chart_hist(_hist(dates, [200_000.0] * 29 + [215_500.0]))
    with patch.dict(sys.modules, {"yfinance": stub}), \
         patch.object(cg._kis_collector, "is_configured", return_value=True), \
         patch.object(cg._kis_collector, "fetch_daily_bars",
                      return_value=[_bar("2026-10-16", 217_000.0)]):
        h = cg.fetch_chart_history("005930.KS", target_date="2026-10-16")
    assert h["Close"].iloc[-1] == 217_000.0
    assert seen[0].get("auto_adjust") is False


def test_us_chart_does_not_touch_kis():
    from app.reports import chart_generator as cg
    dates = pd.bdate_range(end="2026-10-16", periods=30).strftime("%Y-%m-%d").tolist()
    stub, seen = _chart_hist(_hist(dates, [100.0] * 30, tz="America/New_York"))
    with patch.dict(sys.modules, {"yfinance": stub}), \
         patch.object(cg._kis_collector, "fetch_daily_bars") as kis_call:
        cg.fetch_chart_history("NVDA", target_date="2026-10-16")
    kis_call.assert_not_called()
    assert seen[0].get("auto_adjust") is True
