"""
정확도 점검 — 누적된 등급 이력 전체로 "등급·점수가 실제로 앞날을 맞혔는가"를 다시 잰다

리포트에 싣는 기존 적중률(history_tracker.compute_accuracy_report)에는 구조적인
한계가 있어, 이 모듈은 그것과 별개로 더 엄격한 기준으로 같은 이력을 재집계한다.

  ① 허용오차 ±2%p — 강세 등급은 −2%까지 떨어져도 '적중', 약세 등급은 +2%까지
     올라도 '적중'이다. 며칠 사이 등락은 대부분 ±2% 안이라 어느 등급이든 적중으로
     집계된다. → 여기서는 허용오차 없이 방향만 본다.
  ② 시장 전체 흐름 — 상승장에서는 모든 강세 등급이 '적중'한다. 등급이 아니라
     장세를 맞힌 것이다. → 같은 시장 관심종목 평균 대비 **초과수익**으로 판정한다.
  ③ 스냅샷 하나 — 기존 방식은 "N일 전 가장 가까운 스냅샷 하나"만 본다. 표본이
     그날의 강세·약세 등급 종목 수(몇 개)뿐이라 날마다 크게 출렁인다.
     → 이력의 모든 거래일 쌍을 모은다.
  ④ 순위 정보 — 등급은 다섯 칸뿐이고 대부분 '보통'이다. → 총점 순위와 이후 초과
     수익 순위의 상관(정보계수, IC)으로 점수 자체의 예측력을 잰다.

스냅샷은 거래일별 **아침 브리핑**을 쓴다. 아침 스냅샷은 한국·미국이 같은 거래일
종가로 맞춰져 있고(계약 C4), 저녁 스냅샷의 국내 종가는 2026-09-19~10-19 사이
KIS 시간외 가격이 섞였을 수 있다(ea62a18). 아침이 없는 날만 저녁을 쓴다.
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import mean, pstdev

from app.utils.market_calendar import is_trading_day, stock_market

HORIZONS = (1, 5, 10)            # 거래일
BULLISH = ("추천", "안전")
BEARISH = ("주의", "위험")
GRADE_ORDER = ("추천", "안전", "보통", "주의", "위험")
LEGACY_TOLERANCE = 2.0           # 기존 방식 비교용
MIN_IC_STOCKS = 5


def _snapshots(entries) -> list[dict]:
    """거래일별 스냅샷 하나씩(아침 우선), 날짜순. entries는 이력 항목들(dict 값 또는 목록)."""
    by_date: dict[str, dict] = {}
    for e in (entries.values() if isinstance(entries, dict) else entries):
        if not e.get("is_trading_day", True) or not e.get("date"):
            continue
        if ((e.get("data_quality") or {}).get("price") or {}).get("mock"):
            continue   # Mock 가격은 무작위라 수익률 비교에 쓰면 결과를 오염시킨다
        cur = by_date.get(e["date"])
        if cur is None or (cur.get("report_type") != "morning" and e.get("report_type") == "morning"):
            by_date[e["date"]] = e
    return [by_date[d] for d in sorted(by_date)]


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = mean(ra), mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


def _nth_trading_day_after(d: str, n: int) -> str:
    cur = date.fromisoformat(d)
    left = n
    while left:
        cur += timedelta(days=1)
        if is_trading_day(cur, "KR"):
            left -= 1
    return cur.isoformat()


def _pairs(snaps: list[dict], h: int):
    """(과거 스냅샷, 정확히 h거래일 뒤 스냅샷, {종목: (원수익률%, 초과수익률%)})

    목록에서 h칸 뒤가 아니라 달력상 h거래일 뒤의 스냅샷을 찾는다. 실행이 빠진
    날이 있으면 h칸 뒤가 몇 주 뒤일 수 있다(로컬 수동 실행 이력에서 흔하다).
    """
    by_date = {s["date"]: s for s in snaps}
    for a in snaps:
        b = by_date.get(_nth_trading_day_after(a["date"], h))
        if b is None:
            continue
        pa, pb = a.get("closing_prices") or {}, b.get("closing_prices") or {}
        raw = {sid: (pb[sid] / pa[sid] - 1) * 100
               for sid in pa if pa.get(sid) and pb.get(sid) and pa[sid] > 0}
        if not raw:
            continue
        by_mkt: dict[str, list[float]] = {}
        for sid, r in raw.items():
            by_mkt.setdefault(stock_market(sid), []).append(r)
        avg = {m: mean(v) for m, v in by_mkt.items()}
        yield a, b, {sid: (r, r - avg[stock_market(sid)]) for sid, r in raw.items()}


def audit(entries) -> dict:
    snaps = _snapshots(entries)
    out: dict = {
        "snapshots": len(snaps),
        "first": snaps[0]["date"] if snaps else None,
        "last": snaps[-1]["date"] if snaps else None,
        "horizons": {},
    }
    for h in HORIZONS:
        grade_ex: dict[str, list[float]] = {g: [] for g in GRADE_ORDER}
        strict_hits, legacy_hits, directional = 0, 0, 0
        ics: list[float] = []
        factor_ics: dict[str, list[float]] = {}
        n_pairs = 0
        for a, _b, rets in _pairs(snaps, h):
            n_pairs += 1
            grades = a.get("grades") or {}
            for sid, (raw, ex) in rets.items():
                g = grades.get(sid)
                if g in grade_ex:
                    grade_ex[g].append(ex)
                if g in BULLISH or g in BEARISH:
                    directional += 1
                    bull = g in BULLISH
                    strict_hits += int(ex > 0 if bull else ex < 0)
                    legacy_hits += int(raw >= -LEGACY_TOLERANCE if bull else raw <= LEGACY_TOLERANCE)
            scores = a.get("scores") or {}
            common = [sid for sid in rets if scores.get(sid) is not None]
            if len(common) >= MIN_IC_STOCKS:
                ic = spearman([scores[s] for s in common], [rets[s][1] for s in common])
                if ic is not None:
                    ics.append(ic)
            comps = a.get("components") or {}
            factors = {f for c in comps.values() for f in (c or {})}
            for f in factors:
                ids = [s for s in rets if (comps.get(s) or {}).get(f) is not None]
                if len(ids) >= MIN_IC_STOCKS:
                    ic = spearman([float(comps[s][f]) for s in ids], [rets[s][1] for s in ids])
                    if ic is not None:
                        factor_ics.setdefault(f, []).append(ic)

        def _ic_stats(v: list[float]) -> dict | None:
            if not v:
                return None
            sd = pstdev(v) if len(v) > 1 else 0.0
            return {"mean": round(mean(v), 3), "n": len(v),
                    "positive_share": round(sum(1 for x in v if x > 0) / len(v) * 100),
                    "t": round(mean(v) / sd * len(v) ** 0.5, 2) if sd else None}

        out["horizons"][h] = {
            "pairs": n_pairs,
            "by_grade": {g: {"n": len(v), "avg_excess": round(mean(v), 2)}
                         for g, v in grade_ex.items() if v},
            "directional_n": directional,
            "strict_hit_rate": round(strict_hits / directional * 100) if directional else None,
            "legacy_hit_rate": round(legacy_hits / directional * 100) if directional else None,
            "score_ic": _ic_stats(ics),
            "factor_ic": {f: _ic_stats(v) for f, v in sorted(factor_ics.items())},
        }
    return out


def summary_lines(result: dict) -> list[str]:
    """실행 결과 주석용 요약 — 기간별 한 줄."""
    if result["snapshots"] < 2:
        return [f"정확도 점검 — 스냅샷 {result['snapshots']}개, 비교 불가"]
    lines = [f"정확도 점검 — 거래일 스냅샷 {result['snapshots']}개 ({result['first']}~{result['last']})"]
    for h, r in result["horizons"].items():
        if not r["pairs"]:
            continue
        ic = r["score_ic"]
        ic_txt = (f"점수 IC {ic['mean']:+.3f}(양수 {ic['positive_share']}%, t={ic['t']}, n={ic['n']})"
                  if ic else "점수 IC 표본 부족")
        grades = " ".join(f"{g}{v['avg_excess']:+.2f}%(n{v['n']})" for g, v in r["by_grade"].items())
        hit = (f"강·약세 등급 적중 {r['strict_hit_rate']}% (기존 ±2%p 기준 {r['legacy_hit_rate']}%, n={r['directional_n']})"
               if r["directional_n"] else "강·약세 등급 표본 없음")
        fic = " ".join(f"{f}{v['mean']:+.2f}" for f, v in r["factor_ic"].items() if v)
        lines.append(f"{h}일 뒤 | {ic_txt} | 등급별 초과수익 {grades} | {hit} | 요인 IC {fic}")
    return lines
