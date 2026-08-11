"""
Market Flow 자동 스케줄러
매일 아침 07:00 브리핑, 저녁 20:30 결산 자동 실행

실행: python app/scheduler.py
      또는  스케줄러 실행.bat  더블클릭
"""
from __future__ import annotations

import io
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import schedule
import time

# Windows UTF-8
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = PROJECT_ROOT / "data" / "scheduler.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("scheduler")

# .env 로드
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass


def run_report(report_type: str, send_email: bool = True) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    label = "아침 브리핑" if report_type == "morning" else "저녁 결산"
    logger.info(f"[자동 실행 시작] {label} — {now}")

    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "app" / "main.py"),
        "--report", report_type,
    ]
    if send_email:
        cmd.append("--send-email")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if result.returncode == 0:
            logger.info(f"[완료] {label} 리포트 생성 성공")
        else:
            logger.error(f"[실패] {label} 리포트 생성 실패\n{result.stderr}")
    except subprocess.TimeoutExpired:
        logger.error(f"[타임아웃] {label} 실행이 5분을 초과했습니다")
    except Exception as e:
        logger.error(f"[오류] {label} 실행 중 예외 발생: {e}")


def morning_job() -> None:
    run_report("morning", send_email=True)


def evening_job() -> None:
    run_report("evening", send_email=True)


def _load_schedule_times() -> tuple[str, str]:
    """user_profile.json에서 리포트 발송 시간을 읽어 반환 (기본: 07:00 / 20:30)"""
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from app.utils.config_loader import get_config
        cfg = get_config()
        return cfg.user.morning_time, cfg.user.evening_time
    except Exception as e:
        logger.warning(f"설정 로드 실패, 기본값 사용: {e}")
        return "07:00", "20:30"


def print_schedule_info(morning_time: str, evening_time: str) -> None:
    print("=" * 50)
    print("  Market Flow 자동 스케줄러 실행 중")
    print("=" * 50)
    print(f"  아침 브리핑 : 매일 {morning_time} (이메일 발송 포함)")
    print(f"  저녁 결산   : 매일 {evening_time} (이메일 발송 포함)")
    print(f"  로그 파일   : {LOG_PATH}")
    print(f"  종료하려면  : Ctrl+C")
    print("=" * 50)

    next_jobs = schedule.get_jobs()
    if next_jobs:
        for job in next_jobs:
            print(f"  다음 실행 예정: {job.next_run}")
    print()


# ── 스케줄 등록 (config에서 시간 읽기) ──────────────────────────────────────
_morning_time, _evening_time = _load_schedule_times()
schedule.every().day.at(_morning_time).do(morning_job).tag("morning")
schedule.every().day.at(_evening_time).do(evening_job).tag("evening")


def main() -> None:
    print_schedule_info(_morning_time, _evening_time)
    logger.info(f"스케줄러 시작 — 아침 {_morning_time} / 저녁 {_evening_time} 자동 실행")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)   # 30초마다 스케줄 체크
    except KeyboardInterrupt:
        logger.info("스케줄러 종료 (Ctrl+C)")
        print("\n스케줄러가 종료되었습니다.")


if __name__ == "__main__":
    main()
