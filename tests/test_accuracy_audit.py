"""
정확도 점검 테스트 — 답을 아는 합성 이력으로 판정 방식을 검증
"""
from __future__ import annotations

import pytest

from app.engine.accuracy_audit import audit, spearman, summary_lines


def _entry(date, prices, grades=None, scores=None, comps=None, rtype="morning", trading=True):
    return {"date": date, "report_type": rtype, "is_trading_day": trading,
            "closing_prices": prices, "grades": grades or {}, "scores": scores or {},
            "components": comps or {}}


def _hist(*entries):
    return {f"{e['date']}_{e['report_type']}": e for e in entries}


def test_spearman_basics():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert spearman([1, 1, 2], [5, 5, 9]) == pytest.approx(1.0)   # 동점 처리


def test_legacy_tolerance_counts_a_loser_as_a_hit_but_strict_does_not():
    """시장 +3% 상승일에 '안전' 종목이 −1% — 기존 ±2%p 기준으로는 적중, 시장 대비로는 실패."""
    a = _entry("2026-10-01", {"KR_A": 100, "KR_B": 100, "KR_C": 100},
               grades={"KR_A": "안전", "KR_B": "보통", "KR_C": "보통"})
    b = _entry("2026-10-02", {"KR_A": 99, "KR_B": 105, "KR_C": 105})
    r = audit(_hist(a, b))["horizons"][1]
    assert r["legacy_hit_rate"] == 100
    assert r["strict_hit_rate"] == 0
    assert r["by_grade"]["안전"]["avg_excess"] == pytest.approx(-4.0)   # −1% − 시장 평균 +3%


def test_excess_return_is_measured_within_each_market():
    """한국·미국 종목은 서로 다른 시장 평균과 비교한다."""
    a = _entry("2026-10-01", {"KR_A": 100, "KR_B": 100, "US_X": 100, "US_Y": 100},
               grades={"KR_A": "안전", "US_X": "안전"})
    b = _entry("2026-10-02", {"KR_A": 102, "KR_B": 100, "US_X": 90, "US_Y": 80})
    r = audit(_hist(a, b))["horizons"][1]
    assert r["strict_hit_rate"] == 100        # 두 종목 모두 자기 시장 평균은 이겼다


def test_score_ic_detects_a_perfect_ranking():
    ids = [f"KR_{i}" for i in range(6)]
    a = _entry("2026-10-01", {s: 100 for s in ids}, scores={s: i * 10 for i, s in enumerate(ids)})
    b = _entry("2026-10-02", {s: 100 + i for i, s in enumerate(ids)})
    ic = audit(_hist(a, b))["horizons"][1]["score_ic"]
    assert ic["mean"] == pytest.approx(1.0) and ic["n"] == 1


def test_factor_ic_is_computed_per_component():
    ids = [f"KR_{i}" for i in range(6)]
    comps = {s: {"news_sentiment": i, "price_momentum": -i} for i, s in enumerate(ids)}
    a = _entry("2026-10-01", {s: 100 for s in ids}, comps=comps)
    b = _entry("2026-10-02", {s: 100 + i for i, s in enumerate(ids)})
    f = audit(_hist(a, b))["horizons"][1]["factor_ic"]
    assert f["news_sentiment"]["mean"] == pytest.approx(1.0)
    assert f["price_momentum"]["mean"] == pytest.approx(-1.0)


def test_morning_snapshot_is_preferred_and_weekends_are_skipped():
    """저녁 스냅샷의 국내 종가에는 한동안 KIS 시간외 가격이 섞였다 — 아침을 쓴다."""
    m = _entry("2026-10-01", {"KR_A": 100, "KR_B": 100}, grades={"KR_A": "안전"})
    e = _entry("2026-10-01", {"KR_A": 999, "KR_B": 100}, grades={"KR_A": "안전"}, rtype="evening")
    sat = _entry("2026-10-03", {"KR_A": 1, "KR_B": 1}, trading=False)
    nxt = _entry("2026-10-02", {"KR_A": 110, "KR_B": 100})
    res = audit(_hist(e, m, sat, nxt))
    assert res["snapshots"] == 2
    assert res["horizons"][1]["by_grade"]["안전"]["avg_excess"] == pytest.approx(5.0)


def test_summary_lines_are_produced():
    ids = [f"KR_{i}" for i in range(6)]
    from datetime import date, timedelta
    from app.utils.market_calendar import is_trading_day
    days, cur = [], date(2026, 10, 1)
    while len(days) < 12:
        if is_trading_day(cur, "KR"):
            days.append(cur.isoformat())
        cur += timedelta(days=1)
    snaps = [_entry(dt, {s: 100 + n * (i - 2) for i, s in enumerate(ids)},
                    grades={ids[0]: "안전", ids[5]: "주의"},
                    scores={s: i for i, s in enumerate(ids)}) for n, dt in enumerate(days, 1)]
    lines = summary_lines(audit(_hist(*snaps)))
    assert lines[0].startswith("정확도 점검 — 거래일 스냅샷 12개")
    assert any(l.startswith("1일 뒤") for l in lines) and any(l.startswith("10일 뒤") for l in lines)


def test_too_little_history_says_so():
    assert "비교 불가" in summary_lines(audit({}))[0]


def test_mock_snapshots_are_excluded():
    a = _entry("2026-10-01", {"KR_A": 100, "KR_B": 100})
    a["data_quality"] = {"price": {"mock": 18, "real": 0}}
    b = _entry("2026-10-02", {"KR_A": 100, "KR_B": 100})
    assert audit(_hist(a, b))["snapshots"] == 1


def test_pairs_use_exact_trading_day_distance_not_list_position():
    """실행이 빠진 날이 있으면 '다음 스냅샷'은 며칠 뒤일 수 있다 — 1일 뒤로 세면 안 된다."""
    a = _entry("2026-10-05", {"KR_A": 100, "KR_B": 100}, grades={"KR_A": "안전"})   # 월(대체공휴일)
    b = _entry("2026-10-14", {"KR_A": 120, "KR_B": 100})                            # 한참 뒤
    r = audit(_hist(a, b))["horizons"]
    assert r[1]["pairs"] == 0
    c = _entry("2026-10-06", {"KR_A": 100, "KR_B": 100}, grades={"KR_A": "안전"})
    d = _entry("2026-10-07", {"KR_A": 101, "KR_B": 100})
    assert audit(_hist(c, d))["horizons"][1]["pairs"] == 1
