"""
포트폴리오 단위 테마/섹터 집중도 및 당일 동조화 진단 테스트

배경: 우선순위 로드맵 4순위 — 지금까지 종목을 각각 독립적으로만 평가하고
      "워치리스트 전체가 특정 테마에 얼마나 쏠려 있는가" 같은 포트폴리오 관점의
      진단이 없었음. 실제 통계적 상관계수는 종목별 추가 과거 시세 수집이 필요해
      비용이 크므로, 이미 수집된 데이터로 계산 가능한 두 지표(테마/섹터 집중도,
      당일 동조화율)로 근사한다.
"""
from __future__ import annotations

from app.engine.portfolio_analyzer import (
    compute_theme_concentration,
    compute_sector_concentration,
    compute_directional_alignment,
    build_portfolio_summary,
)
from app.reports.report_builder import _format_portfolio_block


def _stocks():
    return [
        {"id": "A", "name": "A사", "sector": "반도체", "themes": ["AI", "반도체"]},
        {"id": "B", "name": "B사", "sector": "반도체", "themes": ["AI", "반도체", "HBM"]},
        {"id": "C", "name": "C사", "sector": "ETF", "themes": ["배당"]},
    ]


def test_theme_concentration_counts_and_percentages():
    result = compute_theme_concentration(_stocks())
    by_theme = {r["theme"]: r for r in result}
    assert by_theme["AI"]["count"] == 2
    assert by_theme["AI"]["pct"] == round(2 / 3 * 100, 1)
    assert by_theme["HBM"]["count"] == 1
    assert set(by_theme["AI"]["stocks"]) == {"A사", "B사"}


def test_theme_concentration_sorted_descending_by_count():
    result = compute_theme_concentration(_stocks())
    counts = [r["count"] for r in result]
    assert counts == sorted(counts, reverse=True)


def test_theme_concentration_empty_stocks_returns_empty():
    assert compute_theme_concentration([]) == []


def test_sector_concentration_sums_to_100_pct():
    result = compute_sector_concentration(_stocks())
    assert sum(r["pct"] for r in result) == 100.0
    by_sector = {r["sector"]: r for r in result}
    assert by_sector["반도체"]["count"] == 2


def test_directional_alignment_counts_up_down_flat():
    price_data = {
        "A": {"change_pct": 1.5}, "B": {"change_pct": 2.0},
        "C": {"change_pct": -0.5}, "D": {"change_pct": 0.0},
    }
    result = compute_directional_alignment(price_data)
    assert result["up"] == 2
    assert result["down"] == 1
    assert result["flat"] == 1
    assert result["majority_direction"] == "상승"
    assert result["alignment_pct"] == 50.0  # 다수(2) / 전체(4)


def test_directional_alignment_handles_missing_change_pct():
    price_data = {"A": {"change_pct": 1.0}, "B": {}}
    result = compute_directional_alignment(price_data)
    assert result["total"] == 1


def test_directional_alignment_empty_returns_empty_dict():
    assert compute_directional_alignment({}) == {}


def test_build_portfolio_summary_flags_high_concentration():
    stocks = [
        {"id": f"S{i}", "name": f"{i}호", "sector": "반도체", "themes": ["AI"]}
        for i in range(5)
    ]
    price_data = {f"S{i}": {"change_pct": 1.0} for i in range(5)}
    summary = build_portfolio_summary(stocks, price_data)
    assert any("AI" in flag for flag in summary["risk_flags"])
    assert any("반도체" in flag for flag in summary["risk_flags"])


def test_build_portfolio_summary_no_flags_when_well_diversified():
    stocks = [
        {"id": "A", "name": "A", "sector": "반도체", "themes": ["AI"]},
        {"id": "B", "name": "B", "sector": "ETF", "themes": ["배당"]},
        {"id": "C", "name": "C", "sector": "광통신", "themes": ["광통신"]},
    ]
    price_data = {sid: {"change_pct": 1.0} for sid in ("A", "B", "C")}
    summary = build_portfolio_summary(stocks, price_data)
    assert summary["risk_flags"] == []


def test_format_portfolio_block_includes_top_themes_and_alignment():
    stocks = _stocks()
    price_data = {"A": {"change_pct": 1.0}, "B": {"change_pct": 2.0}, "C": {"change_pct": -0.5}}
    summary = build_portfolio_summary(stocks, price_data)
    block = _format_portfolio_block(summary)
    assert "AI" in block
    assert "동조화" in block


def test_format_portfolio_block_excludes_single_stock_themes():
    """여러 종목 중 1개 종목에만 있는 테마는 '집중도'라 부를 수 없고 위험 수준도
    아니므로(비중 낮음) 상위 테마·리스크 경고 어디에도 나타나지 않아야 함
    """
    stocks = [
        {"id": "A", "name": "A", "sector": "반도체", "themes": ["AI", "반도체"]},
        {"id": "B", "name": "B", "sector": "반도체", "themes": ["AI", "반도체"]},
        {"id": "C", "name": "C", "sector": "ETF", "themes": ["희귀테마"]},
    ]
    price_data = {sid: {"change_pct": 1.0} for sid in ("A", "B", "C")}
    summary = build_portfolio_summary(stocks, price_data)
    block = _format_portfolio_block(summary)
    assert "희귀테마" not in block
    assert "AI" in block


def test_format_portfolio_block_handles_none():
    assert "포트폴리오 집중도 데이터 없음" in _format_portfolio_block(None)
    assert "포트폴리오 집중도 데이터 없음" in _format_portfolio_block({})
