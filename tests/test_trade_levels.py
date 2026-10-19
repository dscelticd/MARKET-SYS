"""
참고 가격대 · 청산 점검 테스트 — 계산, 검증 집계, 리포트 섹션, 비공개 단가 파일
"""
from __future__ import annotations

import json

import pytest

pd = pytest.importorskip("pandas")

from app.collectors.price_collector import _calc_volatility  # noqa: E402
from app.engine.trade_level_tracker import ENTRY_HORIZON, EXIT_HORIZON, TradeLevelTracker  # noqa: E402
from app.engine.trade_levels import (  # noqa: E402
    build_trade_levels,
    compute_entry_levels,
    compute_exit_signals,
    round_price,
)
from app.reports.report_builder import format_trade_levels_section  # noqa: E402


def _p(price=217_000.0, atr=5_000.0, high22=240_000.0, supports=None, resistances=None,
       near_res=None, ma20=None, rsi=50.0, direction="양봉", cur="KRW", date="2026-10-16"):
    return {
        "price": price, "currency": cur, "data_date": date, "_mock": False,
        "technical": {"atr14": atr, "high_22d": high22, "ma20": ma20, "rsi_14": rsi},
        "support_resistance": {
            "support_zones": supports if supports is not None else [{"low": 205_000.0, "high": 208_000.0}],
            "resistance_zones": resistances if resistances is not None else
                [{"low": 230_000.0, "high": 233_000.0}, {"low": 245_000.0, "high": 248_000.0}],
            "nearest_resistance_pct": near_res,
        },
        "candle_pattern": {"direction": direction},
    }


# ── 가격 단위 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("price,expected", [
    (1_234.4, 1_234), (4_312, 4_310), (17_333, 17_330), (43_210, 43_200),
    (152_340, 152_300), (412_300, 412_500), (1_263_400, 1_263_000),
])
def test_krx_tick_rounding(price, expected):
    assert round_price(price, "KRW") == expected


def test_etf_uses_five_won_ticks():
    assert round_price(88_752, "KRW", is_etf=True) == 88_750


def test_usd_rounds_to_cents():
    assert round_price(222.2749, "USD") == 222.27


# ── 진입 참고 구간 ───────────────────────────────────────────────────────────

def test_entry_band_stop_and_targets():
    lv = compute_entry_levels(_p())
    assert (lv["entry_low"], lv["entry_high"]) == (205_000, 208_000)
    assert lv["stop"] == 200_000                     # 205,000 − ATR 5,000
    assert lv["targets"] == [230_000, 245_000]
    # 중앙 206,500 → 손절폭 6,500, 목표1까지 23,500 → 3.62
    assert lv["risk_reward"] == 3.62 and lv["meets_bar"]
    assert lv["distance_pct"] == pytest.approx(-4.1)


def test_weak_risk_reward_is_flagged():
    lv = compute_entry_levels(_p(resistances=[{"low": 210_000.0, "high": 212_000.0}]))
    assert lv["risk_reward"] < 2.0 and not lv["meets_bar"]


def test_no_resistance_falls_back_to_twice_the_risk_and_says_so():
    lv = compute_entry_levels(_p(resistances=[]))
    assert lv["target_basis"].startswith("손절폭 2배")
    assert lv["targets"] == [219_500]                 # 206,500 + 6,500 × 2
    assert lv["risk_reward"] == 2.0


def test_no_support_means_no_band_rather_than_an_invented_one():
    lv = compute_entry_levels(_p(supports=[]))
    assert not lv["available"] and "지지 구간" in lv["reason"]


def test_missing_atr_means_no_band():
    assert not compute_entry_levels(_p(atr=None))["available"]


# ── 청산 점검 ────────────────────────────────────────────────────────────────

def test_trailing_stop_is_recent_high_minus_three_atr():
    lv = compute_exit_signals(_p())
    assert lv["trailing_stop"] == 225_000            # 240,000 − 5,000 × 3
    assert "추적 손절선" in lv["signals"][0]          # 217,000 ≤ 225,000
    assert lv["state"] == "점검 필요"


def test_quiet_holding_has_no_signals():
    lv = compute_exit_signals(_p(high22=220_000.0, ma20=210_000.0))
    assert lv["signals"] == [] and lv["state"] == "유지 조건 충족"


@pytest.mark.parametrize("kw,needle", [
    ({"ma20": 228_300.0}, "20일선"),
    ({"near_res": 0.6}, "저항 구간 도달"),
    ({"rsi": 74.0, "direction": "음봉"}, "RSI 과열"),
])
def test_each_exit_signal(kw, needle):
    lv = compute_exit_signals(_p(high22=220_000.0, **kw))
    assert any(needle in s for s in lv["signals"])


def test_hot_rsi_alone_is_not_a_signal():
    lv = compute_exit_signals(_p(high22=220_000.0, rsi=74.0, direction="양봉"))
    assert lv["signals"] == []


def test_grade_drop_is_a_signal_but_data_quality_holds_are_not():
    drop = {"direction": "하락", "prev_grade": "안전", "curr_grade": "보통"}
    hold = {"direction": "하락", "prev_grade": "안전", "curr_grade": "판단보류"}
    assert any("등급 하락" in s for s in compute_exit_signals(_p(high22=220_000.0), grade_change=drop)["signals"])
    assert compute_exit_signals(_p(high22=220_000.0), grade_change=hold)["signals"] == []


def test_average_cost_adds_pnl_and_stop_versus_cost():
    lv = compute_exit_signals(_p(high22=220_000.0), avg_cost=185_000.0)
    assert lv["pnl_pct"] == 17.3
    assert lv["trail_vs_cost_pct"] == pytest.approx(10.8)   # 205,000 / 185,000


# ── 상태별 배분 ──────────────────────────────────────────────────────────────

_TARGET = {"kr_date": "2026-10-16", "us_date": "2026-10-16"}


def test_levels_follow_holding_status():
    stocks = [{"id": "KR_A", "name": "A", "status": "보유"},
              {"id": "KR_B", "name": "B", "status": "관찰"},
              {"id": "KR_C", "name": "C", "status": "매도완료"}]
    data = {"KR_A": _p(), "KR_B": _p(), "KR_C": _p()}
    lv = build_trade_levels(stocks, data, [], {"KR_A": {"avg_cost": 185_000.0}}, _TARGET)
    assert lv["KR_A"]["kind"] == "exit" and lv["KR_A"]["avg_cost"] == 185_000.0
    assert lv["KR_B"]["kind"] == "entry"
    assert "KR_C" not in lv


def test_stale_or_mock_data_gets_no_levels():
    stocks = [{"id": "KR_A", "name": "A", "status": "관찰"}, {"id": "KR_B", "name": "B", "status": "관찰"}]
    mock = dict(_p(), _mock=True)
    lv = build_trade_levels(stocks, {"KR_A": _p(date="2026-10-15"), "KR_B": mock}, [], {}, _TARGET)
    assert lv == {}


# ── 변동성 입력 ──────────────────────────────────────────────────────────────

def test_atr_uses_true_range_including_gaps():
    idx = pd.bdate_range("2026-09-01", periods=16)
    h = pd.DataFrame({"High": [101.0] * 16, "Low": [99.0] * 16, "Close": [100.0] * 16}, index=idx)
    h.iloc[-1] = [111.0, 109.0, 110.0]      # 갭 상승 — 실제 변동폭은 고가 111 − 전일 종가 100 = 11
    v = _calc_volatility(h)
    assert v["atr14"] == pytest.approx((2 * 13 + 11) / 14, abs=1e-3)
    assert v["high_22d"] == 111.0


# ── 검증 집계 ────────────────────────────────────────────────────────────────

def _entry_lv(date="2026-10-16"):
    return {"KR_A": {"kind": "entry", "available": True, "name": "A", "data_date": date,
                     "entry_low": 205_000, "entry_high": 208_000, "stop": 200_000,
                     "targets": [230_000], "risk_reward": 3.6, "price": 217_000}}


def _day(date, o, h, l, c):
    return {"KR_A": {"data_date": date, "day_ohlc": {"open": o, "high": h, "low": l, "close": c}}}


@pytest.fixture
def tracker(tmp_path):
    t = TradeLevelTracker(tmp_path / "levels.json")
    t.issue(_entry_lv())
    return t


def test_fill_then_target_is_a_win(tracker):
    tracker.update(_day("2026-10-19", 212_000, 213_000, 207_000, 209_000))   # 진입 (체결 208,000)
    tracker.update(_day("2026-10-20", 215_000, 231_000, 214_000, 229_000))   # 목표 도달
    r = tracker.records[0]
    assert r["status"] == "win" and r["fill_price"] == 208_000
    assert r["r_multiple"] == pytest.approx((230_000 - 208_000) / 8_000, abs=0.01)


def test_fill_then_stop_is_a_loss(tracker):
    tracker.update(_day("2026-10-19", 212_000, 213_000, 207_000, 209_000))
    tracker.update(_day("2026-10-20", 205_000, 206_000, 199_000, 200_500))
    assert tracker.records[0]["status"] == "loss" and tracker.records[0]["r_multiple"] == -1.0


def test_touching_both_on_one_day_counts_as_a_loss(tracker):
    tracker.update(_day("2026-10-19", 212_000, 213_000, 207_000, 209_000))
    tracker.update(_day("2026-10-20", 210_000, 231_000, 199_000, 220_000))
    assert tracker.records[0]["status"] == "loss"


def test_target_on_the_fill_day_is_not_credited(tracker):
    """같은 날 고점이 먼저였을 수 있다 — 진입한 날의 목표 도달은 인정하지 않는다."""
    tracker.update(_day("2026-10-19", 225_000, 231_000, 207_000, 228_000))
    assert tracker.records[0]["status"] == "active"


def test_gap_down_fills_at_the_open(tracker):
    tracker.update(_day("2026-10-19", 204_000, 206_000, 203_000, 205_000))
    assert tracker.records[0]["fill_price"] == 204_000


def test_stop_on_the_fill_day(tracker):
    tracker.update(_day("2026-10-19", 210_000, 211_000, 198_000, 199_000))
    assert tracker.records[0]["status"] == "loss"


def test_never_filled_expires(tracker):
    for i in range(ENTRY_HORIZON):
        tracker.update(_day(f"2026-11-{i + 1:02d}", 220_000, 222_000, 215_000, 220_000))
    assert tracker.records[0]["status"] == "expired_unfilled"


def test_same_trading_day_is_evaluated_once(tracker):
    """저녁 결산(한국 D)과 다음날 아침 브리핑(한국 D)은 같은 일봉을 본다."""
    tracker.update(_day("2026-10-19", 220_000, 222_000, 215_000, 220_000))
    tracker.update(_day("2026-10-19", 220_000, 222_000, 215_000, 220_000))
    assert tracker.records[0]["age"] == 1


def test_issue_day_bar_is_not_used_to_judge_the_level(tracker):
    tracker.update(_day("2026-10-16", 210_000, 211_000, 190_000, 195_000))
    assert tracker.records[0]["status"] == "pending" and tracker.records[0]["age"] == 0


def test_same_stock_and_day_is_recorded_once(tracker):
    assert tracker.issue(_entry_lv()) == 0
    assert len(tracker.records) == 1


def test_exit_signal_is_scored_after_ten_sessions(tmp_path):
    t = TradeLevelTracker(tmp_path / "x.json")
    t.issue({"KR_A": {"kind": "exit", "name": "A", "data_date": "2026-10-16",
                      "price": 217_000, "signals": ["20일선 아래 마감"]}})
    for i in range(EXIT_HORIZON):
        t.update(_day(f"2026-11-{i + 1:02d}", 210_000, 211_000, 205_000, 208_000))
    assert t.records[0]["status"] == "exit_hit" and t.records[0]["change_pct"] < 0


def test_records_survive_a_save_and_reload(tracker, tmp_path):
    tracker.save()
    assert len(TradeLevelTracker(tmp_path / "levels.json").records) == 1


def test_summary_waits_for_enough_samples(tracker):
    tracker.update(_day("2026-10-19", 212_000, 213_000, 207_000, 209_000))
    tracker.update(_day("2026-10-20", 215_000, 231_000, 214_000, 229_000))
    s = tracker.summary()
    assert s["entry"]["wins"] == 1 and not s["entry"]["enough"]


# ── 리포트 섹션 ──────────────────────────────────────────────────────────────

def _forbidden() -> list[str]:
    from app.utils.config_loader import get_config
    return get_config().report.forbidden_expressions


def test_section_renders_both_tables_with_exact_numbers():
    stocks = [{"id": "KR_A", "name": "삼성전자", "status": "보유"},
              {"id": "KR_B", "name": "LS ELECTRIC", "status": "관찰"}]
    lv = build_trade_levels(stocks, {"KR_A": _p(), "KR_B": _p()}, [],
                            {"KR_A": {"avg_cost": 185_000.0}}, _TARGET)
    md = format_trade_levels_section(lv, {"entry": {}, "exit": {}})
    assert "참고 진입 구간" in md and "청산 점검" in md
    assert "205,000원 ~ 208,000원" in md and "200,000원" in md
    assert "평단 대비" in md and "+17.3%" in md
    assert "검증 중" in md
    assert not [w for w in _forbidden() if w in md]


def test_cost_column_is_omitted_when_no_cost_is_known():
    stocks = [{"id": "KR_A", "name": "삼성전자", "status": "보유"}]
    md = format_trade_levels_section(build_trade_levels(stocks, {"KR_A": _p()}, [], {}, _TARGET),
                                     {"entry": {}, "exit": {}})
    assert "평단 대비" not in md


def test_unavailable_bands_are_listed_with_the_reason():
    stocks = [{"id": "KR_B", "name": "오이솔루션", "status": "관찰"}]
    md = format_trade_levels_section(
        build_trade_levels(stocks, {"KR_B": _p(supports=[])}, [], {}, _TARGET),
        {"entry": {}, "exit": {}})
    assert "산출하지 않음" in md and "오이솔루션" in md


def test_empty_levels_render_nothing():
    assert format_trade_levels_section({}, {}) == ""


# ── 비공개 단가 파일 ─────────────────────────────────────────────────────────

def test_holdings_round_trip_and_secret_payload(tmp_path, monkeypatch):
    import app.utils.config_loader as cl
    monkeypatch.setattr(cl, "_HOLDINGS_PATH", tmp_path / "holdings.json")
    secret = cl.save_holdings({"KR_005930": 185_000, "US_NVDA": None, "KR_000660": float("nan")})
    assert json.loads(secret) == {"holdings": {"KR_005930": {"avg_cost": 185000.0}}}
    assert cl.load_holdings() == {"KR_005930": {"avg_cost": 185000.0}}


def test_broken_holdings_file_is_ignored(tmp_path, monkeypatch):
    import app.utils.config_loader as cl
    p = tmp_path / "holdings.json"
    p.write_text("{oops", encoding="utf-8")
    monkeypatch.setattr(cl, "_HOLDINGS_PATH", p)
    assert cl.load_holdings() == {}


def test_holdings_file_is_never_committed():
    from pathlib import Path
    gi = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert "config/holdings.json" in gi


def test_zero_or_negative_costs_are_dropped(tmp_path, monkeypatch):
    """0이나 음수를 단가로 받으면 '평단 대비' 수익률이 무한대·음수로 터진다."""
    import app.utils.config_loader as cl
    p = tmp_path / "holdings.json"
    p.write_text(json.dumps({"holdings": {"A": {"avg_cost": 0}, "B": {"avg_cost": -5},
                                          "C": {"avg_cost": "abc"}, "D": {"avg_cost": 100}}}),
                 encoding="utf-8")
    monkeypatch.setattr(cl, "_HOLDINGS_PATH", p)
    assert cl.load_holdings() == {"D": {"avg_cost": 100.0}}


# ── 한 점 구간 · 신호 연속성 · 기록 중복 ─────────────────────────────────────

def test_single_point_support_gets_a_minimum_band_width():
    """스윙 저점 하나로 된 지지 구간은 '148,000원 ~ 148,000원'이 된다."""
    lv = compute_entry_levels(_p(supports=[{"low": 148_000.0, "high": 148_000.0}], atr=6_000.0))
    assert lv["entry_low"] == 148_000 and lv["entry_high"] == 151_000      # + ATR × 0.5


def _exit_lv(keys, date):
    texts = {"trail": "추적 손절선(248,000원) 이탈", "ma20": "20일선(258,000원) 아래 마감",
             "grade": "등급 하락(안전→보통)"}
    return {"KR_A": {"kind": "exit", "name": "A", "data_date": date, "price": 215_500,
                     "signals": [texts[k] for k in keys], "signal_keys": list(keys)}}


def test_first_sighting_is_treated_as_ongoing_with_unknown_start(tmp_path):
    """처음 보는 종목의 신호는 몇 주째 이어진 것일 수 있다. 신규로 치면 첫
    실행에 이미 진행 중인 하락을 '적중'으로 세게 된다."""
    t = TradeLevelTracker(tmp_path / "s.json")
    lv = _exit_lv(["trail", "ma20"], "2026-10-15")
    t.annotate_signals(lv)
    t.issue(lv)
    assert lv["KR_A"]["new_signal_keys"] == []
    assert lv["KR_A"]["signal_since"] == {"trail": None, "ma20": None}
    assert not [r for r in t.records if r["kind"] == "exit"]


def test_signals_are_split_into_new_and_ongoing(tmp_path):
    t = TradeLevelTracker(tmp_path / "s.json")
    t.annotate_signals(_exit_lv([], "2026-10-14"))
    day1 = _exit_lv(["trail", "ma20"], "2026-10-15"); t.annotate_signals(day1)
    assert day1["KR_A"]["new_signal_keys"] == ["trail", "ma20"]
    day2 = _exit_lv(["trail", "ma20", "grade"], "2026-10-16"); t.annotate_signals(day2)
    assert day2["KR_A"]["new_signal_keys"] == ["grade"]
    assert day2["KR_A"]["signal_since"]["trail"] == "2026-10-15"


def test_rerun_on_the_same_trading_day_keeps_signals_new(tmp_path):
    """저녁 결산(10/16)에서 신규였던 신호는 다음날 아침 브리핑(같은 10/16)에서도 신규다."""
    t = TradeLevelTracker(tmp_path / "s.json")
    t.annotate_signals(_exit_lv([], "2026-10-14"))
    t.annotate_signals(_exit_lv(["trail"], "2026-10-15"))
    evening = _exit_lv(["trail", "ma20"], "2026-10-16"); t.annotate_signals(evening)
    morning = _exit_lv(["trail", "ma20"], "2026-10-16"); t.annotate_signals(morning)
    assert morning["KR_A"]["new_signal_keys"] == ["ma20"]
    assert morning["KR_A"]["signal_since"]["trail"] == "2026-10-15"


def test_a_signal_that_clears_and_returns_is_new_again(tmp_path):
    t = TradeLevelTracker(tmp_path / "s.json")
    t.annotate_signals(_exit_lv(["ma20"], "2026-10-14"))
    t.annotate_signals(_exit_lv([], "2026-10-15"))
    back = _exit_lv(["ma20"], "2026-10-16"); t.annotate_signals(back)
    assert back["KR_A"]["new_signal_keys"] == ["ma20"]


def test_only_new_exit_signals_are_recorded(tmp_path):
    """하락장에서 매일 이어지는 신호를 날마다 세면 한 번의 하락이 수십 건의 적중이 된다."""
    t = TradeLevelTracker(tmp_path / "s.json")
    t.annotate_signals(_exit_lv([], "2026-10-13"))
    for d in ("2026-10-14", "2026-10-15", "2026-10-16"):
        lv = _exit_lv(["trail", "ma20"], d)
        t.annotate_signals(lv)
        t.issue(lv)
    exits = [r for r in t.records if r["kind"] == "exit"]
    assert len(exits) == 1 and exits[0]["issued_on"] == "2026-10-14"


def test_signal_state_survives_a_reload(tmp_path):
    t = TradeLevelTracker(tmp_path / "s.json")
    t.annotate_signals(_exit_lv([], "2026-10-14"))
    t.annotate_signals(_exit_lv(["trail"], "2026-10-15")); t.save()
    t2 = TradeLevelTracker(tmp_path / "s.json")
    lv = _exit_lv(["trail"], "2026-10-16"); t2.annotate_signals(lv)
    assert lv["KR_A"]["new_signal_keys"] == []
    assert lv["KR_A"]["signal_since"]["trail"] == "2026-10-15"   # 상태를 잃으면 None(미상)


def test_one_open_entry_record_per_stock(tmp_path):
    t = TradeLevelTracker(tmp_path / "e.json")
    t.issue(_entry_lv("2026-10-15"))
    t.issue(_entry_lv("2026-10-16"))
    assert sum(1 for r in t.records if r["kind"] == "entry") == 1


def test_signal_cell_shows_new_first_and_ongoing_with_start_date():
    stocks = [{"id": "KR_A", "name": "삼성전자", "status": "보유"}]
    lv = build_trade_levels(stocks, {"KR_A": _p(ma20=228_300.0)}, [], {}, _TARGET)
    lv["KR_A"]["new_signal_keys"] = ["ma20"]
    lv["KR_A"]["signal_since"] = {"trail": "2026-10-14", "ma20": "2026-10-16"}
    md = format_trade_levels_section(lv, {"entry": {}, "exit": {}})
    assert "**신규** 20일선" in md and "지속: 추적 손절선(225,000원) 이탈 (10/14~)" in md
