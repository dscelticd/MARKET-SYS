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


# ── 국내 테마 ETF의 KIS 우선 조회 ────────────────────────────────────────────
# 배경: 개별 종목과 같은 이유로 국내 ETF도 마감 직후 몇 분간 yfinance 종가가
# 정산 중일 수 있다(price_collector에서 실측: 2026-09-18 삼성전자
# +2.87%→5분 뒤 +3.37%로 정정). 대상일과 무관하게 항상 KIS를 먼저 시도한다.

def _kr_universe():
    return [{"id": "battery_kr", "name": "2차전지(국내)", "ticker": "305720.KS", "market": "KR"}]


def test_kr_theme_etf_prefers_kis_even_when_yfinance_date_matches():
    import pandas as pd
    hist = pd.DataFrame(
        {"Close": [14700.0, 14300.0]},  # yfinance = 아직 정산 전 값(예: -2.72%)
        index=pd.to_datetime(["2026-09-17", "2026-09-18"]),
    )
    kis_quote = {"value": 14670.0, "prev_close": 14625.0, "change_pct": 0.31, "data_date": "2026-09-18"}

    with patch("app.collectors.theme_scanner._load_theme_universe", return_value=_kr_universe()), \
         patch("yfinance.Ticker", return_value=MagicMock(history=MagicMock(return_value=hist))), \
         patch("app.collectors.theme_scanner._kis_collector.is_configured", return_value=True), \
         patch("app.collectors.theme_scanner._kis_collector.fetch_stock_price", return_value=kis_quote), \
         patch("app.collectors.theme_scanner.market_session_state", return_value="마감"):
        results = scan_theme_strength(use_mock=False, target={"KR": "2026-09-18", "US": "2026-09-17"})

    assert len(results) == 1
    assert results[0]["change_pct"] == 0.31   # yfinance의 -2.72%가 아니라 KIS 확정치
    assert results[0]["price"] == 14670.0
    assert results[0]["data_date"] == "2026-09-18"


def test_kr_theme_etf_kis_attempt_is_unconditional_on_date_match():
    """KIS 시도가 "yfinance가 대상일을 안 줄 때만"으로 게이트되면 정산 중
    오차를 놓친다 — 날짜 일치 여부와 무관하게 항상 시도해야 한다."""
    import pandas as pd
    hist = pd.DataFrame(
        {"Close": [14700.0, 14300.0]},
        index=pd.to_datetime(["2026-09-17", "2026-09-18"]),
    )
    kis_quote = {"value": 14670.0, "prev_close": 14625.0, "change_pct": 0.31, "data_date": "2026-09-18"}

    with patch("app.collectors.theme_scanner._load_theme_universe", return_value=_kr_universe()), \
         patch("yfinance.Ticker", return_value=MagicMock(history=MagicMock(return_value=hist))), \
         patch("app.collectors.theme_scanner._kis_collector.is_configured", return_value=True), \
         patch("app.collectors.theme_scanner._kis_collector.fetch_stock_price",
               return_value=kis_quote) as kis_call, \
         patch("app.collectors.theme_scanner.market_session_state", return_value="마감"):
        scan_theme_strength(use_mock=False, target={"KR": "2026-09-18", "US": "2026-09-17"})

    kis_call.assert_called_once()


def test_kr_theme_etf_skips_kis_while_market_is_open():
    """target_date가 실수로 당일(장중)이면 KIS의 당일 행은 미확정 실시간가다
    — 확정 종가로 오인하지 않도록 보류한다."""
    import pandas as pd
    hist = pd.DataFrame(
        {"Close": [14700.0, 14300.0]},
        index=pd.to_datetime(["2026-09-17", "2026-09-18"]),
    )

    with patch("app.collectors.theme_scanner._load_theme_universe", return_value=_kr_universe()), \
         patch("yfinance.Ticker", return_value=MagicMock(history=MagicMock(return_value=hist))), \
         patch("app.collectors.theme_scanner.now_kst") as nk, \
         patch("app.collectors.theme_scanner.market_session_state", return_value="개장중"), \
         patch("app.collectors.theme_scanner._kis_collector.fetch_stock_price") as kis_call:
        nk.return_value.strftime.return_value = "2026-09-18"
        scan_theme_strength(use_mock=False, target={"KR": "2026-09-18", "US": "2026-09-17"})

    kis_call.assert_not_called()


def test_kr_theme_etf_falls_back_to_yfinance_when_kis_fails():
    import pandas as pd
    hist = pd.DataFrame(
        {"Close": [14700.0, 14625.0]},
        index=pd.to_datetime(["2026-09-17", "2026-09-18"]),
    )

    with patch("app.collectors.theme_scanner._load_theme_universe", return_value=_kr_universe()), \
         patch("yfinance.Ticker", return_value=MagicMock(history=MagicMock(return_value=hist))), \
         patch("app.collectors.theme_scanner._kis_collector.is_configured", return_value=True), \
         patch("app.collectors.theme_scanner._kis_collector.fetch_stock_price",
               side_effect=TimeoutError("read timed out")), \
         patch("app.collectors.theme_scanner.market_session_state", return_value="마감"):
        results = scan_theme_strength(use_mock=False, target={"KR": "2026-09-18", "US": "2026-09-17"})

    assert len(results) == 1
    assert results[0]["price"] == 14625.0
    assert results[0]["data_date"] == "2026-09-18"


def test_us_theme_etf_never_calls_kis():
    import pandas as pd
    hist = pd.DataFrame({"Close": [100.0, 105.0]}, index=pd.to_datetime(["2026-09-17", "2026-09-18"]))
    fake_universe = [{"id": "a", "name": "A테마", "ticker": "AAA", "market": "US"}]

    with patch("app.collectors.theme_scanner._load_theme_universe", return_value=fake_universe), \
         patch("yfinance.Ticker", return_value=MagicMock(history=MagicMock(return_value=hist))), \
         patch("app.collectors.theme_scanner._kis_collector.fetch_stock_price") as kis_call:
        results = scan_theme_strength(use_mock=False, target={"KR": "2026-09-18", "US": "2026-09-18"})

    kis_call.assert_not_called()
    assert len(results) == 1 and results[0]["change_pct"] == 5.0
