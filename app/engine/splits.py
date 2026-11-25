"""
액면분할 기록 — 실행 사이에 저장된 가격을 분할 후 기준으로 환산한다

한 번의 실행 안에서는 문제가 없다. yfinance·KIS 일봉은 받을 때마다 분할을 반영한
수정주가로 오기 때문이다. 문제는 **실행 사이에 저장해 둔 가격**이다 — 등급 이력의
종가, 참고 가격대의 진입 구간·손절·목표는 저장 당시 기준이라 분할 뒤에는 단위가
달라진다.

실측(2026-11-20 SanDisk 8:1 분할):
  - 정확도 점검이 분할 전 755달러와 분할 후 79달러를 비교해 −89% 수익률로 집계
  - 참고 가격대 검증이 859달러 진입 기록을 분할 후 89.91달러에 '손절'로 판정(−4.6R)

분할 일자는 첫 분할 후 거래일이다(yfinance 'Stock Splits' 열 기준). 그날 이전 가격을
그날 이후 단위로 바꾸려면 비율로 나눈다. 기록은 data/history/에 두어 등급 이력과
함께 실행 사이에 이어지고, 90일 일봉 창에 들어온 분할을 매 실행 쌓는다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = _PROJECT_ROOT / "data" / "history" / "splits.json"


class SplitRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_PATH
        self.events: dict[str, list[dict]] = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.events = {k: list(v) for k, v in (data.get("events") or {}).items()}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            _logger.error("[SPLIT_REGISTRY_ERROR] 분할 기록을 읽지 못함 (%s) — 새로 시작", exc)

    def update(self, price_data: dict) -> list[tuple[str, str, float]]:
        """이번 수집에서 보인 분할을 기록한다. 새로 알게 된 것만 반환."""
        new = []
        for sid, p in (price_data or {}).items():
            for ev in (p or {}).get("splits") or []:
                d, ratio = ev.get("date"), ev.get("ratio")
                if not d or not ratio:
                    continue
                known = self.events.setdefault(sid, [])
                if any(e["date"] == d for e in known):
                    continue
                known.append({"date": d, "ratio": float(ratio)})
                known.sort(key=lambda e: e["date"])
                new.append((sid, d, float(ratio)))
        return new

    def factor(self, sid: str, after: str, upto: str) -> float:
        """after 날짜의 가격을 upto 날짜 단위로 바꾸려면 나눌 값.
        after < 분할일 <= upto 인 분할 비율의 곱. 분할이 없으면 1.0."""
        f = 1.0
        for e in self.events.get(sid, []):
            if after < e["date"] <= upto:
                f *= e["ratio"]
        return f

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"events": self.events}, ensure_ascii=False, indent=2),
                             encoding="utf-8")
