"""
참고 가격대 검증 — 제시한 진입 구간·손절·목표가 실제로 어떻게 됐는지 기록·집계

리포트가 매일 내놓는 가격대가 쓸모 있는지는 숫자로만 판단할 수 있다. 제시한
뒤의 실제 일봉으로 다음을 판정한다.

진입 참고 구간 (최대 20거래일)
  대기 → 그날 저가가 구간 상단 이하로 내려오면 '진입'으로 본다
         (체결가 = 구간 상단, 갭 하락으로 시가가 더 낮으면 시가)
  진입 → 저가가 손절 기준 이하면 '손절 선도달', 고가가 목표1 이상이면 '목표 선도달'
         같은 날 둘 다 닿으면 손절로 친다 — 장중 순서를 모르므로 보수적으로
         진입한 날에는 목표 도달을 인정하지 않는다(진입 전에 고점이었을 수 있다)
  20거래일 안에 진입하지 않으면 '미진입 만료', 진입 후 미결이면 종가로 평가해 '보유 만료'
  결과는 R 배수로 남긴다 — (청산가 − 체결가) ÷ (체결가 − 손절)

청산 점검 신호 (10거래일)
  신호를 낸 날 종가와 10거래일 뒤 종가를 비교 — 더 낮으면 신호가 맞았다고 본다
  **새로 뜬 신호만** 기록한다. 하락장에서는 같은 신호가 매일 이어지는데, 그걸
  날마다 세면 한 번의 하락을 수십 건의 '적중'으로 부풀리게 된다. 리포트에도
  신규와 지속(언제부터)을 나눠 보여준다.

진입 참고 구간도 종목당 열린 기록은 하나만 둔다 — 매일 비슷한 구간을 새로
기록하면 같은 사건이 겹쳐 집계된다. 앞 기록이 결론 나면 다음 구간을 기록한다.

같은 거래일은 한 번만 평가한다. 저녁 결산(한국 D일)과 다음날 아침 브리핑(한국 D일)이
같은 데이터를 보기 때문이다. 기록은 data/history/에 두어 등급 이력과 함께 실행
사이에 이어지고, 시험 실행(dry_run)에서는 저장되지 않는다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = _PROJECT_ROOT / "data" / "history" / "trade_levels.json"

ENTRY_HORIZON = 20
EXIT_HORIZON = 10
MIN_SAMPLES = 20   # 이보다 적으면 통계로 제시하지 않는다


class TradeLevelTracker:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_PATH
        self.records: list[dict] = []
        self.signal_state: dict[str, dict] = {}   # 종목별 직전 청산 신호 — 신규/지속 판별
        self._load()

    # ── 저장 ────────────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.records = list(data.get("records", []))
            self.signal_state = dict(data.get("signal_state", {}))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            _logger.error("[TRADE_LEVEL_LOG_ERROR] 참고 가격대 기록을 읽지 못함 (%s) — 새로 시작", exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 해결된 지 오래된 기록은 정리한다 — 통계에는 최근 기록이면 충분하다
        open_ = [r for r in self.records if r["status"] in ("pending", "active")]
        closed = [r for r in self.records if r["status"] not in ("pending", "active")][-500:]
        self.records = open_ + closed
        self.path.write_text(
            json.dumps({"records": self.records, "signal_state": self.signal_state},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")

    # ── 평가 ────────────────────────────────────────────────────────────────

    def update(self, price_data: dict) -> int:
        """열린 기록을 이번 거래일 일봉으로 평가한다. 평가한 건수를 반환."""
        n = 0
        for r in self.records:
            if r["status"] not in ("pending", "active"):
                continue
            p = price_data.get(r["stock_id"]) or {}
            d = p.get("data_date")
            ohlc = p.get("day_ohlc") or {}
            if not d or not ohlc or d <= r["issued_on"] or d <= (r.get("last_eval") or ""):
                continue
            r["last_eval"] = d
            r["age"] = r.get("age", 0) + 1
            n += 1
            if r["kind"] == "entry":
                self._eval_entry(r, d, ohlc)
            else:
                self._eval_exit(r, d, ohlc)
        return n

    @staticmethod
    def _resolve(r: dict, status: str, d: str, exit_price: float) -> None:
        risk = r["fill_price"] - r["stop"]
        r.update(status=status, resolved_on=d, exit_price=exit_price,
                 r_multiple=round((exit_price - r["fill_price"]) / risk, 2) if risk > 0 else None)

    def _eval_entry(self, r: dict, d: str, o: dict) -> None:
        if r["status"] == "pending":
            if o["low"] <= r["entry_high"]:
                r.update(status="active", filled_on=d,
                         fill_price=min(r["entry_high"], o["open"]))
                if o["low"] <= r["stop"]:                      # 진입한 날 바로 손절
                    self._resolve(r, "loss", d, min(r["stop"], o["open"]))
                return
            if r["age"] >= ENTRY_HORIZON:
                r.update(status="expired_unfilled", resolved_on=d)
            return
        # active
        if o["low"] <= r["stop"]:
            self._resolve(r, "loss", d, min(r["stop"], o["open"]))
        elif o["high"] >= r["target1"]:
            self._resolve(r, "win", d, max(r["target1"], o["open"]))
        elif r["age"] >= ENTRY_HORIZON:
            self._resolve(r, "expired_open", d, o["close"])

    @staticmethod
    def _eval_exit(r: dict, d: str, o: dict) -> None:
        if r["age"] >= EXIT_HORIZON:
            chg = round((o["close"] / r["price_at_signal"] - 1) * 100, 2)
            r.update(status="exit_hit" if chg < 0 else "exit_miss",
                     resolved_on=d, change_pct=chg)

    # ── 신규/지속 판별 ──────────────────────────────────────────────────────

    def annotate_signals(self, levels: dict[str, dict]) -> None:
        """청산 점검 결과에 신규 신호와 지속 신호(시작일)를 표시한다.

        같은 거래일을 두 번 보는 경우(저녁 결산 → 다음날 아침 브리핑)에는 그 거래일
        이전 상태와 비교해야 한다 — 그렇지 않으면 아침에는 전부 '지속'으로 보인다.
        """
        for sid, lv in levels.items():
            if lv.get("kind") != "exit":
                continue
            d = lv.get("data_date")
            keys = lv.get("signal_keys") or []
            st = self.signal_state.get(sid)
            if st and st.get("date") == d:
                prev, prev_since = st.get("prev", []), st.get("prev_since", {})
            elif st:
                prev, prev_since = st.get("keys", []), st.get("since", {})
            else:
                # 처음 보는 종목 — 지금 뜬 신호가 오늘 새로 뜬 것인지 몇 주째인지 모른다.
                # 신규로 치면 첫 실행에 이미 진행 중인 하락을 '신호 적중'으로 세게 된다.
                # 시작일 미상의 지속으로 두고 기록하지 않는다.
                prev, prev_since = list(keys), {}
            # 이어지는 신호는 원래 시작일을 물려받는다(처음 볼 때부터 있던 신호는 None = 미상)
            since = {k: (prev_since.get(k) if k in prev else d) for k in keys}
            lv["new_signal_keys"] = [k for k in keys if k not in prev]
            lv["signal_since"] = since
            self.signal_state[sid] = {"date": d, "keys": keys, "since": since,
                                      "prev": prev, "prev_since": prev_since}

    # ── 발행 ────────────────────────────────────────────────────────────────

    def issue(self, levels: dict[str, dict]) -> int:
        """이번에 제시한 가격대를 기록한다. 같은 종목·같은 거래일은 한 번만,
        진입 구간은 종목당 열린 기록 하나만, 청산 신호는 새로 뜬 것만."""
        seen = {(r["stock_id"], r["kind"], r["issued_on"]) for r in self.records}
        open_entry = {r["stock_id"] for r in self.records
                      if r["kind"] == "entry" and r["status"] in ("pending", "active")}
        n = 0
        for sid, lv in levels.items():
            d = lv.get("data_date")
            if not d or (sid, lv["kind"], d) in seen:
                continue
            if lv["kind"] == "entry" and lv.get("available") and sid not in open_entry:
                self.records.append({
                    "stock_id": sid, "name": lv.get("name"), "kind": "entry", "issued_on": d,
                    "entry_low": lv["entry_low"], "entry_high": lv["entry_high"],
                    "stop": lv["stop"], "target1": lv["targets"][0],
                    "risk_reward": lv.get("risk_reward"), "status": "pending", "age": 0,
                })
                n += 1
            elif lv["kind"] == "exit":
                keys = lv.get("signal_keys") or list(lv.get("signals", []))   # 종류가 없으면 문구로
                new = set(lv.get("new_signal_keys", keys))   # 판별 전이면 전부 신규로 본다
                fresh = [t for k, t in zip(keys, lv.get("signals", [])) if k in new]
                if not fresh:
                    continue
                self.records.append({
                    "stock_id": sid, "name": lv.get("name"), "kind": "exit", "issued_on": d,
                    "price_at_signal": lv["price"], "signals": fresh,
                    "status": "pending", "age": 0,
                })
                n += 1
        return n

    # ── 집계 ────────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        e = [r for r in self.records if r["kind"] == "entry"]
        wins = [r for r in e if r["status"] == "win"]
        losses = [r for r in e if r["status"] == "loss"]
        decided = wins + losses
        rs = [r["r_multiple"] for r in decided + [r for r in e if r["status"] == "expired_open"]
              if r.get("r_multiple") is not None]
        x = [r for r in self.records if r["kind"] == "exit"]
        x_done = [r for r in x if r["status"] in ("exit_hit", "exit_miss")]
        return {
            "entry": {
                "decided": len(decided), "wins": len(wins), "losses": len(losses),
                "win_rate": round(len(wins) / len(decided) * 100) if decided else None,
                "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
                "pending": sum(1 for r in e if r["status"] == "pending"),
                "active": sum(1 for r in e if r["status"] == "active"),
                "unfilled": sum(1 for r in e if r["status"] == "expired_unfilled"),
                "enough": len(decided) >= MIN_SAMPLES,
            },
            "exit": {
                "decided": len(x_done),
                "hits": sum(1 for r in x_done if r["status"] == "exit_hit"),
                "pending": sum(1 for r in x if r["status"] == "pending"),
                "enough": len(x_done) >= MIN_SAMPLES,
            },
        }
