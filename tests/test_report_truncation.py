"""
리포트 절단(max_tokens 도달) 감지 테스트

배경: 2026-09-01 아침 리포트가 출력 한도에 걸려 문장 중간에서 끊긴 채 발송됐다.
      로그의 out_tokens가 정확히 10000(=max_tokens)이었고, 결과물에서는
        - 관심종목 18개 중 13개까지만 생성 (점수 1위 SanDisk 64점 '안전'이 0회 언급)
        - 8개 섹션 중 5개만 존재 (단기 전망·모니터링 포인트·투자 유의사항 소실)
        - **면책 문구 소실**
      가 확인됐다.

      가장 위험했던 부분은 "겉보기에 멀쩡했다"는 것이다. 말미의 "📊 데이터 상태"는
      _append_data_quality()가 사후에 붙이는 블록이라 절단됐는데도 그대로 남아,
      리포트가 정상 종료된 것처럼 보였다. _call_claude()가 stop_reason을 확인하지
      않아 잘린 응답과 완결 응답이 구분되지 않았다.

      한도를 올려도 종목·근거 항목이 더 늘면 재발할 수 있다. 따라서 이 테스트는
      "한도 값"과 "절단을 감지하는가"를 함께 지킨다.
"""
from __future__ import annotations

import json
import logging
import pathlib

import pytest

from app.reports.report_builder import ReportBuilder

_ROOT = pathlib.Path(__file__).resolve().parents[1]

# 실측 하한: 18종목·8개 섹션을 모두 담은 정상 리포트가 out_tokens=15,470이었다.
# 여유를 두되, 10000처럼 확실히 잘리는 값으로 되돌아가는 것을 막는다.
_MIN_MAX_TOKENS = 16000


class _FakeMessage:
    def __init__(self, text: str, stop_reason: str):
        self.content = [type("Block", (), {"text": text})()]
        self.stop_reason = stop_reason
        self.usage = type("Usage", (), {"input_tokens": 100, "output_tokens": 200})()


class _FakeMessages:
    def __init__(self, message): self._message = message
    def create(self, **kwargs): return self._message


class _FakeClient:
    def __init__(self, text: str, stop_reason: str):
        self.messages = _FakeMessages(_FakeMessage(text, stop_reason))


def _builder_with(text: str, stop_reason: str) -> ReportBuilder:
    """API 키 유무와 무관하게 동작하도록 client를 직접 주입한다."""
    b = ReportBuilder()
    b.client = _FakeClient(text, stop_reason)
    return b


_CUT = "## 5. 관심종목 등급\n\n**핵심 판단**: 신호 3 "   # 실제로 잘렸던 마지막 줄


# ── 절단 감지 ────────────────────────────────────────────────────────────────

def test_truncated_response_gets_visible_warning():
    """잘린 리포트가 아무 표시 없이 발송되면 안 된다."""
    out = _builder_with(_CUT, "max_tokens")._call_claude("prompt", max_tokens=10000)
    assert "잘렸습니다" in out
    assert out.startswith(">")          # 인용 블록 = 리포트 최상단에 눈에 띄게
    assert _CUT in out                  # 원문은 보존한다 (버려서는 안 됨)


def test_complete_response_is_untouched():
    """정상 완결 리포트에 경고가 붙으면 매번 오탐이 된다."""
    body = "## 1. 글로벌 시장 개요\n정상 리포트입니다.\n"
    out = _builder_with(body, "end_turn")._call_claude("prompt", max_tokens=20000)
    assert out == body


def test_truncation_is_logged_as_error(caplog):
    """운영 중 재발을 로그로 추적할 수 있어야 한다 (Actions 아티팩트에 남는다)."""
    with caplog.at_level(logging.ERROR, logger="app.reports.report_builder"):
        _builder_with(_CUT, "max_tokens")._call_claude("prompt", max_tokens=10000)
    assert any("[CLAUDE_TRUNCATED]" in r.message for r in caplog.records)


def test_missing_stop_reason_is_not_treated_as_truncation():
    """stop_reason이 없는 응답(구 SDK·스텁)을 절단으로 오판하면 안 된다."""
    class _NoStopReason(_FakeClient):
        def __init__(self, text):
            super().__init__(text, "end_turn")
            del self.messages._message.stop_reason
    b = ReportBuilder()
    b.client = _NoStopReason("정상 본문")
    assert b._call_claude("prompt", max_tokens=20000) == "정상 본문"


# ── 한도 값 회귀 방지 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("section", ["morning_report", "evening_report"])
def test_configured_max_tokens_fits_a_full_report(section):
    cfg = json.loads((_ROOT / "config" / "report_config.json").read_text(encoding="utf-8"))
    value = cfg[section]["max_tokens"]
    assert value >= _MIN_MAX_TOKENS, (
        f"{section}.max_tokens={value} — 18종목 전체 리포트(실측 15,470토큰)가 잘린다"
    )


def test_fallback_defaults_are_not_the_truncating_value():
    """설정 키가 빠지면 코드의 폴백 기본값이 쓰인다. 그 값이 10000이면
    config/report_config.json을 고쳐도 조용히 잘리던 상태로 되돌아간다.

    검사 대상은 **리포트에 실제로 쓰이는 기본값**뿐이다. _call_claude()의
    시그니처 기본값(4096)은 범용 헬퍼용이고 리포트 경로는 항상 값을 명시해
    넘기므로 제외한다 — ast로 함수를 특정해 오탐을 없앤다.
    """
    import ast

    # ① 리포트 생성 진입점의 시그니처 기본값
    tree = ast.parse((_ROOT / "app" / "reports" / "report_builder.py").read_text(encoding="utf-8"))
    checked = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef)
                and node.name in ("build_morning_report", "build_evening_report")):
            continue
        args = node.args.kwonlyargs or node.args.args
        defaults = node.args.kw_defaults or node.args.defaults
        offset = len(args) - len(defaults)
        for i, a in enumerate(args[offset:]):
            if a.arg == "max_tokens":
                value = defaults[i].value
                assert value >= _MIN_MAX_TOKENS, f"{node.name}() 기본값 {value}이 낮음"
                checked += 1
    assert checked == 2, f"build_*_report의 max_tokens 기본값을 찾지 못함 ({checked}/2)"

    # ② main.py가 config를 읽을 때 쓰는 폴백
    tree = ast.parse((_ROOT / "app" / "main.py").read_text(encoding="utf-8"))
    fallbacks = [
        n.args[1].value for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "get" and len(n.args) == 2
        and isinstance(n.args[0], ast.Constant) and n.args[0].value == "max_tokens"
    ]
    assert len(fallbacks) == 2, f"main.py의 max_tokens 폴백이 2건이 아님: {fallbacks}"
    assert all(v >= _MIN_MAX_TOKENS for v in fallbacks), f"main.py 폴백이 낮음: {fallbacks}"
