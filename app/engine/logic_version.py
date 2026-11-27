"""
계산 로직 버전 — 이력·검증 기록이 '어떤 규칙으로' 계산됐는지 남긴다.

왜 필요한가 — 2026-11-25 점검에서 9월 이력은 지금 로직으로 다시 계산한 값과 65~88%만
일치했다(그 사이 수집·계산 코드가 여러 번 고쳐졌다). 그런데 이력에는 어느 로직으로
계산했는지가 없어서, 정확도 점검이 서로 다른 규칙의 결과를 한데 묶어 평가하고 있었다.

두 가지를 남긴다.
  tag          사람이 올리는 표시. 데이터 출처·의미가 바뀔 때 올린다
               (2026-11-27: 저녁 한국 거래량을 KIS 당일 값으로, 지표 창을 90봉으로 고정).
  fingerprint  점수·등급·참고 가격대를 계산하는 코드와 설정에서 자동으로 만든 지문.
               주석·문서 문자열·빈 줄만 바뀌면 그대로다. 클라우드(Python 3.11)와
               로컬(3.12)에서 같은 값이 나오도록 구문 트리 덤프가 아닌 소스 글자로 만든다.

지문 계산이 실패해도 파이프라인을 멈추지 않는다 — 실패 사실을 지문에 섞어 둔다.
대상 함수 이름이 바뀌면 tests/test_logic_version.py가 잡는다.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import logging
import tokenize
from functools import lru_cache
from pathlib import Path

_logger = logging.getLogger(__name__)

LOGIC_TAG = "2026-11-27"

_ROOT = Path(__file__).resolve().parents[2]

# 통째로 지문에 넣는 모듈 — 점수·등급·참고 가격대 규칙
MODULES = (
    "app/engine/signal_scorer.py",
    "app/engine/rating_analyzer.py",
    "app/engine/trade_levels.py",
)
# 수집기에서는 지표를 계산하는 순수 함수만 — 수집·로그 코드가 바뀌어도 지문은 그대로다
COLLECTOR = "app/collectors/price_collector.py"
COLLECTOR_FUNCS = (
    "_sanitize_ohlc", "_last_bars", "_calc_rsi", "_interpret_trend", "_calc_technicals",
    "_calc_volatility", "_classify_candle", "_detect_swing_points", "_cluster_zones",
    "_calc_support_resistance",
)
COLLECTOR_CONSTS = ("HISTORY_BARS",)
# 설정 중 점수에 들어가는 부분
CONFIGS = (
    ("config/user_profile.json", ("rating_thresholds", "signal_weights")),
    ("config/sector_scoring.json", None),
)


def normalize_source(src: str) -> str:
    """주석·문서 문자열·빈 줄·줄 끝 공백을 걷어낸 소스 — 의미가 같으면 같은 글자.

    문서 문자열은 구문 트리에서 줄 위치만 얻고, 주석은 토큰으로 찾는다(문자열 안의
    '#'를 주석으로 오인하지 않는다). 어느 쪽도 파이썬 버전에 따라 결과가 달라지지 않는다.
    """
    drop: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            first = body[0] if body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                drop.update(range(first.lineno, first.end_lineno + 1))
    lines = src.splitlines()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            lines[row - 1] = lines[row - 1][:col]
    kept = (ln.rstrip() for i, ln in enumerate(lines, 1) if i not in drop)
    return "\n".join(ln for ln in kept if ln.strip())


def function_sources(path: Path, names: tuple[str, ...]) -> tuple[str, list[str]]:
    """모듈 최상위 함수들의 정규화된 소스(이름 순서대로)와 찾지 못한 이름."""
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    found: dict[str, str] = {}
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            start = min([d.lineno for d in node.decorator_list] + [node.lineno])
            found[node.name] = normalize_source("\n".join(lines[start - 1:node.end_lineno]))
    missing = [n for n in names if n not in found]
    return "\n".join(found.get(n, f"<없음:{n}>") for n in names), missing


def _constants(path: Path, names: tuple[str, ...]) -> str:
    vals: dict = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in names:
                    try:
                        vals[t.id] = ast.literal_eval(node.value)
                    except ValueError:
                        vals[t.id] = ast.dump(node.value)
    return json.dumps(vals, sort_keys=True)


def _config_text(rel: str, keys: tuple[str, ...] | None) -> str:
    try:
        data = json.loads((_ROOT / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"{rel}:<없음>"
    if keys:
        data = {k: data.get(k) for k in keys}
    return f"{rel}:" + json.dumps(data, sort_keys=True, ensure_ascii=False)


def _part(label: str, fn) -> str:
    try:
        return fn()
    except Exception as e:   # 지문 실패로 리포트를 멈추지 않는다
        _logger.warning("[LOGIC_VERSION] %s 지문 계산 실패: %s", label, e)
        return f"<실패:{label}:{type(e).__name__}>"


@lru_cache(maxsize=1)
def fingerprint() -> str:
    h = hashlib.sha256()
    for rel in MODULES:
        h.update(rel.encode())
        h.update(_part(rel, lambda r=rel: normalize_source((_ROOT / r).read_text(encoding="utf-8"))).encode())
    h.update(_part(COLLECTOR, lambda: function_sources(_ROOT / COLLECTOR, COLLECTOR_FUNCS)[0]).encode())
    h.update(_part("consts", lambda: _constants(_ROOT / COLLECTOR, COLLECTOR_CONSTS)).encode())
    for rel, keys in CONFIGS:
        h.update(_config_text(rel, keys).encode())
    return h.hexdigest()[:12]


def current() -> dict:
    """이력 항목에 남길 값 — {"tag": 사람이 올린 표시, "fingerprint": 자동 지문}."""
    return {"tag": LOGIC_TAG, "fingerprint": fingerprint()}
