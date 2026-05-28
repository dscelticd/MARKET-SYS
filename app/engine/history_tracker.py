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

GRADE_ORDER = {"추천": 5, "안전": 4, "보통": 3, "주의": 2, "위험": 1}

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

    def save_today(self, ratings: list[dict], report_type: str = "morning") -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        key   = f"{today}_{report_type}"

        entry: dict[str, Any] = {
            "date":        today,
            "report_type": report_type,
            "timestamp":   datetime.now().isoformat(),
            "grades":      {},
            "scores":      {},
        }
        for r in ratings:
            sid = r["stock_id"]
            entry["grades"][sid] = r["grade"]
            entry["scores"][sid] = r["total_score"]

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
                    "date":  entry["date"],
                    "type":  entry.get("report_type", "morning"),
                    "grade": entry["grades"][stock_id],
                    "score": entry["scores"].get(stock_id, 0),
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
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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
