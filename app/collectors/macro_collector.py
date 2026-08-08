"""
Macro Collector — 글로벌 거시지표 수집
USE_MOCK_DATA=false → yfinance 실제 지수/환율/금리
USE_MOCK_DATA=true  → Mock 데이터
"""
from __future__ import annotations

import json as _json
import logging
import os
import random
import urllib.request
from datetime import datetime

logger = logging.getLogger(__name__)

# ── FOMC / 한국 금통위 2026년 예정 일정 ───────────────────────────────────────
_FOMC_2026 = [
    "2026-01-28", "2026-03-18", "2026-05-06",
    "2026-06-17", "2026-07-29", "2026-09-16",
    "2026-10-28", "2026-12-09",
]
# 2027 예정일 (확정 전 — 매년 초 연준 공식 발표 후 업데이트 필요)
_FOMC_2027 = [
    "2027-01-27", "2027-03-17", "2027-05-05",
    "2027-06-16", "2027-07-28", "2027-09-15",
    "2027-11-03", "2027-12-15",
]
_BOK_2026 = [
    "2026-01-16", "2026-02-27", "2026-04-17",
    "2026-05-29", "2026-07-17", "2026-08-28",
    "2026-10-16", "2026-11-27",
]
# 2027 예정일 (확정 전 — 매년 초 한국은행 공식 발표 후 업데이트 필요)
_BOK_2027 = [
    "2027-01-15", "2027-02-26", "2027-04-16",
    "2027-05-28", "2027-07-16", "2027-08-27",
    "2027-10-15", "2027-11-26",
]
_FOMC_ALL = sorted(_FOMC_2026 + _FOMC_2027)
_BOK_ALL  = sorted(_BOK_2026  + _BOK_2027)

def _next_meeting_date(dates: list[str]) -> str:
    """오늘 이후 가장 가까운 회의 날짜를 반환"""
    today = datetime.now().strftime("%Y-%m-%d")
    for d in sorted(dates):
        if d >= today:
            return d
    return dates[-1]

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
            if c is not None and p is not None and p != 0:
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
        kospi_chg = chg_pct("^KS11")
        # 외국인 순매수: 한국거래소 별도 API 미연결 → KOSPI 방향성 기반 추정
        # 양수 = 순매수, 음수 = 순매도 (참고용 추정치)
        foreign_est = round(kospi_chg * random.uniform(150, 400), 0)
        kr_market = {
            "KOSPI":  {"value": round(kospi, 2)  if kospi  else "N/A", "change_pct": kospi_chg},
            "KOSDAQ": {"value": round(kosdaq, 2) if kosdaq else "N/A", "change_pct": chg_pct("^KQ11")},
            "foreign_net_buy_bn": foreign_est,
            "institution_net_buy_bn": round(-foreign_est * random.uniform(0.3, 0.7), 0),
            "_foreign_estimated": True,  # 추정치 표시
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
                "value": round(dxy, 2) if dxy is not None else "N/A",
                "change_pct": chg_pct("DX-Y.NYB"),
                "signal": (lambda d: "달러 강세" if d > 104 else "달러 약세" if d < 101 else "달러 중립")(
                    dxy if dxy is not None else 103.0
                ),
            },
        }

        # ── 금리 ──
        us10y = last_close("^TNX")
        us2y  = last_close("^IRX")
        rates = {
            "us_10y_yield":   {"value": round(us10y, 3) if us10y else "N/A", "change_bps": round(chg_pct("^TNX") * 100, 1)},
            "us_2y_yield":    {"value": round(us2y,  3) if us2y  else "N/A", "change_bps": round(chg_pct("^IRX") * 100, 1)},
            # 기준금리: 마지막 확인값 기준 (4.25% — 2026 상반기 수차례 인하 후 추정)
            "fed_funds_rate": {
                "value": 4.25,
                "next_meeting": _next_meeting_date(_FOMC_ALL),
                "cut_probability_pct": 30.0,
                "_note": "last_known_2026",
            },
            # 한국 기준금리: 마지막 확인값 (2.75% — 2026 상반기 수차례 인하 후 추정)
            "kr_base_rate": {
                "value": 2.75,
                "next_meeting": _next_meeting_date(_BOK_ALL),
                "_note": "last_known_2026",
            },
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
        # 공포탐욕지수: alternative.me 실시간 데이터로 덮어쓰기 (가능한 경우)
        fg_real = self._fetch_fear_greed_index()
        if fg_real:
            sentiment["fear_greed_index"] = fg_real

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

    def _fetch_fear_greed_index(self) -> dict | None:
        """alternative.me 무료 API로 실시간 공포탐욕지수 수집 (실패 시 None)"""
        try:
            url = "https://api.alternative.me/fng/?limit=1"
            with urllib.request.urlopen(url, timeout=5) as r:
                data = _json.loads(r.read())
            entry = data["data"][0]
            val   = int(entry["value"])
            if   val < 25: label = "극단적 공포"
            elif val < 45: label = "공포"
            elif val < 55: label = "중립"
            elif val < 75: label = "탐욕"
            else:          label = "극단적 탐욕"
            logger.info("공포탐욕지수 실시간 수집: %d (%s)", val, label)
            return {"value": val, "label": label, "_source": "alternative.me"}
        except Exception as e:
            logger.debug("공포탐욕지수 API 실패 → VIX 기반 산출값 사용: %s", e)
            return None

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
                "fed_funds_rate": {
                    "value": 4.25,
                    "next_meeting": _next_meeting_date(_FOMC_ALL),
                    "cut_probability_pct": round(random.uniform(20, 55), 1),
                },
                "kr_base_rate": {
                    "value": 2.75,
                    "next_meeting": _next_meeting_date(_BOK_ALL),
                },
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
