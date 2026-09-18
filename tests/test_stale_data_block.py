"""
지연 데이터 안내 블록 테스트 — _format_stale_block

배경: 결측 가드(계약 C3)를 "대상일과 정확히 일치하지 않으면 결측"으로 엄격하게
짰더니, yfinance의 정상적인 발표 지연 때문에 관심종목 절반 이상이 매일
리포트에서 통째로 빠지는 회귀가 생겼다(실측 2026-09-14~18, 5거래일 연속 —
한국장 마감 9시간 뒤에도, 미국장 마감 4시간 뒤에도 최신 종가가 없었다).

이제 결측(데이터가 아예 없음)과 지연(데이터는 있지만 대상일보다 이전)을
분리한다. 지연 종목은 등급 산정에서 빠지지 않고, 실제 날짜와 함께 리포트에
포함된다 — 다만 "오늘 등락"으로 서술되지 않도록 이 블록이 명시적으로 못박는다.
이 정직함이 LG전자 사고(늦은 종가를 당일 종가인 척 낸 것) 재발을 막는다.
"""
from __future__ import annotations

from app.reports.report_builder import _format_stale_block

_TARGET = {"kr_date": "2026-09-17", "us_date": "2026-09-17"}


def _price(name, ticker, data_date, missing=False):
    return {"name": name, "ticker": ticker, "data_date": data_date, "missing": missing}


def test_lists_stocks_whose_data_is_behind_the_target():
    price_data = {
        "KR_005930": _price("삼성전자", "005930", "2026-09-16"),
        "US_NVDA":   _price("NVIDIA",   "NVDA",   "2026-09-17"),  # 신선함 — 대상 밖
    }
    b = _format_stale_block(price_data, _TARGET)
    assert "삼성전자(005930)" in b
    assert "실제 데이터 2026-09-16" in b
    assert "NVIDIA" not in b   # 대상일과 일치하는 종목은 나오면 안 된다


def test_empty_when_everything_matches_the_target():
    """지연이 없는데 안내가 붙으면 매번 노이즈가 된다."""
    price_data = {
        "KR_005930": _price("삼성전자", "005930", "2026-09-17"),
        "US_NVDA":   _price("NVIDIA",   "NVDA",   "2026-09-17"),
    }
    assert _format_stale_block(price_data, _TARGET) == ""


def test_empty_for_no_price_data():
    assert _format_stale_block(None, _TARGET) == ""
    assert _format_stale_block({}, _TARGET) == ""


def test_missing_stocks_are_not_double_counted_as_stale():
    """결측(_format_missing_block이 이미 다루는 영역)과 지연은 서로 배타적이어야
    한다 — 결측 종목이 지연 블록에도 나오면 리포트에 같은 종목이 두 번, 서로
    다른 설명으로 등장한다."""
    price_data = {
        "KR_005930": {"name": "삼성전자", "ticker": "005930",
                      "data_date": None, "missing": True},
    }
    assert _format_stale_block(price_data, _TARGET) == ""


def test_instructs_the_model_not_to_narrate_stale_prices_as_todays_move():
    price_data = {"KR_005930": _price("삼성전자", "005930", "2026-09-16")}
    b = _format_stale_block(price_data, _TARGET)
    assert "'오늘' 움직임으로 서술하지 마세요" in b


def test_market_specific_target_compares_each_stock_to_its_own_market():
    """저녁 결산처럼 한·미 대상일이 다를 때, 종목은 자기 시장의 대상일과만
    비교해야 한다 — 미국 대상일로 국내 종목의 신선도를 재면 오탐이 난다."""
    target = {"kr_date": "2026-09-02", "us_date": "2026-09-01"}
    price_data = {
        "KR_005930": _price("삼성전자", "005930", "2026-09-02"),   # KR 대상과 일치
        "US_NVDA":   _price("NVIDIA",   "NVDA",   "2026-09-01"),   # US 대상과 일치
    }
    assert _format_stale_block(price_data, target) == ""


def test_both_report_prompts_receive_the_stale_block():
    import inspect
    from app.reports.report_builder import ReportBuilder
    for name in ("build_morning_report", "build_evening_report"):
        src = inspect.getsource(getattr(ReportBuilder, name))
        assert "stale_block = _format_stale_block(price_data, target_session or {})" in src
        assert "{stale_block}" in src
