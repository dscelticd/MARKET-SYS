"""
Calendar Collector — 향후 N일 예정 이벤트 캘린더 (매크로 지표·통화정책·공시·법정기한)

기존 "오늘/내일 주요 모니터링 포인트" 리포트 섹션은 전용 데이터 없이 Claude가 거시지표·
공시 블록을 보고 자유 추론으로 채우는 구조였다. 이 모듈은 그걸 실측 데이터로 대체한다:

  1. 미국 매크로 지표 발표일 — FRED(St. Louis 연준) API. release_id는 하드코딩하지 않고
     /fred/releases 목록을 config/economic_calendar.json의 release_name_match 문자열로
     검색해 런타임에 확정한다(잘못된 ID 매핑 위험 회피 — DART corp_code 사고 전례).
  2. FOMC/한국은행 금통위 일정 — macro_collector.py에 이미 있는 정적 캘린더 재사용.
  3. 국내 종목 법정 공시기한 — 사업보고서(결산 후 90일)/분기·반기보고서(45일), 순수 날짜
     계산이라 외부 데이터 불필요.
  4. DART IR/설명회 관련 안내공시 — main.py가 이미 수집한 disclosure_data를 재사용
     (DART API 재호출 없음). date는 공시 접수일(rcept_dt)이며 실제 행사일이 아니므로
     "공시 접수" 라벨을 붙여 다른 소스와 구분한다.

네 소스 모두 개별 실패가 전체를 막지 않는 비치명적(non-fatal) 보조 진단 계층이다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ECON_CALENDAR_FILE = _PROJECT_ROOT / "config" / "economic_calendar.json"
_RELEASES_CACHE_FILE = _PROJECT_ROOT / "data" / "cache" / "fred_releases.json"
_RELEASES_CACHE_TTL_SEC = 30 * 24 * 3600  # 30일 — release 목록은 거의 안 바뀜

_FRED_BASE = "https://api.stlouisfed.org/fred"

# disclosure_collector._CORP_CODE와 동일한 KR 종목만 법정기한 대상 (ETF 제외)
_KR_FILING_STOCKS = {
    "KR_005930": "삼성전자",
    "KR_000660": "SK하이닉스",
    "KR_010120": "LS ELECTRIC",
    "KR_015760": "한국전력공사",
    "KR_066570": "LG전자",
    "KR_138080": "오이솔루션",
}

_IR_KEYWORDS = ("설명회", "IR", "안내공시")


def _load_indicator_config() -> list[dict]:
    if not _ECON_CALENDAR_FILE.exists():
        return []
    try:
        data = json.loads(_ECON_CALENDAR_FILE.read_text(encoding="utf-8"))
        return data.get("fred_indicators", [])
    except Exception as e:
        logger.warning("경제 캘린더 설정 로드 실패: %s", e)
        return []


class CalendarCollector:
    def __init__(self) -> None:
        self.api_key = os.getenv("FRED_API_KEY", "")

    def is_configured(self) -> bool:
        return bool(self.api_key)

    # ── release_id 해석/캐싱 ────────────────────────────────────────────────

    def _read_cached_releases(self) -> list[dict] | None:
        if not _RELEASES_CACHE_FILE.exists():
            return None
        try:
            data = json.loads(_RELEASES_CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - data.get("cached_at", 0) > _RELEASES_CACHE_TTL_SEC:
                return None
            return data.get("releases")
        except Exception:
            return None

    def _write_cached_releases(self, releases: list[dict]) -> None:
        _RELEASES_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _RELEASES_CACHE_FILE.write_text(
            json.dumps({"cached_at": time.time(), "releases": releases}),
            encoding="utf-8",
        )

    def _fetch_all_releases(self) -> list[dict]:
        resp = requests.get(
            f"{_FRED_BASE}/releases",
            params={"api_key": self.api_key, "file_type": "json", "limit": 1000},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return [{"id": r["id"], "name": r["name"]} for r in data.get("releases", [])]

    def _resolve_release_ids(self) -> dict[str, int]:
        """config의 각 indicator를 release_name_match 부분 문자열로 실제 release_id에 매핑."""
        releases = self._read_cached_releases()
        if releases is None:
            releases = self._fetch_all_releases()
            self._write_cached_releases(releases)

        resolved: dict[str, int] = {}
        for ind in _load_indicator_config():
            match = ind.get("release_name_match", "")
            found = next(
                (r for r in releases if match.lower() in r["name"].lower()), None
            )
            if found:
                resolved[ind["id"]] = found["id"]
            else:
                logger.debug("FRED release 매칭 실패: %s (%s)", ind["id"], match)
        return resolved

    # ── 매크로 지표 발표일 ───────────────────────────────────────────────────

    def fetch_fred_events(self, days_ahead: int = 14) -> list[dict]:
        if not self.is_configured():
            return []

        indicator_names = {i["id"]: i["name"] for i in _load_indicator_config()}
        try:
            release_ids = self._resolve_release_ids()
        except Exception as e:
            logger.warning("FRED release 목록 조회 실패: %s", e)
            return []

        today = datetime.now().date()
        end = today + timedelta(days=days_ahead)
        events: list[dict] = []

        for ind_id, release_id in release_ids.items():
            try:
                resp = requests.get(
                    f"{_FRED_BASE}/release/dates",
                    params={
                        "api_key": self.api_key,
                        "release_id": release_id,
                        "realtime_start": today.isoformat(),
                        "realtime_end": end.isoformat(),
                        "include_release_dates_with_no_data": "true",
                        "file_type": "json",
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                for rd in data.get("release_dates", []):
                    events.append({
                        "date": rd["date"],
                        "category": "macro",
                        "country": "US",
                        "title": f"{indicator_names.get(ind_id, ind_id)} 발표 예정",
                        "source": "fred",
                    })
            except Exception as e:
                logger.debug("FRED 발표일 조회 실패 (%s): %s", ind_id, e)
                continue

        return events


# ── 국내 법정 공시기한 (순수 날짜 계산, 외부 데이터 불필요) ─────────────────────

def get_kr_filing_deadlines(days_ahead: int = 14) -> list[dict]:
    """분기/반기/사업보고서 법정 제출기한. 대상 종목은 12월 결산 법인으로 가정
    (워치리스트 KR 종목 6개 전부 12월 결산 확인됨)."""
    today = datetime.now().date()
    end = today + timedelta(days=days_ahead)

    # (기말월, 기말일, 제출기한 일수, 보고서명)
    _PERIODS = [
        (3, 31, 45, "1분기보고서"),
        (6, 30, 45, "반기보고서"),
        (9, 30, 45, "3분기보고서"),
        (12, 31, 90, "사업보고서(연간)"),
    ]

    events: list[dict] = []
    for year in (today.year - 1, today.year, today.year + 1):
        for month, day, offset_days, label in _PERIODS:
            period_end = datetime(year, month, day).date()
            deadline = period_end + timedelta(days=offset_days)
            if today <= deadline <= end:
                events.append({
                    "date": deadline.isoformat(),
                    "category": "filing_deadline",
                    "country": "KR",
                    "title": f"{label} 법정 제출기한 (워치리스트 KR 종목 공통, 12월 결산 기준)",
                    "source": "legal",
                })
    return sorted(events, key=lambda e: e["date"])


# ── DART IR/설명회 관련 안내공시 (기존 disclosure_data 재사용) ───────────────────

def get_dart_ir_events(disclosure_data: dict | None) -> list[dict]:
    """main.py가 이미 수집한 disclosure_data에서 IR/설명회 키워드가 포함된 항목만 추출.
    date는 공시 접수일(rcept_dt)이며 실제 행사일이 아니다 — DART list API는 행사 예정일
    자체를 구조화된 필드로 제공하지 않으므로 접수일 기준으로만 표기한다."""
    if not disclosure_data:
        return []

    events: list[dict] = []
    for items in disclosure_data.values():
        for item in items or []:
            title = item.get("title", "")
            if not any(kw in title for kw in _IR_KEYWORDS):
                continue
            rcept_dt = item.get("rcept_dt", "")
            if not re.fullmatch(r"\d{8}", rcept_dt):
                continue
            date_str = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:]}"
            events.append({
                "date": date_str,
                "category": "disclosure",
                "country": "KR",
                "title": f"{item.get('corp_name', '')}: {title} (공시 접수)",
                "source": "dart",
            })
    return sorted(events, key=lambda e: e["date"])


# ── 통합 ───────────────────────────────────────────────────────────────────

def build_event_calendar(
    disclosure_data: dict | None = None,
    days_ahead: int = 14,
) -> list[dict]:
    """4개 소스(FRED 매크로/FOMC·금통위/국내 법정기한/DART IR공시)를 합쳐 날짜순 정렬.
    소스 하나가 실패해도 나머지는 계속 진행 — 리포트 생성을 막지 않는다."""
    events: list[dict] = []

    try:
        collector = CalendarCollector()
        events.extend(collector.fetch_fred_events(days_ahead))
    except Exception as e:
        logger.warning("FRED 이벤트 수집 실패 (무시하고 계속): %s", e)

    try:
        from app.collectors.macro_collector import get_upcoming_policy_meetings
        events.extend(get_upcoming_policy_meetings(days_ahead))
    except Exception as e:
        logger.warning("정책회의 일정 수집 실패 (무시하고 계속): %s", e)

    try:
        events.extend(get_kr_filing_deadlines(days_ahead))
    except Exception as e:
        logger.warning("국내 법정기한 계산 실패 (무시하고 계속): %s", e)

    try:
        events.extend(get_dart_ir_events(disclosure_data))
    except Exception as e:
        logger.warning("DART IR 이벤트 추출 실패 (무시하고 계속): %s", e)

    return sorted(events, key=lambda e: e["date"])
