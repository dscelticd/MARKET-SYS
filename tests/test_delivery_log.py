"""
발송 기록 테스트 — 중복 발송 방지와 누락 회차 감지

실측 근거 (2026-09-14 ~ 10-16 GitHub Actions 127회):
  - 10/9(한글날) 저녁 결산이 "2026-10-08 저녁 결산"으로 한 번 더 발송됐다
  - 10/5 저녁 실행이 러너 미배정으로 취소됐는데 아무 알림도 없었다
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from app.utils import delivery_log as dl
from app.utils.market_calendar import resolve_target_session


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    p = tmp_path / "delivery_log.json"
    monkeypatch.setattr(dl, "DEFAULT_PATH", p)
    return p


# ── 중복 방지 ────────────────────────────────────────────────────────────────

def test_records_and_detects_a_sent_report(log_path):
    assert dl.already_sent("morning", "2026-10-16") is None
    dl.record_sent("morning", "2026-10-16", now=datetime(2026, 10, 16, 7, 12))
    assert dl.already_sent("morning", "2026-10-16") == "2026-10-16 07:12"
    assert dl.already_sent("evening", "2026-10-16") is None


def test_holiday_evening_collides_with_the_previous_evening(log_path):
    """10/9(한글날) 저녁 실행의 발행일은 직전 거래일 10/8 — 이미 보낸 회차다."""
    on_time = resolve_target_session("evening", datetime(2026, 10, 9, 2, 58))
    dl.record_sent("evening", on_time["report_date"], now=datetime(2026, 10, 9, 3, 0))

    holiday = resolve_target_session("evening", datetime(2026, 10, 10, 2, 32))
    assert holiday["report_date"] == on_time["report_date"] == "2026-10-08"
    assert dl.already_sent("evening", holiday["report_date"])


def test_delayed_backup_run_gets_the_same_key_as_the_on_time_run(log_path):
    """정시(외부 트리거)와 몇 시간 밀린 예비 크론이 같은 발행일을 얻어야 중복이 걸러진다."""
    on_time = resolve_target_session("morning", datetime(2026, 10, 16, 7, 10))
    delayed = resolve_target_session("morning", datetime(2026, 10, 16, 12, 1))
    assert on_time["report_date"] == delayed["report_date"]

    on_time = resolve_target_session("evening", datetime(2026, 10, 15, 20, 40))
    delayed = resolve_target_session("evening", datetime(2026, 10, 16, 5, 24))
    assert on_time["report_date"] == delayed["report_date"]


def test_corrupted_log_does_not_block_delivery(log_path):
    log_path.write_text("{not json", encoding="utf-8")
    assert dl.already_sent("morning", "2026-10-16") is None
    dl.record_sent("morning", "2026-10-16", now=datetime(2026, 10, 16, 7, 12))
    assert dl.already_sent("morning", "2026-10-16")


def test_old_entries_are_pruned(log_path):
    dl.record_sent("morning", "2026-07-01", now=datetime(2026, 7, 1, 7, 10))
    dl.record_sent("morning", "2026-10-16", now=datetime(2026, 10, 16, 7, 10))
    sent = json.loads(log_path.read_text(encoding="utf-8"))["sent"]
    assert list(sent) == ["morning:2026-10-16"]


# ── 발송 일정 ────────────────────────────────────────────────────────────────

def test_expected_schedule_matches_the_workflow():
    """10/5(월, 개천절 대체공휴일) ~ 10/11(일)."""
    got = {(t, d) for t, d, _ in dl.expected_reports(date(2026, 10, 5), date(2026, 10, 11))}
    mornings = {d for t, d in got if t == "morning"}
    evenings = {d for t, d in got if t == "evening"}
    assert "2026-10-11" not in mornings                  # 일요일 아침 없음
    assert "2026-10-10" in mornings                      # 토요일 아침은 있음
    assert evenings == {"2026-10-06", "2026-10-07", "2026-10-08"}   # 휴장일·주말 제외


# ── 누락 감지 ────────────────────────────────────────────────────────────────

def _seed(sent: list[tuple[str, str]], since: str = "2026-10-12 00:00") -> None:
    for t, d in sent:
        dl.record_sent(t, d, now=datetime(2026, 10, 12, 0, 0))
    data = dl.load()
    data["since"] = since
    dl._save(data)


def test_finds_a_report_that_never_ran(log_path):
    _seed([("morning", "2026-10-12"), ("morning", "2026-10-13"), ("evening", "2026-10-13")])
    # 10/12 저녁이 없다. 10/14 09:30 기준으로 12시간이 넘게 지났다.
    missed = dl.find_missed(now=datetime(2026, 10, 14, 9, 30))
    assert ("evening", "2026-10-12") in missed


def test_recent_report_is_not_flagged_while_still_within_grace(log_path):
    """실측 최대 지연이 8시간 49분 — 예정 후 12시간 안에는 아직 오는 중일 수 있다."""
    _seed([("morning", "2026-10-12")])
    missed = dl.find_missed(now=datetime(2026, 10, 13, 5, 0))   # 10/12 저녁 예정 후 8시간 20분
    assert ("evening", "2026-10-12") not in missed


def test_reports_before_logging_started_are_not_flagged(log_path):
    _seed([("evening", "2026-10-15")], since="2026-10-15 21:00")
    missed = dl.find_missed(now=datetime(2026, 10, 16, 12, 0))
    assert ("morning", "2026-10-15") not in missed
    assert ("evening", "2026-10-14") not in missed


def test_a_missed_report_is_alerted_only_once(log_path):
    _seed([("morning", "2026-10-12")])
    now = datetime(2026, 10, 14, 9, 30)
    first = dl.find_missed(now=now)
    assert first
    dl.mark_alerted(first)
    assert dl.find_missed(now=now) == []


def test_no_log_means_nothing_to_compare(log_path):
    assert dl.find_missed(now=datetime(2026, 10, 16, 9, 0)) == []


# ── 파이프라인 연결 ──────────────────────────────────────────────────────────

class _CollectorCalled(Exception):
    pass


@pytest.fixture
def pipeline(log_path, monkeypatch):
    import app.main as main

    target = resolve_target_session("evening", datetime(2026, 10, 10, 2, 32))   # 한글날 저녁
    monkeypatch.setattr(main, "resolve_target_session", lambda _t: target)
    monkeypatch.setattr(main, "_setup_logging", lambda: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    def _boom(*a, **k):
        raise _CollectorCalled
    monkeypatch.setattr(main, "get_config", _boom)   # 수집 단계에 들어가면 여기서 멈춘다
    dl.record_sent("evening", "2026-10-08", now=datetime(2026, 10, 9, 3, 0))
    return main


def test_duplicate_run_stops_before_collecting_or_calling_claude(pipeline):
    pipeline.run_pipeline("evening", send_email=True)   # 예외 없이 조용히 끝나야 한다


def test_force_sends_again(pipeline):
    with pytest.raises(_CollectorCalled):
        pipeline.run_pipeline("evening", send_email=True, force=True)


def test_runs_without_email_are_never_blocked(pipeline):
    """발송하지 않는 로컬 생성까지 막을 이유는 없다."""
    with pytest.raises(_CollectorCalled):
        pipeline.run_pipeline("evening", send_email=False)
