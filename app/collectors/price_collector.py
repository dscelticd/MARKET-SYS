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
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 수급(외국인/기관/개인 순매매) 데이터 소스 — 네이버 금융 (비공식, 구조 변경 시 실패 가능 → Mock 폴백)
_NAVER_FRGN_URL = "https://finance.naver.com/item/frgn.naver"

# ETF 및 한국 종목은 야후 애널리스트 데이터 제공이 불안정 → 건너뜀
_SKIP_ANALYST_SYMBOLS  = {"QQQ", "VOO", "QTUM", "SCHD"}
_SKIP_ANALYST_SUFFIXES = (".KS", ".KQ")

# 모의 데이터에서 애널리스트 컨센서스를 추가할 US 개별 종목
_MOCK_ANALYST_SIDS = {"US_NVDA", "US_VST", "US_SNDK", "US_COHR", "US_CIEN", "US_SPCX", "TW_TSM"}


# ── 기술적 지표 헬퍼 ────────────────────────────────────────────────────────

def _calc_rsi(closes, period: int = 14) -> float:
    """RSI(period) 계산 — pandas Series 입력"""
    if len(closes) < period + 1:
        return 50.0
    delta = closes.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    denom = loss.rolling(window=period, min_periods=period).mean()
    denom = denom.where(denom != 0, other=1e-10)   # 0 나누기 방지
    rs    = gain.rolling(window=period, min_periods=period).mean() / denom
    rsi   = 100 - (100 / (1 + rs))
    valid = rsi.dropna()
    return round(float(valid.iloc[-1]), 1) if len(valid) > 0 else 50.0


def _interpret_trend(price: float, ma5, ma20, ma60, macd_hist) -> str:
    """이동평균·MACD → 추세 해석 문자열"""
    bull = 0
    bear = 0
    if ma20 is not None:
        if price > ma20: bull += 1
        else:            bear += 1
    if ma60 is not None:
        if price > ma60: bull += 1
        else:            bear += 1
    if ma5 is not None and ma20 is not None:
        if ma5 > ma20: bull += 1
        else:          bear += 1
    if macd_hist is not None:
        if macd_hist > 0: bull += 1
        else:             bear += 1
    if bull >= 4:   return "강한 상승 추세"
    if bull >= 3:   return "상승 추세"
    if bear >= 4:   return "강한 하락 추세"
    if bear >= 3:   return "하락 추세"
    return "보합/중립"


def _calc_technicals(closes, price: float) -> dict:
    """RSI / 이동평균(5·20·60일) / MACD 계산"""
    n = len(closes)

    rsi  = _calc_rsi(closes)
    ma5  = round(float(closes.rolling(5).mean().iloc[-1]),  2) if n >= 5  else None
    ma20 = round(float(closes.rolling(20).mean().iloc[-1]), 2) if n >= 20 else None
    ma60 = round(float(closes.rolling(60).mean().iloc[-1]), 2) if n >= 60 else None

    macd_val = macd_sig = macd_hist = None
    if n >= 35:
        ema12      = closes.ewm(span=12, adjust=False).mean()
        ema26      = closes.ewm(span=26, adjust=False).mean()
        macd_line  = ema12 - ema26
        signal_ln  = macd_line.ewm(span=9, adjust=False).mean()
        histogram  = macd_line - signal_ln
        macd_val   = round(float(macd_line.iloc[-1]),  6)
        macd_sig   = round(float(signal_ln.iloc[-1]),  6)
        macd_hist  = round(float(histogram.iloc[-1]),  6)

    trend = _interpret_trend(price, ma5, ma20, ma60, macd_hist)
    return {
        "rsi_14":          rsi,
        "ma5":             ma5,
        "ma20":            ma20,
        "ma60":            ma60,
        "macd_line":       macd_val,
        "macd_signal":     macd_sig,
        "macd_histogram":  macd_hist,
        "trend_signal":    trend,
    }


def _detect_swing_points(high_s, low_s, window: int = 5) -> tuple[list, list]:
    """스윙 고점/저점 탐지 — 좌우 window개 봉보다 고점/저점이면 스윙 포인트로 인정.
    반환: ([(index, price), ...] 고점, [(index, price), ...] 저점)
    """
    n = len(high_s)
    swing_highs: list[tuple[int, float]] = []
    swing_lows:  list[tuple[int, float]] = []
    for i in range(window, n - window):
        h_win = high_s.iloc[i - window: i + window + 1]
        l_win = low_s.iloc[i - window: i + window + 1]
        if high_s.iloc[i] >= h_win.max():
            swing_highs.append((i, float(high_s.iloc[i])))
        if low_s.iloc[i] <= l_win.min():
            swing_lows.append((i, float(low_s.iloc[i])))
    return swing_highs, swing_lows


def _cluster_zones(points: list[tuple[int, float]], n_bars: int, tolerance_pct: float = 1.5) -> list[dict]:
    """근접한 스윙 포인트를 하나의 지지/저항 zone으로 묶고 강도점수(0~100)를 매긴다.
    강도점수 = 터치 횟수(25점/회) + 최근 20봉 이내 터치 시 가산(15점), 100점 상한.
    """
    if not points:
        return []
    ordered = sorted(points, key=lambda p: p[1])
    clusters: list[list[tuple[int, float]]] = [[ordered[0]]]
    for idx, price in ordered[1:]:
        cluster_mid = sum(p for _, p in clusters[-1]) / len(clusters[-1])
        if cluster_mid and abs(price - cluster_mid) / cluster_mid * 100 <= tolerance_pct:
            clusters[-1].append((idx, price))
        else:
            clusters.append([(idx, price)])

    zones = []
    for cluster in clusters:
        prices  = [p for _, p in cluster]
        idxs    = [i for i, _ in cluster]
        touches = len(cluster)
        recent  = (n_bars - 1 - max(idxs)) <= 20
        strength = min(100, touches * 25 + (15 if recent else 0))
        zones.append({
            "low":      round(min(prices), 2),
            "high":     round(max(prices), 2),
            "touches":  touches,
            "strength": strength,
        })
    return zones


def _calc_support_resistance(hist, price: float) -> dict:
    """일봉 High/Low 기반 스윙 고점/저점 클러스터링으로 지지·저항 박스와 손익비를 계산한다.
    손익비 = 저항까지 상승여력% / 지지까지 하락위험% — 2.0 이상이면 기준 충족으로 표시.
    (매수·매도 지시가 아닌 조건부 참고 지표 — report_builder.py에서 "~검토 가능한 자리" 형태로만 서술)
    """
    try:
        hl = hist[["High", "Low"]].dropna()
        if len(hl) < 15:
            return {}
        high_s = hl["High"].reset_index(drop=True)
        low_s  = hl["Low"].reset_index(drop=True)
        n = len(high_s)

        swing_highs, swing_lows = _detect_swing_points(high_s, low_s)
        resistance_pts = [(i, p) for i, p in swing_highs if p > price]
        support_pts    = [(i, p) for i, p in swing_lows  if p < price]

        resistance_zones = sorted(_cluster_zones(resistance_pts, n), key=lambda z: z["low"])
        support_zones    = sorted(_cluster_zones(support_pts, n),    key=lambda z: -z["high"])

        nearest_resistance = resistance_zones[0] if resistance_zones else None
        nearest_support    = support_zones[0]    if support_zones    else None

        upside_pct   = round((nearest_resistance["low"]  - price) / price * 100, 1) if nearest_resistance else None
        downside_pct = round((price - nearest_support["high"]) / price * 100, 1)    if nearest_support    else None

        risk_reward = None
        if upside_pct is not None and downside_pct is not None and downside_pct > 0:
            risk_reward = round(upside_pct / downside_pct, 2)

        return {
            "resistance_zones":       resistance_zones[:2],
            "support_zones":          support_zones[:2],
            "nearest_resistance_pct": upside_pct,
            "nearest_support_pct":    downside_pct,
            "risk_reward_ratio":      risk_reward,
            "risk_reward_meets_bar":  bool(risk_reward is not None and risk_reward >= 2.0),
        }
    except Exception as e:
        logger.debug("지지/저항 계산 실패: %s", e)
        return {}


def _mock_support_resistance(price: float) -> dict:
    """Mock 모드용 지지/저항 — 현재가 기준 랜덤 오프셋으로 합성 (실제 스윙 탐지 아님)"""
    resistance_low = round(price * random.uniform(1.02, 1.08), 2)
    support_high   = round(price * random.uniform(0.92, 0.98), 2)
    upside_pct     = round((resistance_low - price) / price * 100, 1)
    downside_pct   = round((price - support_high) / price * 100, 1)
    risk_reward    = round(upside_pct / downside_pct, 2) if downside_pct > 0 else None

    return {
        "resistance_zones": [{
            "low": resistance_low, "high": round(resistance_low * 1.01, 2),
            "touches": random.randint(1, 3), "strength": random.randint(30, 70),
        }],
        "support_zones": [{
            "low": round(support_high * 0.99, 2), "high": support_high,
            "touches": random.randint(1, 3), "strength": random.randint(30, 70),
        }],
        "nearest_resistance_pct": upside_pct,
        "nearest_support_pct":    downside_pct,
        "risk_reward_ratio":      risk_reward,
        "risk_reward_meets_bar":  bool(risk_reward is not None and risk_reward >= 2.0),
    }


def _parse_frgn_table(html: str) -> list[dict]:
    """네이버 금융 frgn.naver 페이지에서 일별 기관/외국인 순매매량(주식수)을 최신순으로 파싱.
    구조 식별은 텍스트가 아닌 테이블 속성(width=680, class=type2)으로 — 페이지 내 다른 type2
    테이블(매도/매수 상위 종목 등)과 혼동되지 않도록 함.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", attrs={"width": "680", "class": "type2"})
    if table is None:
        return []
    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 9:
            continue
        date_text = tds[0].get_text(strip=True)
        if not re.match(r"^\d{4}\.\d{2}\.\d{2}$", date_text):
            continue
        try:
            institution_net = int(tds[5].get_text(strip=True).replace(",", ""))
            foreign_net = int(tds[6].get_text(strip=True).replace(",", ""))
        except ValueError:
            continue
        rows.append({"institution_net": institution_net, "foreign_net": foreign_net})
    return rows


def _summarize_investor_flow(daily_rows: list[dict]) -> dict:
    """일별 수급(최신순 리스트) → 3/5/10/20일 누적 순매매량(주식수) 요약.
    개인 순매매는 KRX가 별도 집계하지 않아 -(기관+외국인)으로 추정(기타법인 등 오차 존재 — 참고용).
    """
    result: dict = {"_mock": False}
    for days in (3, 5, 10, 20):
        window = daily_rows[:days]
        if not window:
            continue
        inst_sum = sum(r["institution_net"] for r in window)
        frgn_sum = sum(r["foreign_net"] for r in window)
        result[f"institution_net_{days}d"] = inst_sum
        result[f"foreign_net_{days}d"] = frgn_sum
        result[f"individual_net_{days}d_est"] = -(inst_sum + frgn_sum)
    return result


def _fetch_investor_flow(ticker: str) -> dict:
    """KR 종목의 외국인/기관 순매매 수급 데이터 수집 (네이버 금융 — 비공식 소스).
    실패(구조 변경·네트워크 오류·데이터 부족) 시 예외를 던져 호출부에서 Mock 폴백 처리.
    """
    resp = requests.get(
        _NAVER_FRGN_URL,
        params={"code": ticker, "page": 1},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    resp.encoding = "euc-kr"
    daily_rows = _parse_frgn_table(resp.text)
    if len(daily_rows) < 3:
        raise ValueError(f"수급 데이터 파싱 실패 또는 부족 (rows={len(daily_rows)})")
    return _summarize_investor_flow(daily_rows)


def _mock_investor_flow() -> dict:
    """Mock 모드 / 실데이터 수집 실패 시 합성 수급 데이터"""
    result: dict = {"_mock": True}
    for days in (3, 5, 10, 20):
        inst = random.randint(-2_000_000, 2_000_000)
        frgn = random.randint(-3_000_000, 3_000_000)
        result[f"institution_net_{days}d"] = inst
        result[f"foreign_net_{days}d"] = frgn
        result[f"individual_net_{days}d_est"] = -(inst + frgn)
    return result


def _fetch_analyst_data(ticker_obj, sym: str, current_price: float) -> dict:
    """애널리스트 목표주가·추천등급 수집 (ETF·한국 종목 제외)"""
    if sym in _SKIP_ANALYST_SYMBOLS or any(sym.endswith(s) for s in _SKIP_ANALYST_SUFFIXES):
        return {}
    try:
        info         = ticker_obj.info
        target_mean  = info.get("targetMeanPrice")
        target_high  = info.get("targetHighPrice")
        target_low   = info.get("targetLowPrice")
        rec          = (info.get("recommendationKey") or "").lower()
        num_analysts = int(info.get("numberOfAnalystOpinions") or 0)
        if not target_mean:
            return {}
        upside = round((float(target_mean) - current_price) / current_price * 100, 1)
        return {
            "target_mean":   round(float(target_mean), 2),
            "target_high":   round(float(target_high), 2) if target_high else None,
            "target_low":    round(float(target_low),  2) if target_low  else None,
            "upside_pct":    upside,
            "recommendation": rec,
            "num_analysts":  num_analysts,
        }
    except Exception as e:
        logger.debug("애널리스트 데이터 수집 실패 (%s): %s", sym, e)
        return {}

# yfinance 심볼 매핑 (stock_id → yfinance 심볼, 한국명, 통화)
YFINANCE_MAP: dict[str, tuple[str, str, str]] = {
    # ── 한국 ────────────────────────────────────────────────────────────────
    "KR_005930": ("005930.KS", "삼성전자",     "KRW"),
    "KR_000660": ("000660.KS", "SK하이닉스",   "KRW"),
    "KR_069500": ("069500.KS", "KODEX 200",    "KRW"),
    "KR_010120": ("010120.KS", "LS ELECTRIC",  "KRW"),
    "KR_015760": ("015760.KS", "한국전력",      "KRW"),
    "KR_066570": ("066570.KS", "LG전자",        "KRW"),
    "KR_138080": ("138080.KQ", "오이솔루션",    "KRW"),   # 광통신 (KOSDAQ)
    # ── 미국 ────────────────────────────────────────────────────────────────
    "US_NVDA":   ("NVDA",  "NVIDIA",              "USD"),
    "US_QQQ":    ("QQQ",   "Invesco QQQ",         "USD"),
    "US_VOO":    ("VOO",   "Vanguard S&P500 ETF", "USD"),
    "US_QTUM":   ("QTUM",  "Defiance Quantum ETF","USD"),
    "US_VST":    ("VST",   "Vistra Energy",        "USD"),
    "US_SCHD":   ("SCHD",  "Schwab Dividend ETF", "USD"),
    "US_SNDK":   ("SNDK",  "SanDisk",             "USD"),
    "US_COHR":   ("COHR",  "Coherent Corp",        "USD"),  # 광통신
    "US_CIEN":   ("CIEN",  "Ciena",               "USD"),   # 광통신
    "US_SPCX":   ("SPCX",  "SpaceX",              "USD"),   # 우주항공
    # ── 대만 ────────────────────────────────────────────────────────────────
    "TW_TSM":    ("TSM",   "TSMC",                "USD"),
}

# Mock 기준 가격 (실제 API 연결 전 테스트용 — USE_MOCK_DATA=true 또는 yfinance 실패 시 사용)
_BASE_PRICES: dict[str, dict] = {
    # ── 한국 ────────────────────────────────────────────────────────────────
    "KR_005930": {"ticker": "005930", "name": "삼성전자",    "base": 58000,   "currency": "KRW", "market_cap_b": 346000},
    "KR_000660": {"ticker": "000660", "name": "SK하이닉스",  "base": 185000,  "currency": "KRW", "market_cap_b": 135000},
    "KR_069500": {"ticker": "069500", "name": "KODEX 200",   "base": 37000,   "currency": "KRW", "market_cap_b": 12000},
    "KR_010120": {"ticker": "010120", "name": "LS ELECTRIC", "base": 280000,  "currency": "KRW", "market_cap_b": 6800},
    "KR_015760": {"ticker": "015760", "name": "한국전력",     "base": 22000,   "currency": "KRW", "market_cap_b": 14000},
    "KR_066570": {"ticker": "066570", "name": "LG전자",       "base": 95000,   "currency": "KRW", "market_cap_b": 15500},
    "KR_138080": {"ticker": "138080", "name": "오이솔루션",   "base": 53000,   "currency": "KRW", "market_cap_b": 2100},
    # ── 미국 ────────────────────────────────────────────────────────────────
    "US_NVDA":   {"ticker": "NVDA",  "name": "NVIDIA",              "base": 135.0,  "currency": "USD", "market_cap_b": 3310},
    "US_QQQ":    {"ticker": "QQQ",   "name": "Invesco QQQ",         "base": 490.0,  "currency": "USD", "market_cap_b": 310},
    "US_VOO":    {"ticker": "VOO",   "name": "Vanguard S&P500 ETF", "base": 520.0,  "currency": "USD", "market_cap_b": 540},
    "US_QTUM":   {"ticker": "QTUM",  "name": "Defiance Quantum ETF","base": 65.0,   "currency": "USD", "market_cap_b": 2},
    "US_VST":    {"ticker": "VST",   "name": "Vistra Energy",        "base": 120.0,  "currency": "USD", "market_cap_b": 48},
    "US_SCHD":   {"ticker": "SCHD",  "name": "Schwab Dividend ETF", "base": 27.0,   "currency": "USD", "market_cap_b": 62},
    "US_SNDK":   {"ticker": "SNDK",  "name": "SanDisk",             "base": 50.0,   "currency": "USD", "market_cap_b": 10},
    "US_COHR":   {"ticker": "COHR",  "name": "Coherent Corp",        "base": 85.0,   "currency": "USD", "market_cap_b": 20},
    "US_CIEN":   {"ticker": "CIEN",  "name": "Ciena",               "base": 75.0,   "currency": "USD", "market_cap_b": 12},
    "US_SPCX":   {"ticker": "SPCX",  "name": "SpaceX",              "base": 161.0,  "currency": "USD", "market_cap_b": 390},
    # ── 대만 ────────────────────────────────────────────────────────────────
    "TW_TSM":    {"ticker": "TSM",   "name": "TSMC",                "base": 192.0,  "currency": "USD", "market_cap_b": 995},
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
                # 90일 히스토리 → 기술적 지표(MA60, RSI14, MACD) 계산에 충분
                hist   = ticker.history(period="90d", auto_adjust=True)

                close_s = hist["Close"].dropna() if "Close" in hist.columns else None
                vol_s   = hist["Volume"].dropna() if "Volume" in hist.columns else None

                if close_s is None or len(close_s) < 2:
                    raise ValueError("종가 데이터 부족")

                price      = float(close_s.iloc[-1])
                prev_close = float(close_s.iloc[-2])
                change     = price - prev_close
                change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

                volume  = int(vol_s.iloc[-1]) if (vol_s is not None and not vol_s.empty) else 0
                # 5일 평균 거래량 (6d→90d로 바꿨으므로 최근 5거래일만 사용)
                avg_vol = float(vol_s.iloc[-6:-1].mean()) if (vol_s is not None and len(vol_s) >= 6) else (float(vol_s.mean()) if (vol_s is not None and not vol_s.empty) else 1.0)
                vol_ratio = round(volume / avg_vol, 2) if avg_vol else 1.0

                try:
                    fi       = ticker.fast_info
                    high_52w = float(getattr(fi, "year_high",   price * 1.4))
                    low_52w  = float(getattr(fi, "year_low",    price * 0.6))
                    mktcap   = round(float(getattr(fi, "market_cap", 0)) / 1e9, 1)
                except Exception:
                    high_52w, low_52w, mktcap = price * 1.4, price * 0.6, 0.0

                # 기술적 지표 (RSI·MA·MACD)
                technical = _calc_technicals(close_s, price)

                # 지지/저항 박스 + 손익비 (스윙 고점/저점 클러스터링)
                support_resistance = _calc_support_resistance(hist, price)

                # 애널리스트 컨센서스 (US 개별 종목만, ETF·KR 제외)
                analyst = _fetch_analyst_data(ticker, sym, price)

                # ticker 표시용 정리 (.KS/.KQ 제거)
                display_ticker = sym.replace(".KS", "").replace(".KQ", "")

                # 수급(외국인/기관/개인 순매매) — KRX 시장 구조상 KR 종목에만 존재
                investor_flow: dict = {}
                if sid.startswith("KR_"):
                    try:
                        investor_flow = _fetch_investor_flow(display_ticker)
                    except Exception as e:
                        logger.debug("수급 데이터 수집 실패 (%s): %s → Mock 폴백", sid, e)
                        investor_flow = _mock_investor_flow()

                result[sid] = {
                    "stock_id":      sid,
                    "ticker":        display_ticker,
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
                    "technical":     technical,
                    "analyst":       analyst,
                    "support_resistance": support_resistance,
                    "investor_flow": investor_flow,
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

            # Mock 기술적 지표 — RSI 범위를 극단값 포함(25~78)으로 확대해 과매도/과매수 신호 테스트 가능
            ma20_offset = random.uniform(-0.06, 0.06)
            ma20 = round(bi["base"] * (1 + ma20_offset), 2)
            technical = {
                "rsi_14":         round(random.uniform(25, 78), 1),
                "ma5":            round(price * random.uniform(0.98, 1.02), 2),
                "ma20":           ma20,
                "ma60":           round(bi["base"] * random.uniform(0.88, 1.08), 2),
                "macd_line":      round(random.uniform(-3, 3), 4),
                "macd_signal":    round(random.uniform(-3, 3), 4),
                "macd_histogram": round(random.uniform(-1.5, 1.5), 4),
                "trend_signal":   random.choice(
                    ["상승 추세", "상승 추세", "보합/중립", "보합/중립", "하락 추세"]
                ),
            }

            # Mock 애널리스트 컨센서스 (US 개별 종목만)
            analyst: dict = {}
            if sid in _MOCK_ANALYST_SIDS:
                upside = round(random.uniform(8, 32), 1)
                analyst = {
                    "target_mean":    round(bi["base"] * (1 + upside / 100), 2),
                    "target_high":    round(bi["base"] * (1 + upside / 100 + 0.12), 2),
                    "target_low":     round(bi["base"] * 0.88, 2),
                    "upside_pct":     upside,
                    "recommendation": random.choice(["buy", "buy", "outperform", "hold"]),
                    "num_analysts":   random.randint(10, 40),
                }

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
                "technical": technical,
                "analyst":   analyst,
                "support_resistance": _mock_support_resistance(price),
                "investor_flow": _mock_investor_flow() if sid.startswith("KR_") else {},
            }
        return result
