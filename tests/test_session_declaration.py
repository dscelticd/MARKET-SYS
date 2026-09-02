"""
기준 세션 선언 테스트 — 계약 C6: 라벨은 계약의 결과이지 대체물이 아니다

배경: 이전에는 수집된 데이터의 기준일 산포를 사후에 관찰해 설명했다.
      실제 발송분에 이런 문장들이 실렸다 —
        "종목별 데이터 기준일이 서로 다릅니다 — 2026-09-01: 12종목 / 2026-08-31: 6종목"
        "기준일이 다른 종목 간 등락률을 직접 비교하는 것은 적절하지 않습니다"
      리포트가 스스로 자기 데이터를 믿지 말라고 경고하는 자기모순이었다.

      C1~C3이 지켜지면 기준일은 관찰 대상이 아니라 선언 대상이다. 이 블록은
      대상 세션을 명시하고, 데이터가 어긋나면 계약 위반으로 경보를 낸다 —
      계약이 지켜지면 조용하고, 깨지면 시끄럽다.
"""
from __future__ import annotations

import logging

from app.reports.report_builder import _format_market_session_block


def _block(target, freshness=None, **kw):
    return _format_market_session_block(freshness or {}, None, None, target=target, **kw)


# ── 선언 ─────────────────────────────────────────────────────────────────────

def test_declares_the_target_session_for_both_markets():
    b = _block({"kr_date": "2026-09-01", "us_date": "2026-09-01"})
    assert "한국 시장: 2026-09-01 (화) 정규장 종가" in b
    assert "미국 시장: 2026-09-01 (화) 정규장 종가" in b


def test_unified_dates_permit_direct_comparison():
    b = _block({"kr_date": "2026-09-01", "us_date": "2026-09-01"},
               {"date_counts": {"2026-09-01": 18}})
    assert "직접 비교해도 됩니다" in b
    assert "🚨" not in b


def test_evening_one_day_gap_is_explained_as_normal_not_warned():
    """저녁 결산의 한·미 하루 차이는 결함이 아니다 — 20:40에 미국장은
    아직 열리지도 않았다. 이걸 경고로 다루면 매번 오탐이 된다."""
    b = _block({"kr_date": "2026-09-02", "us_date": "2026-09-01"},
               {"date_counts": {"2026-09-02": 11, "2026-09-01": 7}})
    assert "정상이며 결함이 아닙니다" in b
    assert "🚨" not in b
    assert "같은 날 움직임" in b     # 인과를 엮지 말라는 지시는 남긴다


# ── 위반 감지 ────────────────────────────────────────────────────────────────

def test_stray_data_date_is_reported_as_a_contract_violation(caplog):
    """수집 단계에서 막혔어야 하는 상태. 리포트에서 얼버무리지 않는다."""
    with caplog.at_level(logging.ERROR, logger="app.reports.report_builder"):
        b = _block({"kr_date": "2026-09-01", "us_date": "2026-09-01"},
                   {"date_counts": {"2026-09-01": 12, "2026-08-31": 6}})
    assert "🚨" in b and "계약 위반" in b
    assert "2026-08-31: 6종목" in b
    assert any("[CONTRACT_VIOLATION]" in r.message for r in caplog.records)


def test_no_violation_banner_when_data_matches_the_target():
    b = _block({"kr_date": "2026-09-02", "us_date": "2026-09-01"},
               {"date_counts": {"2026-09-02": 11, "2026-09-01": 7}})
    assert "🚨" not in b


def test_old_scatter_warning_is_gone_from_the_declared_path():
    """이 문장이 다시 나타나면 사후 설명 방식으로 되돌아간 것이다."""
    b = _block({"kr_date": "2026-09-02", "us_date": "2026-09-01"},
               {"date_counts": {"2026-09-02": 11, "2026-09-01": 7},
                "mixed_dates": True})
    assert "종목별 데이터 기준일이 서로 다릅니다" not in b


# ── 배선 ─────────────────────────────────────────────────────────────────────

def test_both_builders_forward_the_target_session():
    import inspect
    from app.reports.report_builder import ReportBuilder
    for name in ("build_morning_report", "build_evening_report"):
        fn = getattr(ReportBuilder, name)
        assert "target_session" in inspect.signature(fn).parameters
        assert "target=target_session" in inspect.getsource(fn)


def test_pipeline_passes_the_target_session():
    import pathlib, re
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text(encoding="utf-8")
    assert len(re.findall(r"target_session=target,", src)) == 2
