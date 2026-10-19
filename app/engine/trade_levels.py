"""
참고 가격대 — 관찰 종목의 진입 참고 구간, 보유 종목의 청산 점검 신호

이 모듈은 매매를 지시하지 않는다. 이미 계산 중인 지지·저항 구간과 변동성(ATR)을
구체적인 가격으로 정리할 뿐이다. 표기는 "참고 진입 구간 / 손절 기준 / 목표 구간 /
청산 점검 신호"로 하고, "추천·매수하라·매도하라"는 쓰지 않는다(금지 표현 규칙과
같은 원칙). 제시한 가격이 실제로 맞았는지는 trade_level_tracker가 매번 기록·집계해
리포트에 함께 싣는다 — 검증 없이 가격만 내놓으면 그럴듯한 숫자를 매일 만들어낼 뿐이다.

진입 참고 구간 (관찰·예정 종목)
  구간   = 현재가 아래 가장 가까운 지지 구간(스윙 저점 클러스터), 최소 폭 ATR × 0.5
  손절   = 구간 하단 − ATR(14) × 1     — 지지가 깨졌다고 볼 수 있는 자리
  목표   = 위쪽 저항 구간 1·2          — 저항이 없으면 손절폭의 2배(따로 표시)
  손익비 = (목표1 − 구간 중앙) ÷ (구간 중앙 − 손절)  — 2.0 미만이면 근거 약함

청산 점검 (보유 종목)
  추적 손절선 = 최근 22거래일 고가 − ATR(14) × 3   (샹들리에 방식 — 주가가 오르면 함께 오른다)
  점검 신호   = 추적 손절선 이탈 · 20일선 아래 마감 · 저항 구간 1% 이내 도달 ·
               RSI 70 이상에서 음봉 · 등급 하락
"""
from __future__ import annotations

ENTRY_STOP_ATR = 1.0
MIN_BAND_ATR = 0.5     # 스윙 저점 하나로 된 지지 구간은 폭이 0이다 — 최소 폭을 준다
TRAIL_ATR = 3.0
RR_BAR = 2.0
NEAR_RESISTANCE_PCT = 1.0
RSI_HOT = 70.0


# ── 가격 단위 ────────────────────────────────────────────────────────────────

def _krx_tick(price: float, is_etf: bool) -> float:
    """KRX 호가 단위(2023년 이후 코스피·코스닥 공통). ETF는 5원."""
    if is_etf:
        return 5
    for limit, tick in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50),
                        (200_000, 100), (500_000, 500)):
        if price < limit:
            return tick
    return 1_000


def round_price(price: float, currency: str, is_etf: bool = False) -> float:
    """실제로 주문 가능한 가격 단위로 맞춘다 — 1원 단위 국내 가격은 읽기도 어렵다."""
    if currency == "KRW":
        tick = _krx_tick(price, is_etf)
        return float(round(price / tick) * tick)
    return round(price, 2)


# ── 진입 참고 구간 ───────────────────────────────────────────────────────────

def compute_entry_levels(p: dict, is_etf: bool = False) -> dict:
    """관찰 종목의 진입 참고 구간. 산출할 수 없으면 available=False와 이유."""
    price = p.get("price")
    tech = p.get("technical") or {}
    atr = tech.get("atr14")
    sr = p.get("support_resistance") or {}
    supports = sr.get("support_zones") or []
    resistances = sr.get("resistance_zones") or []
    cur = p.get("currency", "USD")

    if not price or not atr:
        return {"kind": "entry", "available": False, "reason": "변동성(ATR) 계산에 필요한 이력 부족"}
    if not supports:
        return {"kind": "entry", "available": False,
                "reason": "현재가 아래 지지 구간이 형성되지 않음 (신고가 부근 등)"}

    z = supports[0]
    lo = round_price(z["low"], cur, is_etf)
    hi = round_price(z["high"], cur, is_etf)
    if hi - lo < atr * MIN_BAND_ATR:
        # "148,000원 ~ 148,000원" 같은 한 점 구간은 쓸모가 없다
        hi = round_price(min(lo + atr * MIN_BAND_ATR, price), cur, is_etf)
    stop = round_price(z["low"] - atr * ENTRY_STOP_ATR, cur, is_etf)
    mid = (lo + hi) / 2
    risk = mid - stop
    if risk <= 0:
        return {"kind": "entry", "available": False, "reason": "손절폭이 0 이하로 계산됨"}

    targets = [round_price(r["low"], cur, is_etf) for r in resistances[:2] if r["low"] > hi]
    target_basis = "저항 구간"
    if not targets:
        targets = [round_price(mid + risk * RR_BAR, cur, is_etf)]
        target_basis = "손절폭 2배 (위쪽 저항 미형성)"

    rr = round((targets[0] - mid) / risk, 2)
    return {
        "kind": "entry", "available": True,
        "entry_low": lo, "entry_high": hi, "stop": stop,
        "targets": targets, "target_basis": target_basis,
        "risk_reward": rr, "meets_bar": rr >= RR_BAR,
        "distance_pct": round((hi - price) / price * 100, 1),   # 음수 = 현재가보다 아래
    }


# ── 청산 점검 ────────────────────────────────────────────────────────────────

def compute_exit_signals(
    p: dict, avg_cost: float | None = None, grade_change: dict | None = None,
    is_etf: bool = False,
) -> dict:
    price = p.get("price")
    tech = p.get("technical") or {}
    atr = tech.get("atr14")
    high_22 = tech.get("high_22d")
    cur = p.get("currency", "USD")
    sr = p.get("support_resistance") or {}
    candle = p.get("candle_pattern") or {}

    trail = None
    if atr and high_22:
        trail = round_price(high_22 - atr * TRAIL_ATR, cur, is_etf)

    # (종류, 문구) — 종류로 "어제도 떴던 신호인가"를 가른다(문구의 숫자는 매일 바뀐다)
    found: list[tuple[str, str]] = []
    if trail is not None and price is not None and price <= trail:
        found.append(("trail", f"추적 손절선({_fmt(trail, cur)}) 이탈"))
    ma20 = tech.get("ma20")
    if ma20 and price is not None and price < ma20:
        found.append(("ma20", f"20일선({_fmt(round_price(ma20, cur, is_etf), cur)}) 아래 마감"))
    near = sr.get("nearest_resistance_pct")
    if near is not None and near <= NEAR_RESISTANCE_PCT:
        found.append(("resistance", f"저항 구간 도달(남은 여력 {near:+.1f}%)"))
    rsi = tech.get("rsi_14")
    if rsi is not None and rsi >= RSI_HOT and candle.get("direction") == "음봉":
        found.append(("rsi_hot", f"RSI 과열({rsi:.0f})에서 음봉"))
    if grade_change and grade_change.get("direction") == "하락" \
            and "판단보류" not in (grade_change.get("prev_grade"), grade_change.get("curr_grade")):
        found.append(("grade", f"등급 하락({grade_change['prev_grade']}→{grade_change['curr_grade']})"))
    signals = [t for _k, t in found]

    out = {
        "kind": "exit", "available": trail is not None or bool(signals),
        "trailing_stop": trail, "signals": signals,
        "signal_keys": [k for k, _t in found],
        "state": "점검 필요" if signals else "유지 조건 충족",
    }
    if avg_cost and price:
        out["avg_cost"] = avg_cost
        out["pnl_pct"] = round((price / avg_cost - 1) * 100, 1)
        if trail is not None:
            out["trail_vs_cost_pct"] = round((trail / avg_cost - 1) * 100, 1)
    return out


# ── 종목 전체 ────────────────────────────────────────────────────────────────

def build_trade_levels(
    stocks: list[dict], price_data: dict, changes: list[dict] | None,
    holdings: dict | None, target: dict,
) -> dict[str, dict]:
    """상태별로 나눠 산출한다 — 관찰·예정은 진입, 보유는 청산, 매도완료는 없음.

    대상 거래일 데이터가 아닌(지연) 종목은 산출하지 않는다. 지난 가격으로 만든
    가격대를 오늘 것처럼 내놓으면 결측·지연을 구분해 온 원칙이 무너진다.
    """
    change_by_id = {c.get("stock_id"): c for c in (changes or [])}
    out: dict[str, dict] = {}
    for s in stocks:
        sid = s["id"]
        p = price_data.get(sid)
        if not p or p.get("_mock"):
            continue
        market_target = target.get("kr_date" if sid.startswith("KR") else "us_date")
        if p.get("data_date") != market_target:
            continue
        is_etf = s.get("sector") == "ETF"
        status = s.get("status", "관찰")
        if status == "보유":
            avg = ((holdings or {}).get(sid) or {}).get("avg_cost")
            lv = compute_exit_signals(p, avg, change_by_id.get(sid), is_etf)
        elif status in ("관찰", "예정"):
            lv = compute_entry_levels(p, is_etf)
        else:
            continue
        lv.update({"name": s.get("name", sid), "price": p.get("price"),
                   "currency": p.get("currency"), "data_date": p.get("data_date")})
        out[sid] = lv
    return out


def _fmt(v: float, cur: str) -> str:
    return f"{v:,.0f}원" if cur == "KRW" else f"${v:,.2f}"
