"""
Portfolio Analyzer — 종목 단위가 아닌 워치리스트 전체 관점의 집중도·동조화 진단

지금까지 시스템은 18개 종목을 각각 독립적으로 평가할 뿐, "이 워치리스트가 특정
테마에 얼마나 쏠려 있는가", "오늘 종목들이 얼마나 같은 방향으로 동시에 움직였는가"
같은 포트폴리오 관점의 진단이 없었다. 실제 통계적 상관계수(historical correlation
matrix)는 종목마다 추가 과거 시세 수집이 필요해 비용·지연이 커서, 대신 이미
수집된 데이터만으로 계산 가능한 두 지표로 근사한다:
  1. 테마/섹터 집중도 — 워치리스트 구성 자체의 쏠림 정도 (정적, 매번 거의 동일)
  2. 당일 동조화율 — 오늘 종목들이 같은 방향으로 얼마나 몰려 움직였는지 (동적)
"""
from __future__ import annotations

_CONCENTRATION_RISK_THRESHOLD_PCT = 40.0  # 이 비중을 넘는 테마/섹터는 집중 리스크로 표시


def compute_theme_concentration(stocks: list[dict]) -> list[dict]:
    """테마별 노출 종목 수·비중(%) — 워치리스트 구성 자체의 테마 쏠림 진단.
    한 종목이 여러 테마에 속할 수 있어 비중 합이 100%를 넘을 수 있음(정상).
    """
    total = len(stocks)
    if total == 0:
        return []

    grouped: dict[str, list[str]] = {}
    for s in stocks:
        for theme in s.get("themes", []):
            grouped.setdefault(theme, []).append(s.get("name", s.get("id", "")))

    result = [
        {"theme": theme, "count": len(names), "pct": round(len(names) / total * 100, 1), "stocks": names}
        for theme, names in grouped.items()
    ]
    return sorted(result, key=lambda r: -r["count"])


def compute_sector_concentration(stocks: list[dict]) -> list[dict]:
    """섹터별 노출 종목 수·비중(%) — 종목당 섹터는 1개뿐이라 비중 합은 항상 100%."""
    total = len(stocks)
    if total == 0:
        return []

    grouped: dict[str, list[str]] = {}
    for s in stocks:
        sector = s.get("sector", "미분류")
        grouped.setdefault(sector, []).append(s.get("name", s.get("id", "")))

    result = [
        {"sector": sector, "count": len(names), "pct": round(len(names) / total * 100, 1), "stocks": names}
        for sector, names in grouped.items()
    ]
    return sorted(result, key=lambda r: -r["count"])


def compute_directional_alignment(price_data: dict) -> dict:
    """당일 종목들이 얼마나 같은 방향으로 동시에 움직였는지 진단하는 참고 지표.
    통계적 상관계수가 아니라 "오늘 하루" 동조화 정도를 보여주는 단순 집계이며,
    포트폴리오의 실제 분산 효과를 엄밀하게 측정하지는 않는다.
    """
    changes = [d.get("change_pct") for d in price_data.values() if d.get("change_pct") is not None]
    if not changes:
        return {}

    up   = sum(1 for c in changes if c > 0)
    down = sum(1 for c in changes if c < 0)
    flat = len(changes) - up - down
    total = len(changes)
    majority = max(up, down)

    return {
        "total": total, "up": up, "down": down, "flat": flat,
        "alignment_pct": round(majority / total * 100, 1) if total else None,
        "majority_direction": "상승" if up >= down else "하락",
    }


def build_portfolio_summary(stocks: list[dict], price_data: dict) -> dict:
    """리포트/대시보드에서 바로 쓸 수 있는 포트폴리오 관점 요약.
    risk_flags: 임계값(기본 40%)을 넘는 테마·섹터 집중 경고 문구 목록.
    """
    theme_conc  = compute_theme_concentration(stocks)
    sector_conc = compute_sector_concentration(stocks)
    alignment   = compute_directional_alignment(price_data)

    risk_flags = []
    for t in theme_conc:
        if t["pct"] >= _CONCENTRATION_RISK_THRESHOLD_PCT:
            risk_flags.append(
                f"테마 '{t['theme']}' 집중도 {t['pct']}% ({t['count']}/{len(stocks)}종목) — "
                f"해당 테마 사이클이 꺾이면 워치리스트 상당 부분이 동시에 영향받을 수 있음"
            )
    for s in sector_conc:
        if s["pct"] >= _CONCENTRATION_RISK_THRESHOLD_PCT:
            risk_flags.append(
                f"섹터 '{s['sector']}' 집중도 {s['pct']}% ({s['count']}/{len(stocks)}종목)"
            )

    return {
        "theme_concentration":  theme_conc,
        "sector_concentration": sector_conc,
        "directional_alignment": alignment,
        "risk_flags": risk_flags,
    }
