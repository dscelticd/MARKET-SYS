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
from app.collectors.kis_collector import KISCollector
from app.utils.market_calendar import market_session_state, now_kst

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_THEME_UNIVERSE_FILE = _PROJECT_ROOT / "config" / "theme_universe.json"
_kis_collector = KISCollector()


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


def scan_theme_strength(
    use_mock: bool = False, target: dict[str, str] | None = None
) -> list[dict]:
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
            ticker = theme["ticker"]
            hist = yf.Ticker(ticker).history(period="20d", auto_adjust=True)
            # 테마 유니버스에는 미국 ETF(XLK 등)와 국내 ETF(305720.KS 등)가
            # 섞여 있다. 미국 기준일을 일괄 적용하면 국내 ETF가 하루 어긋난다 —
            # 두 시장의 대상 거래일은 휴장·개장 시각 때문에 자주 갈린다.
            is_kr = ticker.endswith((".KS", ".KQ"))
            market = "KR" if is_kr else "US"
            target_date = (target or {}).get(market)
            if target_date is not None and hist is not None and not hist.empty:
                # 계약 C2 — 장중 봉이 테마 등락률로 잡히면 본문 수치와 어긋난다
                def _d(ix):
                    try:
                        return ix.date().isoformat()
                    except AttributeError:
                        return str(ix)[:10]
                hist = hist.iloc[[i for i, ix in enumerate(hist.index) if _d(ix) <= target_date]]
            close = hist["Close"].dropna()

            price = prev = change_pct = data_date = None
            if len(close) >= 2:
                price = float(close.iloc[-1])
                prev = float(close.iloc[-2])
                change_pct = round((price - prev) / prev * 100, 2) if prev else 0.0
                try:
                    data_date = close.index[-1].date().isoformat()
                except AttributeError:
                    data_date = str(close.index[-1])[:10]

            # 국내 ETF는 개별 종목·지수와 같은 이유로 KIS를 우선한다 — 마감
            # 직후 몇 분간 yfinance 종가가 정산 중이라 대상일과 날짜가
            # 일치해도 값 자체가 아직 확정 전일 수 있다(price_collector에서
            # 실측: 2026-09-18 15:48 삼성전자 +2.87%→5분 뒤 +3.37%로 정정).
            # target_date와 무관하게 항상 시도하고, 장중이면 보류한다.
            kr_session_open = (
                target_date == now_kst().strftime("%Y-%m-%d")
                and market_session_state("KR") != "마감"
            )
            if is_kr and target_date and not kr_session_open and _kis_collector.is_configured():
                try:
                    display_ticker = ticker.replace(".KS", "").replace(".KQ", "")
                    kis = _kis_collector.fetch_stock_price(display_ticker, target_date=target_date)
                    price = float(kis["value"])
                    change_pct = float(kis["change_pct"])
                    data_date = kis["data_date"]
                    logger.info("%s: KIS 확정 종가로 대상 거래일 %s 값 사용",
                               theme.get("id"), target_date)
                except Exception as e:
                    logger.debug("%s: KIS 테마 ETF 조회 실패 → yfinance 값 사용: %s",
                                theme.get("id"), e)

            if price is None or change_pct is None:
                continue
            # 이 등락률이 실제로 어느 거래일 것인지 — 주말에 금요일 등락을
            # "당일 등락률"로 보고하던 문제를 막기 위해 기준일을 함께 남긴다
            results.append({
                **theme, "change_pct": change_pct, "price": round(price, 2),
                "data_date": data_date, "_mock": False,
            })
        except Exception as e:
            logger.debug("테마 ETF 수집 실패 (%s/%s): %s", theme.get("id"), theme.get("ticker"), e)
            continue

    return sorted(results, key=lambda x: -x["change_pct"])
