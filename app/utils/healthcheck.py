"""
Healthcheck — 시스템 의존성 및 환경변수 검증
python -m app.utils.healthcheck  으로 단독 실행 가능
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


_MIN_VERSIONS: dict[str, tuple[int, ...]] = {
    "anthropic":    (0, 40, 0),
    "yfinance":     (1,  4, 0),
    "pydantic":     (2,  9, 0),
    "dotenv":       (1,  0, 0),  # python-dotenv → import dotenv
    "jinja2":       (3,  1, 0),
    "rich":        (13,  9, 0),
}

_REQUIRED_ENV = [
    "ANTHROPIC_API_KEY",
    "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO",
]
_OPTIONAL_ENV = [
    "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET",
    "CLAUDE_MODEL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
]


def _parse_ver(ver_str: str) -> tuple[int, ...]:
    parts = []
    for p in ver_str.split(".")[:3]:
        try:
            parts.append(int("".join(c for c in p if c.isdigit()) or "0"))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def check_packages() -> list[dict]:
    results = []
    for pkg, min_ver in _MIN_VERSIONS.items():
        try:
            mod = importlib.import_module(pkg)
            ver_str = getattr(mod, "__version__", "0.0.0")
            actual  = _parse_ver(ver_str)
            ok      = actual >= min_ver
            results.append({
                "package": pkg,
                "status":  "OK" if ok else "WARN",
                "version": ver_str,
                "min_required": ".".join(str(v) for v in min_ver),
                "message": "" if ok else f"{ver_str} < 최소 {'.'.join(str(v) for v in min_ver)}",
            })
        except ImportError:
            results.append({
                "package": pkg,
                "status":  "FAIL",
                "version": "미설치",
                "min_required": ".".join(str(v) for v in min_ver),
                "message": f"pip install {pkg}>={'.'.join(str(v) for v in min_ver)}",
            })
    return results


def check_env() -> list[dict]:
    results = []
    for key in _REQUIRED_ENV:
        val = os.getenv(key, "")
        results.append({
            "key":      key,
            "status":   "OK" if val else "FAIL",
            "required": True,
            "message":  "" if val else ".env에 설정 필요",
        })
    for key in _OPTIONAL_ENV:
        val = os.getenv(key, "")
        results.append({
            "key":      key,
            "status":   "OK" if val else "INFO",
            "required": False,
            "message":  "" if val else "미설정 (선택 사항)",
        })
    return results


def check_dirs() -> list[dict]:
    project_root = Path(__file__).resolve().parents[2]
    dirs = ["data/reports", "data/history", "data/logs", "data/cache", "config"]
    results = []
    for d in dirs:
        p = project_root / d
        results.append({
            "path":   str(p.relative_to(project_root)),
            "status": "OK" if p.exists() else "WARN",
            "message": "" if p.exists() else "mkdir 필요",
        })
    return results


def run_all(verbose: bool = True) -> bool:
    """전체 체크 실행. 실패 항목 있으면 False 반환"""
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")

    ok = True
    pkg_results = check_packages()
    env_results = check_env()
    dir_results = check_dirs()

    if verbose:
        print("\n[패키지 버전 검증]")
        for r in pkg_results:
            icon = "✅" if r["status"] == "OK" else ("⚠️ " if r["status"] == "WARN" else "❌")
            msg  = f"  {r['message']}" if r["message"] else ""
            print(f"  {icon} {r['package']:12s} {r['version']:12s}{msg}")

        print("\n[환경변수 검증]")
        for r in env_results:
            icon = "✅" if r["status"] == "OK" else ("ℹ️ " if r["status"] == "INFO" else "❌")
            msg  = f"  {r['message']}" if r["message"] else ""
            print(f"  {icon} {r['key']:30s}{msg}")

        print("\n[디렉토리 검증]")
        for r in dir_results:
            icon = "✅" if r["status"] == "OK" else "⚠️ "
            msg  = f"  {r['message']}" if r["message"] else ""
            print(f"  {icon} {r['path']:25s}{msg}")

    failures = [r for r in pkg_results  if r["status"] == "FAIL"]
    failures += [r for r in env_results if r["status"] == "FAIL"]
    if failures:
        ok = False
        if verbose:
            print(f"\n❌ 실패 항목 {len(failures)}개 — 위 항목 확인 후 재실행")
    else:
        if verbose:
            print("\n✅ 모든 필수 항목 통과")
    return ok


if __name__ == "__main__":
    success = run_all(verbose=True)
    sys.exit(0 if success else 1)
