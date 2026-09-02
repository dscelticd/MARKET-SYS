"""
Market Flow 헬스체크 — 시스템 설정 및 연결 상태 전체 점검
실행: python app/healthcheck.py
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

OK   = "  [OK]  "
WARN = "  [경고] "
FAIL = "  [오류] "

results: list[tuple[str, str, str]] = []   # (status, item, message)


def check(status: str, item: str, msg: str) -> None:
    results.append((status, item, msg))
    icon = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "❌"}.get(status, "  ")
    print(f"  {icon}  {item:30s}  {msg}")


# ── 1. Python 패키지 ─────────────────────────────────────────────────────────
print("\n[1] 패키지 설치 확인")
packages = {
    "anthropic":   "Claude API",
    "dotenv":      "환경변수 로드",
    "yfinance":    "실시간 시장 데이터",
    "schedule":    "자동 스케줄러",
    "streamlit":   "대시보드",
    "plotly":      "차트",
    "pandas":      "데이터 처리",
    "requests":    "네이버 수급 스크래핑 / DART 공시",
    "bs4":         "네이버 수급 스크래핑 (beautifulsoup4)",
    "mplfinance":  "캔들차트 이미지 생성",
}
for pkg, desc in packages.items():
    try:
        __import__(pkg)
        check("OK", f"{pkg}", f"설치됨 ({desc})")
    except ImportError:
        check("FAIL", f"{pkg}", f"미설치 — pip install {pkg}")

# ── 2. 환경변수 ──────────────────────────────────────────────────────────────
print("\n[2] 환경변수 (.env) 확인")
api_key  = os.getenv("ANTHROPIC_API_KEY", "")
smtp_user = os.getenv("SMTP_USER", "")
smtp_pw   = os.getenv("SMTP_PASSWORD", "")
email_to  = os.getenv("EMAIL_TO", "")
use_mock  = os.getenv("USE_MOCK_DATA", "true")

if api_key and len(api_key) > 20:
    check("OK", "ANTHROPIC_API_KEY", f"설정됨 (앞 15자: {api_key[:15]}...)")
else:
    check("FAIL", "ANTHROPIC_API_KEY", "설정되지 않음 — .env 파일에 입력 필요")

if smtp_user:
    check("OK", "SMTP_USER", f"설정됨 ({smtp_user})")
else:
    check("WARN", "SMTP_USER", "미설정 — 이메일 발송 불가 (선택 사항)")

if smtp_pw:
    check("OK", "SMTP_PASSWORD", "설정됨 (앱 비밀번호)")
else:
    check("WARN", "SMTP_PASSWORD", "미설정 — 이메일 발송 불가 (선택 사항)")

if email_to:
    check("OK", "EMAIL_TO", f"설정됨 ({email_to})")
else:
    check("WARN", "EMAIL_TO", "미설정 — 이메일 발송 불가 (선택 사항)")

check("OK", "USE_MOCK_DATA", f"{'Mock 모드' if use_mock == 'true' else '실제 데이터 모드'} ({use_mock})")

naver_id     = os.getenv("NAVER_CLIENT_ID", "")
naver_secret = os.getenv("NAVER_CLIENT_SECRET", "")
telegram_tok = os.getenv("TELEGRAM_BOT_TOKEN", "")
telegram_cid = os.getenv("TELEGRAM_CHAT_ID", "")
dart_key     = os.getenv("DART_API_KEY", "")

if naver_id and naver_secret:
    check("OK", "NAVER_CLIENT_ID/SECRET", "설정됨 — 국내 종목 뉴스 실데이터")
else:
    check("WARN", "NAVER_CLIENT_ID/SECRET", "미설정 — 국내 종목 뉴스는 테스트 데이터로 대체 (선택 사항)")

if telegram_tok and telegram_cid:
    check("OK", "TELEGRAM_BOT_TOKEN/CHAT_ID", "설정됨 — 등급 변화/오류 알림 발송")
else:
    check("WARN", "TELEGRAM_BOT_TOKEN/CHAT_ID", "미설정 — 텔레그램 알림 비활성화 (선택 사항)")

if dart_key:
    check("OK", "DART_API_KEY", "설정됨 — 공시 데이터 연동")
else:
    check("WARN", "DART_API_KEY", "미설정 — 공시 데이터 미연동 (선택 사항, opendart.fss.or.kr에서 무료 발급)")

kis_key    = os.getenv("KIS_APP_KEY", "")
kis_secret = os.getenv("KIS_APP_SECRET", "")
if kis_key and kis_secret:
    check("OK", "KIS_APP_KEY/SECRET", f"설정됨 — 수급 데이터 공식 연동 ({os.getenv('KIS_ENV', 'demo')} 모드)")
else:
    check("WARN", "KIS_APP_KEY/SECRET", "미설정 — 수급 데이터는 네이버 스크래핑으로 폴백 (선택 사항)")

fred_key = os.getenv("FRED_API_KEY", "")
if fred_key:
    check("OK", "FRED_API_KEY", "설정됨 — 이벤트 캘린더에 미국 매크로 지표 발표일 포함")
else:
    check("WARN", "FRED_API_KEY", "미설정 — 이벤트 캘린더에서 미국 매크로 지표 항목만 제외 (선택 사항, fred.stlouisfed.org에서 무료 발급)")

# ── 3. 설정 파일 ─────────────────────────────────────────────────────────────
print("\n[3] 설정 파일 확인")
config_files = {
    "config/watchlist.json":    "관심종목",
    "config/themes.json":       "관심테마",
    "config/user_profile.json": "투자성향",
    "config/report_config.json":"리포트 설정",
    "config/market_holidays.json":"휴장일 달력",
}
for fname, desc in config_files.items():
    p = _PROJECT_ROOT / fname
    if p.exists():
        check("OK", fname, f"존재 ({p.stat().st_size:,} bytes, {desc})")
    else:
        check("FAIL", fname, f"파일 없음 ({desc})")

# ── 4. 데이터 폴더 ───────────────────────────────────────────────────────────
print("\n[4] 데이터 폴더 확인")
folders = ["data/reports", "data/history", "data/cache"]
for folder in folders:
    p = _PROJECT_ROOT / folder
    p.mkdir(parents=True, exist_ok=True)
    files = list(p.glob("*"))
    check("OK", folder, f"존재 ({len(files)}개 파일)")

# ── 5. Claude API 연결 ───────────────────────────────────────────────────────
print("\n[5] Claude API 연결 테스트")
if api_key and len(api_key) > 20:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
        check("OK", "Claude API", f"연결 성공 (모델: {os.getenv('CLAUDE_MODEL', 'claude-sonnet-4-6')})")
    except Exception as e:
        check("FAIL", "Claude API", f"연결 실패: {str(e)[:60]}")
else:
    check("FAIL", "Claude API", "API 키 미설정으로 테스트 불가")

# ── 6. 실제 데이터 수집 테스트 ───────────────────────────────────────────────
print("\n[6] 데이터 수집 테스트")
if use_mock == "false":
    try:
        from app.collectors.price_collector import PriceCollector
        prices = PriceCollector().collect(["US_NVDA"])
        if prices and not prices.get("US_NVDA", {}).get("_mock"):
            p = prices["US_NVDA"]
            check("OK", "주가 수집 (yfinance)", f"NVDA ${p['price']:,.2f} ({p['change_pct']:+.2f}%)")
        else:
            check("WARN", "주가 수집 (yfinance)", "Mock 폴백됨 — 인터넷 연결 확인")
    except Exception as e:
        check("FAIL", "주가 수집 (yfinance)", f"오류: {str(e)[:50]}")

    try:
        from app.collectors.macro_collector import MacroCollector
        macro = MacroCollector().collect()
        if not macro.get("_mock"):
            sp = macro["us_market"]["SP500"]["value"]
            check("OK", "거시지표 수집 (yfinance)", f"S&P500 {sp:,.1f}")
        else:
            check("WARN", "거시지표 수집 (yfinance)", "Mock 폴백됨")
    except Exception as e:
        check("FAIL", "거시지표 수집 (yfinance)", f"오류: {str(e)[:50]}")

    if kis_key and kis_secret:
        try:
            from app.collectors.kis_collector import KISCollector
            kis = KISCollector()
            flow = kis.fetch_investor_flow("005930")
            check("OK", "수급 수집 (KIS)", f"삼성전자 5일 외국인 {flow.get('foreign_net_5d', 0):+,}주")
        except Exception as e:
            check("WARN", "수급 수집 (KIS)", f"실패 — 네이버로 자동 폴백됨: {str(e)[:50]}")

    if fred_key:
        try:
            from app.collectors.calendar_collector import CalendarCollector
            resolved = CalendarCollector()._resolve_release_ids()
            check("OK", "이벤트 캘린더 수집 (FRED)", f"매크로 지표 {len(resolved)}개 release_id 확인됨")
        except Exception as e:
            check("WARN", "이벤트 캘린더 수집 (FRED)", f"실패 — FOMC/금통위·법정기한·DART 항목은 계속 표시됨: {str(e)[:50]}")
else:
    check("OK", "데이터 수집 모드", "Mock 모드 (USE_MOCK_DATA=true)")

# ── 7. 이메일 발송 설정 ──────────────────────────────────────────────────────
print("\n[7] 이메일 발송 설정 확인")
if smtp_user and smtp_pw and email_to:
    try:
        import smtplib, ssl
        ctx = ssl.create_default_context()
        host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        port = int(os.getenv("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.login(smtp_user, smtp_pw)
        check("OK", "SMTP 연결", f"{host}:{port} 로그인 성공")
    except Exception as e:
        check("FAIL", "SMTP 연결", f"실패: {str(e)[:60]}")
else:
    check("WARN", "SMTP 연결", "설정 미완료 (이메일 발송 비활성화)")

# ── 최종 결과 ────────────────────────────────────────────────────────────────
ok_cnt   = sum(1 for r in results if r[0] == "OK")
warn_cnt = sum(1 for r in results if r[0] == "WARN")
fail_cnt = sum(1 for r in results if r[0] == "FAIL")

print("\n" + "=" * 55)
print(f"  헬스체크 결과: ✅ {ok_cnt}개 정상  ⚠️  {warn_cnt}개 경고  ❌ {fail_cnt}개 오류")
if fail_cnt == 0 and warn_cnt == 0:
    print("  🎉 모든 항목 정상! Market Flow 시스템이 준비됐습니다.")
elif fail_cnt == 0:
    print("  ✅ 핵심 기능은 정상입니다. 경고 항목은 선택 사항입니다.")
else:
    print("  ❌ 오류 항목을 수정해야 정상 실행이 가능합니다.")
print("=" * 55 + "\n")
