"""
Report Builder — Claude API를 사용해 아침/저녁 리포트를 생성
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic


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

## 출력 형식
마크다운 형식으로 작성하며, 이모지를 적절히 활용해 가독성을 높입니다.
"""


def _format_price_block(price_data: dict[str, dict]) -> str:
    lines = []
    for sid, p in price_data.items():
        chg_sign = "+" if p["change_pct"] >= 0 else ""
        lines.append(
            f"- {p['name']} ({p['ticker']}): "
            f"{p['price']:,.2f} {p['currency']} "
            f"({chg_sign}{p['change_pct']:.2f}%) "
            f"| 거래량비율 {p['volume_ratio']:.1f}x"
        )
    return "\n".join(lines)


def _format_rating_block(ratings: list[dict]) -> str:
    lines = []
    for r in ratings:
        pos = r["positive_factors"][:2] if r["positive_factors"] else ["없음"]
        neg = r["negative_factors"][:2] if r["negative_factors"] else ["없음"]
        lines.append(
            f"- {r['emoji']} {r['name']} [{r['grade']}] "
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
- 공포탐욕 지수: {sent.get('fear_greed_index', {}).get('value', 'N/A')} ({sent.get('fear_greed_index', {}).get('label', '')})
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
    ) -> str:
        date_str = report_date or datetime.now().strftime("%Y-%m-%d")
        changes_block = _format_changes_block(grade_changes or [])

        prompt = f"""오늘은 {date_str}입니다. 아래 데이터를 바탕으로 아침 브리핑 리포트를 작성하세요.

## 거시지표 스냅샷
{_format_macro_block(macro_data)}

## 관심종목 가격 현황
{_format_price_block(price_data)}

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
5. **관심종목 투자 판단 보조 등급** — 각 종목별 등급과 핵심 판단 근거
6. **오늘 주요 모니터링 포인트** — 확인이 필요한 이벤트·지표·공시
7. **투자 유의사항** — 면책 고지 포함

리포트 끝에 반드시 다음 면책문구를 포함하세요:
"※ 본 리포트는 투자 권유가 아닌 시장 데이터 기반 판단 보조 참고 자료입니다. 실제 투자 결정은 개인의 판단과 책임 하에 이루어져야 합니다."
"""
        return self._call_claude(prompt, max_tokens=4096)

    def build_evening_report(
        self,
        price_data: dict[str, dict],
        news_data: dict[str, list],
        macro_data: dict,
        ratings: list[dict],
        report_date: str | None = None,
        grade_changes: list[dict] | None = None,
    ) -> str:
        date_str = report_date or datetime.now().strftime("%Y-%m-%d")
        changes_block = _format_changes_block(grade_changes or [])

        prompt = f"""오늘은 {date_str}입니다. 아래 데이터를 바탕으로 저녁 결산 리포트를 작성하세요.

## 거시지표 스냅샷
{_format_macro_block(macro_data)}

## 관심종목 당일 가격 결산
{_format_price_block(price_data)}

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
5. **내일 주요 모니터링 포인트** — 예정 이벤트, 주목할 지표
6. **투자 유의사항** — 면책 고지 포함

리포트 끝에 반드시 다음 면책문구를 포함하세요:
"※ 본 리포트는 투자 권유가 아닌 시장 데이터 기반 판단 보조 참고 자료입니다. 실제 투자 결정은 개인의 판단과 책임 하에 이루어져야 합니다."
"""
        return self._call_claude(prompt, max_tokens=4096)

    def _call_claude(self, user_prompt: str, max_tokens: int = 4096) -> str:
        if not self.client:
            return self._mock_report(user_prompt)

        message = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text

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
