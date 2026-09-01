"""
History Tracker — 날짜별 등급 기록 저장 및 전일 대비 변화 추적
data/history/ratings_history.json 에 누적 저장
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.utils.market_calendar import is_trading_day
from app.utils.market_calendar import now_kst

_logger = logging.getLogger(__name__)

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

# ── 요인별 적중률 판정 기준 ──────────────────────────────────────────────────
# 각 신호 축(0~100)에서 55↑는 강세, 45↓는 약세 신호로 본다. 그 사이는 중립이라
# 방향 판정에서 제외 — 애매한 값을 억지로 한쪽으로 몰면 통계가 의미를 잃는다.
_FACTOR_NEUTRAL_LOW  = 45.0
_FACTOR_NEUTRAL_HIGH = 55.0
# 표본이 이보다 적으면 통계로 제시하지 않는다. 적은 표본의 "80% 적중"은
# 근거가 아니라 착시이며, 리포트가 그걸 인용하면 오히려 신뢰를 떨어뜨린다.
_FACTOR_MIN_SAMPLES = 20

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
        self._backfill_trading_day_flags()
        self._migrate_keys_to_session_date()

    # ── 거래일 판정 ──────────────────────────────────────────────────────────

    @staticmethod
    def _entry_is_trading_day(entry: dict) -> bool:
        """엔트리가 실제 거래일 스냅샷인지. 필드가 없는 과거 엔트리는 날짜 요일로 유추.

        주말 실행분은 직전 거래일(금요일) 종가를 그대로 복사한 중복 스냅샷이라,
        적중률 집계나 전일 대비 비교의 기준으로 쓰면 통계가 왜곡된다.
        (실측: 2026-08-08~09 주말 4개 엔트리가 전부 삼성전자 231,000원/+0.22%로 동일)
        """
        flag = entry.get("is_trading_day")
        if isinstance(flag, bool):
            return flag
        try:
            return is_trading_day(datetime.strptime(entry["date"], "%Y-%m-%d").date())
        except (KeyError, ValueError, TypeError):
            return True  # 판정 불가 시 기존 동작 유지 (배제하지 않음)

    def _backfill_trading_day_flags(self) -> None:
        """과거 엔트리에 is_trading_day 플래그를 소급 기록 (데이터는 보존).
        이미 플래그가 있으면 건드리지 않으며, 변경이 없으면 파일도 쓰지 않는다."""
        changed = False
        for entry in self._data.values():
            if isinstance(entry.get("is_trading_day"), bool):
                continue
            try:
                d = datetime.strptime(entry["date"], "%Y-%m-%d").date()
            except (KeyError, ValueError, TypeError):
                continue
            entry["is_trading_day"] = is_trading_day(d)
            changed = True
        if changed:
            self._persist()

    def _migrate_keys_to_session_date(self) -> None:
        """실행일로 잡혀 있던 과거 키를 대상 거래일 기준으로 옮긴다 (계약 C5).

        예약 실행이 4시간가량 밀리는 환경(실측: 9/1 아침 4:38, 9/2 저녁 3:56)에서
        실행일 키는 분석 대상과 어긋난다. 9월 1일 저녁 결산이 00:36에 발송되면서
        "2026-09-02" 키로 저장된 것이 그 예다. 적중률은 D일 등급을 D+5일 가격과
        대조하는 계산이라, 키가 하루 밀리면 비교 대상이 통째로 어긋난다.

        데이터는 지우지 않는다. data_date가 있는 엔트리만 옮기고, 목적지 키가
        이미 있으면(같은 거래일을 두 번 실행) 건드리지 않는다.
        """
        moved: dict[str, str] = {}
        for key, entry in list(self._data.items()):
            data_date = entry.get("data_date")
            if not data_date or entry.get("date") == data_date:
                continue
            report_type = entry.get("report_type", "morning")
            new_key = f"{data_date}_{report_type}"
            if new_key in self._data:
                continue
            entry["run_date"] = entry.get("date")   # 실행일은 감사용으로 보존
            entry["date"] = data_date
            entry["is_trading_day"] = True          # 대상 거래일은 정의상 거래일
            self._data[new_key] = self._data.pop(key)
            moved[key] = new_key
        if moved:
            _logger.info("[HISTORY_MIGRATE] 실행일 키 → 대상 거래일 키 %d건: %s",
                         len(moved), moved)
            self._persist()

    # ── 저장 ────────────────────────────────────────────────────────────────

    def save_today(
        self,
        ratings: list[dict],
        report_type: str = "morning",
        price_data: dict | None = None,
        news_data: dict | None = None,
        data_quality: dict | None = None,
        session_date: str | None = None,
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
        # 계약 C5 — 이력의 키는 실행일이 아니라 분석 대상 거래일이다.
        # 예약 실행이 밀려도 같은 거래일은 같은 칸에 들어가고, 거래가 없던
        # 날에는 칸이 생기지 않는다.
        run_date = now_kst().strftime("%Y-%m-%d")
        today    = session_date or run_date
        key      = f"{today}_{report_type}"

        # 이 스냅샷의 가격이 실제로 어느 거래일 것인지 — 주말 실행이면 실행일(today)과
        # 다르다. 적중률 계산이 "일요일 가격"으로 라벨된 금요일 가격을 쓰지 않도록,
        # 실행 날짜와 데이터 기준일을 분리해 저장한다.
        data_dates = {
            p.get("data_date") for p in (price_data or {}).values() if p.get("data_date")
        }
        data_date = max(data_dates) if data_dates else None

        entry: dict[str, Any] = {
            # 1.2: is_trading_day·data_date 추가(주말 중복 스냅샷 구분)
            # 1.3: components 추가(요인별 적중률 산출용 — 총점만으로는 어떤 신호가
            #      맞았는지 사후에 알 수 없었다)
            "schema_version":   "1.4",
            "date":             today,        # 대상 거래일
            "run_date":         run_date,     # 실제 실행일 (감사용)
            "report_type":      report_type,
            "generated_at":     now_kst().isoformat(),
            "is_trading_day":   is_trading_day(datetime.strptime(today, "%Y-%m-%d").date()),
            "data_date":        data_date,
            # ── 등급/점수 ──
            "grades":           {},   # final_grade (표시 등급)
            "raw_grades":       {},   # raw_grade  (원본 등급, 백테스팅용)
            "grade_capped":     {},   # bool — 등급 제한 적용 여부
            "cap_reasons":      {},   # 제한 사유
            "scores":           {},
            "components":       {},   # 요인별 점수 — 요인별 적중률 산출용(누적 시작)
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
            # 요인별 점수(7개 축)를 함께 남긴다.
            # 지금까지는 총점만 저장해서, "어떤 신호가 실제로 맞았는가"를
            # 사후에 계산할 방법이 아예 없었다(적중률은 등급 단위 집계뿐).
            # 축적되면 요인별 적중률을 산출해 "이 신호는 과거 N% 적중" 같은
            # 실증 근거를 리포트에 쓸 수 있다 — 그래서 지금부터 쌓아둔다.
            if r.get("components"):
                entry["components"][sid] = r["components"]

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
        cutoff = now_kst() - timedelta(days=days)

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
        cutoff = now_kst() - timedelta(days=days)
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

    def compute_factor_accuracy(
        self,
        current_price_data: dict,
        lookback_days: int = 20,
        min_samples: int = _FACTOR_MIN_SAMPLES,
    ) -> dict:
        """요인별(7개 축) 신호가 실제로 방향성을 맞혔는지 집계.

        기존 적중률은 **등급 단위**(추천/안전 ↔ 주의/위험)라 "어떤 신호가 맞았는가"는
        알 수 없었다. 요인별 점수(components)를 저장하기 시작하면서 가능해진 집계다.

        판정 방식: 각 축의 점수가 55 이상이면 강세 신호, 45 이하면 약세 신호로 보고
        (그 사이는 중립이라 판정 제외) 이후 수익률 방향과 대조한다.
        등급 적중률과 같은 ±2%p 허용오차를 적용해 보합을 오분류하지 않는다.

        표본이 min_samples 미만인 축은 통계로 제시하면 오해를 부르므로
        sufficient=False로 표시해 리포트가 "데이터 부족"임을 밝히도록 한다.

        반환: {"lookback_days", "reference_date", "factors": {축: {...}}, "ready": bool}
        """
        empty = {"lookback_days": lookback_days, "reference_date": None,
                 "factors": {}, "ready": False}

        target = self._find_closest_entry_before(lookback_days)
        if target is None or not target.get("components"):
            return empty

        confidence = target.get("data_quality", {}).get("overall", {}).get("confidence")
        if confidence is not None and confidence < _ACCURACY_MIN_CONFIDENCE:
            return empty

        raw: dict[str, dict] = {}
        for sid, comps in (target.get("components") or {}).items():
            past_price = target.get("closing_prices", {}).get(sid)
            curr_price = current_price_data.get(sid, {}).get("price")
            if not past_price or not curr_price or past_price <= 0:
                continue
            return_pct = (curr_price - past_price) / past_price * 100

            for factor, score in (comps or {}).items():
                try:
                    s = float(score)
                except (TypeError, ValueError):
                    continue
                if _FACTOR_NEUTRAL_LOW < s < _FACTOR_NEUTRAL_HIGH:
                    continue  # 중립 구간 — 방향 신호로 보지 않음
                bullish = s >= _FACTOR_NEUTRAL_HIGH
                hit = (return_pct >= -_ACCURACY_TOLERANCE_PCT) if bullish \
                    else (return_pct <= _ACCURACY_TOLERANCE_PCT)
                f = raw.setdefault(factor, {"count": 0, "hit": 0, "returns": []})
                f["count"] += 1
                f["hit"] += int(hit)
                f["returns"].append(return_pct)

        if not raw:
            return empty

        factors = {
            name: {
                "count": v["count"],
                "hit": v["hit"],
                "hit_rate": round(v["hit"] / v["count"] * 100, 1),
                "avg_return_pct": round(sum(v["returns"]) / len(v["returns"]), 2),
                "sufficient": v["count"] >= min_samples,
            }
            for name, v in raw.items()
        }
        return {
            "lookback_days": lookback_days,
            "reference_date": target["date"],
            "factors": factors,
            "ready": any(f["sufficient"] for f in factors.values()),
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
        target_date = now_kst() - timedelta(days=lookback_days)
        dated: list[tuple[datetime, dict]] = []
        for entry in self._data.values():
            # 주말 엔트리는 직전 거래일 종가를 복사한 중복본이라 기준점으로 쓰면
            # "N일 전 가격"이 실제로는 N±2일 전 가격이 되어 수익률 구간이 어긋난다.
            # 또 주말끼리 비교되면 수익률이 정확히 0%가 되는데, ±2%p 허용오차 때문에
            # 강세·약세 등급이 **양쪽 다 무조건 적중**으로 집계되는 허점이 있었다.
            if not self._entry_is_trading_day(entry):
                continue
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
        """오늘 제외 가장 최근 같은 report_type 항목 반환.
        주말 엔트리는 새 거래가 없는 중복 스냅샷이므로 비교 대상에서 제외한다 —
        그렇지 않으면 월요일 리포트가 "일요일 대비 변화 없음"이라는 무의미한 비교를
        하게 되고, 실제로 필요한 금요일 대비 변화가 가려진다."""
        today = now_kst().strftime("%Y-%m-%d")
        candidates = [
            v for k, v in self._data.items()
            if v.get("report_type") == report_type
            and v.get("date") != today
            and self._entry_is_trading_day(v)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda x: x["date"])

    def get_last_data_date(self, report_type: str | None = None) -> str | None:
        """직전 실행(오늘 제외)이 다룬 데이터 기준일. 리포트가 "직전 리포트 이후
        새로운 거래가 없음"을 판별하는 데 쓰인다."""
        today = now_kst().strftime("%Y-%m-%d")
        candidates = [
            v for v in self._data.values()
            if v.get("date") != today and v.get("data_date")
            and (report_type is None or v.get("report_type") == report_type)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda x: (x["date"], x.get("generated_at", ""))).get("data_date")

    @staticmethod
    def _direction(prev: str, curr: str) -> str:
        p = GRADE_ORDER.get(prev, 3)
        c = GRADE_ORDER.get(curr, 3)
        if c > p:
            return "상승"
        elif c < p:
            return "하락"
        return "유지"
