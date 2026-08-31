"""
거시지표 수집 테스트

배경: macro_collector는 실패 시 raise로 파이프라인을 세우는 **필수 경로**인데
      테스트가 하나도 없었다. 동시에 하드코딩이 가장 많이 모인 파일이기도 하다
      (기준금리 4건, FOMC·금통위 일정 8건). 필수 경로 · 테스트 없음 · 수동 갱신
      의존이 한 파일에 겹쳐 있어 구조 검토에서 "가장 약한 고리"로 지목됐다.

      또한 이 파일에는 시장 전체 외국인·기관 순매수를
      `kospi_chg * random.uniform(150, 400)`으로 만들어 리포트가 사실처럼 서술하던
      난수 필드가 있었다(같은 데이터 3회 실행 시 -279억/-292억/-223억). 실측 대체가
      불가능해(KIS 모의투자는 해당 필드를 전부 0으로 반환) 필드를 제거했으며,
      아래 테스트가 재도입을 막는다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from app.collectors.macro_collector import (
    MacroCollector,
    _BOK_ALL,
    _FOMC_ALL,
    _next_meeting_date,
    get_upcoming_policy_meetings,
)

_SECTIONS = ("us_market", "kr_market", "currencies", "rates", "commodities", "sentiment")


# ── Mock 경로 (스키마 계약) ──────────────────────────────────────────────────

def test_mock_returns_all_expected_sections():
    m = MacroCollector()._collect_mock()
    for key in _SECTIONS:
        assert key in m, f"{key} 누락"
    assert m["_mock"] is True


def test_mock_and_real_share_section_schema():
    """Mock과 실데이터의 섹션 구조가 어긋나면 리포트 포맷터가 한쪽에서만 깨진다."""
    mock = MacroCollector()._collect_mock()
    # 실호출 없이 구조만 비교하기 위해 yfinance 임포트 실패를 유도 → Mock 폴백 경로
    with patch.dict("sys.modules", {"yfinance": None}):
        fallback = MacroCollector()._collect_real()
    assert set(mock.keys()) == set(fallback.keys())


def test_collect_respects_use_mock_env(monkeypatch):
    monkeypatch.setenv("USE_MOCK_DATA", "true")
    assert MacroCollector().collect()["_mock"] is True


def test_real_path_falls_back_to_mock_when_yfinance_missing():
    """필수 경로지만 라이브러리 부재는 예외가 아니라 Mock 폴백으로 처리한다."""
    with patch.dict("sys.modules", {"yfinance": None}):
        out = MacroCollector()._collect_real()
    assert out["_mock"] is True


# ── 난수 수급 필드 재도입 방지 ───────────────────────────────────────────────

@pytest.mark.parametrize("build", ["mock", "fallback"])
def test_fabricated_market_flow_fields_are_gone(build):
    """`kospi_chg * random.uniform(...)`으로 만든 시장 전체 순매수 필드는
    리포트가 사실로 서술해버려 제거했다 — 어떤 경로로도 되살아나면 안 된다."""
    if build == "mock":
        m = MacroCollector()._collect_mock()
    else:
        with patch.dict("sys.modules", {"yfinance": None}):
            m = MacroCollector()._collect_real()
    kr = m["kr_market"]
    assert "foreign_net_buy_bn" not in kr
    assert "institution_net_buy_bn" not in kr
    assert "_foreign_estimated" not in kr


def test_macro_block_no_longer_reports_market_wide_foreign_flow():
    from app.reports.report_builder import _format_macro_block
    block = _format_macro_block(MacroCollector()._collect_mock())
    assert "외국인 순매수" not in block


# ── 정책회의 캘린더 (수동 갱신 대상) ─────────────────────────────────────────

def test_policy_meeting_dates_are_well_formed_and_sorted():
    """매년 수동 갱신하는 값이라 형식이 깨지면 조용히 잘못된 날짜가 나간다."""
    for dates in (_FOMC_ALL, _BOK_ALL):
        assert dates == sorted(dates), "정렬되어 있어야 이분 탐색·최근일 계산이 맞는다"
        for d in dates:
            datetime.strptime(d, "%Y-%m-%d")  # 형식 오류면 예외


def test_policy_calendar_covers_current_year_forward():
    """올해 이후 일정이 남아 있어야 '다음 회의일' 계산이 의미를 가진다.
    이 테스트가 깨지면 연간 수동 갱신을 놓쳤다는 신호다."""
    today = date.today().isoformat()
    assert any(d >= today for d in _FOMC_ALL), "FOMC 일정 갱신 필요"
    assert any(d >= today for d in _BOK_ALL), "금통위 일정 갱신 필요"


def test_next_meeting_returns_first_future_date():
    dates = ["2020-01-01", "2099-06-01", "2099-12-01"]
    assert _next_meeting_date(dates) == "2099-06-01"


def test_next_meeting_falls_back_to_last_when_all_past():
    assert _next_meeting_date(["2020-01-01", "2020-06-01"]) == "2020-06-01"


def test_upcoming_policy_meetings_filters_to_window():
    fixed = datetime(2026, 9, 10)
    with patch("app.collectors.macro_collector.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        mock_dt.strptime = datetime.strptime
        events = get_upcoming_policy_meetings(days_ahead=14)
    assert events, "9/16 FOMC가 14일 창에 들어와야 한다"
    for e in events:
        assert fixed.date().isoformat() <= e["date"] <= (fixed + timedelta(days=14)).date().isoformat()
        assert e["category"] == "policy"
        assert e["country"] in ("US", "KR")


def test_upcoming_policy_meetings_empty_window_returns_empty():
    fixed = datetime(2026, 9, 1)
    with patch("app.collectors.macro_collector.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        mock_dt.strptime = datetime.strptime
        assert get_upcoming_policy_meetings(days_ahead=3) == []


# ── 하드코딩된 금리 값 ───────────────────────────────────────────────────────

def test_hardcoded_policy_rates_are_plausible():
    """기준금리는 실시간 조회가 아니라 코드에 박힌 last_known 값이다.
    갱신을 놓쳐도 오류가 나지 않으므로, 최소한 값이 상식적 범위인지는 지킨다."""
    with patch.dict("sys.modules", {"yfinance": None}):
        rates = MacroCollector()._collect_real()["rates"]
    for key in ("fed_funds_rate", "kr_base_rate"):
        val = rates.get(key, {}).get("value")
        assert isinstance(val, (int, float)), f"{key} 값이 숫자가 아님"
        assert 0.0 <= val <= 15.0, f"{key}={val} — 정상 범위를 벗어남"


def test_sentiment_derives_labels_from_vix():
    m = MacroCollector()._collect_mock()
    sent = m["sentiment"]
    assert "fear_greed_index" in sent
    assert "global_risk_appetite" in sent


# ── 국내 지수 KIS 우선 사용 ──────────────────────────────────────────────────
# 배경: yfinance의 ^KS11·^KQ11 피드가 거래일을 통째로 누락하는 사고가 발생했다.
# 실측(2026-09-01): ^KS11이 8/27에 멈춰 8/28·8/31 두 거래일을 빠뜨린 채
# "6912.37 (+1.53%)"로 응답했으나 실제 8/31 종가는 6820.02였다. 같은 시점
# 삼성전자 등 개별 종목은 8/31까지 정상이라 지수만 어긋났고, 그 결과 리포트가
# "미국 하락 + 한국 상승 디커플링"이라는 실재하지 않는 서사를 만들었다.

def test_kis_index_skips_pre_market_placeholder_row():
    """장 시작 전 당일 행은 시가=고가=저가=종가이고 등락률 0.00이다.
    이 행을 쓰면 '오늘 지수 0.00%'라는 허위 데이터가 리포트에 들어간다."""
    from app.collectors.kis_collector import _is_completed_session
    placeholder = {"bstp_nmix_prpr": "6820.02", "bstp_nmix_hgpr": "6820.02",
                   "bstp_nmix_lwpr": "6820.02"}
    real = {"bstp_nmix_prpr": "6820.02", "bstp_nmix_hgpr": "6820.10",
            "bstp_nmix_lwpr": "6547.76"}
    assert _is_completed_session(placeholder) is False
    assert _is_completed_session(real) is True


def test_kis_index_parses_latest_completed_session():
    from unittest.mock import MagicMock, patch
    from app.collectors.kis_collector import KISCollector

    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"rt_cd": "0", "output": [
        # 장전 자리표시자 — 건너뛰어야 함
        {"stck_bsop_date": "20260901", "bstp_nmix_prpr": "6820.02",
         "bstp_nmix_prdy_ctrt": "0.00", "bstp_nmix_hgpr": "6820.02", "bstp_nmix_lwpr": "6820.02"},
        # 실제 마감된 거래일
        {"stck_bsop_date": "20260831", "bstp_nmix_prpr": "6820.02",
         "bstp_nmix_prdy_ctrt": "0.46", "bstp_nmix_hgpr": "6820.10", "bstp_nmix_lwpr": "6547.76"},
    ]}
    c = KISCollector()
    with patch.object(KISCollector, "get_token", return_value="tok"), \
         patch("app.collectors.kis_collector.requests.get", return_value=resp):
        out = c.fetch_market_index("KOSPI")
    assert out == {"value": 6820.02, "change_pct": 0.46, "data_date": "2026-08-31"}


def test_kis_index_rejects_unknown_index_name():
    import pytest as _pytest
    from app.collectors.kis_collector import KISCollector
    with _pytest.raises(ValueError):
        KISCollector().fetch_market_index("NIKKEI")


def test_macro_keeps_yfinance_values_when_kis_unavailable(monkeypatch):
    """KIS 미설정·실패 시 기존 yfinance 값을 유지해야 한다(비치명적 폴백)."""
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    with patch.dict("sys.modules", {"yfinance": None}):
        m = MacroCollector()._collect_real()
    assert "KOSPI" in m["kr_market"]   # 값이 사라지지 않음


# ── 지수 신선도 검증 ─────────────────────────────────────────────────────────

def test_stale_index_versus_stock_data_raises_warning():
    """지수가 종목보다 과거면 경고해야 한다 — 기존에는 '✅ 정상'으로 통과했다."""
    from app.utils.data_validator import DataValidator
    price = {"KR_005930": {"change_pct": 1.17, "_mock": False, "data_date": "2026-08-31"}}
    macro = {"kr_market": {"KOSPI": {"value": 6912.37, "change_pct": 1.53}},
             "data_dates": {"KOSPI": "2026-08-27"}, "_mock": False}
    _, _, warnings = DataValidator._validate_kospi_consistency(price, macro)
    assert any("지수가 종목 데이터보다 과거" in w for w in warnings)


def test_aligned_index_dates_produce_no_staleness_warning():
    from app.utils.data_validator import DataValidator
    price = {"KR_005930": {"change_pct": 0.5, "_mock": False, "data_date": "2026-08-31"}}
    macro = {"kr_market": {"KOSPI": {"value": 6820.02, "change_pct": 0.46}},
             "data_dates": {"KOSPI": "2026-08-31", "SP500": "2026-08-31"}, "_mock": False}
    _, _, warnings = DataValidator._validate_kospi_consistency(price, macro)
    assert not any("지수가 종목 데이터보다 과거" in w for w in warnings)


# ── 미국 지수 ETF 프록시 대체 ────────────────────────────────────────────────
# 배경: yfinance "^" 지수 티커가 거래일을 누락하는 문제는 국내뿐 아니라 미국에서도
# 발생했다. 실측(2026-08-31 저녁): ^GSPC·^IXIC·^SOX가 8/27에 멈춰 8/28(금)을 빠뜨린 채
# 응답했고 개별 종목은 정상이었다. ETF(SPY·QQQ·SOXX)는 일반 티커라 같은 문제가
# 관측되지 않아, ETF를 신선도 판정 기준으로 삼고 지수가 뒤처지면 대체한다.

def test_index_note_marks_proxy_substitution():
    from app.reports.report_builder import _index_note
    proxied = {"value": 650.23, "change_pct": -0.58, "_source": "etf_proxy",
               "_proxy_ticker": "SPY", "_index_stale_date": "2026-08-27"}
    note = _index_note(proxied)
    assert "SPY" in note and "2026-08-27" in note


def test_index_note_empty_for_normal_index():
    from app.reports.report_builder import _index_note
    assert _index_note({"value": 7730.99, "change_pct": 0.5}) == ""
    assert _index_note({}) == ""
    assert _index_note(None) == ""


def test_macro_block_labels_proxy_substituted_index():
    """대체 사실을 알리지 않으면 Claude가 ETF 가격을 지수 레벨로 오인한다."""
    from app.reports.report_builder import _format_macro_block
    macro = {"us_market": {"SP500": {"value": 650.23, "change_pct": -0.58,
                                     "_source": "etf_proxy", "_proxy_ticker": "SPY",
                                     "_index_stale_date": "2026-08-27"}},
             "kr_market": {}, "currencies": {}, "rates": {}, "commodities": {}, "sentiment": {}}
    block = _format_macro_block(macro)
    assert "SPY ETF 기준 대체" in block


def test_proxy_substitution_rule_present_in_prompts():
    """대체 표기만 있고 서술 규칙이 없으면 Claude가 ETF 가격을 지수로 서술한다."""
    import app.reports.report_builder as rb
    assert "지수 대체 표기 서술 규칙" in rb._SHARED_NARRATION_RULES


def test_collect_real_executes_full_body_with_stub_yfinance(monkeypatch):
    """_collect_real 본문 전체가 실행되는지 검증한다.

    기존 테스트는 yfinance를 None으로 막아 ImportError 폴백 경로만 탔기 때문에
    함수 본문의 이름 오류를 잡지 못했다. 실제로 미국 지수 ETF 대체 로직을 넣으며
    지역변수 `sox`를 제거했는데, 아래쪽 _derive_sentiment 호출이 그대로 참조해
    NameError로 파이프라인이 죽었고 테스트는 전부 통과했다.
    """
    import sys as _sys
    import types
    import pandas as pd

    class _FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol
        def history(self, period="10d", auto_adjust=True):
            idx = pd.to_datetime(["2026-08-28", "2026-08-31"])
            return pd.DataFrame({"Close": [100.0, 101.0]}, index=idx)

    fake = types.ModuleType("yfinance")
    fake.Ticker = _FakeTicker
    monkeypatch.setitem(_sys.modules, "yfinance", fake)
    monkeypatch.delenv("KIS_APP_KEY", raising=False)   # KIS 경로는 이 테스트 범위 밖
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)

    out = MacroCollector()._collect_real()
    assert out["_mock"] is False
    for key in _SECTIONS:
        assert key in out
    assert out["sentiment"].get("semiconductor_cycle")   # 심리 산출까지 도달했는지
    assert out["data_dates"]["SP500"] == "2026-08-31"


def test_us_index_falls_back_to_etf_when_index_feed_lags(monkeypatch):
    """지수 티커가 ETF보다 과거면 ETF 값으로 대체돼야 한다."""
    import sys as _sys
    import types
    import pandas as pd

    class _FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol
        def history(self, period="10d", auto_adjust=True):
            if self.symbol.startswith("^"):          # 지수 — 8/27에 멈춤
                idx = pd.to_datetime(["2026-08-26", "2026-08-27"])
                return pd.DataFrame({"Close": [7700.0, 7730.99]}, index=idx)
            idx = pd.to_datetime(["2026-08-28", "2026-08-31"])   # ETF·종목 — 최신
            return pd.DataFrame({"Close": [652.0, 650.23]}, index=idx)

    fake = types.ModuleType("yfinance")
    fake.Ticker = _FakeTicker
    monkeypatch.setitem(_sys.modules, "yfinance", fake)
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)

    out = MacroCollector()._collect_real()
    sp = out["us_market"]["SP500"]
    assert sp["_source"] == "etf_proxy"
    assert sp["_proxy_ticker"] == "SPY"
    assert sp["_index_stale_date"] == "2026-08-27"
    assert out["data_dates"]["SP500"] == "2026-08-31"   # 대체 소스의 기준일
