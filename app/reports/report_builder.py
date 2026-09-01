"""
Report Builder — Claude API를 사용해 아침/저녁 리포트를 생성
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import logging
import time

import anthropic

_logger = logging.getLogger(__name__)

from app.utils.data_validator import DataValidator
from app.utils.market_calendar import now_kst

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


# ── 아침/저녁 리포트 공통 서술 규칙 ─────────────────────────────────────────
# 두 프롬프트가 133줄 중 112줄(84%)이 동일했고, 서술 규칙 11개가 통째로 복사돼
# 있었다. 규칙을 하나 추가할 때마다 양쪽을 동시에 고쳐야 해서, 한쪽만 수정되면
# 아침·저녁 리포트가 조용히 다르게 동작하는 드리프트 위험이 있었다.
# 규칙은 리포트 유형과 무관하게 동일해야 하므로 단일 상수로 관리한다.
_SHARED_NARRATION_RULES = """## 지지/저항·손익비 서술 규칙 (중요)
- 지지/저항·손익비 정보는 "매수/매도하라"가 아니라 "이 조건이 뜨면 ~검토할 수 있는 자리" 형태의 조건부 참고로만 서술하세요.
- 손익비가 낮은(2.0 미만) 종목은 "현재 가격대는 손익비 기준으로 신규 진입을 서두를 근거가 약한 구간" 정도로만 언급하고, 특정 행동을 지시하지 마세요.
- 저항/지지가 확인되지 않은 종목(신고가·신저가 구간)은 그 사실 자체를 있는 그대로 서술하세요.

## 수급 서술 규칙 (중요)
- 수급(외국인/기관/개인 순매매)은 매매 신호가 아닌 참고 정보로만 서술하세요.
- "개인(추정)"으로 표시된 수치는 KRX가 별도 집계하지 않아 외국인·기관 합산의 잔차로 추정한 값입니다 — 처음 언급할 때 "추정치"임을 명시하세요. "개인(실측)"으로 표시된 수치는 한국투자증권 공식 API 실측값이므로 추정치라고 서술하지 마세요.
- 해외 종목(미국·대만)은 이 데이터가 존재하지 않으므로 언급하지 마세요.

## 공시 서술 규칙
- 공시는 사실 확인 정보로만 서술하세요 — 공시 발생 자체를 호재/악재로 단정하지 말고, 내용을 있는 그대로 전달하세요.
- 공시 데이터가 없으면("공시 데이터 없음") 이 항목은 언급하지 마세요.

## 등급 적중률 서술 규칙
- 적중률은 과거 등급이 사후적으로 방향성이 맞았는지에 대한 참고 통계이며, 미래 수익을 보장하지 않습니다 — 이 점을 명시하세요.
- "누적 이력 부족"으로 나온 항목은 아직 통계를 낼 만큼 데이터가 쌓이지 않았다는 뜻이므로, 그 자체를 부정적 신호로 해석하지 마세요.
- 샘플 수가 적으면(예: 5건 미만) 통계적 유의성이 낮다는 점을 함께 언급하세요.

## 포트폴리오 관점 서술 규칙
- 테마/섹터 집중도와 당일 동조화율은 워치리스트 구성에 대한 참고 진단이며, "리밸런싱하라"·"비중을 줄여라" 같은 구체적 매매 지시를 하지 마세요.
- 당일 동조화율은 통계적 상관계수가 아닙니다 — "오늘 하루" 종목들이 같은 방향으로 몰렸는지를 보여주는 단순 집계임을 처음 언급할 때 명시하세요.
- 집중 리스크 경고가 있으면 "해당 테마/섹터가 흔들릴 경우 워치리스트 상당 부분이 동시에 영향받을 수 있다"는 사실을 전달하되, 이것이 좋다/나쁘다는 가치 판단은 하지 마세요.

## 시장 전체 테마 동향 서술 규칙 (중요)
- 이 섹션의 강세/약세 테마는 워치리스트 밖 섹터 ETF의 당일 등락률일 뿐이며, 새로운 매수 후보를 추천하는 것이 아닙니다. "이 테마를 사라"·"편입을 검토하라" 같은 문장을 쓰지 마세요.
- "오늘 시장에서 이런 흐름이 있었다"는 사실 전달과, 이미 보유 중인 관심종목·테마와의 연관성(있다면)을 짚어주는 참고 정보로만 다루세요.
- ETF 하나의 등락률로 테마 전체를 단정하지 말고, 참고 지표라는 점을 톤에 반영하세요.

## 이벤트 캘린더 서술 규칙 (중요)
- 이 섹션은 FRED(미국 연준), FOMC/한국은행 공식 일정, 국내 법정 공시기한, DART 공시 접수 이력만 담은 실측 데이터입니다 — 목록에 없는 이벤트를 추측해서 추가하지 마세요.
- "[📄 공시]" 항목의 날짜는 공시가 접수된 날짜이지 실제 행사(IR 등)가 열리는 날짜가 아닙니다 — "~일에 공시가 접수됐다"로만 서술하고 "~일에 행사가 열린다"처럼 단정하지 마세요.
- "[⚖️ 법정기한]" 항목은 특정 종목의 실적 발표를 예고하는 것이 아니라, 법으로 정해진 제출 마감일일 뿐입니다 — 그 안에 실제 언제 발표할지는 회사마다 다르다는 점을 필요시 명시하세요.
- 이벤트 발생이 주가에 어떤 영향을 줄지 예측하지 말고, "이 날짜를 전후로 변동성이 커질 수 있는 시점"이라는 정도의 중립적 참고 정보로만 다루세요.
- 데이터가 없으면("이벤트 캘린더 데이터 없음") 이 항목은 언급하지 마세요.

## 보유/관찰 서술 규칙 (중요)
- "보유 중"으로 표시된 종목과 "관찰 중(미보유)" 종목은 판단의 성격이 다릅니다. 보유 종목은 이미 포지션이 있다는 전제에서 "현재 상태를 어떻게 볼 것인가·무엇을 지켜볼 것인가" 관점으로, 관찰 종목은 "진입을 검토한다면 어떤 조건이 확인돼야 하는가" 관점으로 서술하세요.
- 다만 어느 쪽이든 "팔아라·사라" 같은 지시는 하지 마세요. 보유 종목이라고 해서 매도·보유 지시를 하거나, 관찰 종목이라고 해서 매수를 권하지 마세요.
- 관심도가 높은 종목(4~5)은 상대적으로 더 비중 있게 다루되, 관심도가 낮다는 이유로 중요한 리스크 신호를 생략하지는 마세요.

## 큐레이션 테마 지식 서술 규칙
- 위 "사용자 큐레이션 테마 지식"의 촉진요인·리스크는 **사용자가 직접 정의한 관점**입니다. 오늘의 데이터(가격·뉴스·거시지표)가 그 촉진요인이나 리스크 중 어떤 것을 지지하거나 반박하는지 연결해서 설명하면 가장 유용합니다.
- 정의된 리스크가 실제로 관찰되고 있다면 짚어주고, 관찰되지 않는다면 억지로 연결하지 마세요.
- 이 정의는 고정된 사실이 아니라 사용자의 현재 가설입니다 — 데이터가 명백히 어긋나면 그 사실을 알려주세요.

## 방향성 근거 서술 규칙 (중요)
- 각 종목의 "신호 균형: 상승 N / 하락 M / 확인필요 K"는 **서로 다른 축(가격·기술적·수급·애널리스트·거시)에서 나온 독립 신호의 개수**입니다. 점수 하나만 인용하지 말고, 근거가 몇 대 몇으로 갈리는지를 함께 전달하세요.
- 상승·하락 근거가 비슷하게 맞선 종목(예: 3 대 3)은 "방향성이 확인되지 않는 구간"으로 서술하세요. 점수가 높다는 이유만으로 상승 쪽으로 정리하지 마세요.
- "확인 필요" 항목은 **신호 간 불일치(다이버전스)** 입니다 — 예: 가격은 올랐는데 거래량이 실리지 않음, 가격은 올랐는데 외국인은 순매도. 이건 애매해서 뺀 정보가 아니라, 그 자체로 중요한 경고 신호이니 반드시 언급하세요.
- 근거를 인용할 때는 수치를 그대로 쓰세요("RSI 28 과매도", "5일 누적 외국인 -612만주"). "기술적으로 양호"처럼 뭉뚱그리지 마세요.
- 수급 근거의 "(실측)"은 한국투자증권 공식 API 값, "(추정)"은 네이버 잔차 추정치입니다 — 처음 언급 시 구분하세요.
- 근거가 하나도 없는 종목("없음")은 신호가 약한 것이지 안전하다는 뜻이 아닙니다.

## 요인별 적중률 서술 규칙
- 적중률이 제시된 축은 "이 신호는 과거 N일 기준 X% 적중"처럼 근거의 신뢰도를 함께 밝히는 데 쓰세요. 방향성 근거를 인용할 때 이 수치가 있으면 훨씬 강한 서술이 됩니다.
- **"표본 부족(누적 중)"으로 표시된 축의 적중률은 언급하지 마세요.** 표본이 적은 적중률은 근거가 아니라 착시입니다.
- "통계로 제시할 만큼 표본이 쌓인 축이 없습니다"라고 나오면, 아직 누적 중이라는 사실만 짧게 알리고 넘어가세요. 없는 통계를 만들어내지 마세요.
- 적중률이 높다고 그 신호가 미래에도 맞는다는 뜻은 아닙니다 — 과거 관측일 뿐임을 톤에 반영하세요.

## 지수 대체 표기 서술 규칙
- "※SPY ETF 기준 대체" 같은 표기가 붙은 지수는 **데이터 제공처의 지수 피드가 거래일을 누락해 추종 ETF 값으로 대체**한 것입니다. 이때 표시된 수치는 지수 레벨이 아니라 ETF 가격이므로, "S&P500이 650이다"처럼 지수 레벨로 서술하지 마세요.
- 이 경우 **등락률만 인용**하고, 레벨을 언급해야 한다면 "SPY ETF 기준"임을 함께 밝히세요.
- 대체가 일어났다는 사실 자체는 데이터 이슈이므로, 시장 해석과 섞지 말고 필요할 때만 짧게 언급하세요.

## 뉴스 서술 규칙 (중요)
- 위 "수집된 뉴스 헤드라인"에 있는 내용만 뉴스로 서술하세요. **목록에 없는 뉴스·실적·사건을 배경지식으로 지어내지 마세요** — 당신의 학습 데이터에 있는 과거 이슈(예: 특정 제품 출시, 규제 동향)는 오늘 시점에 사실이 아닐 수 있습니다.
- 종목의 가격 변동을 설명할 때, 수집된 헤드라인에 근거가 있으면 연결하고, 없으면 "구체적 원인은 수집된 뉴스에서 확인되지 않음"이라고 솔직히 쓰세요. 그럴듯한 원인을 추측해서 붙이지 마세요.
- <지정학/전쟁>, <관세/무역>, <재해/공급망> 등으로 태깅된 거시 이벤트는 여러 종목·섹터에 동시에 파급될 수 있으므로, 해당 이벤트가 워치리스트의 어느 부분과 연결되는지 짚어주세요. 다만 영향의 크기를 단정하지 말고 "~에 영향을 줄 수 있는 변수"로 서술하세요.
- 태그는 키워드 기반 자동 분류라 완벽하지 않습니다. 헤드라인 내용과 태그가 어긋나 보이면 헤드라인 쪽을 따르세요.
- 뉴스가 없으면("뉴스 데이터 없음") 뉴스 기반 해설을 시도하지 말고 가격·지표 데이터로만 서술하세요.

## 데이터 기준 시점 서술 규칙 (최우선 — 다른 모든 규칙보다 먼저 적용)
- 위 "데이터 기준 시점" 블록에 적힌 날짜가 이 리포트가 다루는 **실제 시장 날짜**입니다. 실행 날짜(오늘)와 다를 수 있습니다.
- 휴장일(주말) 실행이라고 표시된 경우: "오늘 시장이 하락했다"처럼 오늘 거래가 있었던 것처럼 쓰지 마세요. 반드시 "8월 21일(금) 종가 기준"처럼 실제 거래일을 명시하고, "직전 거래일", "금요일 마감 기준" 같은 표현을 쓰세요.
- "직전 리포트 이후 새로운 거래가 없습니다"라고 표시된 경우: 이 사실을 리포트 도입부에 명확히 알리고, 새 시장 움직임이 있었던 것처럼 서술하지 마세요. 이럴 때는 새로운 등락 해설 대신 다음 거래일 관전 포인트와 누적된 흐름 정리에 집중하세요.
- "종목별 데이터 기준일이 서로 다릅니다"라고 표시된 경우: 기준일이 다른 종목의 등락률을 같은 날 움직임처럼 비교·연결하지 마세요(예: 8/28 기준 종목과 8/27 기준 종목의 등락을 "같은 날 엇갈렸다"고 해석하면 안 됩니다). 필요하면 기준일이 다르다는 점을 독자에게 알리세요.
- 등급·점수는 위 기준일 데이터로 산출된 것입니다. 등급 변화를 언급할 때도 "오늘 바뀌었다"가 아니라 어느 거래일 기준인지 함께 밝히세요.
- **"시장이 개장 중"이라고 표시된 경우 해당 시장 수치를 "종가"라고 쓰지 마세요.** 아직 마감 전이라 장중 현재가이며 마감까지 변합니다 — "9월 1일 장중"·"현재가 기준"처럼 쓰고, 마감된 시장만 "종가"로 서술하세요.
- "시장 상태" 줄에 각 시장이 개장전/개장중/마감/휴장 중 무엇인지 표시됩니다. 서로 다른 상태의 시장을 비교할 때는 그 사실을 밝히세요(예: 한국은 마감했고 미국은 개장 전이라 아직 오늘 움직임이 없음)."""


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


def _format_memo_block(stocks: list[dict] | None) -> str:
    """사용자가 watchlist.json에 직접 남긴 종목별 관전 포인트(memo) — 대시보드
    편집 화면에서만 쓰이고 리포트 프롬프트에는 전달되지 않던 필드를 연결.
    """
    if not stocks:
        return "(등록된 관전 포인트 없음)"
    lines = [f"  {s['name']}: {s['memo']}" for s in stocks if s.get("memo")]
    if not lines:
        return "(등록된 관전 포인트 없음)"
    return "\n".join(lines)


# 부정·부인 표현. 한국어 활용형이 다양해(아니다/아닙니다/아님/아니라) 어간 조각으로 잡는다.
_NEGATION_RE = re.compile(r"않|없|못|아[니닙님]|아 ?니")

# 문장 경계 — 금지 표현이 "쓰인 문장" 안에서만 부정 여부를 판단한다.
_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n]")


def verify_forbidden_expressions(
    report: str,
    forbidden: list[str] | None,
) -> list[dict]:
    """생성된 리포트에 금지 표현이 실제로 쓰였는지 사후 검증.

    기존에는 config/report_config.json에 forbidden_expressions가 정의되고
    config_loader에 프로퍼티까지 있는데도 **호출하는 코드가 없어**, 방어 수단이
    "시스템 프롬프트로 Claude에게 부탁하기" 하나뿐이었다. 자동 실행되는 파이프라인에는
    사람이 확인할 기회조차 없으므로 여기서 채운다.

    핵심 난점 — 정상 리포트가 금지어를 부정형으로 자주 쓴다:
      "미래 수익을 보장하지 않습니다" / "반등 보장이 아님" / "도달한다는 보장이 없습니다"
      / "수익을 보장하거나 손실을 예방하는 수단이 아닙니다"
    심지어 "## 등급 적중률 서술 규칙"이 이런 문장을 쓰라고 **지시**하고 있다.
    처음엔 금지어 직후 12자만 검사했더니 기존 리포트 56개 중 6개가 전부 오탐으로
    잡혔다(활용형·거리 문제). 매일 허위 경보가 뜨면 알림 자체가 무의미해지므로,
    **금지 표현이 포함된 문장 전체**에 부정 표현이 있는지로 판단한다.

    한계: 키워드 휴리스틱이라 "수익을 보장합니다. 걱정 없습니다."처럼 부정이 다른
    문장에 있으면 정상 검출되지만, 한 문장 안에서 긍정 약속과 부정이 섞이면 놓칠 수
    있다. 그래서 이 검사는 발송을 막지 않고 경고 용도로만 쓴다.

    반환: [{"expression", "context", "position"}] — 비어 있으면 통과.
    """
    if not report or not forbidden:
        return []

    violations: list[dict] = []
    for expr in forbidden:
        if not expr:
            continue
        start = 0
        while True:
            idx = report.find(expr, start)
            if idx == -1:
                break
            start = idx + len(expr)

            # 이 표현이 속한 문장 추출
            left = report.rfind("\n", 0, idx)
            s_begin = max(
                (report.rfind(ch, 0, idx) for ch in ".!?"),
                default=-1,
            )
            s_begin = max(s_begin, left) + 1
            m = _SENTENCE_SPLIT_RE.search(report, idx + len(expr))
            s_end = m.start() if m else len(report)
            sentence = report[s_begin:s_end]

            # 금지 표현 자체를 제거한 뒤 부정 여부 판단 —
            # "손실 없음"처럼 표현 안에 부정어가 든 경우 자기 자신 때문에 면제되는 것 방지
            rest = sentence.replace(expr, " ")
            if _NEGATION_RE.search(rest):
                continue  # 부정·부인 용법 — 위반 아님

            violations.append({
                "expression": expr,
                "position": idx,
                "context": report[max(0, idx - 30):idx + len(expr) + 30].replace("\n", " ").strip(),
            })
    return violations


def _format_watchlist_context_block(stocks: list[dict] | None) -> str:
    """보유/관찰 구분과 관심도를 전달하는 블록.

    watchlist.json에 status(보유/관찰)와 interest_level(1~5)이 이미 있는데도
    리포트에 전혀 전달되지 않아, 이미 포지션이 있는 종목과 진입을 검토 중인 종목을
    Claude가 동일하게 서술하고 있었다. 두 상황은 판단 논리가 다르다.
    """
    if not stocks:
        return "(워치리스트 정보 없음)"

    held, watching, other = [], [], []
    for s in stocks:
        name = s.get("name", s.get("id", "?"))
        level = s.get("interest_level")
        label = f"{name}(관심도{level})" if level else name
        status = (s.get("status") or "").strip()
        if status == "보유":
            held.append(label)
        elif status == "관찰":
            watching.append(label)
        else:
            other.append(f"{label}[{status or '상태미지정'}]")

    lines = []
    if held:
        lines.append(f"  [보유 중 — {len(held)}종목] {' · '.join(held)}")
    if watching:
        lines.append(f"  [관찰 중(미보유) — {len(watching)}종목] {' · '.join(watching)}")
    if other:
        lines.append(f"  [기타] {' · '.join(other)}")

    return "\n".join(lines) if lines else "(워치리스트 정보 없음)"


def _format_theme_knowledge_block(
    themes: list[dict] | None,
    stocks: list[dict] | None = None,
) -> str:
    """사용자가 themes.json에 직접 큐레이션한 테마별 촉진요인·리스크 블록.

    key_drivers·key_risks·macro_sensitivity가 8개 테마에 걸쳐 정의돼 있는데도
    코드 어디에서도 읽지 않아 전량 사장되고 있었다(signal_scorer는 theme_config를
    인자로 받기만 하고 실제로는 하드코딩된 섹터 상수를 쓴다).
    """
    if not themes:
        return "(테마 정의 없음)"

    names = {s.get("id"): s.get("name", s.get("id")) for s in (stocks or [])}

    lines = []
    for t in sorted(themes, key=lambda x: -(x.get("interest_level") or 0)):
        related = [names[sid] for sid in (t.get("related_stocks") or []) if sid in names]
        head = f"  {t.get('name', t.get('id'))}"
        meta = []
        if t.get("interest_level"):
            meta.append(f"관심도 {t['interest_level']}")
        if t.get("macro_sensitivity"):
            meta.append(f"거시민감도 {t['macro_sensitivity']}")
        if meta:
            head += f" [{', '.join(meta)}]"
        if related:
            head += f" — 워치리스트 연결: {', '.join(related[:6])}"
        lines.append(head)

        drivers = t.get("key_drivers") or []
        risks = t.get("key_risks") or []
        if drivers:
            lines.append(f"    촉진 요인: {' / '.join(drivers[:4])}")
        if risks:
            lines.append(f"    리스크: {' / '.join(risks[:4])}")

    return "\n".join(lines) if lines else "(테마 정의 없음)"


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
                # 절대값은 주가 스케일에 비례하므로 종목 간 비교가 불가능하다.
                # 실측 사례: 삼성전자 +1,411 / SK하이닉스 +29,518인데 이는 주가 차이일 뿐인데도
                # Claude가 "+29518(↑강한 개선)"처럼 크기를 모멘텀 강도로 해석했다.
                # 해석 가능한 정보는 부호뿐이므로 방향만 전달한다.
                parts.append(f"MACD히스토그램={'양(개선방향)' if hist > 0 else '음(둔화방향)'}")

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


def _fmt_price(value: float, currency: str) -> str:
    """통화에 맞는 자릿수로 가격 표기. 원화는 소수점 단위로 거래되지 않으므로
    "267,000.00 KRW" 같은 표기는 불필요한 노이즈다."""
    if currency == "KRW":
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _fmt_zone(low: float, high: float, currency: str) -> str:
    """지지/저항 구간 표기. 상단=하단이면 범위가 아니므로 값 하나만 쓴다
    ("267,000~267,000"처럼 같은 수를 두 번 쓰던 퇴화 표기 제거)."""
    lo, hi = _fmt_price(low, currency), _fmt_price(high, currency)
    return lo if lo == hi else f"{lo}~{hi}"


def _format_support_resistance_block(price_data: dict[str, dict]) -> str:
    """지지/저항 박스 + 손익비 블록 (Claude 리포트 작성용)
    예측이 아닌 "이 조건이 뜨면 검토 가능한 자리" 형태의 조건부 참고 정보 — 매수·매도 지시가 아님.

    조건부 시나리오 줄("▲ X 위 거래량 동반 종가 마감 → 돌파로 볼 여지")은 제거했다.
    종목마다 같은 템플릿에 숫자만 바꿔 넣은 기계적 반복이었고(18종목 36줄·1,641자로
    데이터 블록의 9%), 그 숫자는 바로 윗줄에 이미 있으며 서술 방식은
    "## 지지/저항·손익비 서술 규칙"에 이미 명시돼 있다.
    """
    lines = []
    for sid, p in price_data.items():
        sr = p.get("support_resistance") or {}
        if not sr:
            continue

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
            zone = _fmt_zone(rz["low"], rz["high"], currency)
            parts.append(
                f"저항 {zone} {currency}(상승여력{up_pct:+.1f}%·강도{rz['strength']})"
            )
        else:
            parts.append("저항 확인 안 됨(신고가 구간)")

        if s_zones:
            sz = s_zones[0]
            zone = _fmt_zone(sz["low"], sz["high"], currency)
            parts.append(
                f"지지 {zone} {currency}(하락위험-{down_pct:.1f}%·강도{sz['strength']})"
            )
        else:
            parts.append("지지 확인 안 됨")

        if rr is not None:
            rr_label = "기준충족" if rr_ok else "기준미달"
            parts.append(f"손익비={rr:.2f}({rr_label})")

        lines.append("  " + " | ".join(parts))

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


def _format_portfolio_block(portfolio_summary: dict | None) -> str:
    """포트폴리오 관점(테마/섹터 집중도·당일 동조화) 블록 — 종목 단위 판단을 보완하는
    워치리스트 전체 시야. 실제 통계적 상관계수가 아닌 근사 지표임을 서술 규칙에서 명시.
    """
    if not portfolio_summary:
        return "(포트폴리오 집중도 데이터 없음)"

    lines = []
    top_themes = [t for t in portfolio_summary.get("theme_concentration", []) if t["count"] >= 2][:5]
    if top_themes:
        theme_parts = [f"{t['theme']} {t['count']}종목({t['pct']}%)" for t in top_themes]
        lines.append(f"  테마 집중도(상위): {' | '.join(theme_parts)}")

    top_sectors = portfolio_summary.get("sector_concentration", [])[:3]
    if top_sectors:
        sector_parts = [f"{s['sector']} {s['count']}종목({s['pct']}%)" for s in top_sectors]
        lines.append(f"  섹터 집중도(상위): {' | '.join(sector_parts)}")

    align = portfolio_summary.get("directional_alignment") or {}
    if align.get("total"):
        lines.append(
            f"  당일 동조화: 상승 {align['up']}·하락 {align['down']}·보합 {align['flat']}"
            f" (다수 방향 {align['majority_direction']} 쏠림 {align['alignment_pct']}%)"
        )

    risk_flags = portfolio_summary.get("risk_flags") or []
    if risk_flags:
        lines.append("  집중 리스크 경고:")
        for flag in risk_flags:
            lines.append(f"    - {flag}")

    if not lines:
        return "(포트폴리오 집중도 데이터 없음)"
    return "\n".join(lines)


def _format_theme_scan_block(theme_scan: list[dict] | None) -> str:
    """워치리스트 밖 시장 전체 섹터/테마 강약 스캔 블록 — 매수 후보 추천이 아닌
    시장 흐름 참고 정보. 상위 5개(강세)·하위 5개(약세)만 노출해 프롬프트 비대화 방지.
    """
    if not theme_scan:
        return "(테마 스캔 데이터 없음)"

    strong = theme_scan[:5]
    weak = theme_scan[-5:] if len(theme_scan) > 5 else []

    lines = ["  강세 테마:"]
    for t in strong:
        lines.append(f"    {t['name']} ({t['ticker']}) {t['change_pct']:+.2f}%")
    if weak:
        lines.append("  약세 테마:")
        for t in reversed(weak):
            lines.append(f"    {t['name']} ({t['ticker']}) {t['change_pct']:+.2f}%")

    return "\n".join(lines)


def _format_news_block(
    news_data: dict[str, list[dict]] | None,
    price_data: dict[str, dict] | None = None,
    max_macro: int = 8,
    max_per_stock: int = 2,
) -> str:
    """수집된 뉴스 헤드라인 블록.

    기존에는 news_data가 build_*_report()의 파라미터로만 존재하고 프롬프트에는
    전혀 삽입되지 않는 죽은 인자였다. 뉴스는 감성 점수 계산에만 쓰이고 헤드라인은
    Claude에게 도달하지 않아, Claude가 학습 지식으로 시장 서사를 채우는 문제가
    있었다(실측: NVIDIA +8.74% 급등의 원인인 실적 발표 헤드라인과 "U.S. Strikes
    Iran" 지정학 이벤트를 수집해놓고도 리포트에 반영하지 못함).

    거시 이벤트(전쟁·관세·금리·정치·재해)는 여러 종목에 동시 영향을 주므로
    종목별 뉴스와 분리해 상단에 모아 보여준다.
    """
    if not news_data:
        return "(뉴스 데이터 없음)"

    from app.collectors.news_collector import detect_macro_event

    def _event_of(item: dict) -> str | None:
        """이벤트 태그를 읽되, 태깅 기능 추가 이전에 수집·저장된 항목에는
        필드가 없으므로 헤드라인에서 즉석 판별한다(과거 데이터 호환)."""
        if "macro_event" in item:
            return item.get("macro_event")
        return detect_macro_event(item.get("headline") or "")

    names = {sid: p.get("name", sid) for sid, p in (price_data or {}).items()}

    def _label(sentiment) -> str:
        try:
            s = float(sentiment)
        except (TypeError, ValueError):
            return "중립"
        return "긍정" if s > 0.15 else ("부정" if s < -0.15 else "중립")

    # ── 거시 이벤트 (종목 경계를 넘는 사건) ──
    macro_seen: dict[str, dict] = {}
    for sid, items in news_data.items():
        for it in items or []:
            event = _event_of(it)
            headline = (it.get("headline") or "").strip()
            if not event or not headline or headline in macro_seen:
                continue
            macro_seen[headline] = {
                "event": event,
                "sentiment": it.get("sentiment"),
                "source": it.get("source", ""),
                "stocks": set(),
            }
        for it in items or []:
            h = (it.get("headline") or "").strip()
            if h in macro_seen:
                macro_seen[h]["stocks"].add(names.get(sid, sid))

    lines: list[str] = []
    shown_macro: set[str] = set()
    if macro_seen:
        lines.append("  [거시 이벤트 — 전쟁·관세·금리·정치·재해 등 시장 전반 변수]")
        for headline, meta in list(macro_seen.items())[:max_macro]:
            related = ", ".join(sorted(meta["stocks"])[:3])
            lines.append(
                f"    <{meta['event']}·{_label(meta['sentiment'])}> {headline}"
                f"{f'  (관련: {related})' if related else ''}"
            )
            shown_macro.add(headline)
    else:
        lines.append("  [거시 이벤트] 해당 유형으로 분류된 뉴스 없음")

    # ── 종목별 뉴스 ──
    stock_lines: list[str] = []
    for sid, items in news_data.items():
        if not items:
            continue
        picked = sorted(
            items,
            key=lambda x: abs(float(x.get("sentiment") or 0)),
            reverse=True,
        )[:max_per_stock]
        for it in picked:
            headline = (it.get("headline") or "").strip()
            # 거시 이벤트 섹션에 이미 나온 헤드라인은 중복이라 생략
            if not headline or headline in shown_macro:
                continue
            event = _event_of(it)
            tag = f"·{event}" if event else ""
            stock_lines.append(
                f"    {names.get(sid, sid)}: <{_label(it.get('sentiment'))}{tag}> {headline}"
            )

    if stock_lines:
        lines.append("  [종목별 주요 뉴스]")
        lines.extend(stock_lines)

    return "\n".join(lines)


_FACTOR_LABEL_KR = {
    "price_momentum": "가격 모멘텀",
    "news_sentiment": "뉴스 감성",
    "macro_alignment": "거시 정렬도",
    "sector_strength": "섹터 강도",
    "volume_signal": "거래량 신호",
    "technical_signal": "기술적 신호",
    "analyst_signal": "애널리스트 신호",
}


def _format_factor_accuracy_block(factor_accuracy: dict | None) -> str:
    """요인별 신호가 과거에 실제로 방향성을 맞혔는지 보여주는 블록.

    "수급이 하락을 가리킨다"보다 "수급이 하락을 가리키는데, 이 신호는 과거 68%
    적중했다"가 훨씬 강한 근거다. 다만 표본이 얇을 때 수치를 제시하면 착시가 되므로,
    충분한 축만 통계로 보여주고 나머지는 누적 중임을 명시한다.
    """
    if not factor_accuracy or not factor_accuracy.get("factors"):
        return "(요인별 적중률 데이터 없음 — 요인별 점수 누적을 막 시작해 통계 산출 전)"

    days = factor_accuracy.get("lookback_days")
    ref = factor_accuracy.get("reference_date")
    ready = [(k, v) for k, v in factor_accuracy["factors"].items() if v.get("sufficient")]
    pending = [k for k, v in factor_accuracy["factors"].items() if not v.get("sufficient")]

    lines = [f"  기준: {days}일 전({ref}) 신호 vs 현재 가격"]
    if ready:
        for name, v in sorted(ready, key=lambda x: -x[1]["hit_rate"]):
            label = _FACTOR_LABEL_KR.get(name, name)
            lines.append(
                f"    {label}: 적중률 {v['hit_rate']}% ({v['hit']}/{v['count']}건, "
                f"평균수익률 {v['avg_return_pct']:+.2f}%)"
            )
    else:
        lines.append("    아직 통계로 제시할 만큼 표본이 쌓인 축이 없습니다.")
    if pending:
        labels = ", ".join(_FACTOR_LABEL_KR.get(k, k) for k in pending)
        lines.append(f"    표본 부족(누적 중): {labels}")
    return "\n".join(lines)


def _format_missing_block(missing_stocks: dict[str, dict] | None) -> str:
    """대상 거래일 데이터가 없어 분석에서 빠진 종목을 명시한다 (계약 C3).

    빠진 사실을 리포트에 싣지 않으면, 독자는 그 종목이 관심종목에서 사라진
    이유를 알 수 없다. 더 나쁜 것은 값을 채워 넣는 쪽이다 —
    2026-09-02 저녁 결산이 LG전자 +7.44%(8월 31일 수치)를 9월 1일 대표
    호재로 서술하고 유일한 '안전' 등급을 준 사고가 그렇게 나왔다.
    """
    if not missing_stocks:
        return ""
    lines = [
        "## 데이터 미도착 종목 (분석 제외)",
        "",
        "아래 종목은 대상 거래일 데이터가 도착하지 않아 등급 산정에서 제외했습니다.",
        "**추정하지 말고, 직전 거래일 수치로 대신 설명하지도 마세요.**",
        "리포트에 이 목록을 그대로 알리고, 해당 종목의 가격·등락·전망은 언급하지 마세요.",
        "",
    ]
    for m in missing_stocks.values():
        lines.append(
            f"- {m.get('name', m.get('stock_id'))}"
            f"({m.get('ticker', '')}) — 대상일 {m.get('target_date') or '미지정'}"
            f" / 사유: {m.get('missing_reason', '알 수 없음')}"
        )
    return "\n".join(lines)


def _format_market_session_block(
    freshness: dict | None,
    macro_data: dict | None = None,
    prev_report_data_date: str | None = None,
) -> str:
    """데이터가 "실제로 언제 것인지"를 Claude에게 명시적으로 알려주는 블록.

    기존에는 이 정보가 프롬프트에 전혀 없어서, 일요일 리포트가 금요일 종가를
    "KOSDAQ -4.63% 급락"처럼 현재형으로 서술하는 문제가 있었다. Claude가 눈치껏
    "(일요일 기준 전일 데이터)"라고 보정한 적도 있지만 매번 달라 신뢰할 수 없었다.
    """
    if not freshness:
        return "(세션 정보 없음)"

    run_date = freshness.get("run_date")
    weekday = freshness.get("run_weekday")
    latest = freshness.get("latest_data_date")
    stale_days = freshness.get("stale_days")

    lines = [f"  실행 시각: {run_date} ({weekday}요일)"]

    if not freshness.get("run_is_trading_day"):
        lines.append("  ⚠️ 오늘은 주말(휴장일)입니다 — 한국·미국 증시 모두 거래가 없었습니다.")
    elif stale_days and stale_days > 0:
        lines.append(f"  ⚠️ 아직 오늘 종가가 없습니다(장 시작 전이거나 반영 지연).")

    sessions = freshness.get("sessions") or {}
    if latest:
        # 장이 열려 있는 동안의 당일 데이터는 종가가 아니라 장중 현재가다.
        # 실측(2026-09-01 09:31 발송분): 개장 31분 후인데 "9월 1일 종가 기준"으로 표기됐다.
        intraday_markets = [
            mk for mk, st in sessions.items()
            if st == "개장중" and latest == freshness.get("run_date")
        ]
        kind = "종가" if not intraday_markets else "가격"
        lines.append(f"  ▶ 아래 모든 가격·등락률은 {latest} {kind} 기준입니다"
                     f"{f' (실행일 기준 {stale_days}일 전)' if stale_days else ''}.")
        if intraday_markets:
            lines.append(
                f"  ⚠️ {'·'.join(intraday_markets)} 시장이 **개장 중**입니다 — 해당 시장 수치는"
                " 종가가 아니라 장중 현재가이며 마감까지 변할 수 있습니다."
            )
    if sessions:
        lines.append("  ▶ 시장 상태: " + " / ".join(f"{mk} {st}" for mk, st in sessions.items()))

    if freshness.get("mixed_dates"):
        counts = freshness.get("date_counts", {})
        detail = " / ".join(f"{d}: {n}종목" for d, n in sorted(counts.items(), reverse=True))
        lines.append(f"  ⚠️ 종목별 데이터 기준일이 서로 다릅니다 — {detail}")
        lines.append("     (데이터 제공처 반영 지연. 서로 다른 날짜의 등락률을 같은 날 것처럼 비교하지 마세요.)")

    macro_dates = (macro_data or {}).get("data_dates") or {}
    macro_distinct = {v for v in macro_dates.values() if v}
    if len(macro_distinct) > 1:
        detail = " / ".join(f"{k} {v}" for k, v in macro_dates.items() if v)
        lines.append(f"  ⚠️ 지수별 기준일도 다릅니다 — {detail}")

    if prev_report_data_date and latest and prev_report_data_date == latest:
        lines.append(f"  ▶ 직전 리포트 이후 새로운 거래가 없습니다 — 가격 데이터가 직전과 완전히 동일합니다.")

    return "\n".join(lines)


_EVENT_CATEGORY_LABEL = {
    "macro": "🇺🇸 매크로",
    "policy": "🏛️ 통화정책",
    "disclosure": "📄 공시",
    "filing_deadline": "⚖️ 법정기한",
}


def _format_event_calendar_block(event_calendar: list[dict] | None) -> str:
    """향후 N일 예정 이벤트 캘린더 블록 — FRED 매크로 지표 발표일, FOMC/금통위,
    국내 법정 공시기한, DART IR 관련 공시(접수일 기준)를 날짜순으로 나열.
    실측 데이터만 포함되며, 매칭/조회 실패한 항목은 애초에 리스트에 없음.
    """
    if not event_calendar:
        return "(이벤트 캘린더 데이터 없음 — FRED_API_KEY 미설정이거나 조회 기간 내 예정 이벤트 없음)"

    lines = []
    for e in event_calendar:
        label = _EVENT_CATEGORY_LABEL.get(e.get("category"), e.get("category", ""))
        lines.append(f"  {e['date']} [{label}] {e['title']}")
    return "\n".join(lines)


def _format_investor_flow_block(price_data: dict[str, dict]) -> str:
    """외국인/기관/개인 순매매 수급 블록 (KR 종목만 — 참고용, 매매 신호 아님).
    소스에 따라 개인 수치의 성격이 다름: KIS(한국투자증권 공식 API, _source="kis")는
    개인 순매수를 실측값으로 직접 제공하고, 네이버 금융(비공식 스크래핑) 폴백 시에는
    KRX가 개인을 별도 집계하지 않아 외국인·기관 합산의 잔차로 추정한 값이다.
    """
    lines = []
    for sid, p in price_data.items():
        flow = p.get("investor_flow") or {}
        if not flow:
            continue

        is_kis = flow.get("_source") == "kis"
        indiv_label = "개인(실측)" if is_kis else "개인(추정)"

        parts = [f"{p['name']}"]
        for days in (5, 20):
            frgn = flow.get(f"foreign_net_{days}d")
            inst = flow.get(f"institution_net_{days}d")
            if is_kis:
                indiv = flow.get(f"individual_net_{days}d")
            else:
                indiv = flow.get(f"individual_net_{days}d_est")
            if frgn is None or inst is None:
                continue
            parts.append(
                f"{days}일누적 외국인{frgn:+,}주·기관{inst:+,}주·{indiv_label}{indiv:+,}주"
            )
        lines.append("  " + " | ".join(parts))

    if not lines:
        return "(수급 데이터 없음 — 해외 종목은 외국인/기관/개인 구분 데이터가 제공되지 않음)"
    return "\n".join(lines)


def _format_rating_block(ratings: list[dict]) -> str:
    """등급 + 방향성 근거 블록.

    이전에는 긍정·부정 근거를 2건씩만 보여줬고, 신호 간 불일치(다이버전스)가 담기는
    check_required는 대시보드에만 쓰이고 프롬프트에는 아예 전달되지 않았다.
    또 가중평균 점수 하나만으로는 "강한 상승 3 + 약한 하락 4"와 "전부 미지근한 중립"이
    구분되지 않아, **신호가 엇갈린다는 사실 자체**가 근거에서 사라졌다.
    여기서는 방향별 신호 개수를 함께 제시해 그 균형을 드러낸다.
    """
    lines = []
    for r in ratings:
        pos_all = r.get("positive_factors") or []
        neg_all = r.get("negative_factors") or []
        chk_all = r.get("check_required") or []

        grade_display = r["grade"]
        if r.get("grade_capped"):
            grade_display += f"(원본:{r['raw_grade']}·신뢰도제한)"

        block = [
            f"- {r['emoji']} {r['name']} [{grade_display}] "
            f"점수:{r['total_score']:.0f} 리스크:{r['risk_score']:.0f} "
            f"신뢰도:{r['data_confidence']:.0f}",
            f"  신호 균형: 상승 {len(pos_all)} / 하락 {len(neg_all)} / 확인필요 {len(chk_all)}",
            f"  상승 근거: {' / '.join(pos_all[:4]) if pos_all else '없음'}",
            f"  하락 근거: {' / '.join(neg_all[:4]) if neg_all else '없음'}",
        ]
        if chk_all:
            block.append(f"  확인 필요: {' / '.join(chk_all[:3])}")
        lines.append("\n".join(block))
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


def _num_or_na(value, fmt: str = "+,.0f") -> str:
    """숫자면 지정 형식으로, 아니면 'N/A'로 표기.

    거시지표 블록이 일부 값에 숫자 포맷을 직접 적용하고 있어, 외부 API가 부분 응답을
    주면 'N/A' 문자열에 :+,.0f를 적용하며 ValueError로 리포트 생성 전체가 죽었다.
    (현재 파이프라인은 macro 수집 실패 시 raise하므로 완전한 dict만 오지만,
     일부 필드만 빠진 응답에는 무방비였다.)
    """
    if isinstance(value, (int, float)):
        return format(value, fmt)
    return "N/A"


def _index_note(entry: dict) -> str:
    """지수가 ETF로 대체됐으면 그 사실을 표기.

    yfinance의 "^" 지수 티커가 거래일을 누락할 때 ETF(SPY·QQQ·SOXX)로 자동
    대체하는데, 대체 사실을 알리지 않으면 독자가 지수 레벨이 갑자기 바뀐 이유를
    알 수 없고 Claude도 ETF 가격을 지수 레벨로 오인한다.
    """
    if not isinstance(entry, dict) or entry.get("_source") != "etf_proxy":
        return ""
    return (f" ※{entry.get('_proxy_ticker')} ETF 기준 대체"
            f"(지수 피드가 {entry.get('_index_stale_date')}에 지연)")


def _format_macro_block(macro: dict) -> str:
    macro = macro or {}
    us = macro.get("us_market") or {}
    kr = macro.get("kr_market") or {}
    cur = macro.get("currencies") or {}
    rates = macro.get("rates") or {}
    sent = macro.get("sentiment") or {}
    comm = macro.get("commodities") or {}

    return f"""[미국 시장]
- S&P500: {us.get('SP500', {}).get('value', 'N/A')} ({_num_or_na(us.get('SP500', {}).get('change_pct'), '+.2f')}%){_index_note(us.get('SP500', {}))}
- NASDAQ: {us.get('NASDAQ', {}).get('value', 'N/A')} ({_num_or_na(us.get('NASDAQ', {}).get('change_pct'), '+.2f')}%){_index_note(us.get('NASDAQ', {}))}
- SOX: {us.get('SOX', {}).get('value', 'N/A')} ({_num_or_na(us.get('SOX', {}).get('change_pct'), '+.2f')}%){_index_note(us.get('SOX', {}))}
- VIX: {us.get('VIX', {}).get('value', 'N/A')} ({us.get('VIX', {}).get('signal', 'N/A')})

[한국 시장]
- KOSPI: {kr.get('KOSPI', {}).get('value', 'N/A')} ({_num_or_na(kr.get('KOSPI', {}).get('change_pct'), '+.2f')}%)
- KOSDAQ: {kr.get('KOSDAQ', {}).get('value', 'N/A')} ({_num_or_na(kr.get('KOSDAQ', {}).get('change_pct'), '+.2f')}%)

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
        portfolio_summary: dict | None = None,
        stocks: list[dict] | None = None,
        theme_scan: list[dict] | None = None,
        event_calendar: list[dict] | None = None,
        data_freshness: dict | None = None,
        prev_report_data_date: str | None = None,
        missing_stocks: dict[str, dict] | None = None,
        themes: list[dict] | None = None,
        factor_accuracy: dict | None = None,
        max_tokens: int = 20000,
    ) -> str:
        date_str = report_date or now_kst().strftime("%Y-%m-%d")
        changes_block = _format_changes_block(grade_changes or [])

        technical_block = _format_technical_block(price_data)
        sr_block = _format_support_resistance_block(price_data)
        flow_block = _format_investor_flow_block(price_data)
        disclosure_block = _format_disclosure_block(disclosure_data)
        accuracy_block = _format_accuracy_block(accuracy_report)
        portfolio_block = _format_portfolio_block(portfolio_summary)
        memo_block = _format_memo_block(stocks)
        theme_scan_block = _format_theme_scan_block(theme_scan)
        event_calendar_block = _format_event_calendar_block(event_calendar)
        news_block = _format_news_block(news_data, price_data)
        watchlist_block = _format_watchlist_context_block(stocks)
        factor_accuracy_block = _format_factor_accuracy_block(factor_accuracy)
        theme_knowledge_block = _format_theme_knowledge_block(themes, stocks)
        session_block = _format_market_session_block(
            data_freshness, macro_data, prev_report_data_date
        )
        missing_block = _format_missing_block(missing_stocks)
        prompt = f"""오늘은 {date_str}입니다. 아래 데이터를 바탕으로 아침 브리핑 리포트를 작성하세요.

## 데이터 기준 시점 (가장 먼저 확인할 것)
{session_block}

{missing_block}

## 거시지표 스냅샷
{_format_macro_block(macro_data)}

## 예정 이벤트 캘린더 (실측, 향후 14일)
{event_calendar_block}

## 수집된 뉴스 헤드라인 (실제 수집분 — 이 목록이 뉴스 서술의 유일한 근거)
{news_block}

## 시장 전체 테마 동향 (워치리스트 밖, 섹터/테마 ETF 기준 — 참고용)
{theme_scan_block}

## 관심종목 가격 현황 (추세·RSI·목표주가 포함)
{_format_price_block(price_data)}

## 보유/관찰 구분 및 관심도 (사용자가 직접 지정)
{watchlist_block}

## 사용자 큐레이션 테마 지식 (직접 정의한 촉진요인·리스크)
{theme_knowledge_block}

## 사용자 관전 포인트 (종목별 직접 등록한 관심사 — 관련 있으면 자연스럽게 반영)
{memo_block}

## 기술적 지표 상세 (RSI·이동평균·MACD·애널리스트 컨센서스)
{technical_block}

## 지지/저항 박스 · 손익비 · 조건부 시나리오 (예측 아님 — 조건 충족 시 참고할 자리)
{sr_block}

## 수급 동향 (외국인/기관/개인 순매매, KR 종목만 — 참고용)
{flow_block}

## 최근 공시 (DART, 최근 7일, KR 종목만 — 참고용)
{disclosure_block}

## 포트폴리오 관점 (테마/섹터 집중도·당일 동조화 — 통계적 상관계수 아닌 근사 지표)
{portfolio_block}

## 등급 적중률 자기검증 (N일 전 등급 vs 오늘 가격 — 참고용)
{accuracy_block}

## 요인별 신호 적중률 (어떤 신호가 실제로 맞았는가 — 참고용)
{factor_accuracy_block}

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
7. **오늘 주요 모니터링 포인트** — 위 "예정 이벤트 캘린더"를 근거로 확인이 필요한 이벤트·지표·공시를 짚어주세요 (캘린더에 없는 내용을 추측해서 채우지 마세요)
8. **투자 유의사항** — 면책 고지 포함

{_SHARED_NARRATION_RULES}

리포트 끝에 반드시 다음 면책문구를 포함하세요:
"※ 본 리포트는 투자 권유가 아닌 시장 데이터 기반 판단 보조 참고 자료입니다. 실제 투자 결정은 개인의 판단과 책임 하에 이루어져야 합니다."
"""
        report = self._call_claude(prompt, max_tokens=max_tokens)
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
        portfolio_summary: dict | None = None,
        stocks: list[dict] | None = None,
        theme_scan: list[dict] | None = None,
        event_calendar: list[dict] | None = None,
        data_freshness: dict | None = None,
        prev_report_data_date: str | None = None,
        missing_stocks: dict[str, dict] | None = None,
        themes: list[dict] | None = None,
        factor_accuracy: dict | None = None,
        max_tokens: int = 20000,
    ) -> str:
        date_str = report_date or now_kst().strftime("%Y-%m-%d")
        changes_block = _format_changes_block(grade_changes or [])

        technical_block = _format_technical_block(price_data)
        sr_block = _format_support_resistance_block(price_data)
        flow_block = _format_investor_flow_block(price_data)
        disclosure_block = _format_disclosure_block(disclosure_data)
        accuracy_block = _format_accuracy_block(accuracy_report)
        portfolio_block = _format_portfolio_block(portfolio_summary)
        memo_block = _format_memo_block(stocks)
        theme_scan_block = _format_theme_scan_block(theme_scan)
        event_calendar_block = _format_event_calendar_block(event_calendar)
        news_block = _format_news_block(news_data, price_data)
        watchlist_block = _format_watchlist_context_block(stocks)
        factor_accuracy_block = _format_factor_accuracy_block(factor_accuracy)
        theme_knowledge_block = _format_theme_knowledge_block(themes, stocks)
        session_block = _format_market_session_block(
            data_freshness, macro_data, prev_report_data_date
        )
        missing_block = _format_missing_block(missing_stocks)
        prompt = f"""오늘은 {date_str}입니다. 아래 데이터를 바탕으로 저녁 결산 리포트를 작성하세요.

## 데이터 기준 시점 (가장 먼저 확인할 것)
{session_block}

{missing_block}

## 거시지표 스냅샷
{_format_macro_block(macro_data)}

## 예정 이벤트 캘린더 (실측, 향후 14일)
{event_calendar_block}

## 수집된 뉴스 헤드라인 (실제 수집분 — 이 목록이 뉴스 서술의 유일한 근거)
{news_block}

## 시장 전체 테마 동향 (워치리스트 밖, 섹터/테마 ETF 기준 — 참고용)
{theme_scan_block}

## 관심종목 당일 가격 결산 (추세·RSI·목표주가 포함)
{_format_price_block(price_data)}

## 보유/관찰 구분 및 관심도 (사용자가 직접 지정)
{watchlist_block}

## 사용자 큐레이션 테마 지식 (직접 정의한 촉진요인·리스크)
{theme_knowledge_block}

## 사용자 관전 포인트 (종목별 직접 등록한 관심사 — 관련 있으면 자연스럽게 반영)
{memo_block}

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

## 요인별 신호 적중률 (어떤 신호가 실제로 맞았는가 — 참고용)
{factor_accuracy_block}

## 포트폴리오 관점 (테마/섹터 집중도·당일 동조화 — 통계적 상관계수 아닌 근사 지표)
{portfolio_block}

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
6. **내일 주요 모니터링 포인트** — 위 "예정 이벤트 캘린더"를 근거로 예정 이벤트·주목할 지표를 짚어주세요 (캘린더에 없는 내용을 추측해서 채우지 마세요)
7. **투자 유의사항** — 면책 고지 포함

{_SHARED_NARRATION_RULES}

리포트 끝에 반드시 다음 면책문구를 포함하세요:
"※ 본 리포트는 투자 권유가 아닌 시장 데이터 기반 판단 보조 참고 자료입니다. 실제 투자 결정은 개인의 판단과 책임 하에 이루어져야 합니다."
"""
        report = self._call_claude(prompt, max_tokens=max_tokens)
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

                text = message.content[0].text
                # 출력 한도에 걸려 잘렸는지 확인한다. 기존에는 stop_reason을 보지 않아
                # 잘린 리포트가 조용히 발송됐다 — 실측(2026-09-01 아침): out_tokens가
                # 정확히 10000에 닿아 18종목 중 5종목(점수 1위 SanDisk 포함)이 통째로
                # 빠지고, 전망·모니터링 포인트 섹션과 **면책 문구까지 사라진 채** 나갔다.
                if getattr(message, "stop_reason", None) == "max_tokens":
                    _logger.error(
                        "[CLAUDE_TRUNCATED] 출력이 max_tokens(%d)에 걸려 잘렸습니다 — "
                        "리포트 일부 섹션이 누락됩니다. config/report_config.json의 "
                        "max_tokens를 올리세요.", max_tokens,
                    )
                    text = (
                        "> 🚨 **자동 검증 경고**: 이 리포트는 출력 한도에 걸려 "
                        "**중간에 잘렸습니다.** 일부 종목·섹션과 투자 유의사항이 누락됐을 "
                        "수 있으니 완전한 분석으로 신뢰하지 마세요.\n\n"
                    ) + text
                return text
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
    date_str = now_kst().strftime("%Y%m%d")
    filename = f"{date_str}_{report_type}.md"
    filepath = save_dir / filename
    filepath.write_text(content, encoding="utf-8")
    return filepath
