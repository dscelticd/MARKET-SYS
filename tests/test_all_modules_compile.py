"""
모든 앱 모듈이 문법적으로 올바른지 — 불러오는 테스트가 없는 모듈의 오류도 잡는다

실측(2026-10-27): telegram_notifier.py의 문자열이 줄바꿈으로 깨진 채 전체 테스트가
통과했다. 그 모듈을 직접 import하는 테스트가 없었기 때문이다. 운영에서는 파이프라인이
시작하자마자 멈췄을 것이다.
"""
from __future__ import annotations

import py_compile
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parents[1] / "app"


@pytest.mark.parametrize("path", sorted(_APP.rglob("*.py")), ids=lambda p: str(p.relative_to(_APP)))
def test_module_compiles(path):
    py_compile.compile(str(path), doraise=True)
