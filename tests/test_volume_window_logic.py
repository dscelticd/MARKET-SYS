"""
2026-11-27 정리 — 저녁 거래량 출처·지표 창 90봉·로직 버전·지연 종목 가격 날짜

실측 배경
  - 저녁 결산의 한국 거래량은 yfinance 잠정치(시간외 거래 포함)였다. 2026-10~11 저녁
    157회 중 153회가 다음 날 아침 확정치보다 컸다(중앙값 +6%). 5일 평균은 확정치라
    거래량 비율이 부풀었고, 거래량 신호가 저녁·아침 사이 10%에서 달랐다.
    KIS 확정 거래량 = yfinance 최종값(3종목 × 6거래일 일치), KIS 가장 최근 행의 거래량은
    마감 뒤 그대로(10/17 토 → 10/19 월: 종가만 바뀜).
  - yfinance period="90d"는 달력 90일이 아니라 최근 90봉이다. 과거 재구성을 달력 90일로
    했을 때 추석 뒤 59봉이라 60일선이 빠져 한국 기술적 신호 40%가 어긋났다.
  - 9월 이력은 지금 로직과 65~88%만 일치했는데 어느 로직인지 기록이 없었다.
  - SanDisk가 세 번 연속 11/19 종가로 '지연'됐는데 이력에는 그 값이 11/24·11/25
    가격처럼 저장됐고, 공개 기록에는 어느 종목인지도 남지 않았다.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

pd = pytest.importorskip("pandas")

from app.collectors import price_collector as pc  # noqa: E402
from app.collectors.kis_collector import KISCollector  # noqa: E402
from app.engine import logic_version  # noqa: E402
from app.engine.history_tracker import HistoryTracker, volume_revisions  # noqa: E402

KST = ZoneInfo("Asia/Seoul")


def _hist(n, end="2026-11-26", vol=1_000_000, tz="Asia/Seoul"):
    idx = pd.bdate_range(end=end, periods=n).tz_localize(tz)
    closes = [100_000.0 + i * 100 for i in range(n)]
    return pd.DataFrame({"Open": closes, "High": [c * 1.01 for c in closes],
                         "Low": [c * 0.99 for c in closes], "Close": closes,
                         "Volume": [vol] * n}, index=idx)


def _stub_yf(hist, seen):
    class _T:
        def __init__(self, sym): pass
        def history(self, **kw):
            seen.append(kw)
            return hist
        @property
        def fast_info(self):
            return types.SimpleNamespace(year_high=1.0, year_low=1.0, market_cap=1.0)
        def get_analyst_price_targets(self, *a, **k): raise RuntimeError("미사용")
    m = types.ModuleType("yfinance"); m.Ticker = _T
    return m


def _collect(hist, *, now, bars=None, same_day=None, target="2026-11-26", sid="KR_005930"):
    seen: list[dict] = []
    bars = bars or [{"date": "2026-11-25", "open": 1.0, "high": 1.0, "low": 1.0,
                     "close": float(hist["Close"].iloc[-2]), "volume": 1_000_000, "change_pct": 0.0}]
    with patch.dict(sys.modules, {"yfinance": _stub_yf(hist, seen)}), \
         patch.object(pc, "now_kst", return_value=now), \
         patch.object(pc, "market_session_state", return_value="마감"), \
         patch.object(pc._kis_collector, "is_configured", return_value=True), \
         patch.object(pc._kis_collector, "fetch_daily_bars", return_value=bars), \
         patch.object(pc._kis_collector, "same_day_volume", return_value=same_day), \
         patch.object(pc._kis_collector, "fetch_investor_flow", side_effect=RuntimeError("미사용")), \
         patch.object(pc, "_fetch_investor_flow", side_effect=RuntimeError("미사용")):
        c = pc.PriceCollector(); c.use_mock = False
        row = c.collect([sid], target={"KR": target, "US": target})[sid]
    return row, seen


# ── 지표 창 ──────────────────────────────────────────────────────────────────

def test_last_bars_skips_rows_without_a_close():
    h = _hist(5)
    h.iloc[2, h.columns.get_loc("Close")] = float("nan")
    out = pc._last_bars(h, 3)
    assert len(out) == 3 and out["Close"].notna().all()
    assert out.index[0] == h.index[1]          # 비어 있는 셋째 봉을 건너뛰고 셋을 채운다


def test_indicators_use_exactly_the_last_90_bars_from_a_longer_download():
    seen_len = []
    real = pc._calc_technicals
    def spy(closes, price):
        seen_len.append(len(closes))
        return real(closes, price)
    with patch.object(pc, "_calc_technicals", side_effect=spy):
        row, seen = _collect(_hist(130), now=datetime(2026, 11, 27, 7, 10, tzinfo=KST))
    assert seen[0]["period"] == "6mo"          # yfinance '90d' 해석에 기대지 않는다
    assert seen_len == [pc.HISTORY_BARS] == [90]
    assert row["technical"]["ma60"] is not None


def test_split_detection_still_reads_the_recent_window_only():
    h = _hist(130)
    h["Stock Splits"] = 0.0
    h.iloc[5, h.columns.get_loc("Stock Splits")] = 2.0      # 90봉보다 오래된 분할
    h.iloc[-3, h.columns.get_loc("Stock Splits")] = 8.0     # 최근 분할
    row, _ = _collect(h, now=datetime(2026, 11, 27, 7, 10, tzinfo=KST))
    assert [s["ratio"] for s in row["splits"]] == [8.0]


# ── 저녁 거래량 ───────────────────────────────────────────────────────────────

def test_evening_volume_comes_from_the_kis_same_day_row_after_18h():
    h = _hist(100, vol=18_410_184)
    kis_prev = [{"date": "2026-11-25", "open": 1.0, "high": 1.0, "low": 1.0,
                 "close": float(h["Close"].iloc[-2]), "volume": 18_410_184, "change_pct": 0.0}]
    row, _ = _collect(h, bars=kis_prev, same_day=17_079_045,
                      now=datetime(2026, 11, 26, 20, 30, tzinfo=KST))
    assert row["volume"] == 17_079_045
    assert row["volume_source"] == "kis" and row["volume_provisional"] is False
    assert row["volume_ratio"] == round(17_079_045 / 18_410_184, 2)   # 5일 평균은 같은 정의


def test_before_after_hours_end_the_yfinance_volume_is_marked_provisional():
    row, _ = _collect(_hist(100, vol=18_410_184), same_day=17_079_045,
                      now=datetime(2026, 11, 26, 16, 30, tzinfo=KST))
    assert row["volume"] == 18_410_184
    assert row["volume_source"] == "yfinance" and row["volume_provisional"] is True


def test_morning_volume_is_already_final_and_not_provisional():
    row, _ = _collect(_hist(100), same_day=None, now=datetime(2026, 11, 27, 7, 10, tzinfo=KST))
    assert row["volume_source"] == "yfinance" and row["volume_provisional"] is False


def test_kis_same_day_volume_only_for_the_matching_latest_row():
    k = KISCollector()
    rows = [{"stck_bsop_date": "20261126", "acml_vol": "17079045", "stck_oprc": "1",
             "stck_hgpr": "1", "stck_lwpr": "1", "stck_clpr": "1"},
            {"stck_bsop_date": "20261125", "acml_vol": "9", "stck_oprc": "1",
             "stck_hgpr": "1", "stck_lwpr": "1", "stck_clpr": "1"}]
    with patch.object(k, "_daily_price_rows", return_value=rows):
        k.fetch_daily_bars("005930", through_date="2026-11-26")
    assert k.same_day_volume("005930", "2026-11-26") == 17_079_045
    assert k.same_day_volume("005930", "2026-11-25") is None     # 가장 최근 행이 아니다
    assert k.same_day_volume("000660", "2026-11-26") is None     # 받은 적 없는 종목
    rows[0]["acml_vol"] = "0"
    with patch.object(k, "_daily_price_rows", return_value=rows):
        k.fetch_daily_bars("005930", through_date="2026-11-26")
    assert k.same_day_volume("005930", "2026-11-26") is None


def test_report_marks_provisional_volume():
    from app.reports.report_builder import _format_price_block
    p = {"name": "삼성전자", "ticker": "005930", "price": 1.0, "currency": "KRW",
         "change_pct": 0.0, "volume_ratio": 1.5, "volume_provisional": True}
    assert "잠정" in _format_price_block({"KR_005930": p})
    p["volume_provisional"] = False
    assert "잠정" not in _format_price_block({"KR_005930": p})


# ── 이력: 로직 버전·종목별 가격 날짜·거래량 재확인 ────────────────────────────

def _tracker(entries):
    t = HistoryTracker.__new__(HistoryTracker)
    t._data = entries
    return t


def test_history_entry_records_logic_version_and_price_dates():
    t = _tracker({})
    rating = {"stock_id": "US_SNDK", "name": "SanDisk", "grade": "보통", "raw_grade": "보통",
              "total_score": 50.0, "risk_score": 40.0, "data_confidence": 100.0}
    with patch.object(HistoryTracker, "_persist"):
        t.save_today([rating], "morning",
                     price_data={"US_SNDK": {"price": 94.39, "change_pct": -2.7, "volume": 1,
                                             "data_date": "2026-11-19"}},
                     session_date="2026-11-26", us_session_date="2026-11-25")
    e = t._data["2026-11-26_morning"]
    assert e["logic"] == {"tag": logic_version.LOGIC_TAG, "fingerprint": logic_version.fingerprint()}
    assert e["price_dates"] == {"US_SNDK": "2026-11-19"}


def test_volume_revisions_flags_an_evening_value_that_changed_by_morning():
    ev = {"kr_date": "2026-11-26", "volumes": {"KR_005930": 18_410_184, "KR_000660": 3_611_127},
          "price_dates": {"KR_005930": "2026-11-26", "KR_000660": "2026-11-26"}}
    now = {"KR_005930": {"data_date": "2026-11-26", "volume": 17_079_045},
           "KR_000660": {"data_date": "2026-11-26", "volume": 3_611_127},
           "US_NVDA": {"data_date": "2026-11-25", "volume": 5}}
    out = volume_revisions(ev, now, "2026-11-26")
    assert out["checked"] == 2
    assert out["revised"] == [{"sid": "KR_005930", "evening": 18_410_184, "confirmed": 17_079_045}]
    assert volume_revisions(None, now, "2026-11-26") == {"checked": 0, "revised": []}


def test_volume_revisions_skips_a_stock_that_was_stale_in_the_evening():
    ev = {"kr_date": "2026-11-26", "volumes": {"KR_005930": 10},
          "price_dates": {"KR_005930": "2026-11-25"}}
    out = volume_revisions(ev, {"KR_005930": {"data_date": "2026-11-26", "volume": 99}}, "2026-11-26")
    assert out == {"checked": 0, "revised": []}


# ── 정확도 점검: 지연된 가격은 그날 가격으로 쓰지 않는다 ─────────────────────

def _snap(date, us_date, prices, dates=None):
    s = {"date": date, "kr_date": date, "us_date": us_date, "data_date": us_date,
         "report_type": "morning", "is_trading_day": True, "closing_prices": prices}
    if dates is not None:
        s["price_dates"] = dates
    return s


def test_stale_price_is_excluded_from_return_pairs():
    from app.engine.accuracy_audit import _pairs, _price
    a = _snap("2026-11-24", "2026-11-24", {"US_SNDK": 94.39, "US_NVDA": 160.0},
              {"US_SNDK": "2026-11-19", "US_NVDA": "2026-11-24"})
    assert _price(a, "US_SNDK") is None and _price(a, "US_NVDA") == 160.0
    b = _snap("2026-11-25", "2026-11-25", {"US_SNDK": 73.61, "US_NVDA": 155.0},
              {"US_SNDK": "2026-11-25", "US_NVDA": "2026-11-25"})
    pairs = list(_pairs([a, b], 1))
    assert pairs and set(pairs[0][2]) == {"US_NVDA"}


def test_old_entries_without_price_dates_are_used_as_before():
    from app.engine.accuracy_audit import _price
    old = _snap("2026-11-24", "2026-11-24", {"US_SNDK": 94.39})
    assert _price(old, "US_SNDK") == 94.39


# ── 실행 요약: 지연 종목 이름·날짜 ─────────────────────────────────────────────

def test_quality_line_names_the_stale_stock_and_its_date():
    from app.main import summarize_run_quality
    pdata = {"KR_005930": {"data_date": "2026-11-26", "price_source": "kis"},
             "US_SNDK": {"data_date": "2026-11-19", "price_source": "stale"}}
    line, degraded = summarize_run_quality(pdata, {}, {"kr_date": "2026-11-26", "us_date": "2026-11-25"})
    assert "지연 1(US_SNDK 11-19)" in line and degraded


# ── 로직 버전 ────────────────────────────────────────────────────────────────

def test_fingerprint_ignores_comments_docstrings_and_blank_lines():
    a = 'def f(x):\n    """설명"""\n    return x + 1  # 더한다\n'
    b = 'def f(x):\n    """다른 설명\n    여러 줄"""\n\n    # 주석만 바뀜\n    return x + 1\n'
    c = 'def f(x):\n    return x + 2\n'
    s = '"#은 문자열 안에서 주석이 아니다"\nX = "a#b"  # 주석\n'
    assert logic_version.normalize_source(a) == logic_version.normalize_source(b)
    assert logic_version.normalize_source(a) != logic_version.normalize_source(c)
    assert logic_version.normalize_source(s) == 'X = "a#b"'


def test_fingerprint_covers_every_listed_function_and_is_stable():
    _, missing = logic_version.function_sources(
        logic_version._ROOT / logic_version.COLLECTOR, logic_version.COLLECTOR_FUNCS)
    assert missing == []          # 이름이 바뀌면 지문 대상에서 조용히 빠진다
    fp = logic_version.fingerprint()
    assert len(fp) == 12 and fp == logic_version.fingerprint()
    assert logic_version.current()["tag"] == logic_version.LOGIC_TAG


def test_new_trade_level_records_carry_the_logic_fingerprint(tmp_path):
    from app.engine.trade_level_tracker import TradeLevelTracker
    t = TradeLevelTracker(tmp_path / "t.json")
    t.issue({"KR_005930": {"kind": "entry", "available": True, "data_date": "2026-11-26",
                           "name": "삼성전자", "entry_low": 1.0, "entry_high": 2.0, "stop": 0.5,
                           "targets": [4.0], "risk_reward": 2.0}})
    assert t.records[0]["logic"] == logic_version.fingerprint()
