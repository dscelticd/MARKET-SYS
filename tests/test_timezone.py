"""
타임존 처리 테스트

배경: 코드 전반이 `datetime.now()`를 쓰면서 결과를 KST로 간주해왔다. GitHub Actions
      러너는 UTC라 9시간 어긋났고, 실측(2026-09-01 발송분)에서 리포트 헤더가
      "2026-09-01 00:31 KST"로 찍혔으나 실제 수신 시각은 09:31 KST였다.

      표기만의 문제가 아니었다. 아침 크론(22:10 UTC = 07:10 KST 다음날)이 돌면
      `datetime.now().date()`가 전날이 되어
        - 리포트 파일명이 하루 전 (20260831_morning.md)
        - 등급 이력 키가 하루 전 (2026-08-31_morning)
        - 거래일 판정이 하루 전 요일 (금 22:10 UTC = 토 07:10 KST인데 "금요일=거래일")
      가 되어, 앞서 만든 주말 처리 로직이 통째로 무력화됐다.
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime, timedelta, timezone

from app.utils.market_calendar import KST, now_kst

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_now_kst_is_nine_hours_ahead_of_utc():
    delta = now_kst() - datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs(delta - timedelta(hours=9)) < timedelta(seconds=5)


def test_now_kst_returns_naive_datetime():
    """tz-aware를 반환하면 strptime 결과(naive)와 비교하는 곳에서 TypeError가 난다."""
    assert now_kst().tzinfo is None
    # 실제로 naive와 비교 가능한지 확인
    assert isinstance(now_kst() - datetime(2020, 1, 1), timedelta)


def test_kst_offset_constant():
    assert KST.utcoffset(None) == timedelta(hours=9)


def test_pipeline_modules_do_not_use_naive_datetime_now():
    """날짜·시각이 결과물에 반영되는 모듈은 now_kst()를 써야 한다.

    dashboard.py는 자체 _now_kst()를 갖고 있어 제외한다.
    이 테스트가 깨지면 UTC 러너에서 날짜가 하루 밀리는 버그가 되살아난 것이다.
    """
    targets = [
        "app/main.py", "app/engine/history_tracker.py", "app/reports/report_builder.py",
        "app/delivery/email_sender.py", "app/utils/telegram_notifier.py",
        "app/utils/data_validator.py", "app/utils/market_calendar.py",
        "app/collectors/price_collector.py", "app/collectors/macro_collector.py",
        "app/collectors/news_collector.py", "app/collectors/calendar_collector.py",
        "app/collectors/disclosure_collector.py", "app/collectors/theme_scanner.py",
        "app/collectors/kis_collector.py",
    ]
    offenders = {}
    for t in targets:
        src = (_ROOT / t).read_text(encoding="utf-8")
        # 문서 문자열/주석 안의 언급은 제외하고 실제 호출만 센다
        code = re.sub(r'""".*?"""', "", src, flags=re.S)
        code = re.sub(r"#.*", "", code)
        hits = re.findall(r"\bdatetime\.now\(\)", code)
        if hits:
            offenders[t] = len(hits)
    assert not offenders, f"now_kst() 대신 datetime.now() 사용: {offenders}"


def test_report_timestamp_label_matches_kst():
    """리포트가 "KST"라고 적는 시각이 실제 KST여야 한다."""
    main_src = (_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    m = re.search(r'"collected_at":\s*(\w+)\(\)', main_src)
    assert m and m.group(1) == "now_kst", "collected_at이 KST 기준이 아님"


def test_morning_cron_run_produces_correct_kst_date():
    """아침 크론(22:10 UTC)이 KST 기준 '다음날'로 처리되는지 확인.

    러너 UTC 시각 2026-08-31 22:10 = KST 2026-09-01 07:10.
    naive datetime.now()를 쓰면 8/31로 잘못 기록된다.
    """
    utc_moment = datetime(2026, 8, 31, 22, 10, tzinfo=timezone.utc)
    kst_moment = utc_moment.astimezone(KST).replace(tzinfo=None)
    assert kst_moment.date().isoformat() == "2026-09-01"
    assert kst_moment.strftime("%H:%M") == "07:10"


def test_friday_night_utc_is_saturday_in_kst():
    """금 22:10 UTC = 토 07:10 KST. 거래일 판정이 UTC 요일을 보면 주말 처리가 무력화된다."""
    from app.utils.market_calendar import is_trading_day
    utc_fri = datetime(2026, 9, 4, 22, 10, tzinfo=timezone.utc)
    kst = utc_fri.astimezone(KST).replace(tzinfo=None)
    assert kst.weekday() == 5                    # 토요일
    assert is_trading_day(kst.date()) is False   # KST 기준으로는 휴장
    assert is_trading_day(utc_fri.replace(tzinfo=None).date()) is True  # UTC 기준이면 거래일로 오판


def test_workflow_persists_grade_history():
    """러너는 매 실행 새 VM이라 캐시 없이는 등급 이력이 누적되지 않는다.
    실측(2026-09-01 발송분): "모든 등급은 첫 등재(신규 등재)", "적중률 통계 아직 없음".
    """
    wf = (_ROOT / ".github" / "workflows" / "market-flow.yml").read_text(encoding="utf-8")
    assert "path: data/history" in wf, "등급 이력 캐시 설정 누락"
    assert "restore-keys" in wf, "직전 이력을 복원하지 않으면 누적되지 않는다"


def test_workflow_report_type_is_schedule_driven():
    """실행 시각으로 유형을 추론하면 지연 시 아침/저녁이 뒤바뀐다."""
    wf = (_ROOT / ".github" / "workflows" / "market-flow.yml").read_text(encoding="utf-8")
    assert "github.event.schedule" in wf
    # 주석에는 "왜 바꿨는지" 설명으로 옛 방식이 언급되므로 주석을 걷어내고 검사한다
    code_only = "\n".join(
        line for line in wf.splitlines() if not line.lstrip().startswith("#")
    )
    assert "date -u +%H" not in code_only, "시각 기반 유형 추론이 되살아남"


# ── 시장 세션 상태 (③ 장중/종가 구분, ④ 시장 간 시차 오탐) ──────────────────

def test_market_session_state_transitions():
    from app.utils.market_calendar import market_session_state
    assert market_session_state("KR", datetime(2026, 9, 1, 8, 30)) == "개장전"
    assert market_session_state("KR", datetime(2026, 9, 1, 9, 31)) == "개장중"
    assert market_session_state("KR", datetime(2026, 9, 1, 20, 40)) == "마감"
    assert market_session_state("KR", datetime(2026, 9, 5, 10, 0)) == "휴장"   # 토요일


def test_us_session_uses_eastern_time():
    """한국 저녁이 미국 개장 전이어야 한다(ET 기준 환산)."""
    from app.utils.market_calendar import market_session_state
    assert market_session_state("US", datetime(2026, 9, 1, 20, 40)) == "개장전"


def test_freshness_includes_session_states():
    from app.utils.market_calendar import summarize_data_freshness
    f = summarize_data_freshness(
        {"KR_005930": {"data_date": "2026-09-01"}}, now=datetime(2026, 9, 1, 9, 31)
    )
    assert f["sessions"]["KR"] == "개장중"


def test_session_block_says_price_not_close_while_market_open():
    """실측(2026-09-01 09:31 발송분): 개장 31분 후인데 "9월 1일 종가 기준"으로 표기됐다."""
    from app.reports.report_builder import _format_market_session_block
    from app.utils.market_calendar import summarize_data_freshness
    f = summarize_data_freshness(
        {"KR_005930": {"data_date": "2026-09-01"}}, now=datetime(2026, 9, 1, 9, 31)
    )
    block = _format_market_session_block(f)
    assert "종가 기준" not in block
    assert "개장 중" in block and "장중 현재가" in block


def test_session_block_says_close_after_market_closes():
    from app.reports.report_builder import _format_market_session_block
    from app.utils.market_calendar import summarize_data_freshness
    f = summarize_data_freshness(
        {"KR_005930": {"data_date": "2026-09-01"}}, now=datetime(2026, 9, 1, 20, 40)
    )
    assert "종가 기준" in _format_market_session_block(f)


def test_index_staleness_compares_within_same_market():
    """시장 간 시차는 결함이 아니다 — 한국 장이 열린 오전에 미국 지수가 전일인 것은 정상."""
    from app.utils.data_validator import DataValidator
    price = {"KR_005930": {"change_pct": -1.4, "_mock": False, "data_date": "2026-09-01"},
             "US_NVDA":   {"change_pct": -3.2, "_mock": False, "data_date": "2026-08-31"}}
    macro = {"kr_market": {"KOSPI": {"value": 6820, "change_pct": -0.45}},
             "data_dates": {"KOSPI": "2026-09-01", "SP500": "2026-08-31"}, "_mock": False}
    _, _, warnings = DataValidator._validate_kospi_consistency(price, macro)
    assert not any("지수가 같은 시장" in w for w in warnings)


def test_index_staleness_still_detects_real_lag_within_market():
    from app.utils.data_validator import DataValidator
    price = {"KR_005930": {"change_pct": -1.4, "_mock": False, "data_date": "2026-09-01"}}
    macro = {"kr_market": {"KOSPI": {"value": 6912, "change_pct": 1.53}},
             "data_dates": {"KOSPI": "2026-08-27"}, "_mock": False}
    _, _, warnings = DataValidator._validate_kospi_consistency(price, macro)
    assert any("KOSPI 지수가 같은 시장" in w for w in warnings)


def test_workflow_actions_run_on_node24():
    """Node 20은 2026-09-23에 러너에서 제거된다. node20 액션을 쓰면 그때 워크플로가 깨진다.

    실측으로 확인한 최소 node24 버전 — upload-artifact는 v5도 여전히 node20이라 v6이 필요하다.
    """
    wf = (_ROOT / ".github" / "workflows" / "market-flow.yml").read_text(encoding="utf-8")
    deprecated = [
        "actions/checkout@v4", "actions/checkout@v3",
        "actions/setup-python@v5", "actions/setup-python@v4",
        "actions/cache@v4", "actions/cache@v3",
        "actions/upload-artifact@v5", "actions/upload-artifact@v4",
    ]
    found = [d for d in deprecated if d in wf]
    assert not found, f"Node 20 기반 액션 사용 중: {found}"
