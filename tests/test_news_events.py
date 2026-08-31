"""
뉴스 헤드라인 리포트 연결 + 거시 이벤트 분류 테스트

배경: news_data가 build_morning_report()/build_evening_report()의 파라미터로만
      존재하고 프롬프트에는 전혀 삽입되지 않는 죽은 인자였다. 뉴스는 감성 점수
      계산에만 쓰이고 헤드라인이 Claude에게 도달하지 않아, Claude가 학습 지식으로
      시장 서사를 채우는 문제가 있었다.

      실측 사고: 2026-08-31 수집분에 "After Nvidia earnings..."(NVDA +8.74% 급등의
      원인)와 "Oil Prices Jump As U.S. Strikes Iran"(지정학 이벤트)이 있었으나,
      전자는 감성 0.0 중립, 후자는 'Fall'이라는 단어 때문에 감성 -1.0 일반 악재로만
      기록되고 사건의 성격은 완전히 소실된 채 리포트에 반영되지 못했다.

      사용자 질문("관세·정치·금리·자연재해·전쟁이 요소에 포함되나?")에 따라
      감성과 별개 축으로 이벤트 유형을 태깅한다. 점수 산식은 의도적으로 건드리지
      않는다 — 검증 없는 가중치는 근거 없는 숫자가 되고 기존 적중률 이력과
      비교 불가능해지기 때문.
"""
from __future__ import annotations

import pytest

from app.collectors.news_collector import classify_news_item, detect_macro_event
from app.reports.report_builder import _format_news_block


# ── 이벤트 유형 분류 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("headline,expected", [
    ("Dow Jones Futures Fall, Oil Prices Jump As U.S. Strikes Iran", "지정학/전쟁"),
    ("중동 전쟁 확산에 유가 급등", "지정학/전쟁"),
    ("Trump announces new 25% tariffs on imported semiconductors", "관세/무역"),
    ("관세 인상으로 수출기업 타격 우려", "관세/무역"),
    ("Fed signals rate cut as inflation cools", "통화정책/금리"),
    ("9월 美금리인상 우려, 코스피 향방은", "통화정책/금리"),
    ("Taiwan earthquake disrupts chip supply chain", "재해/공급망"),
    ("반도체 공장 화재로 생산차질", "재해/공급망"),
    ("After Nvidia earnings, the tech trade is getting more segmented", "실적/가이던스"),
    ("US Senate passes antitrust bill targeting big tech", "정치/선거/규제"),
])
def test_detect_macro_event_classifies_known_event_types(headline, expected):
    assert detect_macro_event(headline) == expected


@pytest.mark.parametrize("headline", [
    # 실측 오탐 회귀: "war"가 Award/Hardware 안에서 매칭되던 버그
    "Infleqtion Rises 6% on $20M NASA Quantum Gravity Award, IonQ Climbs 5%",
    "This Semiconductor Giant Will Be the Ultimate Winner of the AI Hardware Race.",
    # 일반 시황 기사는 이벤트 없음
    "If You'd Invested $1,000 in QQQ 20 Years Ago, Here's What You'd Have Today",
])
def test_detect_macro_event_returns_none_for_non_events(headline):
    assert detect_macro_event(headline) is None


def test_english_keywords_require_word_boundary():
    """영문은 단어 경계 매칭 — 부분 문자열이면 오탐이 난다."""
    assert detect_macro_event("Hardware upgrade cycle") is None
    assert detect_macro_event("Trade war escalates") == "관세/무역"


def test_korean_keywords_match_with_particles_attached():
    """한글은 조사가 붙으므로 부분 문자열 매칭이어야 한다."""
    assert detect_macro_event("관세를 인상한다고 발표") == "관세/무역"
    assert detect_macro_event("전쟁이 장기화되면서") == "지정학/전쟁"


def test_classify_news_item_attaches_macro_event():
    result = classify_news_item("Fed signals rate cut as inflation cools")
    assert result["macro_event"] == "통화정책/금리"
    assert result["category"] == "일반"


def test_derivative_classification_still_works_and_carries_event():
    """파생상품 분류(기존 기능)와 이벤트 태깅이 공존해야 한다."""
    result = classify_news_item("레버리지 ETN 괴리율 급등, 금리인상 우려 반영")
    assert result["category"] == "파생상품 이슈"
    assert result["exclude_from_direct_negative_news"] is True
    assert result["macro_event"] == "통화정책/금리"


# ── 리포트 블록 ──────────────────────────────────────────────────────────────

def _news(headline, sentiment=0.0, macro_event=None):
    item = {"headline": headline, "sentiment": sentiment, "source": "테스트"}
    if macro_event is not None:
        item["macro_event"] = macro_event
    return item


_PRICE = {"US_NVDA": {"name": "NVIDIA"}, "KR_005930": {"name": "삼성전자"}}


def test_news_block_surfaces_macro_events_separately():
    news = {
        "US_NVDA": [_news("Oil Prices Jump As U.S. Strikes Iran", -1.0, "지정학/전쟁")],
        "KR_005930": [_news("삼성전자 신제품 출시", 0.5, None)],
    }
    block = _format_news_block(news, _PRICE)
    assert "거시 이벤트" in block
    assert "지정학/전쟁" in block
    assert "Strikes Iran" in block
    assert "종목별 주요 뉴스" in block


def test_news_block_lists_affected_stocks_for_shared_event():
    """같은 거시 이벤트가 여러 종목에 걸리면 관련 종목을 모아 표시한다."""
    shared = "Oil Prices Jump As U.S. Strikes Iran"
    news = {
        "US_NVDA": [_news(shared, -1.0, "지정학/전쟁")],
        "KR_005930": [_news(shared, -1.0, "지정학/전쟁")],
    }
    block = _format_news_block(news, _PRICE)
    assert "NVIDIA" in block and "삼성전자" in block
    # 거시 섹션에 한 번만 나오고 종목별 섹션에서는 중복 생략
    assert block.count(shared) == 1


def test_news_block_infers_event_for_legacy_items_without_field():
    """태깅 기능 추가 이전에 저장된 뉴스(macro_event 필드 없음)도 분류돼야 한다."""
    news = {"US_NVDA": [{"headline": "Fed signals rate cut", "sentiment": 0.0}]}
    block = _format_news_block(news, _PRICE)
    assert "통화정책/금리" in block


def test_news_block_reports_when_no_macro_event_present():
    news = {"US_NVDA": [_news("QQQ 20-year performance review", 0.0, None)]}
    block = _format_news_block(news, _PRICE)
    assert "분류된 뉴스 없음" in block


def test_news_block_handles_empty_input():
    assert "뉴스 데이터 없음" in _format_news_block(None, _PRICE)
    assert "뉴스 데이터 없음" in _format_news_block({}, _PRICE)


def test_news_block_limits_volume_to_keep_prompt_small():
    """종목당 노출 건수를 제한해 프롬프트가 비대해지지 않아야 한다."""
    news = {"US_NVDA": [_news(f"headline {i}", 0.9) for i in range(20)]}
    block = _format_news_block(news, _PRICE, max_per_stock=2)
    assert sum(1 for ln in block.splitlines() if "NVIDIA:" in ln) == 2
