"""
대상 거래일(target session) 테스트 — 계약 C1·C2·C4

배경: 파이프라인 전체에 "이 리포트가 분석하는 거래일"이라는 개념이 없었다.
      수집기는 심볼별로 가장 최근 봉을 독립적으로 가져오고(close_s.iloc[-1]),
      리포트 날짜는 따로 now_kst()에서 만들어졌다. 그래서
        - 한 리포트에 기준일이 뒤섞이고 (2026-09-02 저녁분: 12종목 9/1 ·
          6종목 8/31 · 미국 9/1 장중)
        - 장이 열려 있으면 미완성 봉이 "종가"로 서술되고
        - 실행이 밀리면 기준이 통째로 이동했다

      결정적 조건: GitHub Actions 예약 실행이 실측 4시간가량 밀린다
      (9/1 아침 4:38, 9/2 저녁 3:56). 따라서 이 테스트의 핵심 합격 기준은
      **실행 시각이 달라도 같은 대상 거래일이 나오는가**이다.
"""
from __future__ import annotations

import pathlib
from datetime import date, datetime

import pytest

from app.utils.market_calendar import (
    last_completed_session,
    resolve_target_session,
    session_close_kst,
)

# 2026-08-31(월) ~ 2026-09-04(금)이 거래일, 9/5(토)·9/6(일)이 휴장인 주간


# ── 세션 마감 시각 환산 ──────────────────────────────────────────────────────

def test_korean_session_closes_same_day_1530():
    assert session_close_kst("KR", date(2026, 9, 1)) == datetime(2026, 9, 1, 15, 30)


def test_us_session_closes_next_day_0500_kst():
    """미국 현지 16:00 = KST 다음날 05:00. 이 환산이 "미국 8/31 종가"와
    "한국시간 9/1 새벽 마감"이 같은 세션임을 성립시킨다."""
    assert session_close_kst("US", date(2026, 8, 31)) == datetime(2026, 9, 1, 5, 0)


# ── 진행 중 세션 배제 (계약 C2) ──────────────────────────────────────────────

def test_open_korean_session_is_not_returned():
    """9/1 11:48 — 한국장 개장 중. 9/1을 완료된 세션으로 보면 안 된다."""
    assert last_completed_session("KR", datetime(2026, 9, 1, 11, 48)) == date(2026, 8, 31)


def test_open_us_session_is_not_returned():
    """9/2 00:36 — 미국 9/1 세션 진행 중(22:30~05:00). 실제로 이 시각에
    "3대 지수 모두 하락 마감"이라 서술된 리포트가 나갔다."""
    assert last_completed_session("US", datetime(2026, 9, 2, 0, 36)) == date(2026, 8, 31)


def test_us_session_becomes_available_right_after_0500_kst():
    assert last_completed_session("US", datetime(2026, 9, 2, 4, 59)) == date(2026, 8, 31)
    assert last_completed_session("US", datetime(2026, 9, 2, 5, 0))  == date(2026, 9, 1)


# ── 아침 브리핑 (계약 C4) ────────────────────────────────────────────────────

def test_morning_unifies_both_markets_on_one_trading_day():
    """아침 브리핑은 한국·미국이 같은 거래일이어야 한다 — 미국 D일 세션은
    KST D+1 05:00에 끝나므로 그날 아침 브리핑에 이미 담긴다."""
    t = resolve_target_session("morning", datetime(2026, 9, 2, 7, 10))
    assert (t["kr_date"], t["us_date"]) == ("2026-09-01", "2026-09-01")
    assert t["unified"] is True


@pytest.mark.parametrize("now", [
    datetime(2026, 9, 2, 7, 10),    # 정시
    datetime(2026, 9, 2, 11, 40),   # 4시간 지연 — 한국장 개장 중
    datetime(2026, 9, 2, 17, 0),    # 한국장 마감 후까지 지연
])
def test_morning_target_is_immune_to_run_time(now):
    """이번 작업의 합격 기준. 실측 지연(약 4시간)에도 기준이 움직이면 안 된다.
    지연된 11:48 실행분이 한국 장중가를 "9월 1일 종가"로 담아 나간 사고를 막는다."""
    t = resolve_target_session("morning", now)
    assert (t["kr_date"], t["us_date"]) == ("2026-09-01", "2026-09-01")


def test_monday_morning_covers_friday():
    t = resolve_target_session("morning", datetime(2026, 9, 7, 7, 10))
    assert (t["kr_date"], t["us_date"]) == ("2026-09-04", "2026-09-04")


def test_saturday_morning_covers_friday_both_markets():
    """토요일 아침 회차를 남겨둔 이유 — 미국 금요일 정규장이 토요일 05:00 KST에
    끝나므로, 이 회차에 미국 금요일 종가가 처음 담긴다."""
    t = resolve_target_session("morning", datetime(2026, 9, 5, 7, 10))
    assert (t["kr_date"], t["us_date"]) == ("2026-09-04", "2026-09-04")
    assert t["unified"] is True


# ── 저녁 결산 (계약 C4) ──────────────────────────────────────────────────────

def test_evening_uses_todays_korean_close_and_prior_us_close():
    """저녁 결산에서 한·미 날짜가 하루 차이 나는 것은 정상이다 —
    20:40 KST에 미국장은 아직 열리지도 않았다(22:30 개장)."""
    t = resolve_target_session("evening", datetime(2026, 9, 2, 20, 40))
    assert (t["kr_date"], t["us_date"]) == ("2026-09-02", "2026-09-01")
    assert t["unified"] is False


def test_evening_delayed_past_midnight_keeps_same_target():
    """실측: 9/2 저녁 결산이 00:36에 발송됐다. 그때 미국 9/2 세션이 진행
    중이었는데 그 값을 "마감"이라 서술했다. 지연돼도 대상은 그대로여야 한다."""
    on_time = resolve_target_session("evening", datetime(2026, 9, 2, 20, 40))
    delayed = resolve_target_session("evening", datetime(2026, 9, 3, 0, 36))
    assert (delayed["kr_date"], delayed["us_date"]) == (on_time["kr_date"], on_time["us_date"])


def test_unknown_report_type_raises():
    """유형을 늘리고 여기를 갱신하지 않으면 조용히 잘못된 기준을 쓰는 대신 실패한다."""
    with pytest.raises(ValueError):
        resolve_target_session("weekly", datetime(2026, 9, 2, 7, 10))


# ── 발행일 (리포트의 이름표) ─────────────────────────────────────────────────

def test_morning_report_date_is_the_day_it_briefs_for():
    for now in (datetime(2026, 9, 2, 7, 10), datetime(2026, 9, 2, 11, 40)):
        assert resolve_target_session("morning", now)["report_date"] == "2026-09-02"


def test_evening_report_date_is_the_session_it_closes_out():
    """실측 사고: 9/1 저녁 결산이 3시간 56분 밀려 00:36에 발송되면서
    now_kst().date() 기준으로 "2026-09-02 저녁 결산"이라 이름 붙어 나갔다."""
    on_time = resolve_target_session("evening", datetime(2026, 9, 1, 20, 40))
    delayed = resolve_target_session("evening", datetime(2026, 9, 2, 0, 36))
    assert on_time["report_date"] == "2026-09-01"
    assert delayed["report_date"] == "2026-09-01"


def test_pipeline_derives_report_date_from_target_not_clock():
    """main.py가 다시 now_kst()로 발행일을 만들면 지연 시 이름표가 어긋난다."""
    import re
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text(encoding="utf-8")
    body = re.sub(r"#.*", "", src)
    assert 'date_str = target["report_date"]' in body
    assert not re.search(r'date_str\s*=\s*now_kst\(\)', body)
    assert not re.search(r'date_prefix\s*=\s*now_kst\(\)', body)
