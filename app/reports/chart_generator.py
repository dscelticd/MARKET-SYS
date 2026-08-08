"""
캔들차트 이미지 생성 — 일봉(90봉)·주봉 + MA5/20/60/120 + 볼린저밴드

이메일 용량·발송 시간·스팸 필터링 위험을 고려해 전 종목이 아닌 "주목 종목"
(등급 추천/위험/판단보류 이거나 당일 등급 변화가 있는 종목)에만 차트를 첨부한다.
Mock 모드에서는 실제 캔들 이력이 없어 합성 데이터가 실제처럼 오인될 수 있으므로
차트 생성을 생략한다.
"""
from __future__ import annotations

import io
import logging
import os

import pandas as pd

from app.collectors.price_collector import YFINANCE_MAP

logger = logging.getLogger(__name__)

_MA_COLORS = {5: "#2563eb", 20: "#f59e0b", 60: "#16a34a", 120: "#dc2626"}
_ATTENTION_GRADES = {"추천", "위험", "판단보류"}


def select_attention_stocks(ratings: list[dict], grade_changes: list[dict] | None = None) -> list[str]:
    """차트를 첨부할 "주목 종목" stock_id 목록 (rating 표시 순서 유지)"""
    ids = {r["stock_id"] for r in ratings if r.get("grade") in _ATTENTION_GRADES}
    for c in (grade_changes or []):
        if c.get("direction") in ("상승", "하락") and c.get("stock_id"):
            ids.add(c["stock_id"])
    return [r["stock_id"] for r in ratings if r["stock_id"] in ids]


def resample_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """일봉 OHLCV → 주봉(금요일 마감 기준) 리샘플링"""
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    return daily.resample("W-FRI").agg(agg).dropna()


def _bollinger_bands(close: pd.Series, window: int = 20, num_std: float = 2.0):
    mid = close.rolling(window).mean()
    std = close.rolling(window).std()
    return mid, mid + num_std * std, mid - num_std * std


def generate_candle_chart_png(df: pd.DataFrame | None, title: str, tail: int) -> bytes | None:
    """OHLCV DataFrame → 캔들차트 PNG 바이트. MA/볼린저밴드는 표시 구간(tail) 이전의
    전체 이력으로 계산 후 마지막 tail개만 잘라 그려 MA120처럼 긴 지표도 정확히 표현.
    데이터 부족·렌더링 실패 시 None (이메일 본문 발송은 계속 진행, 차트만 생략).
    """
    if df is None or len(df) < 10:
        return None
    try:
        import mplfinance as mpf

        close = df["Close"]
        addplots = []
        for period in (5, 20, 60, 120):
            if len(close) >= period:
                ma = close.rolling(period).mean()
                addplots.append(mpf.make_addplot(ma.tail(tail), color=_MA_COLORS[period], width=0.9))

        if len(close) >= 20:
            _, upper, lower = _bollinger_bands(close)
            addplots.append(mpf.make_addplot(upper.tail(tail), color="#9ca3af", width=0.7, linestyle="--"))
            addplots.append(mpf.make_addplot(lower.tail(tail), color="#9ca3af", width=0.7, linestyle="--"))

        display = df.tail(tail)
        if len(display) < 5:
            return None

        buf = io.BytesIO()
        mpf.plot(
            display,
            type="candle",
            style="yahoo",
            addplot=addplots if addplots else None,
            volume=True,
            title=title,
            figsize=(9, 5.5),
            savefig=dict(fname=buf, dpi=110, bbox_inches="tight"),
        )
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.warning("캔들차트 생성 실패 (%s): %s", title, e)
        return None


def fetch_chart_history(sym: str, period: str = "1y") -> pd.DataFrame | None:
    """차트 전용 장기 일봉 히스토리 수집 — 기존 price_collector의 90일 수집과 별개
    (MA120·주봉 계산에 필요한 더 긴 이력)"""
    try:
        import yfinance as yf
        hist = yf.Ticker(sym).history(period=period, auto_adjust=True)
        hist = hist[["Open", "High", "Low", "Close", "Volume"]].dropna()
        return hist if len(hist) >= 20 else None
    except Exception as e:
        logger.warning("차트용 히스토리 수집 실패 (%s): %s", sym, e)
        return None


def generate_stock_charts(stock_id: str) -> dict:
    """관심종목 하나의 일봉(90봉)·주봉 차트 PNG 생성. 실패한 항목은 None.
    차트 제목은 영문 티커만 사용 — GitHub Actions(Ubuntu) 러너에는 한글 폰트가 없어
    matplotlib 렌더링 시 한글이 깨짐(□). 종목명(한글)은 이메일 HTML 텍스트에서 표시.
    """
    result: dict = {"daily": None, "weekly": None}
    mapping = YFINANCE_MAP.get(stock_id)
    if not mapping:
        return result
    sym, _name, _ = mapping
    ticker_label = sym.replace(".KS", "").replace(".KQ", "")

    daily_hist = fetch_chart_history(sym)
    if daily_hist is None:
        return result

    result["daily"] = generate_candle_chart_png(daily_hist, f"{ticker_label} - Daily (90D)", tail=90)
    weekly_hist = resample_weekly(daily_hist)
    result["weekly"] = generate_candle_chart_png(weekly_hist, f"{ticker_label} - Weekly", tail=52)
    return result


def generate_report_charts(ratings: list[dict], grade_changes: list[dict] | None = None) -> list[dict]:
    """주목 종목(추천/위험/판단보류 또는 당일 등급 변화)의 차트를 생성해 이메일 첨부용
    리스트로 반환: [{"stock_id", "name", "daily": bytes|None, "weekly": bytes|None}, ...]
    Mock 모드에서는 실제 캔들 이력이 아니므로 빈 리스트를 반환한다.
    """
    if os.getenv("USE_MOCK_DATA", "true").lower() == "true":
        return []

    attention_ids = select_attention_stocks(ratings, grade_changes)
    name_map = {r["stock_id"]: r["name"] for r in ratings}

    charts = []
    for sid in attention_ids:
        imgs = generate_stock_charts(sid)
        if imgs["daily"] or imgs["weekly"]:
            charts.append({"stock_id": sid, "name": name_map.get(sid, sid), **imgs})
    return charts
