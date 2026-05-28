"""
Market Flow Intelligence System — CLI 진입점

실행 방법:
  python app/main.py --report morning
  python app/main.py --report morning --send-email
  python app/main.py --report evening
  python app/main.py --report evening --send-email
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime
from pathlib import Path

# Windows 터미널 UTF-8 강제 (이모지/한글 출력)
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# .env 로드 (프로젝트 루트 기준)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass  # python-dotenv 미설치 시 환경변수는 시스템 레벨에서 주입

from app.utils.config_loader import get_config
from app.collectors.price_collector import PriceCollector
from app.collectors.news_collector import NewsCollector
from app.collectors.macro_collector import MacroCollector
from app.engine.signal_scorer import SignalScorer
from app.engine.rating_analyzer import RatingAnalyzer
from app.engine.history_tracker import HistoryTracker, CHANGE_EMOJI
from app.reports.report_builder import ReportBuilder, save_report
from app.delivery.email_sender import EmailSender


# ── 컬러 출력 헬퍼 ──────────────────────────────────────────────────────────
GRADE_COLORS = {
    "추천": "\033[92m",   # 초록
    "안전": "\033[94m",   # 파랑
    "보통": "\033[93m",   # 노랑
    "주의": "\033[91m",   # 빨강(밝)
    "위험": "\033[31m",   # 빨강(진)
}
RESET = "\033[0m"
BOLD  = "\033[1m"


def print_section(title: str) -> None:
    print(f"\n{BOLD}{'─' * 60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'─' * 60}{RESET}")


def print_rating(r: dict) -> None:
    color = GRADE_COLORS.get(r["grade"], "")
    print(
        f"  {r['emoji']} {BOLD}{r['name']}{RESET} ({r['ticker']}) "
        f"| {color}{BOLD}[{r['grade']}]{RESET} "
        f"점수:{r['total_score']:.0f}  리스크:{r['risk_score']:.0f}  신뢰도:{r['data_confidence']:.0f}"
    )
    if r["positive_factors"]:
        print(f"     ✅ {r['positive_factors'][0]}")
    if r["negative_factors"]:
        print(f"     ⚠️  {r['negative_factors'][0]}")


# ── 파이프라인 ───────────────────────────────────────────────────────────────

def run_pipeline(report_type: str, send_email: bool) -> None:
    date_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H:%M")

    print(f"\n{BOLD}🚀 Market Flow Intelligence System{RESET}")
    print(f"   리포트 유형: {report_type.upper()}  |  일시: {date_str} {time_str}")

    # ── Step 1: 설정 로드 ──
    print_section("Step 1. 설정 로드")
    cfg = get_config()
    stocks = cfg.watchlist.stocks
    stock_ids = [s["id"] for s in stocks]
    print(f"  관심종목: {len(stocks)}개 로드 완료")
    print(f"  관심테마: {len(cfg.themes.themes)}개 로드 완료")
    print(f"  투자성향: {cfg.user.risk_tolerance}")

    # ── Step 2: Collector 실행 ──
    import os as _os
    use_mock = _os.getenv("USE_MOCK_DATA", "true").lower() == "true"
    data_mode = "Mock" if use_mock else "실제 데이터 (yfinance)"
    print_section(f"Step 2. 데이터 수집 ({data_mode})")

    price_col = PriceCollector()
    news_col  = NewsCollector()
    macro_col = MacroCollector()

    price_data = price_col.collect(stock_ids)
    news_data  = news_col.collect(stock_ids)
    macro_data = macro_col.collect()

    real_count = sum(1 for d in price_data.values() if not d.get("_mock"))
    mock_count = len(price_data) - real_count
    print(f"  가격 데이터: {len(price_data)}개 종목 (실제:{real_count} / Mock:{mock_count})")
    print(f"  뉴스 데이터: {sum(len(v) for v in news_data.values())}건")
    print(f"  거시 지표: {'실제' if not macro_data.get('_mock') else 'Mock'} 수집 완료")

    # ── Step 3: 신호 점수 계산 ──
    print_section("Step 3. 신호 점수 계산")
    scorer = SignalScorer(weights=cfg.user.signal_weights)
    theme_map = {t["id"]: t for t in cfg.themes.themes}
    score_results = []

    for stock in stocks:
        sid = stock["id"]
        sr = scorer.score(
            stock_info=stock,
            price_data=price_data.get(sid, {}),
            news_data=news_data.get(sid, []),
            macro_data=macro_data,
            theme_config=theme_map,
        )
        score_results.append(sr)

    print(f"  신호 점수 계산 완료: {len(score_results)}개 종목")

    # ── Step 4: 등급 산정 ──
    print_section("Step 4. 투자 판단 보조 등급 산정")
    analyzer = RatingAnalyzer()
    rating_results = analyzer.analyze_batch(score_results, stocks)
    rating_dicts   = [r.to_dict() for r in rating_results]

    # 표시 순서 적용
    order_map = {sid: i for i, sid in enumerate(cfg.user.display_order)}
    rating_dicts.sort(key=lambda r: order_map.get(r["stock_id"], 999))

    dist = analyzer.grade_distribution(rating_results)
    print(f"  등급 분포: {' | '.join(f'{g}: {n}개' for g, n in dist.items() if n > 0)}")
    print()
    for r in rating_dicts:
        print_rating(r)

    # ── Step 4-1: 히스토리 저장 & 변화 감지 ──
    tracker = HistoryTracker()
    changes = tracker.get_changes(rating_dicts, report_type)
    changed = [c for c in changes if c["direction"] in ("상승", "하락")]
    if changed:
        print(f"\n  [{CHANGE_EMOJI['상승']} 등급 변화 감지: {len(changed)}개]")
        for c in changed:
            arrow = CHANGE_EMOJI[c["direction"]]
            print(f"    {arrow} {c['name']:16s}  {c['prev_grade']} → {c['curr_grade']}  ({c['score_delta']:+.0f}점)")
    tracker.save_today(rating_dicts, report_type)

    # ── Step 5: Claude API 리포트 생성 ──
    print_section("Step 5. 리포트 생성")
    builder = ReportBuilder()

    if report_type == "morning":
        print("  아침 브리핑 리포트 생성 중...")
        report_content = builder.build_morning_report(
            price_data=price_data,
            news_data=news_data,
            macro_data=macro_data,
            ratings=rating_dicts,
            report_date=date_str,
            grade_changes=changes,
        )
    else:
        print("  저녁 결산 리포트 생성 중...")
        report_content = builder.build_evening_report(
            price_data=price_data,
            news_data=news_data,
            macro_data=macro_data,
            ratings=rating_dicts,
            report_date=date_str,
            grade_changes=changes,
        )
    print("  리포트 생성 완료")

    # ── Step 6: 리포트 저장 ──
    print_section("Step 6. 리포트 저장")
    save_dir = cfg.report_save_dir()
    saved_path = save_report(report_content, report_type, save_dir)
    print(f"  저장 완료: {saved_path}")

    # 등급 JSON도 함께 저장 (대시보드 참조용)
    date_prefix = datetime.now().strftime("%Y%m%d")
    json_path = save_dir / f"{date_prefix}_{report_type}_ratings.json"
    json_path.write_text(
        json.dumps(
            {"date": date_str, "type": report_type, "ratings": rating_dicts, "macro": macro_data},
            ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    print(f"  등급 JSON 저장: {json_path}")

    # ── Step 7: 이메일 발송 (옵션) ──
    if send_email:
        print_section("Step 7. 이메일 발송")
        sender = EmailSender()
        if sender.is_configured():
            sender.send_report(report_type, report_content, date_str)
        else:
            print("  ⚠️  이메일 설정 미완료 — .env에 SMTP 정보를 입력하세요.")

    # ── 완료 ──
    print(f"\n{BOLD}✅ Market Flow {report_type.upper()} 리포트 완료{RESET}")
    print(f"   리포트 파일: {saved_path}\n")


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Market Flow Intelligence System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python app/main.py --report morning
  python app/main.py --report morning --send-email
  python app/main.py --report evening
  python app/main.py --report evening --send-email
  streamlit run app/dashboard.py
        """,
    )
    parser.add_argument(
        "--report",
        choices=["morning", "evening"],
        required=True,
        help="리포트 유형: morning(아침) 또는 evening(저녁)",
    )
    parser.add_argument(
        "--send-email",
        action="store_true",
        default=False,
        help="리포트 생성 후 이메일 발송",
    )

    args = parser.parse_args()
    run_pipeline(report_type=args.report, send_email=args.send_email)


if __name__ == "__main__":
    main()
