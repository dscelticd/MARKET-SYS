"""
시장 전체 테마 강약 스캔 테스트 (워치리스트 밖 섹터/테마 ETF)

배경: 사용자 요청 — 현재 워치리스트(18종목)가 속한 테마 외에, 시장 전체에서
      어떤 섹터/테마가 강세·약세인지 참고할 수 있는 기능. 개별 종목 심층분석과
      달리 등락률만 가볍게 조회하는 보조 진단 계층. 24개 후보 ETF 티커 전부
      실제 yfinance 데이터로 사전 검증 완료(2026-08-11).
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from app.collectors.theme_scanner import scan_theme_strength, _load_theme_universe
from app.reports.report_builder import _format_theme_scan_block


def test_theme_universe_loads_and_has_required_fields():
    universe = _load_theme_universe()
    assert len(universe) > 0
    for theme in universe:
        assert "id" in theme and "name" in theme and "ticker" in theme and "market" in theme


def test_scan_theme_strength_mock_mode_returns_sorted_results():
    results = scan_theme_strength(use_mock=True)
    assert len(results) > 0
    changes = [r["change_pct"] for r in results]
    assert changes == sorted(changes, reverse=True)
    assert all(r["_mock"] is True for r in results)


def test_scan_theme_strength_real_mode_sorts_and_skips_failures():
    fake_universe = [
        {"id": "a", "name": "A테마", "ticker": "AAA", "market": "US"},
        {"id": "b", "name": "B테마", "ticker": "BBB", "market": "US"},
    ]

    def fake_ticker(symbol):
        mock_t = MagicMock()
        import pandas as pd
        if symbol == "AAA":
            mock_t.history.return_value = pd.DataFrame({"Close": [100.0, 105.0]})
        else:
            # BBB 실패 시뮬레이션
            mock_t.history.side_effect = RuntimeError("network error")
        return mock_t

    with patch("app.collectors.theme_scanner._load_theme_universe", return_value=fake_universe), \
         patch("yfinance.Ticker", side_effect=fake_ticker):
        results = scan_theme_strength(use_mock=False)

    assert len(results) == 1  # BBB는 실패해서 제외
    assert results[0]["id"] == "a"
    assert results[0]["change_pct"] == 5.0


def test_scan_theme_strength_returns_empty_when_universe_missing():
    with patch("app.collectors.theme_scanner._load_theme_universe", return_value=[]):
        assert scan_theme_strength(use_mock=True) == []


def test_format_theme_scan_block_shows_top_and_bottom_five():
    # 12개 중 상위 5(인덱스 0~4)·하위 5(인덱스 7~11)만 노출 — 인덱스 5·6은 중간권이라 생략
    theme_scan = [
        {"id": f"t{i}", "name": f"테마{i}", "ticker": f"T{i}", "market": "US", "change_pct": 12 - i}
        for i in range(12)
    ]
    block = _format_theme_scan_block(theme_scan)
    assert "강세 테마" in block
    assert "약세 테마" in block
    assert "테마0" in block   # 1위(가장 강세)
    assert "테마11" in block  # 꼴찌(가장 약세)
    assert "테마5" not in block  # 중간권은 생략
    assert "테마6" not in block


def test_format_theme_scan_block_handles_empty():
    assert "테마 스캔 데이터 없음" in _format_theme_scan_block(None)
    assert "테마 스캔 데이터 없음" in _format_theme_scan_block([])
