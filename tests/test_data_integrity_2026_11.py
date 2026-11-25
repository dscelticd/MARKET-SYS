"""
2026-11-25 데이터 점검에서 찾은 오류의 재발 방지 테스트

  ① 0원 봉        — LG전자 11/23 저녁: 시가·저가 0 → 청산가 0원(−10.74R) 기록
  ② 액면분할      — SanDisk 11/20 8:1: −89% 가짜 수익률, 분할 전 가격대로 '손절' 판정
  ③ 직전 봉 공백  — SanDisk 11/23 실제 +10.69%가 −7.08%로(직전 봉이 11/19)
  ④ 휴장일 이동   — 옛 마이그레이션이 정상 항목을 한국 휴장일 날짜로 옮김(5건)
  ⑤ 아침 덮어쓰기 — 한국만 쉰 날 다음 아침이 같은 칸의 미국 가격을 하루 뒤 것으로 바꿈
"""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

pd = pytest.importorskip("pandas")

from app.collectors.price_collector import PriceCollector, _sanitize_ohlc  # noqa: E402
from app.engine.history_tracker import HistoryTracker  # noqa: E402
from app.engine.splits import SplitRegistry  # noqa: E402
from app.engine.trade_level_tracker import TradeLevelTracker, exit_base_rate  # noqa: E402


# ── 수집기 공통 ───────────────────────────────────────────────────────────────

def _frame(dates, closes, opens=None, lows=None, splits=None, tz="America/New_York"):
    idx = pd.DatetimeIndex(pd.to_datetime(dates)).tz_localize(tz)
    df = pd.DataFrame({
        "Open": opens or closes, "High": [c * 1.01 for c in closes],
        "Low": lows or [c * 0.99 for c in closes], "Close": closes,
        "Volume": [1_000_000] * len(closes),
    }, index=idx)
    df["Stock Splits"] = splits or [0.0] * len(closes)
    return df


def _stub(hist, info=None):
    class _T:
        def __init__(self, sym): pass
        def history(self, **kw): return hist
        def get_info(self):
            if info is None:
                raise RuntimeError("info 없음")
            return info
        @property
        def fast_info(self):
            return types.SimpleNamespace(year_high=1.0, year_low=1.0, market_cap=1.0)
        def get_analyst_price_targets(self, *a, **k): raise RuntimeError("미사용")
    m = types.ModuleType("yfinance"); m.Ticker = _T
    return m


def _collect(hist, sid="US_SNDK", target="2026-11-23", info=None):
    with patch.dict(sys.modules, {"yfinance": _stub(hist, info)}), \
         patch("app.collectors.price_collector._kis_collector.is_configured", return_value=False), \
         patch("app.collectors.price_collector._fetch_investor_flow", side_effect=RuntimeError("차단")):
        c = PriceCollector(); c.use_mock = False
        return c.collect([sid], target={"KR": target, "US": target})[sid]


# ── ① 0원 봉 ──────────────────────────────────────────────────────────────────

def test_zero_values_are_treated_as_missing():
    df = _frame(["2026-11-20", "2026-11-23"], [130600.0, 123600.0], opens=[130400.0, 0.0],
                lows=[129400.0, 0.0], tz="Asia/Seoul")
    out = _sanitize_ohlc(df)
    assert out["Open"].isna().iloc[-1] and out["Low"].isna().iloc[-1]
    assert out["Close"].iloc[-1] == 123600.0          # 종가는 그대로


def test_broken_day_bar_is_not_used_for_candles_or_verification():
    dates = pd.bdate_range(end="2026-11-23", periods=25).strftime("%Y-%m-%d").tolist()
    closes = [130000.0] * 24 + [123600.0]
    opens = closes[:-1] + [0.0]
    lows = [c * 0.99 for c in closes[:-1]] + [0.0]
    row = _collect(_frame(dates, closes, opens=opens, lows=lows, tz="Asia/Seoul"), sid="KR_066570")
    assert row["price"] == 123600.0
    assert row["day_ohlc"] == {} and row["candle_pattern"] == {}
    # 0원 저가가 ATR에 들어가면 하루 변동폭이 12만원대가 된다
    assert row["technical"]["atr14"] < 10_000


# ── ② 분할 기록 ───────────────────────────────────────────────────────────────

def test_split_events_are_read_from_the_daily_bars():
    dates = pd.bdate_range(end="2026-11-23", periods=25).strftime("%Y-%m-%d").tolist()
    closes = [94.39] * 23 + [79.24, 87.71]
    splits = [0.0] * 23 + [8.0, 0.0]
    row = _collect(_frame(dates, closes, splits=splits))
    assert row["splits"] == [{"date": dates[-2], "ratio": 8.0}]


def test_split_registry_learns_once_and_converts_old_prices(tmp_path):
    reg = SplitRegistry(tmp_path / "splits.json")
    pdata = {"US_SNDK": {"splits": [{"date": "2026-11-20", "ratio": 8.0}]}}
    assert reg.update(pdata) == [("US_SNDK", "2026-11-20", 8.0)]
    assert reg.update(pdata) == []                                  # 두 번 배우지 않는다
    reg.save()
    reg2 = SplitRegistry(tmp_path / "splits.json")
    assert reg2.factor("US_SNDK", "2026-11-19", "2026-11-20") == 8.0  # 분할일 포함
    assert reg2.factor("US_SNDK", "2026-11-20", "2026-11-24") == 1.0  # 이미 분할 후
    assert reg2.factor("US_NVDA", "2026-11-01", "2026-11-24") == 1.0


# ── ③ 직전 봉 공백 ────────────────────────────────────────────────────────────

_TS_1123 = int(datetime(2026, 11, 23, 21, 0, tzinfo=timezone.utc).timestamp())   # 16:00 ET


def _gap_history():
    # 11/20 봉이 빠졌다 — 11/19 다음이 바로 11/23
    dates = pd.bdate_range(end="2026-11-19", periods=24).strftime("%Y-%m-%d").tolist() + ["2026-11-23"]
    closes = [94.39] * 24 + [87.71]
    return _frame(dates, closes)


def test_missing_previous_bar_uses_the_exchange_previous_close():
    info = {"regularMarketPrice": 87.71, "regularMarketPreviousClose": 79.24,
            "regularMarketTime": _TS_1123, "exchangeTimezoneName": "America/New_York"}
    row = _collect(_gap_history(), info=info)
    assert row["change_pct"] == 10.69                  # 저장된 값은 −7.08%였다
    assert row["prev_close_source"] == "info"
    assert row["prev_gap_sessions"] == 0
    assert row["prev_data_date"] == "2026-11-20"


def test_unverifiable_previous_close_is_flagged_as_a_multi_day_change():
    info = {"regularMarketPrice": 90.00, "regularMarketPreviousClose": 79.24,   # 우리 종가와 다름
            "regularMarketTime": _TS_1123, "exchangeTimezoneName": "America/New_York"}
    row = _collect(_gap_history(), info=info)
    assert row["prev_gap_sessions"] == 1
    assert row["prev_close_source"] == "series"


def test_contiguous_bars_are_left_alone():
    dates = pd.bdate_range(end="2026-11-23", periods=25).strftime("%Y-%m-%d").tolist()
    row = _collect(_frame(dates, [100.0] * 24 + [101.0]))
    assert row["prev_gap_sessions"] == 0 and row["change_pct"] == 1.0


# ── ④ 휴장일 이동 복구 · ⑤ 아침 덮어쓰기 ─────────────────────────────────────

def _tracker(tmp_path, data: dict) -> HistoryTracker:
    import app.engine.history_tracker as ht
    path = tmp_path / "ratings_history.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with patch.object(ht, "_HISTORY_DIR", tmp_path), patch.object(ht, "_HISTORY_FILE", path):
        return HistoryTracker()


def _entry(date, rtype="morning", run_date=None, data_date=None, schema="1.4"):
    return {"schema_version": schema, "date": date, "run_date": run_date or date,
            "report_type": rtype, "data_date": data_date or date, "is_trading_day": True,
            "grades": {}, "scores": {}, "closing_prices": {}}


def test_entries_moved_onto_kr_holidays_are_restored(tmp_path):
    """한글날(10/9) 다음 토요일 아침: 한국 10/8 · 미국 10/9 → 옛 코드가 10/9 칸으로 옮겼다."""
    t = _tracker(tmp_path, {
        "2026-10-08_morning": _entry("2026-10-08"),                                   # 정상(통합)
        "2026-10-09_morning": _entry("2026-10-09", run_date="2026-10-08", data_date="2026-10-09"),
    })
    assert "2026-10-09_morning" not in t._data
    fixed = t._data["2026-10-08_morning_us2026-10-09"]
    assert fixed["date"] == "2026-10-08" and fixed["us_date"] == "2026-10-09"
    assert not HistoryTracker.is_unified(fixed)
    assert HistoryTracker.is_unified(t._data["2026-10-08_morning"])


def test_repair_is_idempotent(tmp_path):
    data = {"2026-10-09_morning": _entry("2026-10-09", run_date="2026-10-08", data_date="2026-10-09")}
    t = _tracker(tmp_path, data)
    before = json.dumps(t._data, sort_keys=True)
    t2 = _tracker(tmp_path, t._data)
    assert json.dumps(t2._data, sort_keys=True) == before


def test_old_migration_no_longer_touches_current_schema_entries(tmp_path):
    """미국 날짜가 더 늦은 정상 항목(한국 휴장 직후)을 옛 마이그레이션이 옮기지 않는다."""
    t = _tracker(tmp_path, {"2026-10-02_morning": _entry("2026-10-02", data_date="2026-10-05")})
    assert "2026-10-02_morning" in t._data


def test_morning_after_kr_only_holiday_does_not_overwrite_a_unified_snapshot(tmp_path):
    t = _tracker(tmp_path, {"2026-10-08_morning": _entry("2026-10-08")})
    rating = {"stock_id": "US_NVDA", "grade": "보통", "total_score": 50.0, "risk_score": 40.0}
    with patch.object(HistoryTracker, "_persist"):
        t.save_today([rating], "morning", price_data={"US_NVDA": {"price": 1, "data_date": "2026-10-09"}},
                     session_date="2026-10-08", us_session_date="2026-10-09")
    assert HistoryTracker.is_unified(t._data["2026-10-08_morning"])
    assert t._data["2026-10-08_morning_us2026-10-09"]["us_date"] == "2026-10-09"


# ── 참고 가격대: 0원 봉 · 분할 ────────────────────────────────────────────────

def _entry_record(**kw):
    base = {"stock_id": "US_SNDK", "name": "SanDisk", "kind": "entry", "issued_on": "2026-11-12",
            "entry_low": 803.97, "entry_high": 859.79, "stop": 692.33, "target1": 1446.62,
            "status": "pending", "age": 0}
    base.update(kw)
    return base


def _levels(tmp_path, records):
    t = TradeLevelTracker(tmp_path / "levels.json")
    t.records = records
    return t


def test_broken_bar_does_not_resolve_a_record(tmp_path):
    t = _levels(tmp_path, [_entry_record(stock_id="KR_066570", status="active", fill_price=134300.0,
                                         stop=121800.0, target1=208500.0, issued_on="2026-11-13")])
    t.update({"KR_066570": {"data_date": "2026-11-23",
                            "day_ohlc": {"open": 0.0, "high": 128500.0, "low": 0.0, "close": 123600.0}}})
    assert t.records[0]["status"] == "active" and t.records[0].get("age", 0) == 0


def test_zero_exit_price_record_is_voided(tmp_path):
    t = _levels(tmp_path, [_entry_record(stock_id="KR_066570", status="loss", exit_price=0.0,
                                         r_multiple=-10.74, resolved_on="2026-11-23", issued_on="2026-11-13")])
    t.update({}, factor=lambda *a: 1.0)
    assert t.records[0]["status"] == "void" and "0원" in t.records[0]["void_reason"]
    assert t.summary()["void"] == 1 and t.summary()["entry"]["decided"] == 0


def test_record_judged_across_an_unadjusted_split_is_voided(tmp_path):
    reg = SplitRegistry(tmp_path / "s.json")
    reg.update({"US_SNDK": {"splits": [{"date": "2026-11-20", "ratio": 8.0}]}})
    t = _levels(tmp_path, [_entry_record(status="loss", fill_price=859.79, exit_price=89.91,
                                         r_multiple=-4.6, resolved_on="2026-11-20")])
    t.update({}, factor=reg.factor)
    assert t.records[0]["status"] == "void" and "8:1" in t.records[0]["void_reason"]


def test_open_record_is_converted_to_post_split_units(tmp_path):
    reg = SplitRegistry(tmp_path / "s.json")
    reg.update({"US_SNDK": {"splits": [{"date": "2026-11-20", "ratio": 8.0}]}})
    t = _levels(tmp_path, [_entry_record(status="active", fill_price=859.79, filled_on="2026-11-13")])
    t.update({"US_SNDK": {"data_date": "2026-11-20",
                          "day_ohlc": {"open": 89.91, "high": 92.0, "low": 78.5, "close": 79.24}}},
             factor=reg.factor)
    r = t.records[0]
    assert r["stop"] == pytest.approx(692.33 / 8, abs=1e-3)
    assert r["status"] == "loss" and r["r_multiple"] == -1.0          # 진짜 손절(−4.6R이 아니라)
    t.update({}, factor=reg.factor)                                     # 두 번 나누지 않는다
    assert r["stop"] == pytest.approx(692.33 / 8, abs=1e-3)


# ── 청산 신호의 비교 기준 ─────────────────────────────────────────────────────

def test_exit_base_rate_counts_any_day_drops():
    from datetime import date, timedelta
    from app.utils.market_calendar import is_trading_day
    days, cur = [], date(2026, 10, 1)
    while len(days) < 25:
        if is_trading_day(cur, "KR"):
            days.append(cur.isoformat())
        cur += timedelta(days=1)
    # 매일 1%씩 하락하는 종목 하나 — 아무 날이나 골라도 10거래일 뒤에는 더 낮다
    entries = [{"date": d, "report_type": "morning", "is_trading_day": True,
                "closing_prices": {"KR_005930": 100 * 0.99 ** i}} for i, d in enumerate(days)]
    base = exit_base_rate(entries, ["KR_005930"], days[0], days[-1])
    assert base["rate"] == 100 and base["n"] == len(days) - 10
