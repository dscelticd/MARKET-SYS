"""
History Tracker — 날짜별 등급 기록 저장 및 전일 대비 변화 추적
data/history/ratings_history.json 에 누적 저장
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HISTORY_DIR  = _PROJECT_ROOT / "data" / "history"
_HISTORY_FILE = _HISTORY_DIR / "ratings_history.json"

GRADE_ORDER = {"추천": 5, "안전": 4, "보통": 3, "주의": 2, "위험": 1, "판단보류": 0}

CHANGE_EMOJI = {
    "상승": "📈",
    "하락": "📉",
    "유지": "➡️",
    "신규": "🆕",
}


class HistoryTracker:
    """
    save_today(ratings, report_type) → 오늘 등급 저장
    get_changes(ratings, report_type) → 전일 대비 변화 목록 반환
    get_history(stock_id, days)       → 특정 종목의 최근 N일 등급 이력
    """

    def __init__(self) -> None:
        _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    # ── 저장 ────────────────────────────────────────────────────────────────

    def save_today(
        self,
        ratings: list[dict],
        report_type: str = "morning",
        price_data: dict | None = None,
        news_data: dict | None = None,
        data_quality: dict | None = None,
    ) -> None:
        """
        등급 이력 저장 (확장 스키마).

        추가 파라미터:
          price_data    — PriceCollector.collect() 반환값 (종가·거래량 저장용)
          news_data     — NewsCollector.collect()  반환값 (주요 뉴스 저장용)
          data_quality  — DataValidator.validate() 반환값 (신뢰도 저장용)

        저장 구조 (entry):
          grades, scores, risk_scores, data_confidence  — 등급·점수
          closing_prices, price_changes, volumes         — 주가 데이터 (백테스팅용)
          top_news                                        — 종목별 뉴스 헤드라인 1건
          data_quality                                    — 전체 신뢰도 요약
          generated_at                                    — 리포트 생성 시각
        """
        today = datetime.now().strftime("%Y-%m-%d")
        key   = f"{today}_{report_type}"

        entry: dict[str, Any] = {
            "schema_version":   "1.1",
            "date":             today,
            "report_type":      report_type,
            "generated_at":     datetime.now().isoformat(),
            # ── 등급/점수 ──
            "grades":           {},   # final_grade (표시 등급)
            "raw_grades":       {},   # raw_grade  (원본 등급, 백테스팅용)
            "grade_capped":     {},   # bool — 등급 제한 적용 여부
            "cap_reasons":      {},   # 제한 사유
            "scores":           {},
            "risk_scores":      {},
            "data_confidence":  {},
            # ── 주가 (백테스팅용) ──
            "closing_prices":   {},
            "price_changes":    {},
            "volumes":          {},
            # ── 뉴스 ──
            "top_news":         {},
            # ── 데이터 품질 ──
            "data_quality":     data_quality or {},
        }

        # 등급·점수 저장
        for r in ratings:
            sid = r["stock_id"]
            entry["grades"][sid]          = r["grade"]                      # final
            entry["raw_grades"][sid]      = r.get("raw_grade", r["grade"])  # raw
            entry["grade_capped"][sid]    = r.get("grade_capped", False)
            entry["cap_reasons"][sid]     = r.get("cap_reason", "")
            entry["scores"][sid]          = r["total_score"]
            entry["risk_scores"][sid]     = r.get("risk_score", 0)
            entry["data_confidence"][sid] = r.get("data_confidence", 0)

        # 주가 저장 (price_data 있을 때만)
        if price_data:
            for sid, p in price_data.items():
                entry["closing_prices"][sid] = p.get("price", None)
                entry["price_changes"][sid]  = p.get("change_pct", None)
                entry["volumes"][sid]        = p.get("volume", None)

        # 뉴스 헤드라인 저장 (첫 번째 뉴스만)
        if news_data:
            for sid, items in news_data.items():
                if items:
                    first = items[0]
                    entry["top_news"][sid] = {
                        "headline":  first.get("headline", ""),
                        "sentiment": first.get("sentiment", "neutral"),
                        "source":    first.get("source", ""),
                    }

        self._data[key] = entry
        self._persist()

    # ── 변화 감지 ────────────────────────────────────────────────────────────

    def get_changes(
        self, ratings: list[dict], report_type: str = "morning"
    ) -> list[dict]:
        """
        반환:
        [
          {
            "stock_id": str,
            "name": str,
            "prev_grade": str | None,
            "curr_grade": str,
            "direction": "상승" | "하락" | "유지" | "신규",
            "score_delta": float,
            "prev_date": str | None,
          },
          ...
        ]
        """
        prev = self._find_previous(report_type)
        changes = []

        for r in ratings:
            sid        = r["stock_id"]
            curr_grade = r["grade"]
            curr_score = r["total_score"]

            if prev is None or sid not in prev["grades"]:
                changes.append({
                    "stock_id":   sid,
                    "name":       r["name"],
                    "prev_grade": None,
                    "curr_grade": curr_grade,
                    "direction":  "신규",
                    "score_delta": 0.0,
                    "prev_date":  None,
                })
                continue

            prev_grade = prev["grades"][sid]
            prev_score = prev["scores"].get(sid, curr_score)
            direction  = self._direction(prev_grade, curr_grade)
            changes.append({
                "stock_id":   sid,
                "name":       r["name"],
                "prev_grade": prev_grade,
                "curr_grade": curr_grade,
                "direction":  direction,
                "score_delta": round(curr_score - prev_score, 1),
                "prev_date":  prev["date"],
            })

        return changes

    # ── 이력 조회 ────────────────────────────────────────────────────────────

    def get_history(self, stock_id: str, days: int = 7) -> list[dict]:
        """특정 종목의 최근 N일 등급/점수 이력 반환"""
        result = []
        cutoff = datetime.now() - timedelta(days=days)

        for key, entry in sorted(self._data.items()):
            try:
                entry_date = datetime.strptime(entry["date"], "%Y-%m-%d")
            except Exception:
                continue
            if entry_date < cutoff:
                continue
            if stock_id in entry["grades"]:
                result.append({
                    "date":        entry["date"],
                    "type":        entry.get("report_type", "morning"),
                    "grade":       entry["grades"][stock_id],          # final
                    "raw_grade":   entry.get("raw_grades", {}).get(stock_id, entry["grades"][stock_id]),
                    "grade_capped": entry.get("grade_capped", {}).get(stock_id, False),
                    "score":       entry["scores"].get(stock_id, 0),
                    "data_quality_score": entry.get("data_quality", {}).get("overall", {}).get("confidence"),
                })

        return sorted(result, key=lambda x: x["date"])

    def get_all_history(self, days: int = 30) -> list[dict]:
        """전체 이력 반환 (대시보드용)"""
        cutoff = datetime.now() - timedelta(days=days)
        result = []
        for key, entry in sorted(self._data.items()):
            try:
                entry_date = datetime.strptime(entry["date"], "%Y-%m-%d")
            except Exception:
                continue
            if entry_date >= cutoff:
                result.append(entry)
        return result

    # ── 내부 헬퍼 ────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if _HISTORY_FILE.exists():
            try:
                return json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _persist(self) -> None:
        _HISTORY_FILE.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),  # ① UTF-8 통일
            encoding="utf-8",
        )

    def get_previous_quality(self, report_type: str) -> float | None:
        """직전 같은 report_type 실행의 data_quality 신뢰도 점수 반환 (없으면 None)"""
        prev = self._find_previous(report_type)
        if prev is None:
            return None
        return prev.get("data_quality", {}).get("overall", {}).get("confidence")

    def _find_previous(self, report_type: str) -> dict | None:
        """오늘 제외 가장 최근 같은 report_type 항목 반환"""
        today = datetime.now().strftime("%Y-%m-%d")
        candidates = [
            v for k, v in self._data.items()
            if v.get("report_type") == report_type and v.get("date") != today
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda x: x["date"])

    @staticmethod
    def _direction(prev: str, curr: str) -> str:
        p = GRADE_ORDER.get(prev, 3)
        c = GRADE_ORDER.get(curr, 3)
        if c > p:
            return "상승"
        elif c < p:
            return "하락"
        return "유지"
