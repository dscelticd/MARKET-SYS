"""
등급 이력 키 테스트 — 계약 C5: 이력은 대상 거래일로 키를 잡는다

배경: 이력 키가 실행일(now_kst)이었다. 예약 실행이 4시간가량 밀리는 환경에서는
      키가 분석 대상과 어긋난다 — 9월 1일 저녁 결산이 00:36에 발송되면서
      "2026-09-02" 키로 저장된 것이 실제 사례다.

      적중률은 D일 등급을 D+5·D+20일 가격과 대조하는 계산이라, 키가 하루
      밀리면 비교 대상이 통째로 어긋난다. 등급 변화("전일 대비") 역시 같다.
"""
from __future__ import annotations

from unittest.mock import patch

from app.engine.history_tracker import HistoryTracker


def _tracker(entries: dict) -> HistoryTracker:
    t = HistoryTracker.__new__(HistoryTracker)   # __init__의 파일 I/O를 건너뛴다
    t._data = entries
    return t


_RATING = {
    "stock_id": "KR_005930", "name": "삼성전자", "grade": "보통",
    "raw_grade": "보통", "total_score": 50.0, "risk_score": 40.0,
    "data_confidence": 100.0,
}
_PRICE = {"KR_005930": {"price": 260000, "change_pct": 1.0,
                        "volume": 1000, "data_date": "2026-09-01"}}


# ── 저장 ─────────────────────────────────────────────────────────────────────

def test_key_uses_session_date_not_run_date():
    t = _tracker({})
    with patch.object(HistoryTracker, "_persist"), \
         patch("app.engine.history_tracker.now_kst") as nk:
        nk.return_value.strftime.return_value = "2026-09-02"   # 지연 실행일
        nk.return_value.isoformat.return_value = "2026-09-02T00:36:00"
        t.save_today([_RATING], "evening", price_data=_PRICE,
                     session_date="2026-09-01")
    assert "2026-09-01_evening" in t._data
    assert "2026-09-02_evening" not in t._data


def test_run_date_is_kept_for_audit():
    """대상일로 키를 옮기더라도 실제로 언제 돌았는지는 남겨야 추적이 된다."""
    t = _tracker({})
    with patch.object(HistoryTracker, "_persist"), \
         patch("app.engine.history_tracker.now_kst") as nk:
        nk.return_value.strftime.return_value = "2026-09-02"
        nk.return_value.isoformat.return_value = "2026-09-02T00:36:00"
        t.save_today([_RATING], "evening", price_data=_PRICE,
                     session_date="2026-09-01")
    e = t._data["2026-09-01_evening"]
    assert e["date"] == "2026-09-01"
    assert e["run_date"] == "2026-09-02"
    assert e["is_trading_day"] is True


def test_same_session_rerun_overwrites_instead_of_duplicating():
    """같은 거래일을 두 번 돌려도 칸이 하나여야 한다 — 중복 스냅샷은
    적중률 통계를 왜곡한다(주말 중복 실행에서 실제로 겪은 문제)."""
    t = _tracker({})
    with patch.object(HistoryTracker, "_persist"), \
         patch("app.engine.history_tracker.now_kst") as nk:
        nk.return_value.strftime.return_value = "2026-09-02"
        nk.return_value.isoformat.return_value = "2026-09-02T00:36:00"
        for _ in range(2):
            t.save_today([_RATING], "evening", price_data=_PRICE,
                         session_date="2026-09-01")
    assert len([k for k in t._data if k.endswith("_evening")]) == 1


# ── 마이그레이션 ─────────────────────────────────────────────────────────────

def test_migration_rekeys_entries_saved_under_the_run_date():
    t = _tracker({
        "2026-09-02_evening": {"date": "2026-09-02", "data_date": "2026-09-01",
                               "report_type": "evening"},
    })
    with patch.object(HistoryTracker, "_persist"):
        t._migrate_keys_to_session_date()
    assert "2026-09-01_evening" in t._data
    assert "2026-09-02_evening" not in t._data
    assert t._data["2026-09-01_evening"]["run_date"] == "2026-09-02"


def test_migration_skips_when_destination_key_exists():
    """같은 거래일 칸이 이미 있으면 덮어써서 기존 이력을 잃으면 안 된다."""
    t = _tracker({
        "2026-09-01_evening": {"date": "2026-09-01", "data_date": "2026-09-01",
                               "report_type": "evening", "keep": True},
        "2026-09-02_evening": {"date": "2026-09-02", "data_date": "2026-09-01",
                               "report_type": "evening"},
    })
    with patch.object(HistoryTracker, "_persist"):
        t._migrate_keys_to_session_date()
    assert t._data["2026-09-01_evening"]["keep"] is True
    assert "2026-09-02_evening" in t._data


def test_migration_leaves_legacy_entries_without_data_date_alone():
    """data_date가 없는 구 스키마 엔트리는 옮길 근거가 없다. 추측하지 않는다."""
    before = {"2026-08-22_morning": {"date": "2026-08-22", "data_date": None,
                                     "report_type": "morning"}}
    t = _tracker(dict(before))
    with patch.object(HistoryTracker, "_persist"):
        t._migrate_keys_to_session_date()
    assert t._data == before


def test_pipeline_passes_session_date_to_history():
    import pathlib, re
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text(encoding="utf-8")
    body = re.sub(r"#.*", "", src)
    assert 'session_date=target["kr_date"]' in body
