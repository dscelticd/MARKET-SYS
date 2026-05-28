"""
Price Collector — 종목 가격 데이터 수집
USE_MOCK_DATA=false → yfinance 실제 주가
USE_MOCK_DATA=true  → Mock 데이터 (기본값)
교체 포인트: _collect_real() 내부를 다른 API(KIS, Alpha Vantage 등)로 교체 가능
"""
from __future__ import annotations

import logging
import os
import random
from datetime import datetime

logger = logging.getLogger(__name__)

# yfinance 심볼 매핑 (stock_id → yfinance 심볼, 한국명, 통화)
YFINANCE_MAP: dict[str, tuple[str, str, str]] = {
    "KR_005930": ("005930.KS", "삼성전자",          "KRW"),
    "KR_000660": ("000660.KS", "SK하이닉스",         "KRW"),
    "KR_010120": ("010120.KS", "LS ELECTRIC",        "KRW"),
    "KR_267260": ("267260.KS", "HD현대일렉트릭",      "KRW"),
    "KR_012450": ("012450.KS", "한화에어로스페이스",   "KRW"),
    "US_NVDA":   ("NVDA",      "NVIDIA",              "USD"),
    "US_AMD":    ("AMD",       "AMD",                 "USD"),
    "TW_TSM":    ("TSM",       "TSMC",                "USD"),
    "NL_ASML":   ("ASML",      "ASML",                "USD"),
    "US_MSFT":   ("MSFT",      "Microsoft",           "USD"),
    "US_GOOGL":  ("GOOGL",     "Alphabet",            "USD"),
    "US_TSLA":   ("TSLA",      "Tesla",               "USD"),
}

# Mock 기준 가격 (실제 API 연결 전 테스트용)
_BASE_PRICES: dict[str, dict] = {
    "KR_005930": {"ticker": "005930", "name": "삼성전자",          "base": 75000,   "currency": "KRW", "market_cap_b": 448000},
    "KR_000660": {"ticker": "000660", "name": "SK하이닉스",        "base": 210000,  "currency": "KRW", "market_cap_b": 153000},
    "KR_010120": {"ticker": "010120", "name": "LS ELECTRIC",       "base": 320000,  "currency": "KRW", "market_cap_b": 7800},
    "KR_267260": {"ticker": "267260", "name": "HD현대일렉트릭",     "base": 410000,  "currency": "KRW", "market_cap_b": 9600},
    "KR_012450": {"ticker": "012450", "name": "한화에어로스페이스",  "base": 680000,  "currency": "KRW", "market_cap_b": 28000},
    "US_NVDA":   {"ticker": "NVDA",   "name": "NVIDIA",            "base": 135.0,   "currency": "USD", "market_cap_b": 3310},
    "US_AMD":    {"ticker": "AMD",    "name": "AMD",               "base": 158.0,   "currency": "USD", "market_cap_b": 255},
    "TW_TSM":    {"ticker": "TSM",    "name": "TSMC",              "base": 192.0,   "currency": "USD", "market_cap_b": 995},
    "NL_ASML":   {"ticker": "ASML",   "name": "ASML",              "base": 780.0,   "currency": "USD", "market_cap_b": 307},
    "US_MSFT":   {"ticker": "MSFT",   "name": "Microsoft",         "base": 440.0,   "currency": "USD", "market_cap_b": 3270},
    "US_GOOGL":  {"ticker": "GOOGL",  "name": "Alphabet",          "base": 175.0,   "currency": "USD", "market_cap_b": 2170},
    "US_TSLA":   {"ticker": "TSLA",   "name": "Tesla",             "base": 248.0,   "currency": "USD", "market_cap_b": 794},
}


class PriceCollector:
    def __init__(self) -> None:
        self.use_mock = os.getenv("USE_MOCK_DATA", "true").lower() == "true"

    def collect(self, stock_ids: list[str] | None = None) -> dict[str, dict]:
        targets = stock_ids or list(_BASE_PRICES.keys())
        if self.use_mock:
            logger.info("PriceCollector: Mock 모드")
            return self._collect_mock(targets)
        logger.info("PriceCollector: 실제 데이터 모드 (yfinance)")
        return self._collect_real(targets)

    # ── 실제 데이터 ──────────────────────────────────────────────────────────

    def _collect_real(self, stock_ids: list[str]) -> dict[str, dict]:
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance 미설치 → Mock 폴백")
            return self._collect_mock(stock_ids)

        result: dict[str, dict] = {}

        for sid in stock_ids:
            if sid not in YFINANCE_MAP:
                result.update(self._collect_mock([sid]))
                continue

            sym, name, currency = YFINANCE_MAP[sid]
            try:
                ticker = yf.Ticker(sym)
                hist   = ticker.history(period="6d", auto_adjust=True)

                close_s = hist["Close"].dropna() if "Close" in hist.columns else None
                vol_s   = hist["Volume"].dropna() if "Volume" in hist.columns else None

                if close_s is None or len(close_s) < 2:
                    raise ValueError("종가 데이터 부족")

                price      = float(close_s.iloc[-1])
                prev_close = float(close_s.iloc[-2])
                change     = price - prev_close
                change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

                volume    = int(vol_s.iloc[-1])  if (vol_s is not None and not vol_s.empty) else 0
                avg_vol   = float(vol_s.mean())  if (vol_s is not None and not vol_s.empty) else 1.0
                vol_ratio = round(volume / avg_vol, 2) if avg_vol else 1.0

                try:
                    info     = ticker.fast_info
                    high_52w = float(getattr(info, "year_high",   price * 1.4))
                    low_52w  = float(getattr(info, "year_low",    price * 0.6))
                    mktcap   = round(float(getattr(info, "market_cap", 0)) / 1e9, 1)
                except Exception:
                    high_52w, low_52w, mktcap = price * 1.4, price * 0.6, 0.0

                result[sid] = {
                    "stock_id":      sid,
                    "ticker":        sym.replace(".KS", ""),
                    "name":          name,
                    "price":         round(price, 2),
                    "prev_close":    round(prev_close, 2),
                    "change":        round(change, 2),
                    "change_pct":    change_pct,
                    "volume":        volume,
                    "avg_volume_5d": int(avg_vol),
                    "volume_ratio":  vol_ratio,
                    "high_52w":      round(high_52w, 2),
                    "low_52w":       round(low_52w,  2),
                    "market_cap_b":  mktcap,
                    "currency":      currency,
                    "timestamp":     datetime.now().isoformat(),
                    "_mock":         False,
                }
            except Exception as e:
                logger.warning(f"{sid}({sym}) 실제 데이터 실패: {e} → Mock 폴백")
                result.update(self._collect_mock([sid]))

        return result

    # ── Mock 데이터 ──────────────────────────────────────────────────────────

    def _collect_mock(self, stock_ids: list[str]) -> dict[str, dict]:
        result: dict[str, dict] = {}
        timestamp = datetime.now().isoformat()
        for sid in stock_ids:
            if sid not in _BASE_PRICES:
                continue
            bi  = _BASE_PRICES[sid]
            chg = round(random.uniform(-4.0, 4.0), 2)
            price = round(bi["base"] * (1 + chg / 100), 2)
            avg_vol   = random.randint(3_000_000, 50_000_000)
            vol_ratio = round(random.uniform(0.5, 2.5), 2)
            result[sid] = {
                "stock_id": sid,
                "ticker": bi["ticker"],
                "name": bi["name"],
                "price": price,
                "prev_close": bi["base"],
                "change": round(price - bi["base"], 2),
                "change_pct": chg,
                "volume": int(avg_vol * vol_ratio),
                "avg_volume_5d": avg_vol,
                "volume_ratio": vol_ratio,
                "high_52w": round(bi["base"] * 1.45, 2),
                "low_52w":  round(bi["base"] * 0.60, 2),
                "market_cap_b": bi["market_cap_b"],
                "currency": bi["currency"],
                "timestamp": timestamp,
                "_mock": True,
            }
        return result
