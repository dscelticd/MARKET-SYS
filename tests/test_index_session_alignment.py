"""
지수·차트·테마의 대상 거래일 정렬 테스트 — 계약 C1·C2 적용 누락분

배경 (놓쳤던 것): 가격 수집기만 대상 거래일을 받게 하고 나머지 경로를 빠뜨렸다.
      실측 2026-09-02 09:03 실행분에서 종목 18개는 전부 9/1이었는데
      KOSPI·KOSDAQ은 9/2 장중값 6630.53(-3.00%)으로 수집됐고, 리포트는
      "모든 가격·등락률은 2026년 9월 1일 종가 기준"이라 선언한 채 나갔다.

      더 나빴던 것은 계약 위반 탐지기가 price_data의 기준일만 검사해서
      이 모순을 잡지 못했다는 점이다. 탐지기가 검사하지 않는 경로는
      계약 밖에 있는 것과 같다.

      KIS의 _is_completed_session()도 장전 자리표시자(시가=고가=저가)만
      걸러낼 뿐이라, 개장 3분 뒤의 행은 고가≠저가라서 "완료"로 통과했다.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

pd = pytest.importorskip("pandas")

from app.collectors.macro_collector import _symbol_market, _truncate_hist  # noqa: E402
from app.reports.report_builder import _format_market_session_block  # noqa: E402


def _hist(dates, closes):
    return pd.DataFrame({"Close": closes}, index=pd.to_datetime(dates))


# ── 거시 지표 ────────────────────────────────────────────────────────────────

def test_kr_indices_follow_the_korean_target_others_follow_us():
    assert _symbol_market("^KS11") == "KR"
    assert _symbol_market("^KQ11") == "KR"
    assert _symbol_market("^GSPC") == "US"
    assert _symbol_market("KRW=X") == "US"    # 24시간 거래 — 미국 기준일에 맞춘다


def test_macro_history_is_truncated_to_the_target():
    h = _truncate_hist(_hist(["2026-08-31", "2026-09-01", "2026-09-02"],
                             [6820.0, 6835.8, 6630.5]), "2026-09-01")
    assert len(h) == 2
    assert float(h["Close"].iloc[-1]) == 6835.8   # 9/2 장중값 6630.5가 아니다


# ── KIS 지수 ─────────────────────────────────────────────────────────────────

def _kis_response(rows):
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"rt_cd": "0", "output": rows}
    return r


def _row(date_yyyymmdd, price, chg, high, low):
    return {"stck_bsop_date": date_yyyymmdd, "bstp_nmix_prpr": str(price),
            "bstp_nmix_prdy_ctrt": str(chg), "bstp_nmix_hgpr": str(high),
            "bstp_nmix_lwpr": str(low)}


def _fetch(rows, target_date, tmp_path, monkeypatch):
    import app.collectors.kis_collector as mod
    monkeypatch.setenv("KIS_APP_KEY", "k"); monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ENV", "demo")
    monkeypatch.setattr(mod, "_TOKEN_CACHE_FILE", tmp_path / "t.json")
    c = mod.KISCollector()
    with patch.object(mod.KISCollector, "get_token", return_value="tok"), \
         patch.object(mod.requests, "get", return_value=_kis_response(rows)):
        return c.fetch_market_index("KOSPI", target_date=target_date)


def test_kis_skips_the_in_progress_session_row(tmp_path, monkeypatch):
    """개장 3분 뒤의 행은 고가≠저가라 _is_completed_session()을 통과한다.
    대상 날짜를 지목해야만 걸러진다 — 실제로 이 행이 리포트에 들어갔다."""
    rows = [_row("20260902", 6630.53, -3.00, 6700, 6600),   # 9/2 장중
            _row("20260901", 6835.80, 0.23, 6850, 6790)]    # 9/1 종가
    got = _fetch(rows, "2026-09-01", tmp_path, monkeypatch)
    assert got["data_date"] == "2026-09-01"
    assert got["value"] == 6835.80


def test_kis_raises_when_the_target_day_is_absent(tmp_path, monkeypatch):
    """대상일이 없으면 다른 날 값을 대신 쓰지 않고 실패시킨다 (계약 C3)."""
    import app.collectors.kis_collector as mod
    with pytest.raises((ValueError, RuntimeError)):
        _fetch([_row("20260902", 6630.53, -3.0, 6700, 6600)],
               "2026-09-01", tmp_path, monkeypatch)


# ── 계약 위반 탐지 ───────────────────────────────────────────────────────────

def test_tripwire_catches_stray_index_dates():
    """탐지기가 종목만 보던 탓에 실제 위반을 놓쳤다. 지수도 검사해야 한다."""
    b = _format_market_session_block(
        {"date_counts": {"2026-09-01": 18}},
        {"data_dates": {"SP500": "2026-09-01", "KOSPI": "2026-09-02"}},
        None,
        target={"kr_date": "2026-09-01", "us_date": "2026-09-01", "report_type": "morning"},
    )
    assert "🚨" in b and "지수가 섞였습니다" in b and "KOSPI 2026-09-02" in b


def test_tripwire_stays_quiet_when_indices_match_the_target():
    b = _format_market_session_block(
        {"date_counts": {"2026-09-02": 18}},
        {"data_dates": {"SP500": "2026-09-01", "KOSPI": "2026-09-02"}},
        None,
        target={"kr_date": "2026-09-02", "us_date": "2026-09-01", "report_type": "evening"},
    )
    assert "🚨" not in b


# ── 차트·테마 ────────────────────────────────────────────────────────────────

def test_chart_history_stops_at_the_target_day():
    """본문은 9/1 기준인데 차트 마지막 봉만 9/2 장중이면 같은 메일 안에서
    숫자와 그림이 어긋난다."""
    from app.reports.chart_generator import _truncate_to
    h = _truncate_to(_hist(["2026-08-31", "2026-09-01", "2026-09-02"], [1, 2, 3]),
                     "2026-09-01")
    assert len(h) == 2


def test_theme_scanner_classifies_korean_etfs_separately():
    """테마 유니버스에 XLK 같은 미국 ETF와 305720.KS 같은 국내 ETF가 섞여 있다.
    미국 기준일을 일괄 적용하면 국내 ETF가 하루 어긋난다."""
    import inspect
    from app.collectors.theme_scanner import scan_theme_strength
    src = inspect.getsource(scan_theme_strength)
    assert '.KS' in src and 'target or {}' in src
    assert "target" in inspect.signature(scan_theme_strength).parameters


def test_pipeline_passes_target_to_every_market_data_path():
    """수집 경로 하나라도 빠지면 그 경로만 다른 날짜를 보게 된다."""
    import pathlib, re
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text(encoding="utf-8")
    body = re.sub(r"#.*", "", src)
    for call in ("price_col.collect(", "macro_col.collect(",
                 "scan_theme_strength(", "generate_report_charts("):
        i = body.index(call)
        assert "target" in body[i:i + 260], f"{call} 에 대상 거래일이 전달되지 않음"


def test_news_recency_is_distinguished_from_the_price_basis():
    """뉴스는 일부러 자르지 않는다(아침 브리핑에 밤사이 소식이 빠지면 안 된다).
    대신 시점이 다르다는 사실을 못박아야, 나중 뉴스로 앞선 등락을 설명하는
    거꾸로 된 인과가 만들어지지 않는다."""
    b = _format_market_session_block(
        {}, None, None,
        target={"kr_date": "2026-09-01", "us_date": "2026-09-01", "report_type": "morning"},
    )
    assert "뉴스·공시는" in b and "최신 정보" in b
    assert "기준일의 등락을 설명하지 마세요" in b
