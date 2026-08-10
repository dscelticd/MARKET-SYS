"""
Report Builder — Claude API를 사용해 아침/저녁 리포트를 생성
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import logging
import time

import anthropic

_logger = logging.getLogger(__name__)

from app.utils.data_validator import DataValidator

SYSTEM_PROMPT = """당신은 Market Flow Intelligence System의 시장 분석 전문가입니다.
수집된 시장 데이터와 신호 점수를 바탕으로 개인 투자자를 위한 시장 브리핑 리포트를 작성합니다.

## 핵심 원칙
- 모든 등급은 "투자 판단 보조 등급"으로 표현합니다.
- 실제 투자 결정은 투자자 본인의 판단과 책임임을 명확히 합니다.
- 데이터 기반의 객관적 분석을 제공합니다.
- 글로벌 시장판 → 국가 흐름 → 섹터/테마 → 밸류체인 → 종목 영향 순서로 흐름을 분석합니다.

## 절대 금지 표현
다음 표현은 절대 사용하지 마세요:
무조건 매수, 반드시 매도, 확실한 수익, 손실 없음, 보장, 지금 사야 한다, 지금 팔아야 한다

## 등급 기준
- 추천: 현재 데이터 기준 우선 검토 가치가 높은 상태
- 안전: 리스크 낮고 변동성 제한적인 상태
- 보통: 긍정/부정 신호 혼재, 방향성 불명확
- 주의: 단기 리스크, 부정 뉴스, 수급 약화 확인
- 위험: 중대 리스크, 강한 하락 신호, 데이터 불확실성 큼
- 판단보류: 지수·ETF·대형주 데이터 간 모순이 감지되어 시장 판단 자체를 보류한 상태 (해당 종목이 위험하다는 뜻이 아니라, 데이터 신뢰도 문제로 판단을 유보한 것임을 명확히 설명하세요)

## 출력 형식
마크다운 형식으로 작성하며, 이모지를 적절히 활용해 가독성을 높입니다.
"""


def _format_price_block(price_data: dict[str, dict]) -> str:
    lines = []
    for sid, p in price_data.items():
        chg_sign = "+" if p["change_pct"] >= 0 else ""
        line = (
            f"- {p['name']} ({p['ticker']}): "
            f"{p['price']:,.2f} {p['currency']} "
            f"({chg_sign}{p['change_pct']:.2f}%) "
            f"| 거래량비율 {p['volume_ratio']:.1f}x"
        )
        tech = p.get("technical") or {}
        if tech:
            trend = tech.get("trend_signal", "")
            rsi   = tech.get("rsi_14")
            if trend:
                line += f" | 추세:{trend}"
            if rsi is not None:
                rsi_note = " ▼과매수" if rsi > 70 else " ▲과매도" if rsi < 30 else ""
                line += f" RSI:{rsi:.0f}{rsi_note}"
        analyst = p.get("analyst") or {}
        if analyst and analyst.get("target_mean"):
            upside = analyst.get("upside_pct")
            rec    = analyst.get("recommendation", "")
            if upside is not None:
                line += f" | 목표가상승여력:{upside:+.1f}%({rec})"
        lines.append(line)
    return "\n".join(lines)


def _format_technical_block(price_data: dict[str, dict]) -> str:
    """기술적 지표 + 애널리스트 컨센서스 상세 블록 (Claude 전망 분석용)"""
    lines = []
    for sid, p in price_data.items():
        tech    = p.get("technical") or {}
        analyst = p.get("analyst") or {}
        if not tech and not analyst:
            continue

        parts = [f"{p['name']}"]

        if tech:
            rsi   = tech.get("rsi_14")
            ma5   = tech.get("ma5")
            ma20  = tech.get("ma20")
            ma60  = tech.get("ma60")
            hist  = tech.get("macd_histogram")
            trend = tech.get("trend_signal", "")
            if rsi is not None:
                rsi_label = " (과매도)" if rsi < 30 else " (과매수)" if rsi > 70 else ""
                parts.append(f"RSI={rsi:.0f}{rsi_label}")
            if trend:
                parts.append(f"추세={trend}")
            if ma5 and ma20:
                cross = "골든크로스" if ma5 > ma20 else "데드크로스"
                parts.append(f"5/20MA={cross}")
            if p.get("price") and ma60:
                pos = "MA60 위" if p["price"] > ma60 else "MA60 아래"
                parts.append(pos)
            if hist is not None:
                parts.append(f"MACD히스토그램={'↑' if hist > 0 else '↓'}({hist:+.2f})")

        candle = p.get("candle_pattern") or {}
        if candle.get("pattern"):
            parts.append(f"당일캔들={candle['pattern']}({candle.get('direction', '')})")

        if analyst and analyst.get("target_mean"):
            upside = analyst.get("upside_pct")
            rec    = analyst.get("recommendation", "")
            num    = analyst.get("num_analysts", 0)
            t_mean = analyst.get("target_mean")
            currency = p.get("currency", "USD")
            if upside is not None:
                parts.append(
                    f"목표가={t_mean:,.2f}{currency}(상승여력{upside:+.1f}%·{rec}·{num}명)"
                )

        if len(parts) > 1:
            lines.append("  " + " | ".join(parts))

    if not lines:
        return "(기술적 지표 데이터 없음)"
    return "\n".join(lines)


def _format_support_resistance_block(price_data: dict[str, dict]) -> str:
    """지지/저항 박스 + 손익비 + 조건부 시나리오 블록 (Claude 리포트 작성용)
    예측이 아닌 "이 조건이 뜨면 검토 가능한 자리" 형태의 조건부 참고 정보 — 매수·매도 지시가 아님.
    """
    lines = []
    for sid, p in price_data.items():
        sr = p.get("support_resistance") or {}
        if not sr:
            continue

        price    = p.get("price")
        currency = p.get("currency", "")
        r_zones  = sr.get("resistance_zones") or []
        s_zones  = sr.get("support_zones") or []
        up_pct   = sr.get("nearest_resistance_pct")
        down_pct = sr.get("nearest_support_pct")
        rr       = sr.get("risk_reward_ratio")
        rr_ok    = sr.get("risk_reward_meets_bar")

        parts = [f"{p['name']}"]

        if r_zones:
            rz = r_zones[0]
            parts.append(
                f"저항 {rz['low']:,.2f}~{rz['high']:,.2f} {currency}(상승여력{up_pct:+.1f}%·강도{rz['strength']})"
            )
        else:
            parts.append("저항 확인 안 됨(신고가 구간)")

        if s_zones:
            sz = s_zones[0]
            parts.append(
                f"지지 {sz['low']:,.2f}~{sz['high']:,.2f} {currency}(하락위험-{down_pct:.1f}%·강도{sz['strength']})"
            )
        else:
            parts.append("지지 확인 안 됨")

        if rr is not None:
            rr_label = "기준충족" if rr_ok else "기준미달"
            parts.append(f"손익비={rr:.2f}({rr_label})")

        lines.append("  " + " | ".join(parts))

        # 조건부 시나리오 — 돌파/이탈 조건 (예측 아님)
        if r_zones and price:
            lines.append(f"    ▲ {r_zones[0]['low']:,.2f} {currency} 위 거래량 동반 종가 마감 → 돌파로 볼 여지")
        if s_zones and price:
            lines.append(f"    ▼ {s_zones[0]['high']:,.2f} {currency} 아래 거래량 동반 종가 이탈 → 지지 붕괴로 볼 여지")

    if not lines:
        return "(지지/저항 데이터 없음)"
    return "\n".join(lines)


def _format_disclosure_block(disclosure_data: dict[str, list[dict]] | None) -> str:
    """최근 7일 DART 공시 블록 (KR 종목만 — DART_API_KEY 미설정 시 항상 비어 있음)"""
    if not disclosure_data:
        return "(공시 데이터 없음 — DART 연동 미설정 또는 최근 7일 공시 없음)"

    lines = []
    for sid, items in disclosure_data.items():
        if not items:
            continue
        for item in items[:3]:  # 종목당 최대 3건만 노출
            corp = item.get("corp_name", sid)
            title = item.get("title", "")
            date = item.get("rcept_dt", "")
            lines.append(f"  {corp}: {title} ({date})")

    if not lines:
        return "(공시 데이터 없음 — DART 연동 미설정 또는 최근 7일 공시 없음)"
    return "\n".join(lines)


def _format_accuracy_block(accuracy_report: dict[int, dict] | None) -> str:
    """등급 적중률 자기검증 블록 — N일 전 등급(추천/안전 ↔ 주의/위험)이 오늘 가격
    기준으로 방향성이 맞았는지 집계한 결과. "보통"·"판단보류"는 집계 대상 아님.
    """
    if not accuracy_report:
        return "(등급 적중률 데이터 없음)"

    lines = []
    for days in sorted(accuracy_report.keys()):
        stats = accuracy_report[days]
        if not stats or stats.get("sample_count", 0) == 0:
            lines.append(f"  {days}일 전 기준: 누적 이력 부족(데이터 쌓이는 중)")
            continue
        parts = [f"{days}일 전 기준 전체 적중률 {stats['overall_hit_rate']}%",
                 f"샘플 {stats['sample_count']}건(기준일 {stats['reference_date']})"]
        grade_parts = [
            f"{grade} {g['hit']}/{g['count']}건({g['hit_rate']}%, 평균수익률{g['avg_return_pct']:+.1f}%)"
            for grade, g in stats.get("grade_stats", {}).items()
        ]
        lines.append(f"  {' | '.join(parts)}")
        if grade_parts:
            lines.append(f"    {' | '.join(grade_parts)}")

    return "\n".join(lines)


def _format_investor_flow_block(price_data: dict[str, dict]) -> str:
    """외국인/기관/개인(추정) 순매매 수급 블록 (KR 종목만 — 참고용, 매매 신호 아님)"""
    lines = []
    for sid, p in price_data.items():
        flow = p.get("investor_flow") or {}
        if not flow:
            continue

        parts = [f"{p['name']}"]
        for days in (5, 20):
            frgn = flow.get(f"foreign_net_{days}d")
            inst = flow.get(f"institution_net_{days}d")
            indiv = flow.get(f"individual_net_{days}d_est")
            if frgn is None or inst is None:
                continue
            parts.append(
                f"{days}일누적 외국인{frgn:+,}주·기관{inst:+,}주·개인(추정){indiv:+,}주"
            )
        lines.append("  " + " | ".join(parts))

    if not lines:
        return "(수급 데이터 없음 — 해외 종목은 외국인/기관/개인 구분 데이터가 제공되지 않음)"
    return "\n".join(lines)


def _format_rating_block(ratings: list[dict]) -> str:
    lines = []
    for r in ratings:
        pos = r["positive_factors"][:2] if r["positive_factors"] else ["없음"]
        neg = r["negative_factors"][:2] if r["negative_factors"] else ["없음"]
        # 등급 캡이 적용된 경우 원본 등급도 함께 표시 (Claude 분석 품질 유지)
        grade_display = r["grade"]
        if r.get("grade_capped"):
            grade_display += f"(원본:{r['raw_grade']}·신뢰도제한)"
        lines.append(
            f"- {r['emoji']} {r['name']} [{grade_display}] "
            f"점수:{r['total_score']:.0f} 리스크:{r['risk_score']:.0f} "
            f"신뢰도:{r['data_confidence']:.0f}\n"
            f"  긍정: {' / '.join(pos)}\n"
            f"  부정: {' / '.join(neg)}"
        )
    return "\n".join(lines)


def _format_changes_block(changes: list[dict]) -> str:
    """전일 대비 등급 변화 목록을 Claude 프롬프트용 텍스트로 변환"""
    if not changes:
        return "전일 대비 등급 변화 없음"

    lines = []
    for c in changes:
        if c["direction"] == "신규":
            lines.append(f"- 🆕 {c['name']}: 신규 등재 [{c['curr_grade']}]")
        elif c["direction"] == "상승":
            lines.append(
                f"- 📈 {c['name']}: {c['prev_grade']} → {c['curr_grade']}"
                f"  ({c['score_delta']:+.0f}점, {c['prev_date']} 기준)"
            )
        elif c["direction"] == "하락":
            lines.append(
                f"- 📉 {c['name']}: {c['prev_grade']} → {c['curr_grade']}"
                f"  ({c['score_delta']:+.0f}점, {c['prev_date']} 기준)"
            )
        else:
            lines.append(
                f"- ➡️  {c['name']}: [{c['curr_grade']}] 유지  ({c['score_delta']:+.0f}점)"
            )
    return "\n".join(lines)


def _format_macro_block(macro: dict) -> str:
    us = macro.get("us_market", {})
    kr = macro.get("kr_market", {})
    cur = macro.get("currencies", {})
    rates = macro.get("rates", {})
    sent = macro.get("sentiment", {})
    comm = macro.get("commodities", {})

    return f"""[미국 시장]
- S&P500: {us.get('SP500', {}).get('value', 'N/A')} ({us.get('SP500', {}).get('change_pct', 0):+.2f}%)
- NASDAQ: {us.get('NASDAQ', {}).get('value', 'N/A')} ({us.get('NASDAQ', {}).get('change_pct', 0):+.2f}%)
- SOX: {us.get('SOX', {}).get('value', 'N/A')} ({us.get('SOX', {}).get('change_pct', 0):+.2f}%)
- VIX: {us.get('VIX', {}).get('value', 'N/A')} ({us.get('VIX', {}).get('signal', 'N/A')})

[한국 시장]
- KOSPI: {kr.get('KOSPI', {}).get('value', 'N/A')} ({kr.get('KOSPI', {}).get('change_pct', 0):+.2f}%)
- KOSDAQ: {kr.get('KOSDAQ', {}).get('value', 'N/A')} ({kr.get('KOSDAQ', {}).get('change_pct', 0):+.2f}%)
- 외국인 순매수: {kr.get('foreign_net_buy_bn', 'N/A'):+,.0f}억 원

[환율/금리]
- USD/KRW: {cur.get('USD_KRW', {}).get('value', 'N/A')}
- DXY: {cur.get('DXY', {}).get('value', 'N/A')} ({cur.get('DXY', {}).get('signal', '')})
- 미국 10년물 금리: {rates.get('us_10y_yield', {}).get('value', 'N/A')}%
- Fed 기준금리: {rates.get('fed_funds_rate', {}).get('value', 'N/A')}%

[원자재/지표]
- 구리: ${comm.get('copper', {}).get('value', 'N/A')}
- DRAM 현물: ${comm.get('dram_spot', {}).get('value', 'N/A')}
- 원유(WTI): ${comm.get('WTI_oil', {}).get('value', 'N/A')}

[시장 심리]
- 공포탐욕 지수: {sent.get('fear_greed_index', {}).get('value') if sent.get('fear_greed_index', {}).get('value') is not None else 'N/A'} ({sent.get('fear_greed_index', {}).get('label', '')})
- 글로벌 리스크 성향: {sent.get('global_risk_appetite', 'N/A')}
- AI CapEx 사이클: {sent.get('ai_capex_cycle', 'N/A')}
- 반도체 사이클: {sent.get('semiconductor_cycle', 'N/A')}"""


class ReportBuilder:
    def __init__(self) -> None:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None
        self.model = model

    def build_morning_report(
        self,
        price_data: dict[str, dict],
        news_data: dict[str, list],
        macro_data: dict,
        ratings: list[dict],
        report_date: str | None = None,
        grade_changes: list[dict] | None = None,
        data_quality: dict | None = None,
        disclosure_data: dict[str, list] | None = None,
        accuracy_report: dict[int, dict] | None = None,
    ) -> str:
        date_str = report_date or datetime.now().strftime("%Y-%m-%d")
        changes_block = _format_changes_block(grade_changes or [])

        technical_block = _format_technical_block(price_data)
        sr_block = _format_support_resistance_block(price_data)
        flow_block = _format_investor_flow_block(price_data)
        disclosure_block = _format_disclosure_block(disclosure_data)
        accuracy_block = _format_accuracy_block(accuracy_report)
        prompt = f"""오늘은 {date_str}입니다. 아래 데이터를 바탕으로 아침 브리핑 리포트를 작성하세요.

## 거시지표 스냅샷
{_format_macro_block(macro_data)}

## 관심종목 가격 현황 (추세·RSI·목표주가 포함)
{_format_price_block(price_data)}

## 기술적 지표 상세 (RSI·이동평균·MACD·애널리스트 컨센서스)
{technical_block}

## 지지/저항 박스 · 손익비 · 조건부 시나리오 (예측 아님 — 조건 충족 시 참고할 자리)
{sr_block}

## 수급 동향 (외국인/기관/개인 순매매, KR 종목만 — 참고용)
{flow_block}

## 최근 공시 (DART, 최근 7일, KR 종목만 — 참고용)
{disclosure_block}

## 등급 적중률 자기검증 (N일 전 등급 vs 오늘 가격 — 참고용)
{accuracy_block}

## 투자 판단 보조 등급 요약
{_format_rating_block(ratings)}

## 전일 대비 등급 변화
{changes_block}

---

위 데이터를 기반으로 아래 구조의 아침 브리핑을 작성해주세요:

1. **글로벌 시장 개요** — 전반적인 시장 분위기와 핵심 변수
2. **거시 신호 분석** — 금리/환율/원자재 흐름이 관심 섹터에 미치는 영향
3. **섹터/테마 흐름** — AI·반도체·HBM·데이터센터·전력인프라 각 테마 현황
4. **밸류체인 영향도** — 테마 흐름이 관심종목 밸류체인에 미치는 영향
5. **관심종목 투자 판단 보조 등급** — 각 종목별 등급과 핵심 판단 근거 (기술적 신호·애널리스트 컨센서스 반영)
6. **단기 전망 시나리오** — 기술적 지표와 매크로를 종합하여 향후 1주·1개월 전망을 작성하세요
   - 🟢 낙관 시나리오: 긍정 신호가 지속될 경우 기대 방향과 주목 종목
   - 🟡 기본 시나리오: 현재 모멘텀 유지 시 중립적 전망
   - 🔴 비관 시나리오: 하락 리스크 요인이 현실화될 경우 대비 포인트
   - 기술적 신호(RSI 과매도/과매수·추세·MACD)와 애널리스트 목표주가 상승여력이 높은 종목을 구체적으로 언급하세요
   - 지지/저항 박스와 손익비가 있는 종목은 "◯◯원 위로 거래량 동반 마감 시 돌파로 볼 여지" 같은 조건부 문장으로 자연스럽게 녹여서 언급하세요 (아래 작성 규칙 참고)
7. **오늘 주요 모니터링 포인트** — 확인이 필요한 이벤트·지표·공시
8. **투자 유의사항** — 면책 고지 포함

## 지지/저항·손익비 서술 규칙 (중요)
- 지지/저항·손익비 정보는 "매수/매도하라"가 아니라 "이 조건이 뜨면 ~검토할 수 있는 자리" 형태의 조건부 참고로만 서술하세요.
- 손익비가 낮은(2.0 미만) 종목은 "현재 가격대는 손익비 기준으로 신규 진입을 서두를 근거가 약한 구간" 정도로만 언급하고, 특정 행동을 지시하지 마세요.
- 저항/지지가 확인되지 않은 종목(신고가·신저가 구간)은 그 사실 자체를 있는 그대로 서술하세요.

## 수급 서술 규칙 (중요)
- 수급(외국인/기관/개인 순매매)은 매매 신호가 아닌 참고 정보로만 서술하세요.
- "개인" 수치는 KRX가 별도 집계하지 않아 외국인·기관 합산의 잔차로 추정한 값입니다 — 처음 언급할 때 "추정치"임을 명시하세요.
- 해외 종목(미국·대만)은 이 데이터가 존재하지 않으므로 언급하지 마세요.

## 공시 서술 규칙
- 공시는 사실 확인 정보로만 서술하세요 — 공시 발생 자체를 호재/악재로 단정하지 말고, 내용을 있는 그대로 전달하세요.
- 공시 데이터가 없으면("공시 데이터 없음") 이 항목은 언급하지 마세요.

## 등급 적중률 서술 규칙
- 적중률은 과거 등급이 사후적으로 방향성이 맞았는지에 대한 참고 통계이며, 미래 수익을 보장하지 않습니다 — 이 점을 명시하세요.
- "누적 이력 부족"으로 나온 항목은 아직 통계를 낼 만큼 데이터가 쌓이지 않았다는 뜻이므로, 그 자체를 부정적 신호로 해석하지 마세요.
- 샘플 수가 적으면(예: 5건 미만) 통계적 유의성이 낮다는 점을 함께 언급하세요.

리포트 끝에 반드시 다음 면책문구를 포함하세요:
"※ 본 리포트는 투자 권유가 아닌 시장 데이터 기반 판단 보조 참고 자료입니다. 실제 투자 결정은 개인의 판단과 책임 하에 이루어져야 합니다."
"""
        report = self._call_claude(prompt, max_tokens=10000)
        return self._append_data_quality(report, data_quality, ratings)

    def build_evening_report(
        self,
        price_data: dict[str, dict],
        news_data: dict[str, list],
        macro_data: dict,
        ratings: list[dict],
        report_date: str | None = None,
        grade_changes: list[dict] | None = None,
        data_quality: dict | None = None,
        disclosure_data: dict[str, list] | None = None,
        accuracy_report: dict[int, dict] | None = None,
    ) -> str:
        date_str = report_date or datetime.now().strftime("%Y-%m-%d")
        changes_block = _format_changes_block(grade_changes or [])

        technical_block = _format_technical_block(price_data)
        sr_block = _format_support_resistance_block(price_data)
        flow_block = _format_investor_flow_block(price_data)
        disclosure_block = _format_disclosure_block(disclosure_data)
        accuracy_block = _format_accuracy_block(accuracy_report)
        prompt = f"""오늘은 {date_str}입니다. 아래 데이터를 바탕으로 저녁 결산 리포트를 작성하세요.

## 거시지표 스냅샷
{_format_macro_block(macro_data)}

## 관심종목 당일 가격 결산 (추세·RSI·목표주가 포함)
{_format_price_block(price_data)}

## 기술적 지표 상세 (RSI·이동평균·MACD·애널리스트 컨센서스)
{technical_block}

## 지지/저항 박스 · 손익비 · 조건부 시나리오 (예측 아님 — 조건 충족 시 참고할 자리)
{sr_block}

## 수급 동향 (외국인/기관/개인 순매매, KR 종목만 — 참고용)
{flow_block}

## 최근 공시 (DART, 최근 7일, KR 종목만 — 참고용)
{disclosure_block}

## 등급 적중률 자기검증 (N일 전 등급 vs 오늘 가격 — 참고용)
{accuracy_block}

## 투자 판단 보조 등급 결산
{_format_rating_block(ratings)}

## 당일 등급 변화 내역
{changes_block}

---

위 데이터를 기반으로 아래 구조의 저녁 결산 리포트를 작성해주세요:

1. **당일 시장 결산** — 오늘 시장의 핵심 흐름 요약
2. **등급 변화 및 주목 종목** — 오늘 신호가 변화한 종목 및 이유
3. **주요 움직임 분석** — 오늘의 주요 뉴스·이벤트와 종목별 영향
4. **섹터/테마 일일 리뷰** — 테마별 강약 정리
5. **내일·단기 기술적 전망** — 오늘 기술적 지표를 종합하여 내일과 단기(1주) 방향성을 분석하세요
   - RSI 과매도/과매수 구간 종목 및 단기 반전 가능성
   - MA 골든크로스/데드크로스 형성 여부와 추세 강도
   - MACD 히스토그램 방향 전환 신호
   - 애널리스트 목표주가 대비 현재가 괴리가 큰 종목 (상승여력 Top 3)
   - 지지/저항 박스와 손익비가 있는 종목은 "◯◯원 아래로 종가 이탈 시 지지 붕괴로 볼 여지" 같은 조건부 문장으로 자연스럽게 녹여서 언급하세요 (아래 작성 규칙 참고)
   - 종합 의견: 내일 주목해야 할 종목과 그 이유
6. **내일 주요 모니터링 포인트** — 예정 이벤트, 주목할 지표
7. **투자 유의사항** — 면책 고지 포함

## 지지/저항·손익비 서술 규칙 (중요)
- 지지/저항·손익비 정보는 "매수/매도하라"가 아니라 "이 조건이 뜨면 ~검토할 수 있는 자리" 형태의 조건부 참고로만 서술하세요.
- 손익비가 낮은(2.0 미만) 종목은 "현재 가격대는 손익비 기준으로 신규 진입을 서두를 근거가 약한 구간" 정도로만 언급하고, 특정 행동을 지시하지 마세요.
- 저항/지지가 확인되지 않은 종목(신고가·신저가 구간)은 그 사실 자체를 있는 그대로 서술하세요.

## 수급 서술 규칙 (중요)
- 수급(외국인/기관/개인 순매매)은 매매 신호가 아닌 참고 정보로만 서술하세요.
- "개인" 수치는 KRX가 별도 집계하지 않아 외국인·기관 합산의 잔차로 추정한 값입니다 — 처음 언급할 때 "추정치"임을 명시하세요.
- 해외 종목(미국·대만)은 이 데이터가 존재하지 않으므로 언급하지 마세요.

## 공시 서술 규칙
- 공시는 사실 확인 정보로만 서술하세요 — 공시 발생 자체를 호재/악재로 단정하지 말고, 내용을 있는 그대로 전달하세요.
- 공시 데이터가 없으면("공시 데이터 없음") 이 항목은 언급하지 마세요.

## 등급 적중률 서술 규칙
- 적중률은 과거 등급이 사후적으로 방향성이 맞았는지에 대한 참고 통계이며, 미래 수익을 보장하지 않습니다 — 이 점을 명시하세요.
- "누적 이력 부족"으로 나온 항목은 아직 통계를 낼 만큼 데이터가 쌓이지 않았다는 뜻이므로, 그 자체를 부정적 신호로 해석하지 마세요.
- 샘플 수가 적으면(예: 5건 미만) 통계적 유의성이 낮다는 점을 함께 언급하세요.

리포트 끝에 반드시 다음 면책문구를 포함하세요:
"※ 본 리포트는 투자 권유가 아닌 시장 데이터 기반 판단 보조 참고 자료입니다. 실제 투자 결정은 개인의 판단과 책임 하에 이루어져야 합니다."
"""
        report = self._call_claude(prompt, max_tokens=10000)
        return self._append_data_quality(report, data_quality, ratings)

    def _call_claude(
        self, user_prompt: str, max_tokens: int = 4096, retries: int = 2
    ) -> str:
        if not self.client:
            return self._mock_report(user_prompt)

        last_exc: Exception = RuntimeError("Claude API 호출 실패")
        for attempt in range(retries + 1):
            try:
                message = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                # 토큰 사용량 로깅 — 비용 추적 및 한도 모니터링
                usage = getattr(message, "usage", None)
                if usage:
                    in_tok  = getattr(usage, "input_tokens",  0)
                    out_tok = getattr(usage, "output_tokens", 0)
                    _logger.info(
                        "[CLAUDE_USAGE] model=%s in_tokens=%d out_tokens=%d total=%d",
                        self.model, in_tok, out_tok, in_tok + out_tok,
                    )
                return message.content[0].text
            except Exception as exc:
                last_exc = exc
                _logger.warning(
                    "[CLAUDE_API_ERROR] attempt %d/%d 실패: %s", attempt + 1, retries + 1, exc
                )
                if attempt < retries:
                    wait = 2 ** attempt   # 1초 → 2초
                    time.sleep(wait)
        _logger.error("[CLAUDE_API_ERROR] 최대 재시도 초과: %s", last_exc)
        raise last_exc

    @staticmethod
    def _append_data_quality(
        report: str,
        data_quality: dict | None,
        ratings: list[dict] | None = None,
    ) -> str:
        """리포트 맨 앞에 [데이터 품질 점검] 배너,
        맨 뒤에 [데이터 상태] 섹션 + 등급 캡 면책 문구를 붙여서 반환합니다."""
        if not data_quality:
            return report
        try:
            banner  = DataValidator.format_quality_banner(data_quality)
            section = DataValidator.format_report_section(data_quality)
            result  = banner + report + section

            # 등급 캡이 적용된 종목이 있으면 면책 문구 추가
            capped = [r for r in (ratings or []) if r.get("grade_capped")]
            if capped:
                names_str = ", ".join(r.get("name", "?") for r in capped)
                result += (
                    "\n> ※ 데이터 신뢰도 제한으로 아래 종목의 원래 산정 등급이 조정되었습니다.  \n"
                    f"> **조정 적용 종목**: {names_str}  \n"
                    "> 원본 등급은 등급 이력 → 상세 보기에서 확인할 수 있습니다.\n"
                )
            return result
        except Exception:
            return report

    @staticmethod
    def _mock_report(prompt: str) -> str:
        """API 키 없을 때 Mock 리포트 반환 (파이프라인 테스트용)"""
        return """# 📊 Market Flow 브리핑 (Mock 리포트)

> **안내**: ANTHROPIC_API_KEY가 설정되지 않아 Mock 리포트를 생성했습니다.
> `.env` 파일에 API 키를 설정하면 Claude AI 기반 실제 분석 리포트가 생성됩니다.

---

## 1. 글로벌 시장 개요
현재 Mock 데이터 기반으로 시장 흐름을 분석 중입니다.
AI CapEx 사이클이 강한 상승 구간에서 반도체·전력 인프라 섹터가 주도하는 흐름이 확인됩니다.

## 2. 거시 신호 분석
- 미국 10년물 금리 안정 구간 → 성장주 밸류에이션 부담 완화
- 달러 강세/약세 흐름에 따른 국내 수출주 모니터링 필요

## 3. 섹터/테마 흐름
- **AI/반도체**: HBM 수요 중심의 업사이클 유지
- **전력 인프라**: 데이터센터 전력 수요 증가로 수주 모멘텀 지속
- **방산**: 지정학 리스크 기반 방어적 강세 유지

## 4. 밸류체인 영향도
NVIDIA Blackwell 수요 → SK하이닉스 HBM → TSMC 패키징 → LS ELECTRIC/HD현대일렉트릭 전력 인프라 순으로 수혜 흐름 이어지는 중

## 5. 관심종목 투자 판단 보조 등급
(실제 등급은 아래 등급 섹션 참조)

## 6. 오늘 주요 모니터링 포인트
- NVIDIA 분기 실적 컨센서스 변화
- HBM 수급 업데이트
- KOSPI 외국인 수급 동향

## 7. 투자 유의사항
※ 본 리포트는 투자 권유가 아닌 시장 데이터 기반 판단 보조 참고 자료입니다.
실제 투자 결정은 개인의 판단과 책임 하에 이루어져야 합니다.
"""


def save_report(content: str, report_type: str, save_dir: Path) -> Path:
    """리포트를 날짜별 파일로 저장"""
    date_str = datetime.now().strftime("%Y%m%d")
    filename = f"{date_str}_{report_type}.md"
    filepath = save_dir / filename
    filepath.write_text(content, encoding="utf-8")
    return filepath
