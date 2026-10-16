"""
실행 품질 요약 테스트 — 실행 이력 주석과 텔레그램 알림의 근거가 되는 한 줄

배경: 2026-10 운영 점검에서 지난 한 달의 결측·지연 횟수와 사용된 데이터 소스를
확인할 수 없었다. 실행 로그는 로그인해야 보이고 7일 뒤 삭제된다.
"""
from __future__ import annotations

from app.main import summarize_run_quality

_TARGET = {"kr_date": "2026-10-15", "us_date": "2026-10-14"}
_DATES_OK = {"SP500": "2026-10-14", "NASDAQ": "2026-10-14", "SOX": "2026-10-14",
             "KOSPI": "2026-10-15", "KOSDAQ": "2026-10-15"}


def _p(name, date, source):
    return {"name": name, "data_date": date, "price_source": source, "_mock": False}


def _healthy():
    return {
        "KR_005930": _p("삼성전자", "2026-10-15", "kis"),
        "KR_000660": _p("SK하이닉스", "2026-10-15", "kis"),
        "US_NVDA":   _p("NVIDIA", "2026-10-14", "yfinance"),
        "US_SNDK":   _p("SanDisk", "2026-10-14", "info"),
    }


def test_healthy_run_is_summarized_with_sources():
    line, degraded = summarize_run_quality(_healthy(), {}, _TARGET, {"data_dates": _DATES_OK}, 91.6)
    assert not degraded
    assert "KR 2/2 (KIS 2)" in line
    assert "US 2/2" in line and "yfinance 1" in line and "공식종가 1" in line
    assert "지수 5/5" in line and "지연 0" in line and "결측 0" in line and "신뢰도 92" in line


def test_stale_stock_marks_the_run_degraded():
    data = _healthy()
    data["US_NVDA"] = _p("NVIDIA", "2026-10-13", "stale")
    line, degraded = summarize_run_quality(data, {}, _TARGET, {"data_dates": _DATES_OK})
    assert degraded
    assert "US 1/2" in line and "지연 1" in line


def test_missing_stock_counts_against_its_market_total():
    data = _healthy()
    del data["KR_000660"]
    missing = {"KR_000660": {"name": "SK하이닉스", "missing": True}}
    line, degraded = summarize_run_quality(data, missing, _TARGET, {"data_dates": _DATES_OK})
    assert degraded
    assert "KR 1/2" in line and "결측 1" in line


def test_index_on_the_wrong_date_marks_the_run_degraded():
    dates = dict(_DATES_OK, KOSPI="2026-10-14")
    line, degraded = summarize_run_quality(_healthy(), {}, _TARGET, {"data_dates": dates})
    assert degraded and "지수 4/5" in line
