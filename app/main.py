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
import logging
import os
import sys
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path

def _force_utf8_console() -> None:
    """Windows 터미널 UTF-8 강제 (이모지/한글 출력).
    CLI로 직접 실행될 때만 호출해야 함 — 모듈 임포트 시점에 무조건 실행하면
    sys.stdout을 재할당해버려 pytest 등 stdout을 자체 캡처하는 도구가 깨진다
    (실제로 이 모듈을 테스트에서 import하자 pytest capture teardown이 크래시함).
    """
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
from app.utils.data_validator import DataValidator
from app.utils.telegram_notifier import TelegramNotifier
from app.collectors.price_collector import PriceCollector
from app.collectors.news_collector import NewsCollector
from app.collectors.macro_collector import MacroCollector
from app.collectors.disclosure_collector import DisclosureCollector
from app.engine.signal_scorer import SignalScorer
from app.engine.rating_analyzer import RatingAnalyzer, apply_grade_cap
from app.engine.history_tracker import HistoryTracker, CHANGE_EMOJI
from app.engine.portfolio_analyzer import build_portfolio_summary
from app.reports.report_builder import ReportBuilder, save_report
from app.reports.chart_generator import generate_report_charts
from app.delivery.email_sender import EmailSender


# ── 로깅 설정 ────────────────────────────────────────────────────────────────
def _setup_logging() -> None:
    """실행 로그를 data/logs/market_flow.log 에 누적 저장"""
    log_dir = _PROJECT_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "market_flow.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

logger = logging.getLogger("market_flow")


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

# 네이버 수급 스크래핑(비공식 소스)의 실패율이 이 값 이상이면 페이지 구조 변경
# 가능성으로 보고 텔레그램 경고 — 1~2종목의 일시적 네트워크 오류는 알림에서 제외
_SCRAPER_FAILURE_ALERT_THRESHOLD = 0.7


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


def check_kr_investor_flow_failure_rate(price_data: dict) -> tuple[int, int] | None:
    """KR 종목의 네이버 수급 스크래핑 (실패 건수, 전체 건수) 반환.
    KR 종목 자체가 없거나 investor_flow 데이터가 하나도 없으면 None.
    """
    kr_flows = [
        d["investor_flow"] for sid, d in price_data.items()
        if sid.startswith("KR_") and d.get("investor_flow")
    ]
    if not kr_flows:
        return None
    fail_count = sum(1 for f in kr_flows if f.get("_mock"))
    return fail_count, len(kr_flows)


# ── 파이프라인 ───────────────────────────────────────────────────────────────

def run_pipeline(report_type: str, send_email: bool) -> None:
    _setup_logging()
    date_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H:%M")

    logger.info("파이프라인 시작 — type=%s  date=%s %s", report_type, date_str, time_str)
    print(f"\n{BOLD}🚀 Market Flow Intelligence System{RESET}")
    print(f"   리포트 유형: {report_type.upper()}  |  일시: {date_str} {time_str}")

    notifier = TelegramNotifier()

    try:
        # ── Step 1: 설정 로드 ──
        print_section("Step 1. 설정 로드")
        cfg = get_config()
        stocks = cfg.watchlist.stocks
        stock_ids = [s["id"] for s in stocks]
        print(f"  관심종목: {len(stocks)}개 로드 완료")
        print(f"  관심테마: {len(cfg.themes.themes)}개 로드 완료")
        print(f"  투자성향: {cfg.user.risk_tolerance}")
        logger.info("설정 로드 완료 — %d개 종목", len(stocks))

        # ── Step 2: Collector 실행 ──
        use_mock = os.getenv("USE_MOCK_DATA", "true").lower() == "true"
        data_mode = "Mock" if use_mock else "실제 데이터 (yfinance)"
        print_section(f"Step 2. 데이터 수집 ({data_mode})")

        price_col       = PriceCollector()
        news_col        = NewsCollector()
        macro_col       = MacroCollector()
        disclosure_col  = DisclosureCollector()

        try:
            price_data = price_col.collect(stock_ids)
        except Exception as e:
            logger.error("[COLLECTOR_ERROR] 가격 데이터 수집 실패: %s", e)
            raise
        try:
            news_data  = news_col.collect(stock_ids)
        except Exception as e:
            logger.error("[COLLECTOR_ERROR] 뉴스 데이터 수집 실패: %s", e)
            raise
        try:
            macro_data = macro_col.collect()
        except Exception as e:
            logger.error("[COLLECTOR_ERROR] 거시지표 수집 실패: %s", e)
            raise
        # 공시(DART)는 보조 데이터 — DART_API_KEY 미설정 시 빈 결과로 자동 폴백되며,
        # 수집 실패가 전체 파이프라인을 막지 않도록 다른 collector와 달리 raise하지 않음
        try:
            disclosure_data = disclosure_col.collect(stock_ids)
        except Exception as e:
            logger.warning("[COLLECTOR_ERROR] 공시 데이터 수집 실패 (무시하고 계속): %s", e)
            disclosure_data = {}

        real_count = sum(1 for d in price_data.values() if not d.get("_mock"))
        mock_count = len(price_data) - real_count
        disc_count = sum(len(v) for v in disclosure_data.values())
        print(f"  가격 데이터: {len(price_data)}개 종목 (실제:{real_count} / Mock:{mock_count})")
        print(f"  뉴스 데이터: {sum(len(v) for v in news_data.values())}건")
        print(f"  거시 지표: {'실제' if not macro_data.get('_mock') else 'Mock'} 수집 완료")
        print(f"  공시 데이터: {'연동' if disclosure_col.is_configured() else '미연동(DART_API_KEY 없음)'} ({disc_count}건)")
        logger.info("데이터 수집 완료 — 가격:%d(실제%d/Mock%d) 뉴스:%d건 공시:%d건",
                    len(price_data), real_count, mock_count,
                    sum(len(v) for v in news_data.values()), disc_count)

        # ── 네이버 수급 스크래핑 실패율 모니터링 (비공식 소스 — 조용히 Mock 폴백되므로
        # 며칠째 실패 중이어도 사용자가 알 방법이 없었음) ──
        flow_check = check_kr_investor_flow_failure_rate(price_data)
        if flow_check is not None:
            flow_fail, flow_total = flow_check
            fail_rate = flow_fail / flow_total
            print(f"  수급 데이터(네이버): {flow_total - flow_fail}/{flow_total}종목 실제 수집 성공")
            if fail_rate >= _SCRAPER_FAILURE_ALERT_THRESHOLD:
                logger.error("[SCRAPER_ERROR] 네이버 수급 스크래핑 실패율 %.0f%% (%d/%d) — 페이지 구조 변경 가능성",
                             fail_rate * 100, flow_fail, flow_total)
                if notifier.is_configured():
                    notifier.notify_scraper_failure(
                        "네이버 금융(외국인/기관 수급)", flow_fail, flow_total, report_type
                    )

        # ── 포트폴리오 관점 진단 (테마/섹터 집중도·당일 동조화) ──
        # 보조 진단 기능이라 disclosure_col과 동일하게 실패해도 파이프라인을 막지 않음
        try:
            portfolio_summary = build_portfolio_summary(stocks, price_data)
        except Exception as e:
            logger.warning("[PORTFOLIO_ANALYSIS_ERROR] 포트폴리오 집중도 진단 실패 (무시하고 계속): %s", e)
            portfolio_summary = {}
        if portfolio_summary.get("risk_flags"):
            print(f"  포트폴리오 집중 리스크: {len(portfolio_summary['risk_flags'])}건 감지")
            for flag in portfolio_summary["risk_flags"]:
                print(f"    ⚠️  {flag}")

        # ── Step 2-1: 데이터 품질 검증 ──
        print_section("Step 2-1. 데이터 품질 검증")
        validator       = DataValidator()
        data_quality    = validator.validate(
            price_data, news_data, macro_data, stocks,
            disclosure_connected=disclosure_col.is_configured(),
        )
        conf            = data_quality["overall"]["confidence"]
        status          = data_quality["overall"]["status"]
        issues          = data_quality["overall"]["issues"]
        critical_error  = data_quality["overall"].get("critical_data_error", False)
        critical_reasons = data_quality["overall"].get("critical_error_reasons", [])
        print(f"  데이터 신뢰도: {conf:.0f}점 ({status})")
        if critical_error:
            print(f"  🚨 치명적 데이터 오류 감지 — 지수·ETF·대형주 모순으로 시장 판단 보류")
            for reason in critical_reasons:
                print(f"     - {reason}")
                logger.error("[CRITICAL_DATA_ERROR] %s", reason)
        if issues:
            for iss in issues:
                print(f"  ⚠️  {iss}")
                logger.warning("[VALIDATION_WARNING] %s", iss)
        logger.info("데이터 품질 — 신뢰도:%.0f점 (%s)  이슈:%d건  치명적오류:%s",
                    conf, status, len(issues), critical_error)

        # 치명적 오류 시 공포탐욕 지수 산출 보류 — 단일 지표 오류가 시장 심리
        # 판단 전체를 오염시키지 않도록 방어
        if critical_error:
            sentiment = macro_data.setdefault("sentiment", {})
            sentiment["fear_greed_index"] = {"value": None, "label": "판단보류"}
            sentiment["_fear_greed_suppressed"] = True
            sentiment["_fear_greed_suppressed_reason"] = "지수 데이터 이상 감지"

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

        # ── Step 4-1: data_quality 기반 등급 캡 적용 ──
        rating_dicts = apply_grade_cap(rating_dicts, conf, critical_data_error=critical_error)
        capped = [r for r in rating_dicts if r.get("grade_capped")]
        if capped:
            print(f"  ⚠️  등급 제한 적용: {len(capped)}개 종목")
            for r in capped:
                print(f"     {r['name']}: {r['raw_grade']} → {r['grade']}  ({r['cap_reason']})")
                logger.warning("[VALIDATION_WARNING] 등급 캡 적용 — %s: %s→%s (%s)",
                               r["name"], r["raw_grade"], r["grade"], r["cap_reason"])

        # 등급 캡 적용 후(rating_dicts) 기준 분포 — rating_results는 캡 적용 전 원본이라 사용하지 않음
        dist = Counter(r["grade"] for r in rating_dicts)
        dist_str = " | ".join(f"{g}: {n}개" for g, n in dist.items() if n > 0)
        print(f"  등급 분포 (final): {dist_str}")
        print()
        for r in rating_dicts:
            print_rating(r)
        logger.info("등급 산정 완료 — %s", dist_str)

        # ── Step 4-2: 히스토리 저장 & 변화 감지 ──
        tracker = HistoryTracker()
        prev_quality = tracker.get_previous_quality(report_type)
        changes = tracker.get_changes(rating_dicts, report_type)
        changed = [c for c in changes if c["direction"] in ("상승", "하락")]
        if changed:
            print(f"\n  [{CHANGE_EMOJI['상승']} 등급 변화 감지: {len(changed)}개]")
            for c in changed:
                arrow = CHANGE_EMOJI[c["direction"]]
                print(f"    {arrow} {c['name']:16s}  {c['prev_grade']} → {c['curr_grade']}  ({c['score_delta']:+.0f}점)")
        tracker.save_today(
            rating_dicts, report_type,
            price_data=price_data,
            news_data=news_data,
            data_quality=data_quality,
        )
        logger.info("이력 저장 완료")

        # ── Step 4-2-1: 등급 적중률 집계 (N일 전 등급 vs 오늘 가격) ──
        # 시스템 운영 초기(누적 이력 부족)에는 자연히 "데이터 부족"으로 비어 있다가
        # 매일 실행이 쌓일수록 채워지는 구조 — 순수 forward-looking 리포트에 자기
        # 검증 통계를 더해 신뢰도를 스스로 증명하기 위한 기능.
        accuracy_report = tracker.compute_accuracy_report(price_data)
        for days, stats in accuracy_report.items():
            if stats["sample_count"] > 0:
                print(f"  등급 적중률({days}일 전 기준): {stats['overall_hit_rate']}% "
                      f"({stats['sample_count']}건, 기준일 {stats['reference_date']})")
            else:
                print(f"  등급 적중률({days}일 전 기준): 데이터 부족")
        logger.info("등급 적중률 집계 완료 — %s",
                     {d: s["overall_hit_rate"] for d, s in accuracy_report.items()})

        # ── Step 4-3: 텔레그램 알림 (치명적 오류 + 등급 변화 + 품질 급락) ──
        if notifier.is_configured():
            try:
                if critical_error:
                    notifier.notify_critical_data_error(critical_reasons, report_type)
                    print(f"  📱 텔레그램 치명적 데이터 오류 알림 발송")
                    logger.error("[CRITICAL_DATA_ERROR] 텔레그램 긴급 알림 발송")

                sent = notifier.notify_grade_changes(
                    changes, data_confidence=conf, report_type=report_type
                )
                if sent:
                    print(f"  📱 텔레그램 등급 변화 알림 발송")
                    logger.info("텔레그램 등급 변화 알림 발송")

                # 품질 급락 알림
                dropped = notifier.notify_quality_drop(conf, prev_quality, report_type)
                if dropped:
                    print(f"  📱 텔레그램 품질 급락 알림 발송")
                    logger.warning("[VALIDATION_WARNING] 텔레그램 품질 급락 알림 발송 — %.0f점", conf)
            except Exception as e:
                logger.error("[TELEGRAM_NOTIFY_ERROR] 텔레그램 알림 실패: %s", e)

        # ── Step 5: Claude API 리포트 생성 ──
        print_section("Step 5. 리포트 생성")
        builder = ReportBuilder()

        try:
            if report_type == "morning":
                print("  아침 브리핑 리포트 생성 중...")
                report_content = builder.build_morning_report(
                    price_data=price_data,
                    news_data=news_data,
                    macro_data=macro_data,
                    ratings=rating_dicts,
                    report_date=date_str,
                    grade_changes=changes,
                    data_quality=data_quality,
                    disclosure_data=disclosure_data,
                    accuracy_report=accuracy_report,
                    portfolio_summary=portfolio_summary,
                    stocks=stocks,
                    max_tokens=cfg.report.morning.get("max_tokens", 10000),
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
                    data_quality=data_quality,
                    disclosure_data=disclosure_data,
                    accuracy_report=accuracy_report,
                    portfolio_summary=portfolio_summary,
                    stocks=stocks,
                    max_tokens=cfg.report.evening.get("max_tokens", 10000),
                )
            print("  리포트 생성 완료")
            logger.info("리포트 생성 완료")
        except Exception as e:
            logger.error("[CLAUDE_API_ERROR] 리포트 생성 실패: %s", e)
            raise

        # ── Step 6: 리포트 저장 ──
        print_section("Step 6. 리포트 저장")
        save_dir = cfg.report_save_dir()
        try:
            saved_path = save_report(report_content, report_type, save_dir)
            print(f"  저장 완료: {saved_path}")
        except Exception as e:
            logger.error("[REPORT_SAVE_ERROR] 리포트 파일 저장 실패: %s", e)
            raise

        # 등급 JSON도 함께 저장 (대시보드 참조용)
        date_prefix = datetime.now().strftime("%Y%m%d")
        json_path = save_dir / f"{date_prefix}_{report_type}_ratings.json"
        # 뉴스 요약: 파일 크기 제한을 위해 종목당 최대 3건만 저장
        news_summary = {k: v[:3] for k, v in news_data.items() if v}
        json_path.write_text(
            json.dumps(
                {
                    "date": date_str,
                    "type": report_type,
                    "ratings": rating_dicts,
                    "macro": macro_data,
                    "data_quality": data_quality,
                    "price": price_data,
                    "news": news_summary,
                    "disclosures": disclosure_data,
                    "accuracy_report": accuracy_report,
                    "portfolio_summary": portfolio_summary,
                    "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M KST"),
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"  등급 JSON 저장: {json_path}")
        logger.info("파일 저장 완료 — %s", saved_path)

        # ── Step 7: 이메일 발송 (옵션) ──
        if send_email:
            print_section("Step 7. 이메일 발송")
            sender = EmailSender()
            if sender.is_configured():
                try:
                    # 주목 종목(추천/위험/판단보류·당일 등급 변화)만 캔들차트 첨부
                    # — 전종목 첨부 시 이미지 과다로 발송 지연/용량 문제 → Mock 모드는 생략
                    chart_images = generate_report_charts(rating_dicts, changes, price_data)
                    if chart_images:
                        print(f"  주목 종목 차트 생성: {len(chart_images)}개 종목")
                    sender.send_report(
                        report_type, report_content, date_str,
                        news_data=news_data, ratings=rating_dicts,
                        chart_images=chart_images,
                    )
                    logger.info("이메일 발송 완료")
                except Exception as e:
                    logger.error("[EMAIL_SEND_ERROR] 이메일 발송 실패: %s", e)
                    print(f"  ❌ 이메일 발송 실패: {e}")
            else:
                print("  ⚠️  이메일 설정 미완료 — .env에 SMTP 정보를 입력하세요.")

        # ── 완료 ──
        print(f"\n{BOLD}✅ Market Flow {report_type.upper()} 리포트 완료{RESET}")
        print(f"   데이터 신뢰도: {conf:.0f}점 ({status})")
        print(f"   리포트 파일: {saved_path}\n")
        logger.info("파이프라인 완료 ✅")

    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("파이프라인 실패 ❌\n%s", tb)
        print(f"\n{BOLD}❌ 오류 발생: {exc}{RESET}")
        if notifier.is_configured():
            notifier.notify_error(str(exc), report_type)
        raise


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
    _force_utf8_console()
    _setup_logging()
    run_pipeline(report_type=args.report, send_email=args.send_email)


if __name__ == "__main__":
    main()
