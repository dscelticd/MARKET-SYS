"""
발송 기록 — 같은 리포트의 중복 발송을 막고, 발송되지 않은 회차를 찾아낸다.

왜 필요한가 (2026-09-14 ~ 10-16 실행 127회 분석):

  ① 휴장일 중복 — 저녁 결산의 발행일은 "결산하는 한국 거래일"이다. 국내
     휴장일(10/9 한글날)에 저녁 크론이 돌면 직전 거래일(10/8)이 다시 대상이
     되어 "2026-10-08 저녁 결산"이 같은 데이터로 한 번 더 발송됐다.

  ② 예약 지연 — GitHub 예약 실행이 아침 중앙값 2시간 43분, 저녁 5시간 25분
     밀린다. 외부 스케줄러로 정시에 띄우고 기존 크론은 예비로 남기려면, 늦게
     도착한 예비 실행이 같은 리포트를 또 보내지 않아야 한다.

  ③ 무음 누락 — 10/5 저녁 실행은 GitHub이 러너를 배정하지 못해 취소됐다.
     실패 알림도 같은 작업 안의 단계라 실행되지 않았고, 아무도 몰랐다.
     실행이 아예 없었던 회차는 그 실행 자신이 알릴 수 없다 — 다음 실행이
     기록을 보고 알려야 한다.

키는 (리포트 유형, 발행일)이다. 발행일은 resolve_target_session()이 실행 시각과
무관하게 정하므로, 몇 시간 밀린 예비 실행도 정시 실행과 같은 키를 얻는다.

저장 위치는 data/history/ — 워크플로가 등급 이력과 함께 캐시로 이어 붙이는
디렉터리라 별도 설정 없이 실행 사이에 유지된다.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from app.utils.market_calendar import is_trading_day, now_kst

_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = _PROJECT_ROOT / "data" / "history" / "delivery_log.json"

# 발송 일정 — .github/workflows/market-flow.yml의 크론과 같은 규칙이다.
#   아침 07:10 KST : 월~토 (일요일 제외)
#   저녁 20:40 KST : 국내 거래일 (주말·국내 휴장일 제외 — 휴장일 저녁은 ①의 중복이다)
# 크론을 바꾸면 여기도 맞춰야 누락 판정이 어긋나지 않는다.
_MORNING_AT = (7, 10)
_EVENING_AT = (20, 40)

# 예정 시각으로부터 이만큼 지나도 기록이 없으면 누락으로 본다. 실측 최대 지연이
# 아침 4시간 57분, 저녁 8시간 49분이라 여유를 둔다.
GRACE = timedelta(hours=12)
LOOKBACK_DAYS = 7


def _key(report_type: str, report_date: str) -> str:
    return f"{report_type}:{report_date}"


def load(path: Path | None = None) -> dict:
    path = path or DEFAULT_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("sent", {})
            data.setdefault("alerted", [])
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        # 기록이 깨졌다고 리포트를 막지는 않는다 — 중복 한 번이 누락보다 낫다
        _logger.error("[DELIVERY_LOG_ERROR] 발송 기록을 읽지 못했습니다 (%s) — 새로 시작합니다", exc)
    return {"since": None, "sent": {}, "alerted": []}


def _save(data: dict, path: Path | None = None) -> None:
    path = path or DEFAULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def already_sent(report_type: str, report_date: str, path: Path | None = None) -> str | None:
    """이미 발송됐으면 발송 시각(KST 문자열), 아니면 None."""
    return load(path)["sent"].get(_key(report_type, report_date))


def record_sent(
    report_type: str, report_date: str,
    now: datetime | None = None, path: Path | None = None,
) -> None:
    now = now or now_kst()
    data = load(path)
    # 기록 시작 시점 — 이보다 앞선 회차는 기록이 없는 게 당연하므로 누락으로 치지 않는다
    data["since"] = data.get("since") or now.strftime("%Y-%m-%d %H:%M")
    data["sent"][_key(report_type, report_date)] = now.strftime("%Y-%m-%d %H:%M")

    # 무한히 자라지 않게 오래된 기록은 정리한다 (판정에는 최근 며칠만 쓴다)
    cutoff = (now.date() - timedelta(days=60)).isoformat()
    data["sent"] = {k: v for k, v in data["sent"].items() if k.split(":", 1)[1] >= cutoff}
    data["alerted"] = [k for k in data["alerted"] if k.split(":", 1)[1] >= cutoff]
    _save(data, path)


def expected_reports(start: date, end: date) -> list[tuple[str, str, datetime]]:
    """[start, end] 기간에 발송됐어야 하는 회차: (유형, 발행일, 예정 시각 KST)."""
    out = []
    d = start
    while d <= end:
        if d.weekday() != 6:   # 일요일 제외
            out.append(("morning", d.isoformat(), datetime(d.year, d.month, d.day, *_MORNING_AT)))
        if is_trading_day(d, "KR"):
            out.append(("evening", d.isoformat(), datetime(d.year, d.month, d.day, *_EVENING_AT)))
        d += timedelta(days=1)
    return out


def find_missed(now: datetime | None = None, path: Path | None = None) -> list[tuple[str, str]]:
    """발송됐어야 하는데 기록이 없는 회차 (이미 알린 것은 제외)."""
    now = now or now_kst()
    data = load(path)
    if not data.get("since"):
        return []   # 기록이 막 시작됐다 — 비교할 기준이 없다
    since = datetime.strptime(data["since"], "%Y-%m-%d %H:%M")
    alerted = set(data["alerted"])
    missed = []
    for rtype, rdate, due in expected_reports(now.date() - timedelta(days=LOOKBACK_DAYS), now.date()):
        key = _key(rtype, rdate)
        if due < since or due + GRACE > now:
            continue
        if key in data["sent"] or key in alerted:
            continue
        missed.append((rtype, rdate))
    return missed


def mark_alerted(missed: list[tuple[str, str]], path: Path | None = None) -> None:
    """같은 누락을 실행마다 반복해서 알리지 않도록 기록한다."""
    if not missed:
        return
    data = load(path)
    data["alerted"] = sorted(set(data["alerted"]) | {_key(t, d) for t, d in missed})
    _save(data, path)
