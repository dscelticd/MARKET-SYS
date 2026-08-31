"""
아침/저녁 프롬프트 구조 회귀 테스트

배경: 두 프롬프트가 133줄 중 112줄(84%)이 완전히 동일했고, 서술 규칙 11개가 통째로
      복사돼 있었다. 규칙을 하나 추가할 때마다 양쪽을 동시에 고쳐야 해서(이번 세션에
      실제로 매번 `.replace(count==2)` 단언을 걸어 수정했다), 한쪽만 수정되면
      아침·저녁 리포트가 조용히 다르게 동작하는 드리프트 위험이 있었다.

      _SHARED_NARRATION_RULES 단일 상수로 통합했으며, 이 테스트는 그 통합이
      되돌려지지 않도록 지킨다.
"""
from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

import pytest

import app.reports.report_builder as rb

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load(name: str):
    return json.loads((_ROOT / "config" / name).read_text(encoding="utf-8"))


def _render_prompts() -> dict[str, str]:
    """실제 build_*_report를 호출해 조립된 프롬프트를 가로챈다.
    (문자열 검사가 아니라 실제 렌더링이라 f-string 참조 오류까지 잡힌다.)"""
    captured: dict[str, str] = {}

    def spy(self, prompt, max_tokens=10000):
        captured["last"] = prompt
        return "dummy report"

    stocks = _load("watchlist.json")["stocks"]
    themes = _load("themes.json")["themes"]
    # macro는 실제와 같은 형태로 채워야 한다 — _format_macro_block이 일부 키에
    # 숫자 포맷(:+,.0f)을 직접 적용해서, 빈 dict를 주면 'N/A' 문자열에 포맷을
    # 적용하며 ValueError로 죽는다(실 파이프라인은 macro 수집 실패 시 raise하므로
    # 항상 완전한 dict가 오지만, 테스트에서 빈 dict를 넘기면 드러난다).
    macro = {
        "us_market": {"SP500": {"value": 7731.0, "change_pct": 0.7}},
        "kr_market": {"KOSPI": {"value": 6912.0, "change_pct": 1.5},
                      "foreign_net_buy_bn": 578.0},
        "currencies": {}, "rates": {}, "commodities": {}, "sentiment": {},
    }
    price = {"KR_005930": {
        "stock_id": "KR_005930", "ticker": "005930", "name": "삼성전자",
        "price": 100.0, "change_pct": 1.0, "currency": "KRW", "volume_ratio": 1.0,
        "data_date": "2026-08-28",
    }}
    ratings = [{
        "stock_id": "KR_005930", "name": "삼성전자", "ticker": "005930", "emoji": "🔵",
        "grade": "안전", "total_score": 60.0, "risk_score": 30.0, "data_confidence": 100.0,
        "positive_factors": [], "negative_factors": [],
    }]

    out = {}
    with patch.object(rb.ReportBuilder, "_call_claude", spy):
        builder = rb.ReportBuilder()
        for kind in ("morning", "evening"):
            fn = builder.build_morning_report if kind == "morning" else builder.build_evening_report
            fn(price_data=price, news_data={}, macro_data=macro, ratings=ratings,
               stocks=stocks, themes=themes)
            out[kind] = captured["last"]
    return out


@pytest.fixture(scope="module")
def prompts():
    return _render_prompts()


def test_both_prompts_render_without_placeholder_leak(prompts):
    """f-string 참조가 깨지면 플레이스홀더가 그대로 남는다."""
    for kind, p in prompts.items():
        assert "{_SHARED" not in p, f"{kind} 프롬프트에 미치환 플레이스홀더"
        assert "{" not in p.split("## 지지/저항")[0][-200:], f"{kind} 프롬프트 치환 누락 의심"


def test_shared_rules_present_in_both_prompts(prompts):
    """공통 규칙이 양쪽에 모두 들어가야 한다 — 한쪽만 빠지면 동작이 갈린다."""
    for rule in ["지지/저항·손익비 서술 규칙", "수급 서술 규칙", "공시 서술 규칙",
                 "등급 적중률 서술 규칙", "포트폴리오 관점 서술 규칙",
                 "시장 전체 테마 동향 서술 규칙", "이벤트 캘린더 서술 규칙",
                 "보유/관찰 서술 규칙", "큐레이션 테마 지식 서술 규칙",
                 "뉴스 서술 규칙", "데이터 기준 시점 서술 규칙"]:
        for kind, p in prompts.items():
            assert rule in p, f"{kind} 프롬프트에 '{rule}' 누락"


def test_narration_rules_are_identical_across_report_types(prompts):
    """규칙 본문이 두 프롬프트에서 완전히 동일해야 한다(드리프트 방지 핵심)."""
    rules = rb._SHARED_NARRATION_RULES
    assert rules in prompts["morning"]
    assert rules in prompts["evening"]


def test_rules_defined_once_in_source():
    """소스에서 규칙이 상수 한 곳에만 존재해야 한다 — 복붙이 되살아나면 실패."""
    src = (_ROOT / "app" / "reports" / "report_builder.py").read_text(encoding="utf-8")
    # 대표 규칙 문장이 소스에 두 번 이상 나오면 다시 복사된 것
    marker = "- 지지/저항·손익비 정보는 \"매수/매도하라\"가 아니라"
    assert src.count(marker) == 1, "서술 규칙이 다시 중복 정의됨"


def test_disclaimer_present_in_both(prompts):
    for kind, p in prompts.items():
        assert "면책문구" in p, f"{kind} 프롬프트에 면책 문구 지시 누락"


def test_forbidden_expressions_consistent_between_config_and_system_prompt():
    """config의 금지 표현과 시스템 프롬프트의 목록이 어긋나면,
    검사기는 잡는데 Claude는 안내받지 못하는(또는 그 반대) 상태가 된다."""
    cfg_list = _load("report_config.json")["rating_system"]["forbidden_expressions"]
    for expr in cfg_list:
        assert expr in rb.SYSTEM_PROMPT, f"시스템 프롬프트에 '{expr}' 안내 누락"
