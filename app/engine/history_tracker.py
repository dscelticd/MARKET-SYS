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

# 등급 적중률 판단 대상 — "보통"은 방향성 판단이 없는 중립 등급, "판단보류"는
# 데이터 품질 문제로 인한 것이라 종목 신호와 무관하므로 둘 다 집계에서 제외.
_BULLISH_GRADES = {"추천", "안전"}
_BEARISH_GRADES = {"주의", "위험"}
# 적중 판정에 ±2%p 여유를 두는 이유: "투자 판단 보조" 등급은 정밀 매매 신호가
# 아니라 방향성 참고 지표이므로, 등락률 0%를 엄격한 기준으로 삼으면 보합에 가까운
# 정상적인 흐름까지 "불일치"로 오분류해 통계가 실제보다 나빠 보이는 왜곡이 생김.
_ACCURACY_TOLERANCE_PCT = 2.0
_ACCURACY_MIN_CONFIDENCE = 50.0  # 이 미만이면 그 날짜의 등급·주가 스냅샷 자체를 신뢰할 수 없어 제외
_ACCURACY_LOOKBACK_DAYS = (5, 20)  # 몇 일 전 등급을 오늘 가격과 비교할지

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

    # ── 등급 적중률 추적 ─────────────────────────────────────────────────────

    def compute_accuracy_report(self, current_price_data: dict) -> dict:
        """N일 전 등급(추천/안전 ↔ 주의/위험)이 오늘 가격 기준으로 방향성이 맞았는지 집계.
        "보통"(중립)·"판단보류"(데이터 품질 이슈)는 종목 신호와 무관하므로 제외.
        누적 이력이 부족하면 해당 lookback의 sample_count가 0으로 반환됨 — 시스템을
        막 시작한 시점에는 자연히 비어 있고, 매일 실행이 쌓일수록 채워지는 구조.

        반환: {lookback_days: {sample_count, overall_hit_rate, reference_date, grade_stats}}
        """
        return {
            days: self._compute_accuracy_for_lookback(current_price_data, days)
            for days in _ACCURACY_LOOKBACK_DAYS
        }

    def _compute_accuracy_for_lookback(self, current_price_data: dict, lookback_days: int) -> dict:
        empty = {
            "lookback_days": lookback_days, "sample_count": 0,
            "overall_hit_rate": None, "reference_date": None, "grade_stats": {},
        }
        target_entry = self._find_closest_entry_before(lookback_days)
        if target_entry is None:
            return empty

        confidence = target_entry.get("data_quality", {}).get("overall", {}).get("confidence")
        if confidence is not None and confidence < _ACCURACY_MIN_CONFIDENCE:
            return empty  # 그 시점 데이터 신뢰도 자체가 낮으면 스냅샷 전체를 신뢰할 수 없음

        raw_stats: dict[str, dict] = {}
        for sid, grade in target_entry.get("grades", {}).items():
            if grade not in _BULLISH_GRADES and grade not in _BEARISH_GRADES:
                continue
            past_price = target_entry.get("closing_prices", {}).get(sid)
            curr_price = current_price_data.get(sid, {}).get("price")
            if not past_price or not curr_price or past_price <= 0:
                continue

            return_pct = (curr_price - past_price) / past_price * 100
            if grade in _BULLISH_GRADES:
                hit = return_pct >= -_ACCURACY_TOLERANCE_PCT
            else:
                hit = return_pct <= _ACCURACY_TOLERANCE_PCT

            s = raw_stats.setdefault(grade, {"count": 0, "hit": 0, "returns": []})
            s["count"] += 1
            s["hit"] += int(hit)
            s["returns"].append(return_pct)

        if not raw_stats:
            return empty

        grade_stats = {
            grade: {
                "count": s["count"],
                "hit": s["hit"],
                "hit_rate": round(s["hit"] / s["count"] * 100, 1),
                "avg_return_pct": round(sum(s["returns"]) / len(s["returns"]), 2),
            }
            for grade, s in raw_stats.items()
        }
        total_count = sum(s["count"] for s in raw_stats.values())
        total_hit   = sum(s["hit"] for s in raw_stats.values())

        return {
            "lookback_days": lookback_days,
            "sample_count": total_count,
            "overall_hit_rate": round(total_hit / total_count * 100, 1),
            "reference_date": target_entry["date"],
            "grade_stats": grade_stats,
        }

    def _find_closest_entry_before(self, lookback_days: int) -> dict | None:
        """오늘로부터 lookback_days 이전 시점에 가장 가까운(그 시점 또는 그 이전 중 최신)
        저장 항목을 반환. 같은 날짜에 아침/저녁 두 건이 있으면 아침을 우선한다.
        """
        target_date = datetime.now() - timedelta(days=lookback_days)
        dated: list[tuple[datetime, dict]] = []
        for entry in self._data.values():
            try:
                d = datetime.strptime(entry["date"], "%Y-%m-%d")
            except Exception:
                continue
            if d <= target_date:
                dated.append((d, entry))
        if not dated:
            return None

        dated.sort(key=lambda x: x[0])
        closest_date = dated[-1][0]
        same_day = [e for d, e in dated if d == closest_date]
        morning = next((e for e in same_day if e.get("report_type") == "morning"), None)
        return morning or same_day[0]

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
