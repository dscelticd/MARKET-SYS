"""
관심종목 설정 점검 테스트 — watchlist.json과 수집 심볼 매핑(YFINANCE_MAP)의 어긋남

대시보드에서 종목을 추가해도 YFINANCE_MAP은 자동으로 생기지 않는다. 빠뜨리면
경고 로그 한 줄만 남기고 그 종목이 매 리포트에서 "데이터 미도착"으로 빠진다.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.utils.watchlist_check import validate_watchlist

_ROOT = Path(__file__).resolve().parents[1]


def _stock(sid, ticker, country, name="테스트"):
    return {"id": sid, "name": name, "ticker": ticker, "country": country}


def test_current_watchlist_is_fully_mapped():
    """실제 설정 파일이 항상 이 점검을 통과해야 한다 — 종목을 추가하고 매핑을
    빠뜨리면 여기서 바로 실패한다."""
    stocks = json.loads((_ROOT / "config" / "watchlist.json").read_text(encoding="utf-8"))["stocks"]
    assert validate_watchlist(stocks) == []


def test_unmapped_stock_is_reported():
    probs = validate_watchlist([_stock("US_AMD", "AMD", "US", "AMD")], symbol_map={})
    assert len(probs) == 1 and "YFINANCE_MAP" in probs[0] and "AMD" in probs[0]


def test_kr_stock_needs_exchange_suffix():
    probs = validate_watchlist(
        [_stock("KR_042700", "042700", "KR")],
        symbol_map={"KR_042700": ("042700", "한미반도체", "KRW")},
    )
    assert any(".KS/.KQ" in p for p in probs)


def test_kosdaq_suffix_is_accepted():
    assert validate_watchlist(
        [_stock("KR_138080", "138080", "KR")],
        symbol_map={"KR_138080": ("138080.KQ", "오이솔루션", "KRW")},
    ) == []


def test_foreign_exchange_listing_is_flagged():
    """도쿄 상장 종목은 미국 거래일 기준으로 수집돼 날짜가 어긋난다."""
    probs = validate_watchlist(
        [_stock("JP_7203", "7203", "JP")],
        symbol_map={"JP_7203": ("7203.T", "도요타", "JPY")},
    )
    assert any("미국 외 거래소" in p for p in probs)


def test_us_listed_adr_from_another_country_is_fine():
    """TSMC는 대만 기업이지만 NYSE ADR이라 미국 거래일 기준이 맞다."""
    assert validate_watchlist(
        [_stock("TW_TSM", "TSM", "TW")],
        symbol_map={"TW_TSM": ("TSM", "TSMC", "USD")},
    ) == []


def test_country_and_id_prefix_must_agree():
    probs = validate_watchlist(
        [_stock("US_005930", "005930", "KR")],
        symbol_map={"US_005930": ("005930.KS", "삼성전자", "KRW")},
    )
    assert any("ID 접두사" in p for p in probs)


def test_ticker_mismatch_is_reported():
    probs = validate_watchlist(
        [_stock("US_NVDA", "NVDA", "US")],
        symbol_map={"US_NVDA": ("AMD", "NVIDIA", "USD")},
    )
    assert any("다름" in p for p in probs)
