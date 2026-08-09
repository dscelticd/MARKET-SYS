"""
캔들차트 이미지 생성 — 일봉(90봉)·주봉 + MA5/20/60/120 + 볼린저밴드

이메일 용량·발송 시간·스팸 필터링 위험을 고려해 전 종목이 아닌 "주목 종목"
(등급 추천/위험/판단보류 · 당일 등급 변화 · 당일 등락률 ±5% 이상 급등락)에만
차트를 첨부한다. 등급만으로는 리스크 점수 등 여러 요소가 상쇄돼 큰 폭으로
급등락한 날에도 "안전/보통"으로 분류되어 차트가 하나도 안 붙는 경우가 실제
발생함 — 등급 기준에 급등락 기준을 추가로 보완함.
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

# 한글 폰트 후보(NanumGothic 등) 중 시스템에 없는 것들에 대해 matplotlib이
# "Font family 'X' not found" 경고를 폰트 후보 개수만큼 반복 출력 — 정상 동작이므로
# CI 로그 가독성을 위해 findfont 경고만 조용히 함(다른 로거에는 영향 없음)
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

_MA_COLORS = {5: "#2563eb", 20: "#f59e0b", 60: "#16a34a", 120: "#dc2626"}
_ATTENTION_GRADES = {"추천", "위험", "판단보류"}
_ATTENTION_MOVE_PCT = 5.0  # 당일 등락률이 이 값 이상(절대값)이면 등급과 무관하게 주목 종목에 포함

# 한글 렌더링용 폰트 후보 — Windows(Malgun Gothic), GitHub Actions(Ubuntu, fonts-nanum
# 워크플로에서 설치), macOS(AppleGothic) 순으로 시도, 전부 없으면 DejaVu Sans로 폴백
# (이 경우 한글은 깨지지만 차트 자체는 계속 생성됨)
_KOREAN_FONT_CANDIDATES = ["Malgun Gothic", "NanumGothic", "NanumBarunGothic", "AppleGothic", "DejaVu Sans"]
_korean_style = None


def _get_korean_style():
    """한글이 깨지지 않는 mplfinance 스타일 객체를 반환한다 (지연 생성, 1회만).
    주의: mpf.plot() 호출 전에 matplotlib.rcParams['font.family']를 직접 설정해도
    mplfinance가 내부적으로 plt.style.use('default')를 호출해 그 값을 초기화해버린다
    — 반드시 스타일 자체의 rc 딕셔너리에 폰트 설정을 담아 스타일 적용 이후 단계에서
    반영되도록 해야 한다.
    """
    global _korean_style
    if _korean_style is None:
        import mplfinance as mpf

        _korean_style = mpf.make_mpf_style(
            base_mpf_style="yahoo",
            rc={
                "font.family": _KOREAN_FONT_CANDIDATES,
                "axes.unicode_minus": False,  # 한글 폰트 사용 시 '-' 기호 깨짐 방지
            },
        )
    return _korean_style


def select_attention_stocks(
    ratings: list[dict],
    grade_changes: list[dict] | None = None,
    price_data: dict[str, dict] | None = None,
) -> list[str]:
    """차트를 첨부할 "주목 종목" stock_id 목록 (rating 표시 순서 유지).
    판단보류 진입/복원(critical_data_error 등 데이터 품질 이벤트)으로 인한 등급 변화는
    종목 고유의 신호 변화가 아니므로 제외 — 그렇지 않으면 판단보류가 한꺼번에 정상
    등급으로 복원되는 시점에 전종목이 "등급 변화"로 잡혀 이 필터 자체가 무력화된다.
    당일 등락률이 ±5%(_ATTENTION_MOVE_PCT) 이상이면 등급과 무관하게 포함 — 리스크
    점수 등 다른 요소에 상쇄돼 급등락에도 등급이 "안전/보통"에 머무는 경우가 있어
    등급 기준만으로는 정작 주목해야 할 날에 차트가 하나도 안 붙는 문제가 있었다.
    """
    ids = {r["stock_id"] for r in ratings if r.get("grade") in _ATTENTION_GRADES}
    for c in (grade_changes or []):
        if c.get("direction") not in ("상승", "하락") or not c.get("stock_id"):
            continue
        if c.get("prev_grade") == "판단보류" or c.get("curr_grade") == "판단보류":
            continue
        ids.add(c["stock_id"])
    for sid, p in (price_data or {}).items():
        chg = p.get("change_pct")
        if chg is not None and abs(chg) >= _ATTENTION_MOVE_PCT:
            ids.add(sid)
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
            style=_get_korean_style(),
            addplot=addplots if addplots else None,
            volume=True,
            title=title,
            ylabel="가격",
            ylabel_lower="거래량",
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
    제목에 한글 종목명을 사용 — GitHub Actions(Ubuntu) 러너에는 fonts-nanum을
    워크플로에서 설치해 한글이 깨지지 않도록 함(_configure_korean_font 참고).
    """
    result: dict = {"daily": None, "weekly": None}
    mapping = YFINANCE_MAP.get(stock_id)
    if not mapping:
        return result
    sym, name, _ = mapping
    ticker_label = sym.replace(".KS", "").replace(".KQ", "")

    daily_hist = fetch_chart_history(sym)
    if daily_hist is None:
        return result

    result["daily"] = generate_candle_chart_png(daily_hist, f"{name}({ticker_label}) — 일봉(90일)", tail=90)
    weekly_hist = resample_weekly(daily_hist)
    result["weekly"] = generate_candle_chart_png(weekly_hist, f"{name}({ticker_label}) — 주봉", tail=52)
    return result


def generate_report_charts(
    ratings: list[dict],
    grade_changes: list[dict] | None = None,
    price_data: dict[str, dict] | None = None,
) -> list[dict]:
    """주목 종목(추천/위험/판단보류·당일 등급 변화·당일 등락률 ±5% 이상)의 차트를
    생성해 이메일 첨부용 리스트로 반환:
    [{"stock_id", "name", "daily": bytes|None, "weekly": bytes|None}, ...]
    Mock 모드에서는 실제 캔들 이력이 아니므로 빈 리스트를 반환한다.
    """
    if os.getenv("USE_MOCK_DATA", "true").lower() == "true":
        return []

    attention_ids = select_attention_stocks(ratings, grade_changes, price_data)
    name_map = {r["stock_id"]: r["name"] for r in ratings}

    charts = []
    for sid in attention_ids:
        imgs = generate_stock_charts(sid)
        if imgs["daily"] or imgs["weekly"]:
            charts.append({"stock_id": sid, "name": name_map.get(sid, sid), **imgs})
    return charts
