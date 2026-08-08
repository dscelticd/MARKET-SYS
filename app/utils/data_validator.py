"""
Data Validator — 파이프라인 실행 전 수집 데이터 품질을 검증하고
리포트에 첨부할 [데이터 상태] 섹션을 생성합니다.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any


# ── KOSPI 지수 Sanity Check / 교차검증 기준 ──────────────────────────────────
# 외부 API(yfinance ^KS11 등) 티커 매핑 오류·데이터 오염으로 발생하는
# "지수 급락인데 대형주·ETF는 급등" 같은 모순을 잡아내기 위한 임계값.
#
# _KOSPI_MIN/MAX는 "절대 음수/0/완전히 다른 지수값" 같은 명백한 garbage만
# 걸러내는 최후 방어선일 뿐, 진짜 핵심 방어는 아래 KODEX200·대형주 교차검증
# (③④, 변동률 기반 — 지수 레벨과 무관하게 항상 유효)이다. 지수 레벨 자체는
# 시장 성장에 따라 계속 올라가므로 절대 범위를 1차 판단 기준으로 쓰지 말 것.
# (2026-06 기준 KOSPI 실거래가 8,200~9,100pt까지 상승 — 과거 1500~5000 범위가
#  실제 정상 시세를 오탐 처리한 사례 발생 → 범위를 넓게 재조정함)
_KOSPI_MIN = 1000.0
_KOSPI_MAX = 20000.0
_KOSPI_CHANGE_WARN_PCT       = 5.0   # 단독 발생 시에는 경고만 (실제 급락일 가능성 배제 못함)
_KOSPI_KODEX200_MISMATCH_PCT = 3.0   # KOSPI vs KODEX200 변동률 차이 허용 한계
_KOSPI_CRASH_PCT             = 5.0   # "급락" 판정 기준
_LARGECAP_SURGE_PCT          = 3.0   # 삼성전자·SK하이닉스 "급등" 판정 기준
_KODEX200_SURGE_PCT          = 2.0   # KODEX200 "상승" 판정 기준 (지수 급락과 모순)
_KOSPI_CONFLICT_CRASH_PCT    = 3.0   # 방향성 충돌 판정 — KOSPI 급락 기준
_KODEX200_CONFLICT_PCT       = 1.0   # 방향성 충돌 판정 — KODEX200 상승 기준


def _as_float(val: Any) -> float | None:
    """'N/A' 등 비정상 값을 None으로 안전 변환. NaN/Inf도 None 처리.
    (NaN은 모든 비교 연산에서 조용히 False를 반환하므로 필터링하지 않으면
    교차검증을 무력화한 채로 통과시킴 — signal_scorer.py의 _sf()와 동일 원칙)
    """
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


# ── 검증 로직 ─────────────────────────────────────────────────────────────────

class DataValidator:
    """
    사용법:
        validator = DataValidator()
        quality = validator.validate(price_data, news_data, macro_data, stocks)
        section = validator.format_report_section(quality)

    quality 구조:
        {
          "price":  { total, real, mock, missing, status },
          "news":   { total, unique, duplicates_removed, avg_per_stock, status },
          "macro":  { is_mock, basis, status },
          "disclosures": { connected },
          "overall": { confidence, status, issues, generated_at },
        }
    """

    def validate(
        self,
        price_data: dict[str, dict],
        news_data: dict[str, list],
        macro_data: dict,
        stocks: list[dict],
    ) -> dict[str, Any]:
        price_q  = self._check_price(price_data, stocks)
        news_q   = self._check_news(news_data)
        macro_q  = self._check_macro(macro_data)
        disc_q   = {"connected": False}  # 공시 연동은 추후 구현

        critical_data_error, critical_reasons, warning_reasons = (
            self._validate_kospi_consistency(price_data, macro_data)
        )

        confidence, status, issues = self._overall(price_q, news_q, macro_q)
        issues = issues + warning_reasons + critical_reasons

        if critical_data_error:
            # 치명적 지수 오류 — 신뢰도를 강하게 하향하고 시장 판단을 보류 (등급 캡은
            # rating_analyzer.apply_grade_cap(critical_data_error=True)에서 전종목 강제 적용)
            confidence = min(confidence, 15.0)
            status = "낮음"

        return {
            "price":       price_q,
            "news":        news_q,
            "macro":       macro_q,
            "disclosures": disc_q,
            "overall": {
                "confidence":             round(confidence, 1),
                "status":                 status,
                "issues":                 issues,
                "critical_data_error":    critical_data_error,
                "critical_error_reasons": critical_reasons,
                "generated_at":           datetime.now().isoformat(),
            },
        }

    # ── KOSPI / KODEX200 / 대형주 교차 검증 ──────────────────────────────────

    @staticmethod
    def _validate_kospi_consistency(
        price_data: dict[str, dict], macro_data: dict,
    ) -> tuple[bool, list[str], list[str]]:
        """
        KOSPI 지수 자체 정합성(Sanity Check) + KODEX200·삼성전자·SK하이닉스
        흐름과의 교차 검증으로 외부 API 데이터 오염(티커 매핑 오류 등)을 감지합니다.

        단독 범위 이탈은 항상 치명적(critical)으로 처리하지만, 단독 변동률 급변은
        실제 시장 급락일 가능성을 배제할 수 없어 경고(warning)로만 두고 — KODEX200·
        대형주와 방향성이 모순될 때만 critical로 승격합니다.

        반환: (critical_data_error, critical_reasons, warning_reasons)
        """
        critical: list[str] = []
        warning:  list[str] = []

        kr_market = (macro_data or {}).get("kr_market", {}) or {}
        kospi     = kr_market.get("KOSPI", {}) if isinstance(kr_market, dict) else {}
        kospi_price = _as_float(kospi.get("value")) if isinstance(kospi, dict) else None
        kospi_chg   = _as_float(kospi.get("change_pct")) if isinstance(kospi, dict) else None

        pd_ = price_data or {}
        kodex_chg   = _as_float(pd_.get("KR_069500", {}).get("change_pct"))
        samsung_chg = _as_float(pd_.get("KR_005930", {}).get("change_pct"))
        skhynix_chg = _as_float(pd_.get("KR_000660", {}).get("change_pct"))

        # Mock 데이터는 가격·거시지표가 각각 독립적으로 무작위 생성되어 상호
        # 연관성이 보장되지 않으므로, 교차검증(③④)은 실데이터일 때만 수행한다.
        # (단독 범위 체크 ①은 mock/real 무관하게 항상 적용 — 방어 심도 유지)
        is_cross_check_mock = bool((macro_data or {}).get("_mock", False)) or any(
            pd_.get(sid, {}).get("_mock", False)
            for sid in ("KR_069500", "KR_005930", "KR_000660")
        )

        # ① 단독 Sanity Check — 정상적인 지수값일 수 없는 범위 이탈은 항상 치명적
        if kospi_price is not None and not (_KOSPI_MIN <= kospi_price <= _KOSPI_MAX):
            critical.append(
                f"KOSPI 지수가 정상 범위({_KOSPI_MIN:.0f}~{_KOSPI_MAX:.0f}pt)를 벗어남: {kospi_price:.0f}pt"
            )

        # ② 단독 변동률 이상 — 경고만 (교차검증에서 모순 확인되면 critical로 승격)
        if kospi_chg is not None and abs(kospi_chg) >= _KOSPI_CHANGE_WARN_PCT:
            warning.append(f"KOSPI 일일 변동률이 비정상적으로 큼: {kospi_chg:+.2f}%")

        if not is_cross_check_mock:
            # ③ KOSPI ↔ KODEX200 교차 검증
            if kospi_chg is not None and kodex_chg is not None:
                if abs(kospi_chg - kodex_chg) > _KOSPI_KODEX200_MISMATCH_PCT:
                    critical.append(
                        f"KOSPI({kospi_chg:+.2f}%)와 KODEX200({kodex_chg:+.2f}%) 변동률 불일치"
                    )
                if kospi_chg < -_KOSPI_CONFLICT_CRASH_PCT and kodex_chg > _KODEX200_CONFLICT_PCT:
                    critical.append(
                        f"KOSPI 급락({kospi_chg:+.2f}%) 중 KODEX200 상승({kodex_chg:+.2f}%) — 방향성 충돌"
                    )

            # ④ KOSPI 급락 vs 대형주·ETF 급등 모순
            if kospi_chg is not None and kospi_chg < -_KOSPI_CRASH_PCT:
                if samsung_chg is not None and samsung_chg > _LARGECAP_SURGE_PCT:
                    critical.append(
                        f"KOSPI 급락({kospi_chg:+.2f}%) 중 삼성전자 급등({samsung_chg:+.2f}%) — 지수 데이터 이상 감지"
                    )
                if skhynix_chg is not None and skhynix_chg > _LARGECAP_SURGE_PCT:
                    critical.append(
                        f"KOSPI 급락({kospi_chg:+.2f}%) 중 SK하이닉스 급등({skhynix_chg:+.2f}%) — 지수 데이터 이상 감지"
                    )
                if kodex_chg is not None and kodex_chg > _KODEX200_SURGE_PCT:
                    critical.append(
                        f"KOSPI 급락({kospi_chg:+.2f}%) 중 KODEX200 상승({kodex_chg:+.2f}%) — 지수 데이터 이상 감지"
                    )

        critical = list(dict.fromkeys(critical))  # 중복 제거, 순서 보존
        return bool(critical), critical, warning

    # ── 항목별 검증 ──────────────────────────────────────────────────────────

    @staticmethod
    def _check_price(price_data: dict[str, dict], stocks: list[dict]) -> dict:
        stock_ids = [s["id"] for s in stocks]
        total     = len(stock_ids)
        real      = sum(1 for sid in stock_ids
                        if not price_data.get(sid, {}).get("_mock", True))
        mock      = sum(1 for sid in stock_ids
                        if price_data.get(sid, {}).get("_mock", True))
        missing   = [sid for sid in stock_ids if sid not in price_data]

        if missing:
            st = "오류"
        elif mock > 0 and real > 0:
            st = "부분"
        elif real == total:
            st = "정상"
        else:
            st = "Mock"

        return {
            "total":   total,
            "real":    real,
            "mock":    mock,
            "missing": missing,
            "status":  st,
        }

    @staticmethod
    def _check_news(news_data: dict[str, list]) -> dict:
        all_headlines: list[str] = []
        for items in news_data.values():
            for item in items:
                all_headlines.append(item.get("headline", ""))

        total    = len(all_headlines)
        unique   = len(set(all_headlines))
        dup_removed = total - unique
        per_stock = round(total / len(news_data), 1) if news_data else 0

        if total == 0:
            st = "오류"
        elif dup_removed > total * 0.3:
            st = "부분"
        else:
            st = "정상"

        return {
            "total":              total,
            "unique":             unique,
            "duplicates_removed": dup_removed,
            "avg_per_stock":      per_stock,
            "status":             st,
        }

    @staticmethod
    def _check_macro(macro_data: dict) -> dict:
        is_mock = bool(macro_data.get("_mock", False))
        basis   = "Mock 데이터 기준" if is_mock else "yfinance 실제 데이터"

        # 핵심 키 존재 여부
        key_fields = ["us_market", "kr_market", "currencies", "rates", "sentiment"]
        present    = sum(1 for k in key_fields if k in macro_data)
        st = "정상" if present >= 4 else ("부분" if present >= 2 else "오류")

        return {
            "is_mock":        is_mock,
            "basis":          basis,
            "fields_present": present,
            "fields_total":   len(key_fields),
            "status":         st,
        }

    # ── 종합 신뢰도 계산 ─────────────────────────────────────────────────────

    @staticmethod
    def _overall(
        price_q: dict, news_q: dict, macro_q: dict
    ) -> tuple[float, str, list[str]]:
        issues: list[str] = []

        # 가격 점수 (가중치 50%)
        total = price_q["total"] or 1
        price_score = (price_q["real"] / total) * 100
        if price_q["mock"] > 0:
            # Mock 데이터가 섞여 있으면 60점을 상한으로 제한 — 실데이터 대비 불확실성 반영
            # (버그 수정: 이전에는 max()를 사용해 mock 비중이 높을수록 오히려 점수가
            #  올라가는 역전이 있었음 — 예: 전종목 mock이어도 60점으로 산정되어
            #  종합 신뢰도가 "높음"으로 잘못 표시됨)
            price_score = min(price_score, 60.0)
        if price_q["missing"]:
            issues.append(f"가격 데이터 누락 종목: {', '.join(price_q['missing'])}")

        # 뉴스 점수 (가중치 30%)
        news_score = 100.0
        if news_q["total"] == 0:
            news_score = 20.0
            issues.append("뉴스 데이터 없음")
        elif news_q["duplicates_removed"] > news_q["total"] * 0.3:
            news_score = 70.0
            issues.append(f"뉴스 중복 {news_q['duplicates_removed']}건 제거됨")

        # 거시 점수 (가중치 20%)
        macro_score = 100.0 if not macro_q["is_mock"] else 65.0
        if macro_q["is_mock"]:
            issues.append("거시 지표 Mock 데이터 사용 중")

        confidence = price_score * 0.50 + news_score * 0.30 + macro_score * 0.20

        if confidence >= 85:
            status = "높음"
        elif confidence >= 65:
            status = "보통"
        else:
            status = "낮음"

        return confidence, status, issues

    # ── 리포트 상단 품질 점검 배너 ────────────────────────────────────────────

    @staticmethod
    def format_quality_banner(quality: dict) -> str:
        """리포트 최상단(시장 요약 직전)에 표시할 간단한 [데이터 품질 점검] 배너.
        상세 테이블은 format_report_section()이 리포트 하단에 별도로 붙입니다.
        """
        o = quality.get("overall", {})
        critical = bool(o.get("critical_data_error", False))

        idx_status   = "❌ 이상 감지" if critical else "✅ 정상"
        etf_status   = "❌ 불일치"   if critical else "✅ 정상"
        cap_status   = "❌ 불일치"   if critical else "✅ 정상"
        fg_status    = "⏸️ 산출 보류" if critical else "✅ 산출 가능"
        final_status = "🚫 데이터 검증 필요" if critical else "✅ 정상 산출"

        lines = [
            "## 📋 데이터 품질 점검",
            "",
            f"- 주요 지수 데이터: {idx_status}",
            f"- ETF 연동성: {etf_status}",
            f"- 대형주 방향성: {cap_status}",
            f"- 시장 심리 지표: {fg_status}",
            f"- 최종 판단: {final_status}",
            "",
        ]
        if critical:
            lines.append(
                "> 🚨 **지수·ETF·대형주 데이터 간 모순이 감지되어 "
                "이번 리포트의 시장 판단은 보류됩니다.**"
            )
            for reason in o.get("critical_error_reasons", []):
                lines.append(f">   - {reason}")
            lines.append("")
        lines += ["---", ""]
        return "\n".join(lines)

    # ── 리포트 섹션 생성 ─────────────────────────────────────────────────────

    @staticmethod
    def format_report_section(quality: dict) -> str:
        """[데이터 상태] 마크다운 섹션 생성"""
        p = quality["price"]
        n = quality["news"]
        m = quality["macro"]
        d = quality["disclosures"]
        o = quality["overall"]

        def _icon(status: str) -> str:
            return {"정상": "✅", "부분": "⚠️", "Mock": "🔶", "오류": "❌"}.get(status, "❓")

        price_detail = (
            f"실제 {p['real']}개 / Mock {p['mock']}개"
            + (f" / 누락 {len(p['missing'])}개" if p["missing"] else "")
        )
        news_detail = (
            f"총 {n['total']}건"
            + (f" (중복 {n['duplicates_removed']}건 제거)" if n["duplicates_removed"] > 0 else "")
        )
        macro_detail = m["basis"]
        disc_detail  = "추후 지원 예정" if not d["connected"] else "✅ 연동됨"
        disc_icon    = "🔶" if not d["connected"] else "✅"

        conf = o["confidence"]
        conf_bar = _confidence_bar(conf)
        status_label = o["status"]

        lines = [
            "",
            "---",
            "## 📊 데이터 상태",
            "",
            "| 항목 | 상태 | 세부 정보 |",
            "|------|:----:|----------|",
            f"| 가격 데이터 | {_icon(p['status'])} {p['status']} | {price_detail} |",
            f"| 뉴스 데이터 | {_icon(n['status'])} {n['status']} | {news_detail} |",
            f"| 거시 지표   | {_icon(m['status'])} {m['status']} | {macro_detail} |",
            f"| 공시 연동   | {disc_icon} 미연동 | {disc_detail} |",
            "",
            f"**전체 데이터 신뢰도: {conf:.0f}점/100점** {conf_bar} ({status_label})",
            "",
        ]

        if o["issues"]:
            lines.append("**⚠️ 주의 사항:**")
            for issue in o["issues"]:
                lines.append(f"- {issue}")
            lines.append("")

        if status_label == "낮음":
            lines.append("> ⚠️ 데이터 신뢰도가 낮습니다. 등급 변화를 신중하게 해석하세요.")
        elif status_label == "보통":
            lines.append("> 💡 일부 지표가 Mock 데이터입니다. 실제 데이터 전환 시 등급이 달라질 수 있습니다.")

        lines.append("")
        return "\n".join(lines)


def _confidence_bar(score: float, width: int = 10) -> str:
    """간단한 텍스트 진행 막대"""
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)
