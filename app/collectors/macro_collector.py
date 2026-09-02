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
from app.utils.market_calendar import now_kst

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

# 심볼이 어느 시장의 거래일을 따르는지.
# 국내 지수만 KR이고 나머지는 US로 본다 — 환율·원자재는 사실상 24시간 거래라
# 고유한 "세션"이 없으므로, 미국 대상 거래일에 맞춰 일관되게 자른다.
_KR_SYMBOLS = {"^KS11", "^KQ11"}


def _symbol_market(sym: str) -> str:
    return "KR" if sym in _KR_SYMBOLS else "US"


def _bar_date_of(index_value) -> str | None:
    try:
        return index_value.date().isoformat()
    except AttributeError:
        try:
            return str(index_value)[:10]
        except Exception:
            return None


def _truncate_hist(hist, target_date: str):
    """대상 거래일 이후의 봉을 잘라낸다 (계약 C2)."""
    keep = [
        i for i, ix in enumerate(hist.index)
        if (d := _bar_date_of(ix)) is not None and d <= target_date
    ]
    return hist.iloc[keep]


def _next_meeting_date(dates: list[str]) -> str:
    """오늘 이후 가장 가까운 회의 날짜를 반환"""
    today = now_kst().strftime("%Y-%m-%d")
    for d in sorted(dates):
        if d >= today:
            return d
    return dates[-1]


def get_upcoming_policy_meetings(days_ahead: int = 14) -> list[dict]:
    """오늘부터 days_ahead일 이내의 FOMC/한국은행 금통위 일정을 반환.
    calendar_collector.py가 이벤트 캘린더 구성 시 재사용한다."""
    from datetime import timedelta
    today = now_kst().date()
    end = today + timedelta(days=days_ahead)

    events: list[dict] = []
    for date_str in _FOMC_ALL:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        if today <= d <= end:
            events.append({
                "date": date_str, "category": "policy", "country": "US",
                "title": "FOMC 회의(미국 기준금리 결정)", "source": "static",
            })
    for date_str in _BOK_ALL:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        if today <= d <= end:
            events.append({
                "date": date_str, "category": "policy", "country": "KR",
                "title": "한국은행 금융통화위원회(기준금리 결정)", "source": "static",
            })
    return sorted(events, key=lambda e: e["date"])

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

    def collect(self, target: dict[str, str] | None = None) -> dict:
        if self.use_mock:
            logger.info("MacroCollector: Mock 모드")
            return self._collect_mock()
        logger.info("MacroCollector: 실제 데이터 모드 (yfinance)")
        return self._collect_real(target)

    # ── 실제 데이터 ──────────────────────────────────────────────────────────

    def _collect_real(self, target: dict[str, str] | None = None) -> dict:
        try:
            import yfinance as yf
            import pandas as pd
        except ImportError:
            logger.warning("yfinance 미설치 → Mock 폴백")
            return self._collect_mock()

        # 개별 티커 히스토리 캐시
        _cache: dict[str, object] = {}

        def _get_hist(sym: str):
            """대상 거래일까지의 봉만 돌려준다 (계약 C2).

            여기가 단일 관문이라, 자르는 것도 여기서 한 번만 하면
            last_close·prev_close_val·chg_pct·bar_date가 모두 같은 기준일 위에
            놓인다. 가격 수집기만 고치고 이곳을 놓쳤던 탓에, 대상 세션이 9/1인
            리포트에 KOSPI가 9/2 장중값(-3.00%)으로 실린 적이 있다.

            기간을 30d로 잡은 이유: 설·추석 연휴 뒤에는 10일치를 잘라내면
            비교용 직전 봉(iloc[-2])이 남지 않을 수 있다.
            """
            if sym not in _cache:
                try:
                    hist = yf.Ticker(sym).history(period="30d", auto_adjust=True)
                    tdate = (target or {}).get(_symbol_market(sym))
                    if tdate is not None and hist is not None and not hist.empty:
                        hist = _truncate_hist(hist, tdate)
                    _cache[sym] = hist
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

        def bar_date(sym: str) -> str | None:
            """이 지수 값이 실제로 언제 종가인지. 주말·휴장일에 직전 거래일 값을
            '오늘'로 오인하는 문제를 막기 위해 기록한다 — 지수마다 yfinance 반영
            시점이 달라(KOSPI=목, 삼성전자=금 관측) 심볼별로 따로 남긴다."""
            hist = _get_hist(sym)
            if hist is None or getattr(hist, "empty", True) or "Close" not in hist.columns:
                return None
            s = hist["Close"].dropna()
            if s.empty:
                return None
            try:
                return s.index[-1].date().isoformat()
            except AttributeError:
                return str(s.index[-1])[:10]

        # ── 미국 시장 ──
        # yfinance의 "^" 지수 티커는 거래일을 통째로 누락하는 일이 있다.
        # 실측: 2026-08-31 저녁 실행에서 ^GSPC·^IXIC·^SOX가 8/27에 멈춰 8/28(금)을
        # 빠뜨린 채 응답했고, 같은 시점 개별 종목은 정상이었다. 그 결과 리포트가
        # 나흘 전 등락률을 당일 시장 흐름으로 서술했다.
        # 반면 ETF(SPY·QQQ·SOXX)는 일반 티커라 같은 문제가 관측되지 않았다.
        # 따라서 ETF를 신선도 판정 기준으로 삼아, 지수가 더 과거면 ETF로 대체한다.
        def index_or_proxy(index_sym: str, etf_sym: str, digits: int = 1) -> dict:
            idx_date, etf_date = bar_date(index_sym), bar_date(etf_sym)
            use_proxy = bool(idx_date and etf_date and etf_date > idx_date)
            if use_proxy:
                logger.warning(
                    "%s 지수 피드 지연(%s) — ETF %s(%s)로 대체",
                    index_sym, idx_date, etf_sym, etf_date,
                )
                val = last_close(etf_sym)
                return {
                    "value": round(val, 2) if val else "N/A",
                    "change_pct": chg_pct(etf_sym),
                    "_source": "etf_proxy",
                    "_proxy_ticker": etf_sym,
                    "_index_stale_date": idx_date,
                }
            val = last_close(index_sym)
            return {"value": round(val, digits) if val else "N/A",
                    "change_pct": chg_pct(index_sym)}

        dow = last_close("^DJI")
        vix = last_close("^VIX") or 18.0

        us_market = {
            "SP500":  index_or_proxy("^GSPC", "SPY"),
            "NASDAQ": index_or_proxy("^IXIC", "QQQ"),
            "SOX":    index_or_proxy("^SOX",  "SOXX"),
            "DOW":    {"value": round(dow, 1) if dow else "N/A", "change_pct": chg_pct("^DJI")},
            "VIX":    {
                "value": round(vix, 2),
                "signal": "low" if vix < 16 else ("medium" if vix < 22 else "high"),
            },
        }
        # 실제로 사용한 소스의 기준일을 남긴다
        us_index_dates = {
            "SP500":  bar_date("SPY")  if us_market["SP500"].get("_source")  else bar_date("^GSPC"),
            "NASDAQ": bar_date("QQQ")  if us_market["NASDAQ"].get("_source") else bar_date("^IXIC"),
            "SOX":    bar_date("SOXX") if us_market["SOX"].get("_source")    else bar_date("^SOX"),
        }

        # ── 한국 시장 ──
        kospi  = last_close("^KS11")
        kosdaq = last_close("^KQ11")
        kospi_chg = chg_pct("^KS11")
        # 시장 전체 외국인·기관 순매수 필드는 제거했다.
        # 이전에는 `kospi_chg * random.uniform(150, 400)`으로 만든 난수를 담았고,
        # 추정 플래그가 프롬프트에 전달되지 않아 리포트가 "외국인 순매도(-1,029억)"처럼
        # 사실로 서술했다(같은 데이터로 3회 실행 시 -279억/-292억/-223억로 재현 불가).
        # 부호가 항상 KOSPI 방향과 같아 등락률 이상의 정보도 없었다.
        # KIS의 시장별 투자자매매동향(FHPTJ04040000)은 모의투자에서 전 필드가 0으로
        # 내려와(300행×30필드 전수 확인) 실데이터 대체가 불가능하다.
        # 종목별 수급은 KIS 실측값이 price_collector 경로로 이미 제공된다.
        kr_market = {
            "KOSPI":  {"value": round(kospi, 2)  if kospi  else "N/A", "change_pct": kospi_chg},
            "KOSDAQ": {"value": round(kosdaq, 2) if kosdaq else "N/A", "change_pct": chg_pct("^KQ11")},
        }

        # ── 국내 지수는 KIS 공식 API를 1순위로 사용 ──
        # yfinance의 ^KS11·^KQ11 피드가 거래일을 통째로 누락하는 사고가 반복됐다.
        # 실측(2026-09-01): ^KS11이 8/27에 멈춰 8/28·8/31을 빠뜨린 채 "6912.37(+1.53%)"로
        # 응답했으나 실제 8/31 종가는 6820.02(-0.49~+0.46% 구간)였고, 같은 시점
        # 삼성전자 등 개별 종목은 8/31까지 정상이었다. 지수만 어긋난 탓에 리포트가
        # "미국 하락 + 한국 상승 디커플링"이라는 실재하지 않는 서사를 만들었다.
        # KIS 실패 시에는 위에서 만든 yfinance 값을 그대로 유지한다(비치명적).
        kr_index_dates: dict[str, str] = {}
        try:
            from app.collectors.kis_collector import KISCollector
            kis = KISCollector()
            if kis.is_configured():
                for name in ("KOSPI", "KOSDAQ"):
                    try:
                        idx = kis.fetch_market_index(name, target_date=(target or {}).get("KR"))
                        kr_market[name] = {
                            "value": idx["value"],
                            "change_pct": idx["change_pct"],
                            "_source": "kis",
                        }
                        kr_index_dates[name] = idx["data_date"]
                        if name == "KOSPI":
                            kospi_chg = idx["change_pct"]
                    except Exception as e:
                        logger.warning("KIS %s 조회 실패 → yfinance 값 유지: %s", name, e)
        except Exception as e:
            logger.warning("KIS 지수 연동 불가 → yfinance 값 유지: %s", e)

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
        # SOX 값·등락률은 위에서 ETF 대체가 적용됐을 수 있으므로 us_market의 최종값을 쓴다
        # (지수 피드가 지연된 날 심리 지표만 옛 등락률로 산출되면 서로 어긋난다).
        _sox_entry = us_market.get("SOX", {})
        _sox_val = _sox_entry.get("value")
        _sox_val = _sox_val if isinstance(_sox_val, (int, float)) else None
        _sox_chg = _sox_entry.get("change_pct")
        _sox_chg = _sox_chg if isinstance(_sox_chg, (int, float)) else 0.0
        sentiment = self._derive_sentiment(vix, _sox_val, _sox_chg, chg_pct("NVDA"))
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
            "timestamp": now_kst().isoformat(),
            # 주요 지수가 실제로 언제 종가인지 — 리포트가 휴장일 데이터를 "오늘"로
            # 서술하지 않도록 프롬프트에 그대로 전달된다
            "data_dates": {
                "SP500":  us_index_dates["SP500"],
                "NASDAQ": us_index_dates["NASDAQ"],
                "SOX":    us_index_dates["SOX"],
                # KIS로 받아온 경우 그 기준일을, 실패해 yfinance를 쓰는 경우 yfinance 기준일을 남긴다
                "KOSPI":  kr_index_dates.get("KOSPI")  or bar_date("^KS11"),
                "KOSDAQ": kr_index_dates.get("KOSDAQ") or bar_date("^KQ11"),
            },
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
            "timestamp": now_kst().isoformat(),
            "_mock": True,
        }
