"""
캔들차트 이미지 생성 로직 테스트 (네트워크 호출 없이 순수 함수만 검증)

배경: "주목 종목"(등급 추천/위험/판단보류 또는 당일 등급 변화)에 한해 일봉/주봉
      캔들차트를 이메일에 첨부하는 기능. 전종목 첨부 시 이메일당 이미지 36장이 되어
      발송 시간·용량·스팸 위험이 커진다는 이유로 사용자가 "주목 종목만"을 선택함.
      Mock 모드에서는 실제 캔들 이력이 없어 차트 생성을 생략한다.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from app.reports.chart_generator import (
    select_attention_stocks,
    resample_weekly,
    generate_candle_chart_png,
    generate_report_charts,
    _get_korean_style,
)


def _synthetic_ohlcv(n: int = 200) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.standard_normal(n))
    open_ = close + rng.standard_normal(n) * 0.5
    high = np.maximum(open_, close) + np.abs(rng.standard_normal(n))
    low = np.minimum(open_, close) - np.abs(rng.standard_normal(n))
    vol = rng.integers(1_000_000, 5_000_000, n)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=dates,
    )


def test_select_attention_stocks_includes_recommend_risk_pending_grades():
    ratings = [
        {"stock_id": "A", "name": "A사", "grade": "추천"},
        {"stock_id": "B", "name": "B사", "grade": "보통"},
        {"stock_id": "C", "name": "C사", "grade": "위험"},
        {"stock_id": "D", "name": "D사", "grade": "판단보류"},
    ]
    selected = select_attention_stocks(ratings)
    assert selected == ["A", "C", "D"]


def test_select_attention_stocks_includes_graded_changes():
    ratings = [
        {"stock_id": "A", "name": "A사", "grade": "보통"},
        {"stock_id": "B", "name": "B사", "grade": "안전"},
    ]
    changes = [{"stock_id": "A", "direction": "상승"}, {"stock_id": "B", "direction": "유지"}]
    selected = select_attention_stocks(ratings, changes)
    assert selected == ["A"]  # B는 "유지"라 제외


def test_select_attention_stocks_excludes_judgement_pending_recovery():
    """판단보류 → 정상 등급으로 전종목이 한꺼번에 복원될 때 필터가 무력화되면 안 됨
    (실제 발생한 회귀: critical_data_error 해제 시 18개 종목이 전부 "등급 변화"로
    잡혀 주목 종목 제한 없이 전종목 차트가 첨부됨)
    """
    ratings = [
        {"stock_id": "A", "name": "A사", "grade": "보통"},
        {"stock_id": "B", "name": "B사", "grade": "안전"},
        {"stock_id": "C", "name": "C사", "grade": "추천"},
    ]
    changes = [
        {"stock_id": "A", "direction": "상승", "prev_grade": "판단보류", "curr_grade": "보통"},
        {"stock_id": "B", "direction": "상승", "prev_grade": "판단보류", "curr_grade": "안전"},
    ]
    selected = select_attention_stocks(ratings, changes)
    assert selected == ["C"]  # C는 추천 등급이라 포함, A/B는 판단보류 복원이라 제외


def test_select_attention_stocks_preserves_ratings_order():
    ratings = [
        {"stock_id": "Z", "name": "Z사", "grade": "위험"},
        {"stock_id": "A", "name": "A사", "grade": "추천"},
    ]
    assert select_attention_stocks(ratings) == ["Z", "A"]


def test_resample_weekly_aggregates_ohlcv_correctly():
    df = _synthetic_ohlcv(30)
    weekly = resample_weekly(df)
    assert len(weekly) > 0
    assert weekly["High"].iloc[0] >= weekly["Open"].iloc[0]
    assert weekly["Low"].iloc[0] <= weekly["Close"].iloc[0]


def test_generate_candle_chart_png_returns_bytes_for_sufficient_data():
    df = _synthetic_ohlcv(200)
    png = generate_candle_chart_png(df, "TEST - Daily (90D)", tail=90)
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG 매직 넘버


def test_generate_candle_chart_png_returns_none_for_insufficient_data():
    df = _synthetic_ohlcv(5)
    assert generate_candle_chart_png(df, "TEST", tail=90) is None
    assert generate_candle_chart_png(None, "TEST", tail=90) is None


def test_korean_style_embeds_font_family_in_rc():
    """한글 폰트 설정은 mpf.plot() 호출 전 rcParams 직접 수정이 아니라 스타일 객체의
    rc 딕셔너리 안에 있어야 함 — mplfinance가 plot() 내부에서 plt.style.use('default')를
    호출해 사전 설정된 rcParams를 초기화해버리기 때문에(실제로 겪은 회귀), 반드시 스타일
    자체를 통해 전달되어야 살아남는다.
    """
    style = _get_korean_style()
    assert "font.family" in style["rc"]
    assert "Malgun Gothic" in style["rc"]["font.family"]


def test_korean_style_is_cached_singleton():
    assert _get_korean_style() is _get_korean_style()


def test_generate_candle_chart_png_renders_korean_title_without_missing_glyphs():
    """한글 제목/축 레이블이 실제로 깨지지 않고 렌더링되는지 확인 (Glyph missing 경고 없음)"""
    import warnings

    df = _synthetic_ohlcv(200)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        png = generate_candle_chart_png(df, "삼성전자(005930) — 일봉(90일)", tail=90)
        glyph_warnings = [str(x.message) for x in caught if "Glyph" in str(x.message)]

    assert png is not None
    assert glyph_warnings == []


def test_generate_report_charts_skips_in_mock_mode(monkeypatch):
    monkeypatch.setenv("USE_MOCK_DATA", "true")
    ratings = [{"stock_id": "US_NVDA", "name": "NVIDIA", "grade": "추천"}]
    assert generate_report_charts(ratings) == []
