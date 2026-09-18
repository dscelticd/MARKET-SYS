"""
KIS(한국투자증권) 오픈API 수급 데이터 수집 테스트 — 실제 네트워크 호출 없이 검증

배경: 한국투자증권 계좌 개설 후 앱키 발급 완료 — 기존 네이버 스크래핑(비공식,
      개인은 잔차 추정)보다 공식적이고 개인 순매수를 실측값으로 제공하는 KIS API를
      1순위 수급 소스로 연동. 네이버는 2순위 폴백, Mock은 최종 폴백으로 유지.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.collectors.kis_collector import KISCollector, _summarize_daily_flows


def _make_collector(tmp_path, monkeypatch, app_key="test-key", app_secret="test-secret"):
    monkeypatch.setenv("KIS_APP_KEY", app_key)
    monkeypatch.setenv("KIS_APP_SECRET", app_secret)
    monkeypatch.setenv("KIS_ENV", "demo")
    import app.collectors.kis_collector as mod
    monkeypatch.setattr(mod, "_TOKEN_CACHE_FILE", tmp_path / "kis_token.json")
    return KISCollector()


def test_is_configured_true_when_both_keys_set(tmp_path, monkeypatch):
    collector = _make_collector(tmp_path, monkeypatch)
    assert collector.is_configured() is True


def test_is_configured_false_when_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.setenv("KIS_APP_SECRET", "secret-only")
    import app.collectors.kis_collector as mod
    monkeypatch.setattr(mod, "_TOKEN_CACHE_FILE", tmp_path / "kis_token.json")
    collector = KISCollector()
    assert collector.is_configured() is False


def test_summarize_daily_flows_computes_cumulative_windows():
    daily = [
        {"institution_net": 100, "foreign_net": 200, "individual_net": -300},
        {"institution_net": -50, "foreign_net": 300, "individual_net": -250},
        {"institution_net": 10, "foreign_net": -20, "individual_net": 10},
    ]
    summary = _summarize_daily_flows(daily)
    assert summary["_source"] == "kis"
    assert summary["_mock"] is False
    assert summary["institution_net_3d"] == 60
    assert summary["foreign_net_3d"] == 480
    assert summary["individual_net_3d"] == -540
    # 개인은 실측값이라 "_est" 접미사가 붙지 않아야 함
    assert "individual_net_3d_est" not in summary


def test_get_token_fetches_and_caches(tmp_path, monkeypatch):
    collector = _make_collector(tmp_path, monkeypatch)
    mock_response = MagicMock()
    mock_response.json.return_value = {"access_token": "abc123", "expires_in": 86400}
    mock_response.raise_for_status.return_value = None

    with patch("app.collectors.kis_collector.requests.post", return_value=mock_response) as mock_post:
        token = collector.get_token()
        assert token == "abc123"
        mock_post.assert_called_once()

        # 캐시에서 재사용 — 두 번째 호출은 네트워크 요청 없이 동일 토큰 반환
        token2 = collector.get_token()
        assert token2 == "abc123"
        mock_post.assert_called_once()  # 여전히 1회만 호출됨


def test_get_token_returns_none_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    import app.collectors.kis_collector as mod
    monkeypatch.setattr(mod, "_TOKEN_CACHE_FILE", tmp_path / "kis_token.json")
    collector = KISCollector()
    assert collector.get_token() is None


def test_get_token_ignores_stale_cache_from_different_app_key(tmp_path, monkeypatch):
    collector = _make_collector(tmp_path, monkeypatch, app_key="key-A")
    import app.collectors.kis_collector as mod
    mod._TOKEN_CACHE_FILE.write_text(
        '{"app_key": "key-B", "access_token": "stale", "expires_at": ' + str(time.time() + 9999) + '}',
        encoding="utf-8",
    )
    mock_response = MagicMock()
    mock_response.json.return_value = {"access_token": "fresh-token", "expires_in": 86400}
    mock_response.raise_for_status.return_value = None
    with patch("app.collectors.kis_collector.requests.post", return_value=mock_response):
        token = collector.get_token()
    assert token == "fresh-token"


def test_fetch_investor_flow_parses_response_fields(tmp_path, monkeypatch):
    collector = _make_collector(tmp_path, monkeypatch)

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "abc123", "expires_in": 86400}
    token_response.raise_for_status.return_value = None

    data_response = MagicMock()
    data_response.raise_for_status.return_value = None
    data_response.json.return_value = {
        "rt_cd": "0",
        "output": [
            {"prsn_ntby_qty": "-1000", "frgn_ntby_qty": "2000", "orgn_ntby_qty": "-500"},
            {"prsn_ntby_qty": "500", "frgn_ntby_qty": "-300", "orgn_ntby_qty": "100"},
            {"prsn_ntby_qty": "200", "frgn_ntby_qty": "100", "orgn_ntby_qty": "-50"},
        ],
    }

    with patch("app.collectors.kis_collector.requests.post", return_value=token_response), \
         patch("app.collectors.kis_collector.requests.get", return_value=data_response):
        flow = collector.fetch_investor_flow("005930")

    assert flow["_source"] == "kis"
    assert flow["individual_net_3d"] == -1000 + 500 + 200
    assert flow["foreign_net_3d"] == 2000 - 300 + 100


def test_fetch_investor_flow_raises_on_api_error(tmp_path, monkeypatch):
    collector = _make_collector(tmp_path, monkeypatch)
    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "abc123", "expires_in": 86400}
    token_response.raise_for_status.return_value = None

    error_response = MagicMock()
    error_response.raise_for_status.return_value = None
    error_response.json.return_value = {"rt_cd": "1", "msg1": "잘못된 요청"}

    with patch("app.collectors.kis_collector.requests.post", return_value=token_response), \
         patch("app.collectors.kis_collector.requests.get", return_value=error_response):
        with pytest.raises(RuntimeError):
            collector.fetch_investor_flow("005930")


def test_fetch_investor_flow_raises_when_no_token(tmp_path, monkeypatch):
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    import app.collectors.kis_collector as mod
    monkeypatch.setattr(mod, "_TOKEN_CACHE_FILE", tmp_path / "kis_token.json")
    collector = KISCollector()
    with pytest.raises(RuntimeError):
        collector.fetch_investor_flow("005930")


# ── fetch_stock_price — 개별 종목 종가 (계약 C2·C3, yfinance 발표 지연 대응) ──
# 배경: yfinance의 국내 종목 종가 발표가 수 시간~다음날까지 늦어지는 사례가
# 반복 관측됐다(실측 2026-09-14~18, 5거래일 연속). KIS는 증권사 자체 체결
# 데이터라 이런 지연이 없어, 개별 종목에도 지수와 같은 원리로 도입했다.

def _daily_price_response(rows):
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"rt_cd": "0", "output": rows}
    return r


def _price_row(date_yyyymmdd, close, prdy_ctrt, volume):
    return {"stck_bsop_date": date_yyyymmdd, "stck_clpr": str(close),
            "prdy_ctrt": str(prdy_ctrt), "acml_vol": str(volume)}


def test_fetch_stock_price_returns_the_target_date_row(tmp_path, monkeypatch):
    collector = _make_collector(tmp_path, monkeypatch)
    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "abc123", "expires_in": 86400}
    token_response.raise_for_status.return_value = None
    rows = [_price_row("20260918", 260500, 3.17, 100),   # 오늘(장중) — 골라내면 안 됨
            _price_row("20260917", 252500, -0.39, 11_827_514),
            _price_row("20260916", 253500, 2.01, 11_757_106)]
    with patch("app.collectors.kis_collector.requests.post", return_value=token_response), \
         patch("app.collectors.kis_collector.requests.get", return_value=_daily_price_response(rows)):
        result = collector.fetch_stock_price("005930", target_date="2026-09-17")
    assert result["value"] == 252500.0
    assert result["prev_close"] == 253500.0   # API가 준 직전 행을 그대로 사용
    assert result["change_pct"] == -0.39
    assert result["volume"] == 11_827_514
    assert result["data_date"] == "2026-09-17"


def test_fetch_stock_price_ignores_the_in_progress_session_row(tmp_path, monkeypatch):
    """오늘(장중) 행은 미확정 실시간가다. target_date로 지목하지 않으면
    걸러져야 한다 — 장중값을 확정 종가로 오인하면 LG전자 사고가 재발한다."""
    collector = _make_collector(tmp_path, monkeypatch)
    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "abc123", "expires_in": 86400}
    token_response.raise_for_status.return_value = None
    rows = [_price_row("20260918", 260500, 3.17, 100),
            _price_row("20260917", 252500, -0.39, 11_827_514)]
    with patch("app.collectors.kis_collector.requests.post", return_value=token_response), \
         patch("app.collectors.kis_collector.requests.get", return_value=_daily_price_response(rows)):
        with pytest.raises(ValueError):
            collector.fetch_stock_price("005930", target_date="2026-09-19")  # 존재하지 않는 날


def test_fetch_stock_price_retries_transient_failures(tmp_path, monkeypatch):
    collector = _make_collector(tmp_path, monkeypatch)
    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "abc123", "expires_in": 86400}
    token_response.raise_for_status.return_value = None
    rows = [_price_row("20260917", 252500, -0.39, 11_827_514),
            _price_row("20260916", 253500, 2.01, 11_757_106)]

    calls = {"n": 0}
    def _flaky_get(*a, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("read timed out")
        return _daily_price_response(rows)

    with patch("app.collectors.kis_collector.requests.post", return_value=token_response), \
         patch("app.collectors.kis_collector.requests.get", side_effect=_flaky_get), \
         patch("app.collectors.kis_collector.time.sleep"):
        result = collector.fetch_stock_price("005930", target_date="2026-09-17", retries=3)
    assert result["value"] == 252500.0
    assert calls["n"] == 3


def test_fetch_stock_price_raises_after_exhausting_retries(tmp_path, monkeypatch):
    collector = _make_collector(tmp_path, monkeypatch)
    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "abc123", "expires_in": 86400}
    token_response.raise_for_status.return_value = None
    with patch("app.collectors.kis_collector.requests.post", return_value=token_response), \
         patch("app.collectors.kis_collector.requests.get", side_effect=TimeoutError("read timed out")), \
         patch("app.collectors.kis_collector.time.sleep"):
        with pytest.raises(TimeoutError):
            collector.fetch_stock_price("005930", target_date="2026-09-17", retries=1)
