"""
Theme Scanner — 워치리스트 밖 시장 전체 섹터/테마 강약 스캔

기존 종목별 심층분석(기술적지표·지지저항·수급·공시)과 달리, config/theme_universe.json에
정의된 섹터/테마 ETF의 가격·등락률만 가볍게 조회해 "오늘 어떤 테마가 강세/약세인가"를
진단하는 참고 정보 계층. 워치리스트에 없는 테마(2차전지·바이오·방산 등)까지 포함해
정보 사각지대를 줄이는 용도 — 매수 후보 추천이나 자동 종목 승격 기능은 아님.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime
from pathlib import Path
from app.utils.market_calendar import now_kst

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_THEME_UNIVERSE_FILE = _PROJECT_ROOT / "config" / "theme_universe.json"


def _load_theme_universe() -> list[dict]:
    if not _THEME_UNIVERSE_FILE.exists():
        return []
    try:
        data = json.loads(_THEME_UNIVERSE_FILE.read_text(encoding="utf-8"))
        return data.get("themes", [])
    except Exception as e:
        logger.warning("테마 유니버스 로드 실패: %s", e)
        return []


def _scan_mock(universe: list[dict]) -> list[dict]:
    from app.utils.market_calendar import is_trading_day, previous_trading_day
    today = now_kst().date()
    data_date = (today if is_trading_day(today) else previous_trading_day(today)).isoformat()
    return [
        {**t, "change_pct": round(random.uniform(-4.0, 4.0), 2), "price": None,
         "data_date": data_date, "_mock": True}
        for t in universe
    ]


def scan_theme_strength(use_mock: bool = False) -> list[dict]:
    """테마 유니버스 각 ETF의 당일 등락률을 조회해 강한 순으로 정렬 반환.
    반환 항목: {id, name, ticker, market, change_pct, price, _mock}
    개별 종목 실패는 건너뛰고 계속 진행 — 이 기능 전체가 보조 진단이라 하나의
    ETF 조회 실패가 리포트 생성을 막아서는 안 됨.
    """
    universe = _load_theme_universe()
    if not universe:
        return []

    if use_mock:
        return sorted(_scan_mock(universe), key=lambda x: -x["change_pct"])

    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance 미설치 — 테마 스캔 건너뜀")
        return []

    results = []
    for theme in universe:
        try:
            hist = yf.Ticker(theme["ticker"]).history(period="5d", auto_adjust=True)
            close = hist["Close"].dropna()
            if len(close) < 2:
                continue
            price = float(close.iloc[-1])
            prev = float(close.iloc[-2])
            change_pct = round((price - prev) / prev * 100, 2) if prev else 0.0
            # 이 등락률이 실제로 어느 거래일 것인지 — 주말에 금요일 등락을
            # "당일 등락률"로 보고하던 문제를 막기 위해 기준일을 함께 남긴다
            try:
                data_date = close.index[-1].date().isoformat()
            except AttributeError:
                data_date = str(close.index[-1])[:10]
            results.append({
                **theme, "change_pct": change_pct, "price": round(price, 2),
                "data_date": data_date, "_mock": False,
            })
        except Exception as e:
            logger.debug("테마 ETF 수집 실패 (%s/%s): %s", theme.get("id"), theme.get("ticker"), e)
            continue

    return sorted(results, key=lambda x: -x["change_pct"])
