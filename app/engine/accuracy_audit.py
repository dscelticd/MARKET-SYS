"""
정확도 점검 — 누적된 등급 이력 전체로 "등급·점수가 실제로 앞날을 맞혔는가"를 잰다

리포트의 등급 적중률은 이 모듈의 결과를 그대로 싣는다(2026-11-25부터). 예전 방식
(history_tracker.compute_accuracy_report, 삭제됨)에는 아래 한계가 있었다 — 같은
데이터로 1거래일 적중률이 예전 방식 69%, 이 방식 47%였다.

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
KIS 시간외 가격이 섞였다(공식 종가와 69.5% 불일치, 2026-11-25 대조). 아침이 없는
날만 저녁을 쓰되, 미국 종목은 두 시장 날짜가 같은 아침 스냅샷끼리만 비교한다 —
한국만 쉰 날 다음 아침은 미국 가격이 하루 뒤 것이다.

저장된 가격은 저장 당시 기준이라, 그 사이 액면분할이 있으면 분할 기록(splits.py)으로
환산한다. 환산하지 않으면 SanDisk 8:1 분할(11/20)이 −89% 수익률로 집계된다.

통계 해석 — 날마다 겹치는 구간으로 비교하므로 h거래일 결과의 표본들은 서로 독립이
아니다. t값을 √h로 나눠 보정하고(adj_t), |adj_t| ≥ 2이고 표본 20 이상일 때만
"의미 있다"고 판정한다. 판정 문장은 여기서 만들어 리포트에 그대로 싣는다 — 생성
모델이 숫자를 보고 해석을 덧붙이지 않게 하기 위해서다.
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import mean, pstdev

from app.utils.market_calendar import is_trading_day, stock_market

HORIZONS = (1, 5, 10, 20)        # 거래일
BULLISH = ("추천", "안전")
BEARISH = ("주의", "위험")
GRADE_ORDER = ("추천", "안전", "보통", "주의", "위험")
LEGACY_TOLERANCE = 2.0           # 기존 방식 비교용
MIN_IC_STOCKS = 5
MIN_SIGNIFICANT_N = 20
SIGNIFICANT_T = 2.0


def _is_unified(e: dict) -> bool:
    us = e.get("us_date") or e.get("data_date")
    return not us or us == e.get("date")


def _rank(e: dict) -> int:
    """같은 날짜의 스냅샷 중 무엇을 쓸지 — 두 시장이 맞춰진 아침 > 아침 > 저녁."""
    if e.get("report_type") == "morning":
        return 2 if _is_unified(e) else 1
    return 0


def _snapshots(entries) -> list[dict]:
    """거래일별 스냅샷 하나씩(아침 우선), 날짜순. entries는 이력 항목들(dict 값 또는 목록)."""
    by_date: dict[str, dict] = {}
    for e in (entries.values() if isinstance(entries, dict) else entries):
        if not e.get("is_trading_day", True) or not e.get("date"):
            continue
        if ((e.get("data_quality") or {}).get("price") or {}).get("mock"):
            continue   # Mock 가격은 무작위라 수익률 비교에 쓰면 결과를 오염시킨다
        cur = by_date.get(e["date"])
        if cur is None or _rank(e) > _rank(cur):
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


def _price(snap: dict, sid: str) -> float | None:
    """스냅샷이 가리키는 거래일의 종가일 때만 돌려준다.

    지연(stale) 종목은 지난 종가가 그날 가격처럼 저장돼 있었다 — 실측 2026-11-25~27
    SanDisk: 11/19 종가가 11/24·11/25 칸에. 종목별 가격 날짜(price_dates)가 있는
    항목(2026-11-27~)은 그 날짜가 시장의 대상일과 같을 때만 쓴다. 없는 옛 항목은 그대로.
    """
    p = (snap.get("closing_prices") or {}).get(sid)
    dates = snap.get("price_dates")
    if not p or not dates:
        return p
    if stock_market(sid) == "KR":
        want = snap.get("kr_date") or snap.get("date")
    else:
        want = snap.get("us_date") or snap.get("date")
    return p if dates.get(sid) == want else None


def _no_split(_sid: str, _after: str, _upto: str) -> float:
    return 1.0


def _pairs(snaps: list[dict], h: int, factor=_no_split):
    """(과거 스냅샷, 정확히 h거래일 뒤 스냅샷, {종목: (원수익률%, 초과수익률%)})

    목록에서 h칸 뒤가 아니라 달력상 h거래일 뒤의 스냅샷을 찾는다. 실행이 빠진
    날이 있으면 h칸 뒤가 몇 주 뒤일 수 있다(로컬 수동 실행 이력에서 흔하다).
    factor(sid, a날짜, b날짜)로 그 사이 액면분할을 환산한다.
    """
    by_date = {s["date"]: s for s in snaps}
    for a in snaps:
        b = by_date.get(_nth_trading_day_after(a["date"], h))
        if b is None:
            continue
        us_ok = (a.get("report_type") == "morning" and b.get("report_type") == "morning"
                 and _is_unified(a) and _is_unified(b))
        raw = {}
        for sid in (a.get("closing_prices") or {}):
            p0, p1 = _price(a, sid), _price(b, sid)
            if not (p0 and p1 and p0 > 0):
                continue
            if stock_market(sid) == "US" and not us_ok:
                continue
            start = p0 / factor(sid, a["date"], b["date"])
            raw[sid] = (p1 / start - 1) * 100
        if not raw:
            continue
        by_mkt: dict[str, list[float]] = {}
        for sid, r in raw.items():
            by_mkt.setdefault(stock_market(sid), []).append(r)
        avg = {m: mean(v) for m, v in by_mkt.items()}
        yield a, b, {sid: (r, r - avg[stock_market(sid)]) for sid, r in raw.items()}


def audit(entries, factor=_no_split) -> dict:
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
        for a, _b, rets in _pairs(snaps, h, factor):
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

        def _ic_stats(v: list[float], horizon: int = h) -> dict | None:
            if not v:
                return None
            sd = pstdev(v) if len(v) > 1 else 0.0
            t = mean(v) / sd * len(v) ** 0.5 if sd else None
            adj = t / horizon ** 0.5 if t is not None else None
            return {"mean": round(mean(v), 3), "n": len(v),
                    "positive_share": round(sum(1 for x in v if x > 0) / len(v) * 100),
                    "t": round(t, 2) if t is not None else None,
                    "adj_t": round(adj, 2) if adj is not None else None,
                    "significant": bool(adj is not None and abs(adj) >= SIGNIFICANT_T
                                        and len(v) >= MIN_SIGNIFICANT_N)}

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


def verdict(result: dict) -> str:
    """점수가 이후 상대 수익률을 예측하는지에 대한 한 문장 판정."""
    pos, neg, judged = [], [], []
    for h, r in result.get("horizons", {}).items():
        ic = r.get("score_ic")
        if not ic or ic["n"] < MIN_SIGNIFICANT_N:
            continue
        judged.append(h)
        if ic["significant"]:
            (pos if ic["mean"] > 0 else neg).append(h)
    if not judged:
        return "아직 판정할 만큼 이력이 쌓이지 않았습니다."
    if pos and not neg:
        return (f"{'·'.join(map(str, pos))}거래일 기준으로 점수가 높은 종목이 이후 상대적으로 더 오른 "
                "관계가 통계적으로 확인됩니다(과거 관측이며 앞으로도 그렇다는 보장은 아닙니다).")
    if neg and not pos:
        return (f"{'·'.join(map(str, neg))}거래일 기준으로 점수가 높은 종목이 이후 오히려 덜 오른 "
                "역방향 관계가 관측됩니다. 등급을 앞날의 방향으로 해석하면 안 됩니다.")
    if pos and neg:
        return "기간에 따라 방향이 엇갈려 일관된 예측력이 확인되지 않습니다."
    return (f"{'·'.join(map(str, judged))}거래일 모두 점수와 이후 상대 수익률의 관계가 0과 구분되지 "
            "않습니다 — 등급·점수가 앞날을 예측한다는 근거는 없습니다.")


def summary_lines(result: dict) -> list[str]:
    """실행 결과 주석용 요약 — 기간별 한 줄."""
    if result["snapshots"] < 2:
        return [f"정확도 점검 — 스냅샷 {result['snapshots']}개, 비교 불가"]
    lines = [f"정확도 점검 — 거래일 스냅샷 {result['snapshots']}개 ({result['first']}~{result['last']})"]
    for h, r in result["horizons"].items():
        if not r["pairs"]:
            continue
        ic = r["score_ic"]
        ic_txt = (f"점수 IC {ic['mean']:+.3f}(양수 {ic['positive_share']}%, 보정 t={ic['adj_t']}, n={ic['n']})"
                  if ic else "점수 IC 표본 부족")
        grades = " ".join(f"{g}{v['avg_excess']:+.2f}%(n{v['n']})" for g, v in r["by_grade"].items())
        hit = (f"강·약세 등급 적중 {r['strict_hit_rate']}% (기존 ±2%p 기준 {r['legacy_hit_rate']}%, n={r['directional_n']})"
               if r["directional_n"] else "강·약세 등급 표본 없음")
        fic = " ".join(f"{f}{v['mean']:+.2f}" for f, v in r["factor_ic"].items() if v)
        lines.append(f"{h}일 뒤 | {ic_txt} | 등급별 초과수익 {grades} | {hit} | 요인 IC {fic}")
    lines.append("판정 — " + verdict(result))
    return lines
