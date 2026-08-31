"""
보유/관찰 구분·관심도 + 큐레이션 테마 지식의 리포트 반영 테스트

배경: 구조 점검에서 발견 — watchlist.json의 status(보유 7 / 관찰 11)와
      interest_level(1~5), themes.json의 key_drivers·key_risks·macro_sensitivity가
      설정에는 존재하지만 리포트에 전혀 전달되지 않고 있었다.
      - 이미 포지션이 있는 종목과 진입 검토 중인 종목을 동일하게 서술
      - 사용자가 큐레이션한 8개 테마 × (촉진요인 4 + 리스크 4) = 64건이 전량 사장
        (signal_scorer는 theme_config를 인자로 받기만 하고 실제로는 하드코딩된
         섹터 상수를 사용)
"""
from __future__ import annotations

from app.reports.report_builder import (
    _format_theme_knowledge_block,
    _format_watchlist_context_block,
)


# ── 보유/관찰 구분 ───────────────────────────────────────────────────────────

def test_watchlist_block_separates_held_and_watching():
    stocks = [
        {"id": "A", "name": "삼성전자", "status": "보유", "interest_level": 5},
        {"id": "B", "name": "TSMC", "status": "관찰", "interest_level": 5},
    ]
    block = _format_watchlist_context_block(stocks)
    assert "보유 중 — 1종목" in block
    assert "관찰 중(미보유) — 1종목" in block
    # 각 종목이 올바른 그룹에 들어갔는지 (줄 단위로 확인)
    held_line = next(ln for ln in block.splitlines() if "보유 중" in ln)
    watch_line = next(ln for ln in block.splitlines() if "관찰 중" in ln)
    assert "삼성전자" in held_line and "삼성전자" not in watch_line
    assert "TSMC" in watch_line and "TSMC" not in held_line


def test_watchlist_block_shows_interest_level():
    stocks = [{"id": "A", "name": "NVIDIA", "status": "보유", "interest_level": 5}]
    assert "NVIDIA(관심도5)" in _format_watchlist_context_block(stocks)


def test_watchlist_block_handles_missing_interest_level():
    stocks = [{"id": "A", "name": "무관심주", "status": "보유"}]
    block = _format_watchlist_context_block(stocks)
    assert "무관심주" in block
    assert "관심도None" not in block


def test_watchlist_block_flags_unknown_status():
    """status가 보유/관찰 외 값이거나 비어 있으면 조용히 누락시키지 않고 드러낸다."""
    stocks = [{"id": "A", "name": "정체불명", "status": ""}]
    block = _format_watchlist_context_block(stocks)
    assert "정체불명" in block
    assert "상태미지정" in block


def test_watchlist_block_handles_empty():
    assert "워치리스트 정보 없음" in _format_watchlist_context_block(None)
    assert "워치리스트 정보 없음" in _format_watchlist_context_block([])


# ── 큐레이션 테마 지식 ───────────────────────────────────────────────────────

_THEMES = [
    {
        "id": "AI", "name": "인공지능 (AI)", "interest_level": 5,
        "macro_sensitivity": "high",
        "key_drivers": ["빅테크 AI CapEx 확대", "AI 반도체 수요 급증"],
        "key_risks": ["과잉 투자 사이클 우려", "AI 수익화 지연"],
        "related_stocks": ["US_NVDA", "KR_005930"],
    },
    {
        "id": "ETF", "name": "ETF / 지수 투자", "interest_level": 3,
        "key_drivers": ["패시브 자금 유입"],
        "key_risks": ["지수 집중도 심화"],
        "related_stocks": [],
    },
]
_STOCKS = [
    {"id": "US_NVDA", "name": "NVIDIA"},
    {"id": "KR_005930", "name": "삼성전자"},
]


def test_theme_block_includes_curated_drivers_and_risks():
    block = _format_theme_knowledge_block(_THEMES, _STOCKS)
    assert "빅테크 AI CapEx 확대" in block
    assert "과잉 투자 사이클 우려" in block
    assert "촉진 요인" in block and "리스크" in block


def test_theme_block_resolves_related_stock_names():
    """related_stocks는 ID로 저장되므로 사람이 읽을 이름으로 변환돼야 한다."""
    block = _format_theme_knowledge_block(_THEMES, _STOCKS)
    assert "NVIDIA" in block and "삼성전자" in block
    assert "US_NVDA" not in block  # 원시 ID가 그대로 노출되면 안 됨


def test_theme_block_sorts_by_interest_level():
    block = _format_theme_knowledge_block(_THEMES, _STOCKS)
    assert block.index("인공지능") < block.index("ETF / 지수 투자")


def test_theme_block_shows_macro_sensitivity_when_present():
    block = _format_theme_knowledge_block(_THEMES, _STOCKS)
    assert "거시민감도 high" in block


def test_theme_block_omits_related_line_when_no_watchlist_match():
    """워치리스트에 연결된 종목이 없는 테마도 촉진요인·리스크는 보여준다."""
    block = _format_theme_knowledge_block([_THEMES[1]], _STOCKS)
    assert "패시브 자금 유입" in block
    assert "워치리스트 연결" not in block


def test_theme_block_handles_empty():
    assert "테마 정의 없음" in _format_theme_knowledge_block(None, _STOCKS)
    assert "테마 정의 없음" in _format_theme_knowledge_block([], _STOCKS)
