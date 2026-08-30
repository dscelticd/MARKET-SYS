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

from datetime import date, datetime, timedelta

_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def weekday_kr(d: date) -> str:
    """날짜의 한글 요일 한 글자 반환 (월~일)"""
    return _WEEKDAY_KR[d.weekday()]


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def is_trading_day(d: date) -> bool:
    """거래일 여부.

    현재는 주말만 판정하며 법정공휴일(설·추석·신정, 미국 독립기념일 등)은 반영하지
    않는다. 공휴일 목록을 별도 관리하면 매년 갱신 부담이 생기는 반면, 실제 데이터의
    기준일(data_date)을 함께 확인하는 summarize_data_freshness()가 공휴일에도
    "새 거래 없음"을 정확히 잡아내므로 요일 판정은 보조 수단으로만 쓴다.
    """
    return not is_weekend(d)


def previous_trading_day(d: date) -> date:
    """d 이전(당일 제외)의 가장 가까운 거래일"""
    cur = d - timedelta(days=1)
    while not is_trading_day(cur):
        cur -= timedelta(days=1)
    return cur


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
    now = now or datetime.now()
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
