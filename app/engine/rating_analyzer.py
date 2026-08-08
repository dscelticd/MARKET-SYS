"""
Rating Analyzer — 신호 점수를 투자 판단 보조 등급으로 변환
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GRADE_META = {
    "추천": {
        "color": "#00C853",
        "emoji": "🟢",
        "description": "현재 데이터 기준으로 우선 검토 가치가 높은 상태",
        "score_range": (75, 100),
    },
    "안전": {
        "color": "#2979FF",
        "emoji": "🔵",
        "description": "상승 모멘텀은 강하지 않아도 리스크가 낮고 변동성이 제한적인 상태",
        "score_range": (55, 74),
    },
    "보통": {
        "color": "#FF9100",
        "emoji": "🟡",
        "description": "긍정/부정 신호가 혼재되어 방향성이 명확하지 않은 상태",
        "score_range": (35, 54),
    },
    "주의": {
        "color": "#FF6D00",
        "emoji": "🟠",
        "description": "단기 리스크, 부정 뉴스, 수급 약화, 섹터 약세 등이 확인되는 상태",
        "score_range": (15, 34),
    },
    "위험": {
        "color": "#D50000",
        "emoji": "🔴",
        "description": "중대 리스크, 강한 하락 신호, 공시 악재, 데이터 불확실성이 큰 상태",
        "score_range": (0, 14),
    },
    "판단보류": {
        "color": "#757575",
        "emoji": "🚫",
        "description": "지수·ETF·대형주 데이터 간 모순이 감지되어 시장 판단을 보류한 상태",
        "score_range": None,
    },
}

DISCLAIMER = (
    "※ 이 등급은 투자 권유가 아닌 시장 데이터 기반 판단 보조 참고 자료입니다. "
    "실제 투자 결정은 개인의 판단과 책임 하에 이루어져야 합니다."
)


@dataclass
class RatingResult:
    stock_id: str
    name: str
    ticker: str
    grade: str
    total_score: float
    risk_score: float
    data_confidence: float
    components: dict[str, float]
    positive_factors: list[str] = field(default_factory=list)
    negative_factors: list[str] = field(default_factory=list)
    check_required: list[str] = field(default_factory=list)
    summary: str = ""
    disclaimer: str = DISCLAIMER

    @property
    def emoji(self) -> str:
        return GRADE_META[self.grade]["emoji"]

    @property
    def color(self) -> str:
        return GRADE_META[self.grade]["color"]

    @property
    def grade_description(self) -> str:
        return GRADE_META[self.grade]["description"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stock_id": self.stock_id,
            "name": self.name,
            "ticker": self.ticker,
            "grade": self.grade,
            "emoji": self.emoji,
            "color": self.color,
            "grade_description": self.grade_description,
            "total_score": self.total_score,
            "risk_score": self.risk_score,
            "data_confidence": self.data_confidence,
            "components": self.components,
            "positive_factors": self.positive_factors,
            "negative_factors": self.negative_factors,
            "check_required": self.check_required,
            "summary": self.summary,
            "disclaimer": self.disclaimer,
        }


_CAP_RULES: list[dict] = [
    # data_quality < 30 — 추천/안전 → 주의
    {
        "max_quality":  30,
        "blocked":      {"추천", "안전"},
        "cap_to":       "주의",
        "reason":       "데이터 신뢰도 매우 낮음으로 긍정 등급 제한 적용",
    },
    # 30 ≤ data_quality < 50 — 추천/안전 → 보통 (안전도 포함 강화)
    {
        "max_quality":  50,
        "blocked":      {"추천", "안전"},
        "cap_to":       "보통",
        "reason":       "데이터 신뢰도 낮음으로 추천·안전 등급 제한 적용",
    },
]


def apply_grade_cap(
    ratings: list[dict],
    data_quality_score: float,
    critical_data_error: bool = False,
) -> list[dict]:
    """
    data_quality_score에 따라 등급을 제한합니다.
    원본(raw_grade)은 반드시 보존하고, 화면 표시용(grade = final_grade)만 조정합니다.

    추가 필드:
      raw_grade           — 원본 계산 등급 (백테스팅용, 절대 변경 안 함)
      grade               — 최종 표시 등급 (제한 적용 후, 기존 grade 필드 덮어쓰기)
      grade_capped        — True: final ≠ raw  /  False: 변경 없음
      cap_reason          — 제한 사유 문자열 (없으면 "")
      data_quality_score  — 적용에 사용된 data_quality 점수
      data_quality_warning — True: 50~70 구간 경고 (등급은 유지)
      critical_data_error — True: 지수·ETF·대형주 데이터 모순 감지로 강제 판단보류

    등급 제한 기준:
      critical_data_error :  전종목 강제 → 판단보류 (data_quality_score 무관)
      < 30                :  추천·안전  → 주의
      30–50               :  추천·안전  → 보통 (안전 등급도 제한 — 신뢰도 낮을 때 과보호 방지)
      50–70               :  등급 유지, data_quality_warning = True
      ≥ 70                :  정상, 제한 없음
    """
    result = []
    for raw in ratings:
        r = dict(raw)                              # shallow copy (원본 수정 금지)
        original_grade = r["grade"]
        r["raw_grade"]            = original_grade
        r["grade_capped"]         = False
        r["cap_reason"]           = ""
        r["data_quality_score"]   = data_quality_score
        r["data_quality_warning"] = False
        r["critical_data_error"]  = critical_data_error

        if critical_data_error:
            final_grade       = "판단보류"
            r["grade_capped"] = True
            r["cap_reason"]   = "지수·ETF·대형주 데이터 간 모순 감지로 시장 판단 보류"
        else:
            final_grade = original_grade
            for rule in _CAP_RULES:
                if data_quality_score < rule["max_quality"] and original_grade in rule["blocked"]:
                    final_grade       = rule["cap_to"]
                    r["grade_capped"] = True
                    r["cap_reason"]   = rule["reason"]
                    break                          # 첫 번째 매칭 룰만 적용

            if not r["grade_capped"] and 50 <= data_quality_score < 70:
                r["data_quality_warning"] = True

        # grade / emoji / color / grade_description 업데이트 (표시용)
        r["grade"]             = final_grade
        r["emoji"]             = GRADE_META[final_grade]["emoji"]
        r["color"]             = GRADE_META[final_grade]["color"]
        r["grade_description"] = GRADE_META[final_grade]["description"]

        result.append(r)
    return result


class RatingAnalyzer:
    """
    SignalScorer 결과 + 종목 정보 → RatingResult 변환
    """

    def analyze(
        self,
        score_result: dict[str, Any],
        stock_info: dict,
    ) -> RatingResult:
        total = score_result["total_score"]
        grade = self._score_to_grade(total)
        summary = self._generate_summary(grade, score_result, stock_info)

        return RatingResult(
            stock_id=score_result["stock_id"],
            name=stock_info["name"],
            ticker=stock_info["ticker"],
            grade=grade,
            total_score=total,
            risk_score=score_result["risk_score"],
            data_confidence=score_result["data_confidence"],
            components=score_result["components"],
            positive_factors=score_result["positive_factors"],
            negative_factors=score_result["negative_factors"],
            check_required=score_result["check_required"],
            summary=summary,
        )

    def analyze_batch(
        self,
        score_results: list[dict],
        watchlist: list[dict],
    ) -> list[RatingResult]:
        stock_map = {s["id"]: s for s in watchlist}
        results = []
        for sr in score_results:
            sid = sr["stock_id"]
            if sid in stock_map:
                results.append(self.analyze(sr, stock_map[sid]))
        return results

    # ------------------------------------------------------------------

    @staticmethod
    def _score_to_grade(score: float) -> str:
        # 임계값 비교 — GRADE_META 정수 끝값 사이 소수점 갭 방지
        if score >= 75: return "추천"
        if score >= 55: return "안전"
        if score >= 35: return "보통"
        if score >= 15: return "주의"
        return "위험"

    @staticmethod
    def _generate_summary(grade: str, sr: dict, stock: dict) -> str:
        name = stock["name"]
        total = sr["total_score"]
        risk = sr["risk_score"]
        positives = sr["positive_factors"]
        negatives = sr["negative_factors"]

        pos_text = positives[0] if positives else "특이 긍정 신호 없음"
        neg_text = negatives[0] if negatives else "특이 부정 신호 없음"

        grade_desc = GRADE_META[grade]["description"]

        return (
            f"{name}은(는) 현재 종합 점수 {total:.0f}점, 리스크 점수 {risk:.0f}점 기준으로 "
            f"[{grade}] 등급입니다. {grade_desc}. "
            f"주요 긍정 요인: {pos_text}. 주요 부정 요인: {neg_text}."
        )

    @staticmethod
    def grade_distribution(results: list[RatingResult]) -> dict[str, int]:
        dist: dict[str, int] = {g: 0 for g in GRADE_META}
        for r in results:
            dist[r.grade] = dist.get(r.grade, 0) + 1
        return dist
