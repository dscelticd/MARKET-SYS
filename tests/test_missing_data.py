"""
결측 처리 테스트 — 계약 C3: 결측은 결측으로 남는다

배경 (실측 사고): 2026-09-02 저녁 결산이 `🟢 LG전자 (+7.44%) — 전장 사업부
      흑자 전환`을 9월 1일 결산의 대표 호재로 서술하고, 18종목 중 **유일한
      '안전' 등급**을 부여했다. 차트도 LG전자를 뽑았다.

      그런데 +7.44% / 216,500원은 **8월 31일** 수치였다. 9월 1일 LG전자는
      하루 종일 밀려 10:33 시점 206,000원 −4.85%였다. 전날 급등으로 최고
      등급이 매겨진 것이다.

      원인은 수집기가 대상일 봉이 없을 때 조용히 직전 봉(iloc[-1])을 집어
      "당일 종가"로 넘긴 것. 예외가 나면 Mock으로 메우는 경로까지 있었다.
      값을 지어내는 것도, 하루 전 값을 쓰는 것도 같은 사고를 만든다.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import patch

import pytest

pd = pytest.importorskip("pandas")

from app.collectors.price_collector import PriceCollector  # noqa: E402
from app.reports.report_builder import _format_missing_block  # noqa: E402


def _hist(dates, closes):
    return pd.DataFrame(
        {"Open": closes, "High": [c * 1.01 for c in closes],
         "Low": [c * 0.99 for c in closes], "Close": closes,
         "Volume": [1_000_000] * len(closes)},
        index=pd.to_datetime(dates),
    )


def _stub(hist, raise_exc=None):
    class _Ticker:
        def __init__(self, sym): pass
        def history(self, **kw):
            if raise_exc:
                raise raise_exc
            return hist
        @property
        def fast_info(self):
            return types.SimpleNamespace(year_high=1.0, year_low=1.0, market_cap=1.0)
    mod = types.ModuleType("yfinance")
    mod.Ticker = _Ticker
    return mod


def _collect(hist, target, raise_exc=None):
    with patch.dict(sys.modules, {"yfinance": _stub(hist, raise_exc)}):
        c = PriceCollector()
        c.use_mock = False
        return c.collect(["KR_066570"], target=target)["KR_066570"]


# ── 수집 단계 ────────────────────────────────────────────────────────────────

def test_stale_bar_is_not_passed_off_as_the_target_day():
    """LG전자 사고의 재현. 대상일 9/1인데 8/31 봉만 있으면 결측이어야 한다.
    이 검사가 깨지면 전날 급등이 다시 당일 등락으로 둔갑한다."""
    row = _collect(_hist(["2026-08-28", "2026-08-31"], [201_600.0, 216_500.0]),
                   {"KR": "2026-09-01", "US": "2026-09-01"})
    assert row["missing"] is True
    assert row["price"] is None
    assert row["change_pct"] is None
    assert row["data_date"] is None
    assert "2026-08-31" in row["missing_reason"]


def test_target_day_present_is_collected_normally():
    row = _collect(_hist(["2026-08-31", "2026-09-01"], [216_500.0, 206_000.0]),
                   {"KR": "2026-09-01", "US": "2026-09-01"})
    assert not row.get("missing")
    assert row["price"] == 206_000.0
    assert row["data_date"] == "2026-09-01"


def test_collection_failure_becomes_missing_not_mock():
    """예외 시 Mock 폴백은 지어낸 값을 실제 리포트에 넣는다."""
    row = _collect(None, {"KR": "2026-09-01", "US": "2026-09-01"},
                   raise_exc=RuntimeError("네트워크 오류"))
    assert row["missing"] is True
    assert row["_mock"] is False          # Mock으로 위장되지 않는다
    assert "네트워크 오류" in row["missing_reason"]


def test_unmapped_symbol_becomes_missing_not_mock():
    with patch.dict(sys.modules, {"yfinance": _stub(_hist(["2026-09-01"], [1.0]))}):
        c = PriceCollector()
        c.use_mock = False
        row = c.collect(["KR_999999"], target={"KR": "2026-09-01", "US": "2026-09-01"})["KR_999999"]
    assert row["missing"] is True
    assert row["_mock"] is False


# ── 리포트 노출 ──────────────────────────────────────────────────────────────

def test_missing_block_names_the_stock_and_forbids_substitution():
    block = _format_missing_block({
        "KR_066570": {"name": "LG전자", "ticker": "066570",
                      "target_date": "2026-09-01",
                      "missing_reason": "대상 거래일 데이터 미도착 (최신 봉 2026-08-31)"},
    })
    assert "LG전자" in block
    assert "2026-09-01" in block
    assert "직전 거래일 수치로 대신 설명하지도 마세요" in block


def test_missing_block_is_empty_when_nothing_is_missing():
    """결측이 없는데 빈 경고 섹션이 붙으면 매번 노이즈가 된다."""
    assert _format_missing_block(None) == ""
    assert _format_missing_block({}) == ""


def test_both_report_prompts_receive_the_missing_block():
    import inspect
    from app.reports.report_builder import ReportBuilder
    for name in ("build_morning_report", "build_evening_report"):
        src = inspect.getsource(getattr(ReportBuilder, name))
        assert "missing_stocks" in inspect.signature(getattr(ReportBuilder, name)).parameters
        assert "{missing_block}" in src


def test_pipeline_excludes_missing_stocks_from_scoring():
    """결측 종목이 등급 산정까지 흘러가면 값이 빈 채로 점수가 매겨진다."""
    import pathlib, re
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text(encoding="utf-8")
    body = re.sub(r"#.*", "", src)
    assert 'p.get("missing")' in body
    assert 'stocks     = [s for s in stocks if s["id"] not in missing_stocks]' in body
