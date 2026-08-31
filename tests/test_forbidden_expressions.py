"""
생성된 리포트의 금지 표현 사후 검증 테스트

배경: config/report_config.json에 forbidden_expressions("무조건 매수", "확실한 수익",
      "보장" 등 7종)가 정의되고 config_loader에 프로퍼티까지 있었는데, **호출하는
      코드가 어디에도 없었다.** 방어 수단이 "시스템 프롬프트로 Claude에게 부탁하기"
      하나뿐이라, 매일 자동 실행되는 파이프라인에는 사후 검증이 전무했다.

핵심 난점: 정상 리포트가 금지어를 부정형으로 자주 쓴다. 심지어 프롬프트의
      "## 등급 적중률 서술 규칙"이 "미래 수익을 보장하지 않습니다"를 쓰라고 지시한다.
      최초 구현(금지어 직후 12자 검사)은 기존 리포트 56개 중 6개를 전부 오탐으로
      잡았다 — 한국어 활용형(아님/아닙니다/없습니다)과 거리 문제 때문.
      매일 허위 경보가 뜨면 알림 자체가 무의미해지므로 문장 단위 판단으로 바꿨다.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.reports.report_builder import verify_forbidden_expressions

_FORBIDDEN = json.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "config" / "report_config.json")
    .read_text(encoding="utf-8")
)["rating_system"]["forbidden_expressions"]


# ── 진짜 위반은 반드시 잡아야 한다 ───────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("이 종목은 확실한 수익이 기대됩니다.", "확실한 수익"),
    ("수익을 보장합니다.", "보장"),
    ("지금 사야 한다고 봅니다.", "지금 사야 한다"),
    ("지금 팔아야 한다는 판단입니다.", "지금 팔아야 한다"),
    ("무조건 매수 구간입니다.", "무조건 매수"),
    ("반드시 매도 하십시오.", "반드시 매도"),
])
def test_detects_genuine_violations(text, expected):
    found = [v["expression"] for v in verify_forbidden_expressions(text, _FORBIDDEN)]
    assert expected in found


def test_expression_containing_negation_is_not_self_exempting():
    """"손실 없음"은 표현 자체에 부정어가 들어 있다 — 자기 자신 때문에 면제되면 안 된다."""
    found = [v["expression"] for v in
             verify_forbidden_expressions("원금 손실 없음을 보장합니다.", _FORBIDDEN)]
    assert "손실 없음" in found


def test_negation_in_a_different_sentence_does_not_exempt():
    """부정이 다른 문장에 있으면 면제되지 않아야 한다."""
    found = verify_forbidden_expressions("수익을 보장합니다. 걱정 없습니다.", _FORBIDDEN)
    assert any(v["expression"] == "보장" for v in found)


# ── 정상 면책 문구는 통과해야 한다 (오탐 방지) ───────────────────────────────

@pytest.mark.parametrize("text", [
    "과거 적중률은 미래 수익을 보장하지 않습니다.",
    "과매도 = 반등 보장이 아님.",
    "실제로 그 목표가에 도달한다는 보장이 없습니다.",
    "모든 등급은 투자 판단 보조 등급이며, 수익을 보장하거나 손실을 예방하는 수단이 아닙니다.",
    "애널리스트 컨센서스는 실현 보장이 없습니다.",
])
def test_disclaimer_usage_is_not_flagged(text):
    assert verify_forbidden_expressions(text, _FORBIDDEN) == []


def test_all_historical_reports_pass():
    """실제 생성 이력 전체에 대해 오탐이 0이어야 한다.

    이 테스트가 깨지면 (a) 검사기가 과민해졌거나 (b) 실제로 문제 있는 리포트가
    생성됐다는 뜻 — 둘 다 확인이 필요한 신호다.
    """
    reports_dir = pathlib.Path(__file__).resolve().parents[1] / "data" / "reports"
    if not reports_dir.exists():
        pytest.skip("생성된 리포트 없음")
    files = sorted(reports_dir.glob("*.md"))
    if not files:
        pytest.skip("생성된 리포트 없음")

    flagged = {
        f.name: [v["expression"] for v in
                 verify_forbidden_expressions(f.read_text(encoding="utf-8"), _FORBIDDEN)]
        for f in files
    }
    offenders = {k: v for k, v in flagged.items() if v}
    assert not offenders, f"금지 표현 감지됨: {offenders}"


# ── 경계 조건 ────────────────────────────────────────────────────────────────

def test_returns_empty_for_clean_report():
    assert verify_forbidden_expressions("삼성전자는 안전 등급입니다.", _FORBIDDEN) == []


def test_handles_empty_inputs():
    assert verify_forbidden_expressions("", _FORBIDDEN) == []
    assert verify_forbidden_expressions("본문", None) == []
    assert verify_forbidden_expressions("본문", []) == []


def test_violation_includes_context_for_diagnosis():
    v = verify_forbidden_expressions("앞부분입니다. 수익을 보장합니다. 뒷부분.", _FORBIDDEN)
    assert v and "context" in v[0] and "position" in v[0]
    assert "보장" in v[0]["context"]


def test_reports_every_occurrence():
    text = "수익을 보장합니다. 원금도 보장합니다."
    assert len(verify_forbidden_expressions(text, _FORBIDDEN)) == 2
