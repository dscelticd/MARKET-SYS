"""
Telegram Notifier — 중요한 등급 변화가 있을 때만 텔레그램으로 알림 발송

알림 기준:
  - 등급 상승: 보통→추천, 주의→보통 이상
  - 등급 하락: 추천→보통 이하, 보통→주의 이하
  - 위험 등급 진입
  - data_confidence 높은데 등급 급변 (신뢰도 ≥ 70 + 2단계 이상 변화)

━━━ 텔레그램 봇 설정 방법 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  [Step 1] 봇 생성
    1. 텔레그램 앱 → @BotFather 검색 → 대화 시작
    2. /newbot 입력 → 봇 이름 입력 (예: MyMarketFlowBot)
    3. 발급된 토큰 복사 (예: 123456789:AAFxxxxxx...)

  [Step 2] Chat ID 확인
    1. 생성한 봇과 대화 시작 (아무 메시지나 전송)
    2. 브라우저에서 아래 URL 접속:
       https://api.telegram.org/bot{YOUR_TOKEN}/getUpdates
    3. "chat":{"id": 숫자} 값이 CHAT_ID

  [Step 3] .env 파일에 추가
    TELEGRAM_BOT_TOKEN=123456789:AAFxxxxxx...
    TELEGRAM_CHAT_ID=987654321

  [Step 4] 테스트 (선택)
    python -c "
    from dotenv import load_dotenv; load_dotenv()
    from app.utils.telegram_notifier import TelegramNotifier
    n = TelegramNotifier()
    print('설정됨:', n.is_configured())
    n.send_message('Market Flow 텔레그램 연동 테스트')
    "

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

logger = logging.getLogger(__name__)

GRADE_ORDER = {"추천": 5, "안전": 4, "보통": 3, "주의": 2, "위험": 1, "판단보류": 0}

# 알림을 보낼 최소 등급 변화 단계
ALERT_THRESHOLD_STEPS = 1  # 1단계 이상 변화 시 알림


class TelegramNotifier:
    """
    사용법:
        notifier = TelegramNotifier()
        if notifier.is_configured():
            notifier.notify_grade_changes(changes, data_confidence=80.0)
    """

    def __init__(self) -> None:
        self.token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    def is_configured(self) -> bool:
        return bool(self.token and self.chat_id)

    # ── 등급 변화 알림 ───────────────────────────────────────────────────────

    def notify_grade_changes(
        self,
        changes: list[dict],
        data_confidence: float = 50.0,
        report_type: str = "morning",
    ) -> int:
        """
        중요한 등급 변화만 필터링해 알림 발송.
        반환값: 발송된 알림 수
        """
        if not self.is_configured():
            logger.info("텔레그램 미설정 — 알림 스킵")
            return 0

        alerts = self._filter_significant(changes, data_confidence)
        if not alerts:
            return 0

        message = self._build_message(alerts, data_confidence, report_type)
        success = self._send(message)
        return 1 if success else 0

    def notify_quality_drop(
        self,
        current_quality: float,
        prev_quality: float | None,
        report_type: str = "morning",
    ) -> bool:
        """
        data_quality 급락 또는 낮은 신뢰도 경고를 발송합니다.

        발송 조건:
          - prev_quality 대비 20점 이상 하락
          - current_quality < 50 (절대 기준 경고)

        반환값: 발송됐으면 True
        """
        if not self.is_configured():
            return False

        reasons: list[str] = []
        if prev_quality is not None and (prev_quality - current_quality) >= 20:
            reasons.append(
                f"직전 실행 대비 {prev_quality - current_quality:.0f}점 하락 "
                f"({prev_quality:.0f} → {current_quality:.0f}점)"
            )
        if current_quality < 50:
            reasons.append(f"신뢰도 {current_quality:.0f}점 — 50점 미만 경고 수준")

        if not reasons:
            return False

        now   = datetime.now().strftime("%Y-%m-%d %H:%M")
        label = "📅 아침" if report_type == "morning" else "🌙 저녁"
        lines = [
            f"⚠️ *Market Flow {label} 데이터 품질 경고*",
            f"일시: {now}",
            "",
        ]
        for reason in reasons:
            lines.append(f"• {_escape(reason)}")
        lines += [
            "",
            f"현재 신뢰도: *{current_quality:.0f}점*",
            "_등급이 자동 조정되었을 수 있습니다\\. 리포트를 확인하세요\\._",
        ]
        return self._send("\n".join(lines))

    def notify_critical_data_error(
        self, reasons: list[str], report_type: str = "morning"
    ) -> bool:
        """
        지수·ETF·대형주 데이터 간 모순(예: KOSPI 급락인데 대형주 급등) 등
        치명적 데이터 오류 감지 시 긴급 경고를 발송합니다.

        반환값: 발송됐으면 True
        """
        if not self.is_configured():
            return False
        now   = datetime.now().strftime("%Y-%m-%d %H:%M")
        label = "📅 아침" if report_type == "morning" else "🌙 저녁"
        lines = [
            f"🚨 *Market Flow {label} 치명적 데이터 오류 감지*",
            f"일시: {now}",
            "",
            "지수·ETF·대형주 데이터 간 모순이 감지되어 시장 판단을 보류했습니다\\.",
            "",
        ]
        for reason in reasons:
            lines.append(f"• {_escape(reason)}")
        lines += [
            "",
            "_모든 종목의 최종 등급이 \\*판단보류\\*로 처리되었습니다\\. "
            "외부 데이터 소스를 확인하세요\\._",
        ]
        return self._send("\n".join(lines))

    def notify_error(self, error_msg: str, report_type: str = "morning") -> None:
        """파이프라인 오류 알림"""
        if not self.is_configured():
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        msg = (
            f"🚨 *Market Flow 오류 알림*\n"
            f"일시: {now}\n"
            f"리포트: {report_type}\n"
            f"오류: `{_escape(error_msg[:200])}`"
        )
        self._send(msg)

    def notify_pipeline_complete(
        self, report_type: str, grades_summary: str
    ) -> None:
        """파이프라인 완료 알림 (선택적 사용 — 매번 보내면 피로감)"""
        if not self.is_configured():
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        type_label = "📅 아침 브리핑" if report_type == "morning" else "🌙 저녁 결산"
        msg = (
            f"✅ *{type_label} 완료* ({now})\n\n"
            f"{grades_summary}"
        )
        self._send(msg)

    # ── 내부 헬퍼 ────────────────────────────────────────────────────────────

    @staticmethod
    def _filter_significant(
        changes: list[dict], data_confidence: float
    ) -> list[dict]:
        """알림 가치 있는 변화만 추출"""
        result = []
        for c in changes:
            direction = c.get("direction", "유지")

            # 신규 등재 — 등급이 추천/주의/위험일 때만
            if direction == "신규":
                if c["curr_grade"] in ("추천", "주의", "위험"):
                    result.append(c)
                continue

            # 유지는 알림 불필요
            if direction == "유지":
                continue

            prev_ord = GRADE_ORDER.get(c.get("prev_grade", "보통"), 3)
            curr_ord = GRADE_ORDER.get(c["curr_grade"], 3)
            steps    = abs(curr_ord - prev_ord)

            # 위험 등급 진입은 무조건 알림
            if c["curr_grade"] == "위험":
                result.append(c)
                continue

            # 신뢰도 높을 때 1단계 이상 변화
            if data_confidence >= 70 and steps >= ALERT_THRESHOLD_STEPS:
                result.append(c)
                continue

            # 신뢰도 낮아도 2단계 이상 급변은 알림
            if steps >= 2:
                result.append(c)

        return result

    @staticmethod
    def _build_message(
        alerts: list[dict], data_confidence: float, report_type: str
    ) -> str:
        now   = datetime.now().strftime("%Y-%m-%d %H:%M")
        label = "📅 아침" if report_type == "morning" else "🌙 저녁"

        lines = [
            f"📊 *Market Flow {label} 등급 변화 알림*",
            f"일시: {now}  |  신뢰도: {data_confidence:.0f}점",
            "",
        ]

        for c in alerts:
            direction = c.get("direction", "")
            name      = _escape(c["name"])
            curr      = c["curr_grade"]

            if direction == "신규":
                lines.append(f"🆕 *{name}* 신규 등재: \\[{_escape(curr)}\\]")
            elif direction == "상승":
                prev = c.get("prev_grade", "?")
                delta = c.get("score_delta", 0)
                lines.append(
                    f"📈 *{name}* 등급 상승: {_escape(prev)} → *{_escape(curr)}*"
                    f"  \\({delta:+.0f}점\\)"
                )
            elif direction == "하락":
                prev = c.get("prev_grade", "?")
                delta = c.get("score_delta", 0)
                lines.append(
                    f"📉 *{name}* 등급 하락: {_escape(prev)} → *{_escape(curr)}*"
                    f"  \\({delta:+.0f}점\\)"
                )

        lines.append("")
        lines.append("_투자 판단 보조 등급입니다\\. 실제 투자 결정은 본인 책임입니다\\._")
        return "\n".join(lines)

    def _send(self, text: str) -> bool:
        """텔레그램 Bot API로 메시지 발송. 성공 시 True 반환."""
        url     = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = json.dumps({
            "chat_id":    self.chat_id,
            "text":       text,
            "parse_mode": "MarkdownV2",
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    logger.info("텔레그램 알림 발송 성공")
                    return True
                logger.warning("텔레그램 응답 이상: %s", resp.status)
                return False
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            logger.error("텔레그램 HTTP 오류 %s: %s", e.code, body[:200])
            return False
        except Exception as exc:
            logger.error("텔레그램 발송 실패: %s", exc)
            return False


def _escape(text: str) -> str:
    """MarkdownV2 특수문자 이스케이프"""
    special = r"\_*[]()~`>#+-=|{}.!"
    for ch in special:
        text = text.replace(ch, f"\\{ch}")
    return text
