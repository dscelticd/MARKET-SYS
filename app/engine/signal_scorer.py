"""
Signal Scoring Engine — 수집된 데이터를 0~100 점수로 변환
각 신호 차원: price_momentum / news_sentiment / macro_alignment / sector_strength / volume_signal
"""
from __future__ import annotations

import math
from typing import Any


class SignalScorer:
    """
    score() → {
        "stock_id": str,
        "total_score": float,     # 0~100 (가중 합산)
        "risk_score": float,      # 0~100 (높을수록 위험 신호 강함)
        "data_confidence": float, # 0~100 (데이터 신뢰도)
        "components": {
            "price_momentum": float,
            "news_sentiment": float,
            "macro_alignment": float,
            "sector_strength": float,
            "volume_signal": float,
        },
        "positive_factors": list[str],
        "negative_factors": list[str],
        "check_required": list[str],
    }
    """

    DEFAULT_WEIGHTS = {
        "price_momentum": 0.25,
        "news_sentiment": 0.20,
        "macro_alignment": 0.20,
        "sector_strength": 0.20,
        "volume_signal":   0.15,
    }

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = weights or self.DEFAULT_WEIGHTS

    def score(
        self,
        stock_info: dict,
        price_data: dict,
        news_data: list[dict],
        macro_data: dict,
        theme_config: dict[str, dict] | None = None,
    ) -> dict[str, Any]:
        sid = stock_info["id"]

        pm = self._price_momentum(price_data)
        ns = self._news_sentiment(news_data)
        ma = self._macro_alignment(stock_info, macro_data)
        ss = self._sector_strength(stock_info, macro_data, theme_config)
        vs = self._volume_signal(price_data)

        components = {
            "price_momentum": pm,
            "news_sentiment": ns,
            "macro_alignment": ma,
            "sector_strength": ss,
            "volume_signal": vs,
        }

        total = sum(components[k] * self.weights[k] for k in self.weights)
        total = round(min(100, max(0, total)), 1)

        risk = self._risk_score(price_data, news_data, macro_data)
        confidence = self._data_confidence(price_data, news_data, macro_data)

        positives, negatives, checks = self._extract_factors(
            stock_info, price_data, news_data, macro_data, components
        )

        return {
            "stock_id": sid,
            "total_score": total,
            "risk_score": round(risk, 1),
            "data_confidence": round(confidence, 1),
            "components": {k: round(v, 1) for k, v in components.items()},
            "positive_factors": positives,
            "negative_factors": negatives,
            "check_required": checks,
        }

    # ------------------------------------------------------------------
    # 개별 신호 계산
    # ------------------------------------------------------------------

    def _price_momentum(self, p: dict) -> float:
        """가격 변화율 기반 모멘텀 점수"""
        if not p:
            return 50.0
        chg = p.get("change_pct", 0)
        # -4% → 0점, 0% → 50점, +4% → 100점 (선형 매핑, 클리핑)
        raw = 50 + (chg / 4) * 50
        return min(100, max(0, raw))

    def _news_sentiment(self, news_list: list[dict]) -> float:
        """뉴스 감성 점수 (sentiment × relevance 가중 평균)"""
        if not news_list:
            return 50.0
        weighted_sum = sum(n["sentiment"] * n.get("relevance", 1.0) for n in news_list)
        weight_total = sum(n.get("relevance", 1.0) for n in news_list)
        avg = weighted_sum / weight_total if weight_total else 0
        # -1~+1 → 0~100
        return round((avg + 1) / 2 * 100, 1)

    def _macro_alignment(self, stock: dict, macro: dict) -> float:
        """거시 환경과 종목 특성의 정렬도"""
        score = 50.0
        sentiment = macro.get("sentiment", {})
        currencies = macro.get("currencies", {})
        rates = macro.get("rates", {})

        country = stock.get("country", "US")
        sector = stock.get("sector", "")

        ai_cycle = sentiment.get("ai_capex_cycle", "보합")
        if ai_cycle == "강한 상승" and ("AI" in stock.get("themes", []) or "반도체" in stock.get("themes", [])):
            score += 15
        elif ai_cycle == "완만한 상승":
            score += 7
        elif ai_cycle == "둔화":
            score -= 10

        semi_cycle = sentiment.get("semiconductor_cycle", "업사이클 중반")
        if "반도체" in sector or "반도체" in stock.get("themes", []):
            if "업사이클" in semi_cycle:
                score += 10
            elif "다운사이클" in semi_cycle:
                score -= 12

        risk_appetite = sentiment.get("global_risk_appetite", "중립")
        if risk_appetite == "Risk-On":
            score += 5
        elif risk_appetite == "Risk-Off":
            score -= 8

        # 한국 종목 — 환율 영향
        if country == "KR":
            usd_krw = currencies.get("USD_KRW", {}).get("value", 1330)
            if usd_krw > 1380:
                score -= 5  # 강달러는 수입 비용 상승
            elif usd_krw < 1290:
                score -= 3  # 원화 강세는 수출 마진 압박

        # 금리 민감도
        yield_10y = rates.get("us_10y_yield", {}).get("value", 4.35)
        if yield_10y > 4.8 and sector in ("빅테크/클라우드", "전기차/AI"):
            score -= 8  # 고금리 → 성장주 밸류에이션 압박

        return min(100, max(0, score))

    def _sector_strength(self, stock: dict, macro: dict, theme_cfg: dict | None) -> float:
        """섹터/테마 강도"""
        score = 50.0
        sentiment = macro.get("sentiment", {})
        ai_cycle = sentiment.get("ai_capex_cycle", "보합")
        semi_cycle = sentiment.get("semiconductor_cycle", "업사이클 중반")

        sector = stock.get("sector", "")
        themes = stock.get("themes", [])

        if sector == "반도체":
            if "업사이클 초반" in semi_cycle:
                score += 18
            elif "업사이클 중반" in semi_cycle:
                score += 12
            elif "피크 논란" in semi_cycle:
                score -= 5

        if sector == "반도체 장비" and "업사이클" in semi_cycle:
            score += 10

        if sector == "전력 인프라":
            score += 12  # 데이터센터 수요 지속적 테마

        if sector == "방산/항공":
            score += 8  # 지정학 리스크 지속

        if sector == "빅테크/클라우드":
            if ai_cycle in ("강한 상승", "완만한 상승"):
                score += 10

        if "HBM" in themes and ai_cycle == "강한 상승":
            score += 10

        return min(100, max(0, score))

    def _volume_signal(self, p: dict) -> float:
        """거래량 신호"""
        if not p:
            return 50.0
        ratio = p.get("volume_ratio", 1.0)
        chg = p.get("change_pct", 0)

        if ratio > 1.5 and chg > 0:
            return min(100, 50 + (ratio - 1) * 20)
        elif ratio > 1.5 and chg < 0:
            return max(0, 50 - (ratio - 1) * 20)
        elif ratio < 0.7:
            return 40.0
        return 50.0

    # ------------------------------------------------------------------
    # 리스크 및 신뢰도
    # ------------------------------------------------------------------

    def _risk_score(self, price_data: dict, news_data: list[dict], macro: dict) -> float:
        risk = 30.0
        if price_data:
            chg = price_data.get("change_pct", 0)
            if chg < -3:
                risk += 20
            elif chg < -1.5:
                risk += 10
        vix = macro.get("us_market", {}).get("VIX", {})
        vix_signal = vix.get("signal", "low") if isinstance(vix, dict) else "low"
        if vix_signal == "high":
            risk += 20
        elif vix_signal == "medium":
            risk += 10
        negative_news = [n for n in news_data if n.get("sentiment", 0) < -0.4]
        risk += min(20, len(negative_news) * 7)
        return min(100, risk)

    def _data_confidence(self, price_data: dict, news_data: list[dict], macro: dict) -> float:
        conf = 60.0
        if price_data:
            conf += 20
        if news_data:
            conf += min(15, len(news_data) * 3)
        if macro:
            conf += 5
        return min(100, conf)

    # ------------------------------------------------------------------
    # 요인 추출
    # ------------------------------------------------------------------

    def _extract_factors(
        self,
        stock: dict,
        price_data: dict,
        news_data: list[dict],
        macro: dict,
        components: dict,
    ) -> tuple[list[str], list[str], list[str]]:
        positives: list[str] = []
        negatives: list[str] = []
        checks: list[str] = []

        # 가격
        chg = price_data.get("change_pct", 0)
        if chg > 2:
            positives.append(f"가격 상승 모멘텀 강함 (+{chg:.1f}%)")
        elif chg < -2:
            negatives.append(f"가격 하락 압력 ({chg:.1f}%)")

        # 거래량
        vol_ratio = price_data.get("volume_ratio", 1.0)
        if vol_ratio > 1.8 and chg > 0:
            positives.append(f"거래량 급증 동반 상승 (5일 평균 대비 {vol_ratio:.1f}배)")
        elif vol_ratio > 1.8 and chg < 0:
            negatives.append(f"거래량 급증 동반 하락 (매도 압력)")

        # 뉴스
        pos_news = [n for n in news_data if n.get("sentiment", 0) > 0.5]
        neg_news = [n for n in news_data if n.get("sentiment", 0) < -0.4]
        if pos_news:
            positives.append(f"긍정 뉴스 {len(pos_news)}건 (최근)")
        if neg_news:
            negatives.append(f"부정 뉴스 {len(neg_news)}건 확인")

        # 거시
        sentiment = macro.get("sentiment", {})
        ai_cycle = sentiment.get("ai_capex_cycle", "보합")
        semi_cycle = sentiment.get("semiconductor_cycle", "")
        if ai_cycle in ("강한 상승",) and "AI" in stock.get("themes", []):
            positives.append(f"AI CapEx 사이클 '{ai_cycle}' — 테마 수혜")
        if semi_cycle and "업사이클" in semi_cycle and "반도체" in stock.get("sector", ""):
            positives.append(f"반도체 사이클 '{semi_cycle}' — 업사이클 구간")
        if semi_cycle and "피크 논란" in semi_cycle:
            checks.append("반도체 사이클 피크 논란 — 사이클 전환 가능성 모니터링")

        # VIX
        vix = macro.get("us_market", {}).get("VIX", {})
        if isinstance(vix, dict) and vix.get("signal") == "high":
            negatives.append(f"VIX 고점 ({vix.get('value', '?')}) — 시장 변동성 확대")

        # 환율 확인
        usd_krw = macro.get("currencies", {}).get("USD_KRW", {})
        if isinstance(usd_krw, dict):
            if usd_krw.get("value", 1330) > 1380 and stock.get("country") == "KR":
                checks.append("달러 강세 지속 시 수입 비용 상승 — 마진 영향 확인 필요")

        return positives, negatives, checks
