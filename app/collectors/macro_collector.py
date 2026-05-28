"""
Macro Collector — 글로벌 거시지표 수집
USE_MOCK_DATA=false → yfinance 실제 지수/환율/금리
USE_MOCK_DATA=true  → Mock 데이터
"""
from __future__ import annotations

import logging
import os
import random
from datetime import datetime

logger = logging.getLogger(__name__)

# yfinance 심볼 정의
_MACRO_SYMBOLS = {
    "SP500":   "^GSPC",
    "NASDAQ":  "^IXIC",
    "SOX":     "^SOX",
    "DOW":     "^DJI",
    "KOSPI":   "^KS11",
    "KOSDAQ":  "^KQ11",
    "VIX":     "^VIX",
    "US10Y":   "^TNX",
    "US2Y":    "^IRX",
    "USD_KRW": "KRW=X",
    "USD_TWD": "TWD=X",
    "EUR_USD": "EURUSD=X",
    "DXY":     "DX-Y.NYB",
    "WTI":     "CL=F",
    "GOLD":    "GC=F",
    "COPPER":  "HG=F",
    "NVDA":    "NVDA",   # AI CapEx 사이클 신호용
    "SOX_ETF": "SOXX",  # 반도체 사이클 신호용
}


class MacroCollector:
    def __init__(self) -> None:
        self.use_mock = os.getenv("USE_MOCK_DATA", "true").lower() == "true"

    def collect(self) -> dict:
        if self.use_mock:
            logger.info("MacroCollector: Mock 모드")
            return self._collect_mock()
        logger.info("MacroCollector: 실제 데이터 모드 (yfinance)")
        return self._collect_real()

    # ── 실제 데이터 ──────────────────────────────────────────────────────────

    def _collect_real(self) -> dict:
        try:
            import yfinance as yf
            import pandas as pd
        except ImportError:
            logger.warning("yfinance 미설치 → Mock 폴백")
            return self._collect_mock()

        # 개별 티커 히스토리 캐시
        _cache: dict[str, object] = {}

        def _get_hist(sym: str):
            if sym not in _cache:
                try:
                    _cache[sym] = yf.Ticker(sym).history(period="10d", auto_adjust=True)
                except Exception:
                    _cache[sym] = None
            return _cache[sym]

        def last_close(sym: str) -> float | None:
            hist = _get_hist(sym)
            if hist is None or hist.empty or "Close" not in hist.columns:
                return None
            s = hist["Close"].dropna()
            return float(s.iloc[-1]) if not s.empty else None

        def prev_close_val(sym: str) -> float | None:
            hist = _get_hist(sym)
            if hist is None or hist.empty or "Close" not in hist.columns:
                return None
            s = hist["Close"].dropna()
            return float(s.iloc[-2]) if len(s) >= 2 else None

        def chg_pct(sym: str) -> float:
            c, p = last_close(sym), prev_close_val(sym)
            if c and p and p != 0:
                return round((c - p) / p * 100, 2)
            return 0.0

        # ── 미국 시장 ──
        sp  = last_close("^GSPC")
        ndq = last_close("^IXIC")
        sox = last_close("^SOX")
        dow = last_close("^DJI")
        vix = last_close("^VIX") or 18.0

        us_market = {
            "SP500":  {"value": round(sp, 1) if sp else "N/A",  "change_pct": chg_pct("^GSPC")},
            "NASDAQ": {"value": round(ndq, 1) if ndq else "N/A","change_pct": chg_pct("^IXIC")},
            "SOX":    {"value": round(sox, 1) if sox else "N/A", "change_pct": chg_pct("^SOX")},
            "DOW":    {"value": round(dow, 1) if dow else "N/A", "change_pct": chg_pct("^DJI")},
            "VIX":    {
                "value": round(vix, 2),
                "signal": "low" if vix < 16 else ("medium" if vix < 22 else "high"),
            },
        }

        # ── 한국 시장 ──
        kospi  = last_close("^KS11")
        kosdaq = last_close("^KQ11")
        kr_market = {
            "KOSPI":  {"value": round(kospi, 2)  if kospi  else "N/A", "change_pct": chg_pct("^KS11")},
            "KOSDAQ": {"value": round(kosdaq, 2) if kosdaq else "N/A", "change_pct": chg_pct("^KQ11")},
            "foreign_net_buy_bn": 0,   # 실시간 수급은 별도 API 필요
            "institution_net_buy_bn": 0,
        }

        # ── 환율 ──
        usd_krw = last_close("KRW=X")
        usd_twd = last_close("TWD=X")
        eur_usd = last_close("EURUSD=X")
        dxy     = last_close("DX-Y.NYB")
        currencies = {
            "USD_KRW": {"value": round(usd_krw, 1) if usd_krw else "N/A", "change_pct": chg_pct("KRW=X")},
            "USD_TWD": {"value": round(usd_twd, 2) if usd_twd else "N/A", "change_pct": chg_pct("TWD=X")},
            "EUR_USD": {"value": round(eur_usd, 4) if eur_usd else "N/A", "change_pct": chg_pct("EURUSD=X")},
            "DXY":     {
                "value": round(dxy, 2) if dxy else "N/A",
                "change_pct": chg_pct("DX-Y.NYB"),
                "signal": "달러 강세" if (dxy or 103) > 104 else "달러 약세" if (dxy or 103) < 101 else "달러 중립",
            },
        }

        # ── 금리 ──
        us10y = last_close("^TNX")
        us2y  = last_close("^IRX")
        rates = {
            "us_10y_yield":   {"value": round(us10y, 3) if us10y else "N/A", "change_bps": round(chg_pct("^TNX") * 100, 1)},
            "us_2y_yield":    {"value": round(us2y,  3) if us2y  else "N/A", "change_bps": round(chg_pct("^IRX") * 100, 1)},
            "fed_funds_rate": {"value": 5.25, "next_meeting": "2025-07-30", "cut_probability_pct": 35.0},
            "kr_base_rate":   {"value": 3.25, "next_meeting": "2025-07-11"},
        }

        # ── 원자재 ──
        wti    = last_close("CL=F")
        gold   = last_close("GC=F")
        copper = last_close("HG=F")
        commodities = {
            "WTI_oil":   {"value": round(wti, 2)    if wti    else "N/A", "change_pct": chg_pct("CL=F")},
            "gold":      {"value": round(gold, 1)   if gold   else "N/A", "change_pct": chg_pct("GC=F")},
            "copper":    {"value": round(copper, 3) if copper else "N/A", "change_pct": chg_pct("HG=F"),
                          "signal": "전력 인프라 비용 압박 모니터링"},
            "dram_spot": {"value": 2.85, "change_pct": 0.0, "signal": "메모리 반도체 수급 지표 (별도 API 필요)"},
        }

        # ── 시장 심리 ──
        sentiment = self._derive_sentiment(vix, sox, chg_pct("^SOX"), chg_pct("NVDA"))

        return {
            "us_market": us_market,
            "kr_market": kr_market,
            "currencies": currencies,
            "rates": rates,
            "commodities": commodities,
            "sentiment": sentiment,
            "timestamp": datetime.now().isoformat(),
            "_mock": False,
        }

    def _derive_sentiment(self, vix: float, sox: float | None, sox_chg: float, nvda_chg: float) -> dict:
        """시장 지표 기반 심리 신호 자동 산출"""
        # 공포탐욕 지수 (VIX 기반 역산)
        fg_value = max(0, min(100, int(100 - (vix - 10) * 4)))
        if fg_value >= 75:
            fg_label = "극단적 탐욕"
        elif fg_value >= 55:
            fg_label = "탐욕"
        elif fg_value >= 45:
            fg_label = "중립"
        elif fg_value >= 25:
            fg_label = "공포"
        else:
            fg_label = "극단적 공포"

        # 글로벌 리스크 성향 (VIX 기준)
        risk_appetite = "Risk-On" if vix < 17 else ("Risk-Off" if vix > 22 else "중립")

        # AI CapEx 사이클 (NVDA + SOX 변화율 기준)
        ai_signal = nvda_chg + sox_chg * 0.5
        if ai_signal > 3:
            ai_cycle = "강한 상승"
        elif ai_signal > 0.5:
            ai_cycle = "완만한 상승"
        elif ai_signal > -1:
            ai_cycle = "보합"
        else:
            ai_cycle = "둔화"

        # 반도체 사이클 (SOX 5일 모멘텀 기준)
        if sox_chg > 2:
            semi_cycle = "업사이클 초반"
        elif sox_chg > 0:
            semi_cycle = "업사이클 중반"
        elif sox_chg > -2:
            semi_cycle = "피크 논란"
        else:
            semi_cycle = "다운사이클"

        return {
            "fear_greed_index":    {"value": fg_value, "label": fg_label},
            "global_risk_appetite": risk_appetite,
            "ai_capex_cycle":       ai_cycle,
            "semiconductor_cycle":  semi_cycle,
        }

    # ── Mock 데이터 ──────────────────────────────────────────────────────────

    def _collect_mock(self) -> dict:
        vix = round(random.uniform(13.5, 22.0), 2)
        fear_greed = random.randint(30, 80)
        return {
            "us_market": {
                "SP500":  {"value": round(5480 + random.uniform(-80, 80), 1),  "change_pct": round(random.uniform(-1.5, 1.5), 2)},
                "NASDAQ": {"value": round(19200 + random.uniform(-300, 300), 1),"change_pct": round(random.uniform(-2.0, 2.0), 2)},
                "SOX":    {"value": round(4650 + random.uniform(-120, 120), 1), "change_pct": round(random.uniform(-2.5, 2.5), 2)},
                "DOW":    {"value": round(40500 + random.uniform(-200, 200), 1),"change_pct": round(random.uniform(-1.2, 1.2), 2)},
                "VIX":    {"value": vix, "signal": "low" if vix < 16 else ("medium" if vix < 22 else "high")},
            },
            "kr_market": {
                "KOSPI":  {"value": round(2680 + random.uniform(-60, 60), 2), "change_pct": round(random.uniform(-1.5, 1.5), 2)},
                "KOSDAQ": {"value": round(850  + random.uniform(-25, 25), 2), "change_pct": round(random.uniform(-2.0, 2.0), 2)},
                "foreign_net_buy_bn": round(random.uniform(-3000, 3000), 0),
                "institution_net_buy_bn": round(random.uniform(-2000, 2000), 0),
            },
            "currencies": {
                "USD_KRW": {"value": round(1330 + random.uniform(-25, 25), 1), "change_pct": round(random.uniform(-0.8, 0.8), 2)},
                "USD_TWD": {"value": round(31.5 + random.uniform(-0.5, 0.5), 2),"change_pct": round(random.uniform(-0.5, 0.5), 2)},
                "EUR_USD": {"value": round(1.082 + random.uniform(-0.01, 0.01), 4),"change_pct": round(random.uniform(-0.5, 0.5), 2)},
                "DXY":     {"value": round(104.2 + random.uniform(-1.5, 1.5), 2), "change_pct": round(random.uniform(-0.5, 0.5), 2),
                            "signal": "달러 강세" if random.random() > 0.5 else "달러 약세"},
            },
            "rates": {
                "us_10y_yield":   {"value": round(4.35 + random.uniform(-0.15, 0.15), 3), "change_bps": round(random.uniform(-8, 8), 1)},
                "us_2y_yield":    {"value": round(4.75 + random.uniform(-0.10, 0.10), 3), "change_bps": round(random.uniform(-6, 6), 1)},
                "fed_funds_rate": {"value": 5.25, "next_meeting": "2025-07-30", "cut_probability_pct": round(random.uniform(20, 60), 1)},
                "kr_base_rate":   {"value": 3.25, "next_meeting": "2025-07-11"},
            },
            "commodities": {
                "WTI_oil":   {"value": round(78.5 + random.uniform(-3, 3), 2),    "change_pct": round(random.uniform(-2, 2), 2)},
                "gold":      {"value": round(2380 + random.uniform(-30, 30), 1),  "change_pct": round(random.uniform(-1, 1), 2)},
                "copper":    {"value": round(4.52 + random.uniform(-0.15, 0.15), 3),"change_pct": round(random.uniform(-2, 2), 2),
                              "signal": "전력 인프라 비용 압박 모니터링"},
                "dram_spot": {"value": round(2.85 + random.uniform(-0.2, 0.2), 3), "change_pct": round(random.uniform(-3, 3), 2),
                              "signal": "메모리 반도체 수급 지표"},
            },
            "sentiment": {
                "fear_greed_index": {
                    "value": fear_greed,
                    "label": ("극단적 공포" if fear_greed < 25 else "공포" if fear_greed < 45 else
                              "중립" if fear_greed < 55 else "탐욕" if fear_greed < 75 else "극단적 탐욕"),
                },
                "global_risk_appetite": random.choice(["Risk-On", "Risk-Off", "중립"]),
                "ai_capex_cycle":       random.choice(["강한 상승", "완만한 상승", "보합", "둔화"]),
                "semiconductor_cycle":  random.choice(["업사이클 초반", "업사이클 중반", "피크 논란", "다운사이클"]),
            },
            "timestamp": datetime.now().isoformat(),
            "_mock": True,
        }
