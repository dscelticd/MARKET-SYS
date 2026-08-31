"""
Signal Scoring Engine — 수집된 데이터를 0~100 점수로 변환
각 신호 차원: price_momentum / news_sentiment / macro_alignment / sector_strength / volume_signal
             technical_signal / analyst_signal
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_SECTOR_SCORING_FILE = Path(__file__).resolve().parents[2] / "config" / "sector_scoring.json"

# 코드 내 최종 방어값 — 설정 파일이 없거나 깨져도 점수 산정이 멈추지 않게 한다.
_SECTOR_SCORING_FALLBACK: dict = {"base_score": 50.0}
_sector_scoring_cache: dict | None = None


def _load_sector_scoring() -> dict:
    """섹터 가산점 설정을 읽어 캐시. 실패 시 기본값으로 동작(점수 산정 중단 방지)."""
    global _sector_scoring_cache
    if _sector_scoring_cache is None:
        try:
            _sector_scoring_cache = json.loads(
                _SECTOR_SCORING_FILE.read_text(encoding="utf-8")
            )
        except Exception as e:
            logger.warning("섹터 가산점 설정 로드 실패, 기본값 사용: %s", e)
            _sector_scoring_cache = dict(_SECTOR_SCORING_FALLBACK)
    return _sector_scoring_cache


def _sf(val, default: float = 0.0) -> float:
    """NaN / Inf / None → default 로 안전 변환"""
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return default


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
        "price_momentum":   0.20,   # 0.25 → 0.20
        "news_sentiment":   0.15,   # 0.20 → 0.15
        "macro_alignment":  0.15,   # 0.20 → 0.15
        "sector_strength":  0.15,   # 0.20 → 0.15
        "volume_signal":    0.10,   # 0.15 → 0.10
        "technical_signal": 0.15,   # 신규 — RSI·MA·MACD
        "analyst_signal":   0.10,   # 신규 — 목표주가·추천등급
    }

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        if weights:
            # 구버전 설정(5개)과 호환 — 신규 신호는 기본값으로 보완 후 합 1.0 정규화
            merged = {**self.DEFAULT_WEIGHTS, **weights}
            total  = sum(merged.values())
            self.weights = {k: v / total for k, v in merged.items()} if abs(total - 1.0) > 0.01 else merged
        else:
            self.weights = self.DEFAULT_WEIGHTS

    def score(
        self,
        stock_info: dict,
        price_data: dict,
        news_data: list[dict],
        macro_data: dict,
    ) -> dict[str, Any]:
        sid = stock_info["id"]

        pm = self._price_momentum(price_data)
        ns = self._news_sentiment(news_data)
        ma = self._macro_alignment(stock_info, macro_data)
        ss = self._sector_strength(stock_info, macro_data)
        vs = self._volume_signal(price_data)
        ts = self._technical_signal(price_data)
        an = self._analyst_signal(price_data)

        components = {
            "price_momentum":   pm,
            "news_sentiment":   ns,
            "macro_alignment":  ma,
            "sector_strength":  ss,
            "volume_signal":    vs,
            "technical_signal": ts,
            "analyst_signal":   an,
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
        chg = _sf(p.get("change_pct", 0))
        raw = 50 + (chg / 4) * 50
        return min(100, max(0, raw))

    def _news_sentiment(self, news_list: list[dict]) -> float:
        """뉴스 감성 점수 (sentiment × relevance 가중 평균)
        실제 뉴스(_mock=False)가 있으면 Mock 뉴스를 제외해 점수 왜곡을 방지합니다.
        레버리지/인버스/ETN 등 파생상품 이슈는 기초자산 직접 신호가 아니므로 제외합니다.
        실제 뉴스가 없으면 50(중립)을 반환합니다.
        """
        if not news_list:
            return 50.0
        real_news = [
            n for n in news_list
            if not n.get("_mock", False) and not n.get("exclude_from_direct_negative_news", False)
        ]
        effective  = real_news if real_news else []
        if not effective:
            return 50.0   # 실제 뉴스 없음 → 중립 처리 (Mock 편향 제거)
        weighted_sum = sum(_sf(n["sentiment"]) * _sf(n.get("relevance", 1.0), 1.0)
                           for n in effective)
        weight_total = sum(_sf(n.get("relevance", 1.0), 1.0) for n in effective)
        avg = weighted_sum / weight_total if weight_total else 0
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
        # "AI 인프라"는 광통신/전력 종목들의 실제 태그 — "AI" 단독 매칭 시 누락되어 함께 확인
        if ai_cycle == "강한 상승" and any(
            t in stock.get("themes", []) for t in ("AI", "AI 인프라", "반도체")
        ):
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

    def _sector_strength(self, stock: dict, macro: dict) -> float:
        """섹터/테마 강도.

        가산점은 config/sector_scoring.json에서 읽는다. 이전에는 이 함수에 상수로
        박혀 있어 조정하려면 코드를 고쳐야 했다(구조 검토에서 하드코딩 15건으로 지목).
        이관 시 값은 그대로 유지해 점수가 변하지 않게 했다 — 값을 함께 바꾸면
        지금까지 쌓은 등급 적중률 이력과 비교가 불가능해지기 때문이다.
        앞으로 조정할 때는 요인별 적중률(components 누적)로 근거를 확인한 뒤 바꿀 것.

        설정 로드에 실패하면 코드 내 기본값으로 동작한다 — 설정 파일 문제로
        점수 산정 전체가 멈추지 않도록.
        """
        cfg = _load_sector_scoring()
        score = float(cfg.get("base_score", 50.0))
        sentiment = macro.get("sentiment", {})
        ai_cycle = sentiment.get("ai_capex_cycle", "보합")
        semi_cycle = sentiment.get("semiconductor_cycle", "업사이클 중반")

        sector = stock.get("sector", "")
        themes = stock.get("themes", [])

        # 섹터별 고정 가산점
        bonus = (cfg.get("sector_bonus") or {}).get(sector)
        if bonus:
            score += _sf(bonus.get("points"), 0.0)

        # 반도체 사이클 연동
        semi = cfg.get("semiconductor_cycle_bonus") or {}
        if sector == semi.get("sector"):
            for rule in semi.get("rules") or []:
                if rule.get("match") and rule["match"] in semi_cycle:
                    score += _sf(rule.get("points"), 0.0)
                    break

        equip = cfg.get("semiconductor_equipment_bonus") or {}
        if sector == equip.get("sector") and equip.get("match", "") in semi_cycle:
            score += _sf(equip.get("points"), 0.0)

        # AI CapEx 사이클 연동
        ai = cfg.get("ai_cycle_bonus") or {}
        if sector == ai.get("sector") and ai_cycle in (ai.get("cycles") or []):
            score += _sf(ai.get("points"), 0.0)

        # ETF — 섹터 중립, 보유 테마로 가감
        etf = cfg.get("etf_rules") or {}
        if sector == etf.get("sector"):
            if any(t in themes for t in (etf.get("index_themes") or [])):
                if ai_cycle in (etf.get("index_cycles") or []):
                    score += _sf(etf.get("index_points"), 0.0)
            elif etf.get("dividend_theme") in themes:
                score += _sf(etf.get("dividend_points"), 0.0)

        # 테마별 가산 (사이클 조건부)
        for theme_name, rule in (cfg.get("theme_bonus") or {}).items():
            if theme_name in themes and ai_cycle == rule.get("requires_ai_cycle"):
                score += _sf(rule.get("points"), 0.0)

        return min(100, max(0, score))

    def _volume_signal(self, p: dict) -> float:
        """거래량 신호"""
        if not p:
            return 50.0
        ratio = _sf(p.get("volume_ratio", 1.0), 1.0)
        chg   = _sf(p.get("change_pct", 0))

        if ratio > 1.5 and chg > 0:
            return min(100, 50 + (ratio - 1) * 20)
        elif ratio > 1.5 and chg < 0:
            return max(0, 50 - (ratio - 1) * 20)
        elif ratio < 0.7:
            return 40.0
        return 50.0

    def _technical_signal(self, p: dict) -> float:
        """RSI·이동평균·MACD 기반 기술적 신호"""
        if not p:
            return 50.0
        tech = p.get("technical")
        if not tech:
            return 50.0

        score = 50.0
        price = p.get("price", 0)

        # RSI — 과매도 반등 기대 / 과매수 조정 가능
        rsi = tech.get("rsi_14", 50)
        if rsi < 30:
            score += 15
        elif rsi < 40:
            score += 7
        elif rsi > 70:
            score -= 15
        elif rsi > 60:
            score -= 5

        # 이동평균 배열 — 추세 방향
        ma5  = tech.get("ma5")
        ma20 = tech.get("ma20")
        ma60 = tech.get("ma60")
        if price and ma20 and ma60:
            if price > ma20 > ma60:
                score += 10   # 정배열 상승
            elif price < ma20 < ma60:
                score -= 10   # 역배열 하락
        if ma5 and ma20:
            if ma5 > ma20:
                score += 5    # 단기 골든크로스
            else:
                score -= 5    # 단기 데드크로스

        # MACD 히스토그램 — 모멘텀 방향
        hist = tech.get("macd_histogram")
        if hist is not None:
            if hist > 0:
                score += 5
            else:
                score -= 5

        return min(100, max(0, score))

    def _analyst_signal(self, p: dict) -> float:
        """애널리스트 목표주가·추천등급 기반 신호"""
        if not p:
            return 50.0
        analyst = p.get("analyst")
        if not analyst or not analyst.get("target_mean"):
            return 50.0

        score = 50.0

        # 상승여력
        upside = analyst.get("upside_pct") or 0
        if upside > 30:
            score += 20
        elif upside > 15:
            score += 12
        elif upside > 5:
            score += 5
        elif upside < -10:
            score -= 15
        elif upside < 0:
            score -= 5

        # 추천 등급
        rec = (analyst.get("recommendation") or "").lower()
        if rec in ("strong_buy", "strongbuy"):
            score += 10
        elif rec == "buy":
            score += 5
        elif rec == "outperform":
            score += 3
        elif rec in ("underperform", "sell"):
            score -= 10
        elif rec in ("strong_sell", "strongsell"):
            score -= 15

        # 애널리스트 수 적으면 신뢰도 감쇠
        num = analyst.get("num_analysts") or 0
        if num < 3:
            score = 50 + (score - 50) * 0.4

        return min(100, max(0, score))

    # ------------------------------------------------------------------
    # 리스크 및 신뢰도
    # ------------------------------------------------------------------

    def _risk_score(self, price_data: dict, news_data: list[dict], macro: dict) -> float:
        risk = 30.0
        if price_data:
            chg = _sf(price_data.get("change_pct", 0))
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
        negative_news = [n for n in news_data
                         if not n.get("_mock", False)
                         and n.get("sentiment", 0) < -0.4
                         and not n.get("exclude_from_direct_negative_news", False)]
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
        chg = _sf(price_data.get("change_pct", 0))
        if chg > 2:
            positives.append(f"가격 상승 모멘텀 강함 (+{chg:.1f}%)")
        elif chg < -2:
            negatives.append(f"가격 하락 압력 ({chg:.1f}%)")

        # 거래량
        vol_ratio = _sf(price_data.get("volume_ratio", 1.0), 1.0)
        if vol_ratio > 1.8 and chg > 0:
            positives.append(f"거래량 급증 동반 상승 (5일 평균 대비 {vol_ratio:.1f}배)")
        elif vol_ratio > 1.8 and chg < 0:
            negatives.append(f"거래량 급증 동반 하락 (매도 압력)")

        # 뉴스 — mock 제외 (_news_sentiment()와 동일 원칙)
        _real = [
            n for n in news_data
            if not n.get("_mock", False) and not n.get("exclude_from_direct_negative_news", False)
        ]
        pos_news = [n for n in _real if n.get("sentiment", 0) > 0.5]
        neg_news = [n for n in _real if n.get("sentiment", 0) < -0.4]
        if pos_news:
            positives.append(f"긍정 뉴스 {len(pos_news)}건 (최근)")
        if neg_news:
            negatives.append(f"부정 뉴스 {len(neg_news)}건 확인")

        # 파생상품(레버리지/인버스/ETN 등) 이슈 — 직접 악재 아님, 참고용으로만 표시
        deriv_news = [
            n for n in news_data
            if not n.get("_mock", False) and n.get("exclude_from_direct_negative_news", False)
        ]
        if deriv_news:
            checks.append(
                f"파생상품 관련 참고 이슈 {len(deriv_news)}건 감지 "
                "(레버리지/인버스/ETN 등 — 기초자산 직접 영향 낮음)"
            )

        # 거시
        sentiment = macro.get("sentiment", {})
        ai_cycle = sentiment.get("ai_capex_cycle", "보합")
        semi_cycle = sentiment.get("semiconductor_cycle", "")
        if ai_cycle in ("강한 상승",) and any(
            t in stock.get("themes", []) for t in ("AI", "AI 인프라")
        ):
            positives.append(f"AI CapEx 사이클 '{ai_cycle}' — 테마 수혜")
        if semi_cycle and "업사이클" in semi_cycle and "반도체" in stock.get("sector", ""):
            positives.append(f"반도체 사이클 '{semi_cycle}' — 업사이클 구간")
        if semi_cycle and "피크 논란" in semi_cycle:
            checks.append("반도체 사이클 피크 논란 — 사이클 전환 가능성 모니터링")

        # ── 기술적 지표 ──
        # 점수(technical_signal)에는 15% 반영되면서 근거 목록에는 한 줄도 없어,
        # "점수는 올랐는데 왜인지 설명되지 않는" 상태였다.
        tech = price_data.get("technical") or {}
        # RSI는 값이 없을 때 0.0으로 떨어지면 "과매도"로 오판되므로 None을 명확히 구분한다
        _rsi_raw = tech.get("rsi_14")
        rsi = _sf(_rsi_raw, -1.0) if _rsi_raw is not None else None
        if rsi is not None and rsi < 0:
            rsi = None
        if rsi is not None:
            if rsi < 30:
                positives.append(f"RSI {rsi:.0f} — 과매도 구간(기술적 반등 여지)")
            elif rsi > 70:
                negatives.append(f"RSI {rsi:.0f} — 과매수 구간(단기 과열)")

        ma5, ma20, ma60 = tech.get("ma5"), tech.get("ma20"), tech.get("ma60")
        if ma5 and ma20:
            if ma5 > ma20:
                positives.append("5일선이 20일선 위 — 단기 정배열")
            else:
                negatives.append("5일선이 20일선 아래 — 단기 역배열")
        price_now = _sf(price_data.get("price"), 0.0)
        if ma60 and price_now:
            if price_now < ma60:
                negatives.append("주가가 60일선 아래 — 중기 추세 약세")

        hist = tech.get("macd_histogram")
        if hist is not None:
            h = _sf(hist, 0.0)
            # 절대값은 주가 스케일에 비례해 종목 간 비교가 불가능하다
            # (삼성전자 +1331.98 vs NVIDIA -0.47 — 같은 강도인지 알 수 없음).
            # 해석 가능한 정보는 부호(방향)이므로 방향만 근거로 제시한다.
            if h > 0:
                positives.append("MACD 히스토그램 양(+) — 모멘텀 개선 방향")
            elif h < 0:
                negatives.append("MACD 히스토그램 음(-) — 모멘텀 둔화 방향")

        # ── 수급 (KR 종목, KIS/네이버) ──
        # 실측 데이터를 수집하면서도 점수·근거 어디에도 쓰지 않고 있었다.
        # 하루가 아닌 5·20일 누적을 보는 이유: 단일 거래일 수급은 노이즈가 크다.
        flow = price_data.get("investor_flow") or {}
        if flow and not flow.get("_mock"):
            is_kis = flow.get("_source") == "kis"
            src_label = "실측" if is_kis else "추정"
            for days in (5, 20):
                frgn = flow.get(f"foreign_net_{days}d")
                inst = flow.get(f"institution_net_{days}d")
                if frgn is None or inst is None:
                    continue
                if frgn > 0 and inst > 0:
                    positives.append(
                        f"{days}일 누적 외국인·기관 동반 순매수({src_label}) "
                        f"— 외국인 {frgn:+,}주·기관 {inst:+,}주"
                    )
                elif frgn < 0 and inst < 0:
                    negatives.append(
                        f"{days}일 누적 외국인·기관 동반 순매도({src_label}) "
                        f"— 외국인 {frgn:+,}주·기관 {inst:+,}주"
                    )
                break  # 5일이 있으면 5일만, 없으면 20일만 — 중복 서술 방지

        # ── 지지/저항·손익비 ──
        sr = price_data.get("support_resistance") or {}
        if sr:
            rr = sr.get("risk_reward_ratio")
            if rr is not None:
                if sr.get("risk_reward_meets_bar"):
                    positives.append(f"손익비 {_sf(rr, 0):.2f} — 기준 충족 구간")
                else:
                    checks.append(f"손익비 {_sf(rr, 0):.2f} — 기준 미달(진입 근거 약함)")
            down_pct = sr.get("nearest_support_pct")
            if down_pct is not None and _sf(down_pct, 99) < 3.0:
                checks.append(f"지지선까지 -{_sf(down_pct, 0):.1f}% — 이탈 시 하방 확인 필요")

        # ── 애널리스트 컨센서스 ──
        analyst = price_data.get("analyst") or {}
        upside = analyst.get("upside_pct")
        if upside is not None and analyst.get("num_analysts"):
            up = _sf(upside, 0.0)
            n_an = analyst.get("num_analysts")
            if up > 20:
                positives.append(f"목표주가 대비 상승여력 {up:+.1f}% (애널리스트 {n_an}명)")
            elif up < 0:
                negatives.append(f"주가가 목표주가 상회 {up:+.1f}% (애널리스트 {n_an}명)")

        # ── 다이버전스 (신호 간 불일치) ──
        # 개별 신호는 정상이어도 서로 어긋나면 그 자체가 중요한 근거다.
        # 가중평균 점수에서는 상쇄되어 사라지므로 별도로 드러낸다.
        if chg > 2 and vol_ratio < 0.8:
            checks.append(
                f"상승(+{chg:.1f}%)에 거래량이 실리지 않음(평균 대비 {vol_ratio:.1f}배) "
                "— 상승 지속력 확인 필요"
            )
        if flow and not flow.get("_mock"):
            frgn5 = flow.get("foreign_net_5d")
            if frgn5 is not None:
                if chg > 2 and frgn5 < 0:
                    checks.append("가격은 상승했으나 5일 누적 외국인 순매도 — 수급 미확인 상승")
                elif chg < -2 and frgn5 > 0:
                    checks.append("가격은 하락했으나 5일 누적 외국인 순매수 — 수급은 지지")
        if rsi is not None and rsi > 70 and hist is not None and _sf(hist, 0.0) < 0:
            checks.append("과매수 구간에서 MACD 모멘텀 둔화 — 단기 되돌림 가능성 점검")

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
