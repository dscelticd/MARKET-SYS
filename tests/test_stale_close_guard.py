"""
2026-09-02 저녁 결산 사고 재현 — 결함 두 개가 겹쳐야 일어난다

증상: 리포트가 "한국 2026-09-02(수) 종가 기준"이라 선언하고 9월 1일 값을 실었다.
        삼성전자   리포트 +0.38%  /  실제 9/2  -4.02%
        SK하이닉스 리포트 +1.14%  /  실제 9/2  -4.73%
        LG전자     리포트 -4.62%  /  실제 9/2  -2.91%
      9월 2일은 국내 증시가 크게 밀린 날이라 서술 방향이 반대였다.
      "워치리스트 18종목 중 하락 13 · 상승 5"도 9월 1일 실측과 정확히 일치했다.

원인 ① 결측 가드가 원본 hist의 마지막 인덱스를 봤다. yfinance는 종가가 확정되지
      않은 날에도 Close=NaN인 자리 행을 준다(비미국 거래소에서 흔하다).
      hist.index[-1]은 대상일과 같아 가드를 통과하는데, 값은 dropna() 뒤의
      하루 전 종가가 된다.

원인 ② 계약 위반 탐지기가 두 시장의 대상일을 **합집합**으로 검사했다.
      대상이 KR=9/2·US=9/1일 때 한국 종목의 9/1 데이터가 "허용 집합"에
      들어 있어 경보가 뜨지 않았다. 시장별로 검사해야 한다.

두 결함 중 하나만 있었어도 드러났을 사고다. 둘 다 막는다.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime
from unittest.mock import patch

import pytest

pd = pytest.importorskip("pandas")

from app.collectors.price_collector import PriceCollector  # noqa: E402
from app.reports.report_builder import _format_market_session_block  # noqa: E402
from app.utils.market_calendar import stock_market, summarize_data_freshness  # noqa: E402


def _hist_with_placeholder(dates, closes):
    """마지막 행의 Close가 NaN — yfinance가 미확정일에 주는 자리 행."""
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes,
         "Volume": [1_000_000] * len(closes)},
        index=pd.to_datetime(dates),
    )


def _stub(hist):
    class _Ticker:
        def __init__(self, sym): pass
        def history(self, **kw): return hist
        @property
        def fast_info(self):
            return types.SimpleNamespace(year_high=1.0, year_low=1.0, market_cap=1.0)
    m = types.ModuleType("yfinance"); m.Ticker = _Ticker
    return m


def _collect(hist, target):
    with patch.dict(sys.modules, {"yfinance": _stub(hist)}):
        c = PriceCollector(); c.use_mock = False
        return c.collect(["KR_005930"], target=target)["KR_005930"]


# ── 원인 ① ──────────────────────────────────────────────────────────────────

def test_nan_close_placeholder_row_does_not_pass_the_guard():
    """실제 사고의 형태 — 9/2 행은 있으나 종가가 NaN이다.
    원본 인덱스로 판정하면 통과해 9/1 종가가 "9/2 종가"로 나간다."""
    h = _hist_with_placeholder(["2026-08-31", "2026-09-01", "2026-09-02"],
                               [260_000.0, 261_000.0, float("nan")])
    row = _collect(h, {"KR": "2026-09-02", "US": "2026-09-02"})
    assert row["missing"] is True
    assert row["price"] is None
    assert "2026-09-01" in row["missing_reason"]


def test_real_close_on_the_target_day_is_collected():
    h = _hist_with_placeholder(["2026-08-31", "2026-09-01", "2026-09-02"],
                               [260_000.0, 261_000.0, 250_500.0])
    row = _collect(h, {"KR": "2026-09-02", "US": "2026-09-02"})
    assert not row.get("missing")
    assert row["price"] == 250_500.0
    assert row["data_date"] == "2026-09-02"
    assert row["change_pct"] == -4.02        # 실제 9/2 등락률


def test_all_nan_tail_rows_are_skipped_not_averaged():
    h = _hist_with_placeholder(["2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03"],
                               [260_000.0, 261_000.0, float("nan"), float("nan")])
    assert _collect(h, {"KR": "2026-09-03", "US": "2026-09-03"})["missing"] is True


# ── 원인 ② ──────────────────────────────────────────────────────────────────

def test_freshness_counts_dates_per_market():
    f = summarize_data_freshness(
        {"KR_005930": {"data_date": "2026-09-01"}, "US_NVDA": {"data_date": "2026-09-01"}},
        now=datetime(2026, 9, 3, 0, 15),
    )
    assert f["date_counts_by_market"] == {"KR": {"2026-09-01": 1}, "US": {"2026-09-01": 1}}


def test_stock_market_classification():
    assert stock_market("KR_005930") == "KR"
    assert stock_market("US_NVDA") == "US"
    assert stock_market("TW_TSM") == "US"     # 미국 상장 ADR


def test_tripwire_catches_kr_data_hiding_behind_the_us_target_date():
    """합집합 검사의 구멍 — 대상 KR=9/2·US=9/1일 때 한국 종목의 9/1 데이터가
    '허용된 날짜'라는 이유로 통과했다. 실제로 이 때문에 경보가 없었다."""
    b = _format_market_session_block(
        {"date_counts": {"2026-09-01": 18},
         "date_counts_by_market": {"KR": {"2026-09-01": 7}, "US": {"2026-09-01": 11}}},
        None, None,
        target={"kr_date": "2026-09-02", "us_date": "2026-09-01", "report_type": "evening"},
    )
    assert "🚨" in b
    assert "KR 시장의 대상 거래일은 2026-09-02" in b
    assert "2026-09-01: 7종목" in b


def test_tripwire_quiet_when_each_market_matches_its_own_target():
    b = _format_market_session_block(
        {"date_counts_by_market": {"KR": {"2026-09-02": 7}, "US": {"2026-09-01": 11}}},
        {"data_dates": {"KOSPI": "2026-09-02", "SP500": "2026-09-01"}}, None,
        target={"kr_date": "2026-09-02", "us_date": "2026-09-01", "report_type": "evening"},
    )
    assert "🚨" not in b


def test_index_dates_are_checked_against_their_own_market():
    """KOSPI를 미국 대상일로 재는 실수를 막는다."""
    b = _format_market_session_block(
        {"date_counts_by_market": {"KR": {"2026-09-02": 7}}},
        {"data_dates": {"KOSPI": "2026-09-01"}}, None,
        target={"kr_date": "2026-09-02", "us_date": "2026-09-01", "report_type": "evening"},
    )
    assert "🚨" in b and "KOSPI 2026-09-01" in b


# ── 파일명 일관성 ────────────────────────────────────────────────────────────

def test_report_and_ratings_are_saved_under_the_same_date(tmp_path):
    """예약 실행이 자정을 넘겨 밀리면 .md와 _ratings.json이 다른 날짜로
    갈린다. 실측(2026-09-02 저녁분): JSON 20260902 / 마크다운 20260903."""
    from app.reports.report_builder import save_report
    p = save_report("본문", "evening", tmp_path, "2026-09-02")
    assert p.name == "20260902_evening.md"


def test_save_report_falls_back_to_today_without_a_report_date(tmp_path):
    from app.reports.report_builder import save_report
    from app.utils.market_calendar import now_kst
    p = save_report("본문", "morning", tmp_path)
    assert p.name == f"{now_kst():%Y%m%d}_morning.md"
