"""
Market Calendar — 거래일 판정 및 "데이터 기준일" 컨텍스트 산출

배경: 기존 파이프라인은 요일 개념이 전혀 없었다(대시보드의 개장/폐장 배지가 유일).
그 결과 주말에 실행하면 금요일 종가를 그대로 "오늘의 등락률"로 보고했고, 같은 값이
토요일 아침·저녁·일요일 아침까지 최대 4번 반복 보고됐다.

핵심 설계 원칙 — **요일 계산보다 데이터 자체의 기준일을 우선한다.**
yfinance는 심볼마다 데이터 반영 시점이 달라, 같은 실행에서도 삼성전자는 금요일 바를,
KOSPI는 목요일 바를 주는 경우가 실제로 관측됐다(2026-08-22 토요일 아침 KOSDAQ +1.99%(목)
→ 같은 날 저녁 -4.63%(금)). 따라서 "오늘이 무슨 요일인가"로 추정하지 않고, 수집된
데이터에 실제로 박혀 있는 기준일(data_date)을 집계해 판단한다. 법정공휴일 목록을
관리하지 않아도 이 방식이 실질적인 보호막이 된다.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_logger = logging.getLogger(__name__)

_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """한국 시각 기준 현재 시각 (naive datetime).

    코드 전반이 `datetime.now()`를 쓰면서 결과를 KST로 간주해왔는데, GitHub Actions
    러너는 UTC라 9시간 어긋났다. 실측(2026-09-01): 리포트 헤더가 "00:31 KST"로
    찍혔으나 실제 수신 시각은 09:31 KST였다.

    표기만의 문제가 아니다. 아침 크론(22:10 UTC = 07:10 KST 다음날)이 돌면
    `datetime.now().date()`가 **전날**이 되어 리포트 파일명·등급 이력 키가 하루씩
    밀리고, 금요일 22:10 UTC(= 토요일 07:10 KST)를 "금요일=거래일"로 판정해
    주말 처리 로직이 통째로 무력화된다.

    tz-aware가 아니라 naive로 반환하는 이유: 기존 코드가 strptime 결과(naive)와
    직접 비교하는 곳이 많아, aware를 반환하면 TypeError가 곳곳에서 터진다.
    """
    return datetime.now(KST).replace(tzinfo=None)


# ── 휴장일 달력 ──────────────────────────────────────────────────────────────
# 주말만으로는 거래일을 맞힐 수 없다. 휴장일을 모르면 두 가지가 깨진다:
#   ① 대상 거래일이 휴장일로 잡혀 전 종목 결측 → 리포트가 빈다
#   ② 휴장일 직전 종가가 "당일 등락"으로 서술된다
# 2026년은 KRX 17일 · 미국 10일이고 겹치는 날은 4일뿐이라, 두 시장의 기준
# 거래일이 휴장 때문에 갈라지는 경우가 실제로 생긴다(예: 9/7 미국 Labor Day).

_HOLIDAYS_PATH = Path(__file__).resolve().parents[2] / "config" / "market_holidays.json"
_holidays_cache: dict | None = None
_gap_warned: set[int] = set()


def _holidays() -> dict:
    """휴장일 설정을 한 번만 읽어 캐시한다. 파일이 없거나 깨져도 죽지 않는다 —
    주말 판정으로 물러나되, 조용히 넘어가지 않고 경고를 남긴다."""
    global _holidays_cache
    if _holidays_cache is None:
        try:
            _holidays_cache = json.loads(_HOLIDAYS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _logger.error(
                "[HOLIDAY_CALENDAR_MISSING] %s 를 읽지 못했습니다 (%s) — "
                "주말만 휴장으로 간주합니다. 공휴일에 리포트가 비거나 전일 종가가 "
                "당일 등락으로 서술될 수 있습니다.", _HOLIDAYS_PATH, exc,
            )
            _holidays_cache = {"markets": {}, "covered_years": []}
    return _holidays_cache


def is_market_holiday(d: date, market: str) -> bool:
    """d가 해당 시장의 휴장일인지 (주말 제외 — 주말은 is_trading_day가 본다)."""
    cal = _holidays()
    covered = cal.get("covered_years") or []
    if covered and d.year not in covered and d.year not in _gap_warned:
        _gap_warned.add(d.year)
        _logger.warning(
            "[HOLIDAY_CALENDAR_GAP] %d년 휴장일이 config/market_holidays.json에 "
            "없습니다 — 주말만 휴장으로 간주합니다. KRX 공식 공고로 갱신하세요.", d.year,
        )
    return d.isoformat() in (cal.get("markets", {}).get(market) or {})


def holiday_name(d: date, market: str) -> str | None:
    return (_holidays().get("markets", {}).get(market) or {}).get(d.isoformat())


def weekday_kr(d: date) -> str:
    """날짜의 한글 요일 한 글자 반환 (월~일)"""
    return _WEEKDAY_KR[d.weekday()]


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def is_trading_day(d: date, market: str | None = None) -> bool:
    """거래일 여부.

    market을 주면 그 시장의 휴장일까지 반영한다. 주지 않으면 주말만 본다 —
    시장이 특정되지 않는 호출부(요일 표기 등)의 기존 동작을 유지하기 위함이다.

    시장을 명시하는 것이 중요한 이유: 2026년 기준 KRX 17일 · 미국 10일 중
    겹치는 날은 4일뿐이라, 한쪽만 쉬는 날이 훨씬 많다.
    """
    if is_weekend(d):
        return False
    if market and is_market_holiday(d, market):
        return False
    return True


def previous_trading_day(d: date, market: str | None = None) -> date:
    """d 이전(당일 제외)의 가장 가까운 거래일"""
    cur = d - timedelta(days=1)
    while not is_trading_day(cur, market):
        cur -= timedelta(days=1)
    return cur


# 정규장 시간 (현지 기준). 한국은 KST, 미국은 ET.
_SESSION_HOURS = {
    "KR": ((9, 0), (15, 30), 0),    # 09:00~15:30 KST — KST와 시차 0
    "US": ((9, 30), (16, 0), -13),  # 09:30~16:00 ET — KST 대비 -13시간(서머타임 기준)
}

# 종목 ID 접두사 → 시장. 지수는 이름으로 매핑한다.
_INDEX_MARKET = {
    "KOSPI": "KR", "KOSDAQ": "KR",
    "SP500": "US", "NASDAQ": "US", "SOX": "US", "DOW": "US",
}


def index_market(index_name: str) -> str | None:
    """지수 이름이 어느 시장인지. 모르면 None."""
    return _INDEX_MARKET.get(index_name)


def market_session_state(market: str, now: datetime | None = None) -> str:
    """시장의 현재 세션 상태 — '개장전' | '개장중' | '마감' | '휴장'.

    "9월 1일 종가 기준"처럼 장중 가격을 종가로 표기하던 문제(2026-09-01 09:31 발송분
    실측)와, 미국 장이 아직 열리지 않은 정상 상황을 "지수가 종목보다 과거"라고
    경고하던 오탐을 함께 잡기 위해 도입했다.
    """
    now = now or now_kst()
    hours = _SESSION_HOURS.get(market)
    if hours is None:
        return "마감"
    (oh, om), (ch, cm), offset = hours
    local = now + timedelta(hours=offset)
    if not is_trading_day(local.date(), market):
        return "휴장"
    minutes = local.hour * 60 + local.minute
    if minutes < oh * 60 + om:
        return "개장전"
    if minutes <= ch * 60 + cm:
        return "개장중"
    return "마감"


# ── 대상 거래일 (target session) ─────────────────────────────────────────────
# 계약 C1: 파이프라인은 시작 시점에 "이 리포트가 분석하는 거래일"을 확정하고,
# 모든 수집기가 그 날짜를 받는다. 실행 시각은 대상일을 고르는 데만 쓰이고
# 그 뒤로는 등장하지 않는다.
#
# 왜 필요한가 — 기존에는 수집기가 심볼별로 "가장 최근 봉"을 독립적으로 가져왔다
# (price_collector: close_s.iloc[-1]). 그래서 한 리포트 안에 기준일이 뒤섞였고
# (실측 2026-09-02 저녁분: 12종목 9/1 · 6종목 8/31 · 미국 9/1 장중), 장이 열려
# 있으면 미완성 봉이 "종가"로 서술됐다. 리포트 날짜는 또 따로 now_kst()에서
# 만들어져, 무엇을 분석한 리포트인지가 실행 시각에 좌우됐다.
#
# 결정적으로 GitHub Actions 예약 실행이 실측 4시간가량 밀린다
# (2026-09-01 아침 4:38 지연, 2026-09-02 저녁 3:56 지연). 기준이 실행 시각에
# 끌려다니면 지연이 곧 데이터 오염이 된다. 아래 규칙은 밀려도 같은 답을 낸다.


def session_close_kst(market: str, d: date) -> datetime | None:
    """market의 거래일 d 정규장이 끝나는 순간을 KST로 환산.

    미국장 현지 16:00은 KST로 **다음날 05:00**이다. 이 환산을 빼먹으면
    "미국 8월 31일 종가"와 "한국시간 9월 1일 새벽 마감"을 서로 다른 것으로
    오해하게 된다 — 실제로는 같은 세션이다.
    """
    hours = _SESSION_HOURS.get(market)
    if hours is None:
        return None
    _, (close_h, close_m), offset = hours
    return datetime(d.year, d.month, d.day, close_h, close_m) - timedelta(hours=offset)


def last_completed_session(market: str, moment: datetime) -> date:
    """moment(KST) 시점에 이미 **끝나 있는** 가장 최근 정규장의 거래일.

    진행 중인 세션은 절대 반환하지 않는다(계약 C2). 저녁 결산이 미국 장중
    수치를 "마감"이라 서술하던 문제를 막는 지점이 여기다.

    미국장은 KST 기준 다음날 새벽에 끝나므로 탐색을 하루 앞에서 시작한다.
    """
    d = moment.date() + timedelta(days=1)
    for _ in range(25):   # 연휴가 길어도(설·추석) 도달하도록 여유를 둔다
        if is_trading_day(d, market):
            close = session_close_kst(market, d)
            if close is not None and close <= moment:
                return d
        d -= timedelta(days=1)
    return d


def resolve_target_session(report_type: str, now: datetime | None = None) -> dict:
    """리포트 유형에 따라 분석 대상 거래일을 확정한다 (계약 C1·C4).

    아침 브리핑 — 개장 전 브리핑이다. 기준선을 **오늘 한국장 개장(09:00)**으로
      잡아, 그 전까지 끝난 세션만 대상으로 한다. 실행이 밀려 이미 장이 열린
      뒤에 돌더라도 기준이 움직이지 않는다. 한국·미국이 같은 거래일로 통일된다
      (미국 D일 세션은 KST D+1 05:00에 끝나므로 아침 브리핑에 이미 담긴다).

    저녁 결산 — 당일 한국장 마감 결산이다. 기준선은 현재 시각. 한국은 당일,
      미국은 직전 완료 세션(당일 미국장은 22:30에야 열린다)이라 두 날짜가
      하루 차이 나는 것이 정상이며, 이는 숨길 것이 아니라 명시할 사실이다.

    반환: report_type / boundary / kr_date / us_date / unified
    """
    now = now or now_kst()
    if report_type == "morning":
        open_h, open_m = _SESSION_HOURS["KR"][0]
        boundary = datetime(now.year, now.month, now.day, open_h, open_m)
    elif report_type == "evening":
        boundary = now
    else:
        raise ValueError(f"알 수 없는 리포트 유형: {report_type!r}")

    kr = last_completed_session("KR", boundary)
    us = last_completed_session("US", boundary)

    # 발행일 — 리포트의 이름표. 이것도 실행 시각에서 떼어낸다.
    #   아침: 브리핑이 대비하는 날(= 기준선의 날짜). 4시간 밀려도 그대로다.
    #   저녁: 마감을 결산하는 바로 그 거래일.
    # 실측 사고: 9월 1일 저녁 결산이 3시간 56분 밀려 00:36에 발송되면서
    # now_kst().date() 기준으로 "2026-09-02 저녁 결산"이라는 이름을 달고 나갔다.
    report_date = boundary.date() if report_type == "morning" else kr
    return {
        "report_type": report_type,
        "boundary":    boundary,
        "report_date": report_date.isoformat(),
        "kr_date":     kr.isoformat(),
        "us_date":     us.isoformat(),
        "unified":     kr == us,
    }


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def summarize_data_freshness(
    price_data: dict[str, dict] | None,
    now: datetime | None = None,
) -> dict:
    """수집된 가격 데이터에 실제로 박혀 있는 기준일(data_date)을 집계해 요약.

    반환:
      run_date          — 실행 날짜 (YYYY-MM-DD)
      run_weekday       — 실행 요일 (월~일)
      run_is_trading_day— 실행일이 거래일인지 (주말 기준)
      latest_data_date  — 수집 데이터 중 가장 최신 기준일
      oldest_data_date  — 가장 오래된 기준일
      mixed_dates       — 종목별 기준일이 서로 다른지 (yfinance 반영 지연)
      date_counts       — 기준일별 종목 수
      stale_days        — run_date - latest_data_date (달력일)
      has_fresh_data    — 오늘자 데이터가 하나라도 있는지
    """
    now = now or now_kst()
    run_date = now.date()

    summary: dict = {
        "run_date": run_date.isoformat(),
        "run_weekday": weekday_kr(run_date),
        "run_is_trading_day": is_trading_day(run_date),
        "latest_data_date": None,
        "oldest_data_date": None,
        "mixed_dates": False,
        "date_counts": {},
        "stale_days": None,
        "has_fresh_data": False,
        # 시장별 세션 상태 — 장중 가격을 "종가"로 표기하던 문제를 막기 위해 전달한다
        "sessions": {mk: market_session_state(mk, now) for mk in ("KR", "US")},
    }

    if not price_data:
        return summary

    counts: dict[str, int] = {}
    for p in price_data.values():
        d = _parse_date(p.get("data_date"))
        if d is None:
            continue
        counts[d.isoformat()] = counts.get(d.isoformat(), 0) + 1

    if not counts:
        return summary

    dates = sorted(counts)
    latest = _parse_date(dates[-1])
    summary["date_counts"] = counts
    summary["latest_data_date"] = dates[-1]
    summary["oldest_data_date"] = dates[0]
    summary["mixed_dates"] = len(counts) > 1
    summary["stale_days"] = (run_date - latest).days if latest else None
    summary["has_fresh_data"] = latest == run_date if latest else False
    return summary
