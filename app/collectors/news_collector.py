"""
News Collector — 종목/테마 관련 뉴스 수집
USE_MOCK_DATA=false → yfinance 뉴스 + 키워드 감성 분석
USE_MOCK_DATA=true  → Mock 뉴스 풀 (기본값)
"""
from __future__ import annotations

import logging
import os
import random
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── 감성 분석 키워드 ─────────────────────────────────────────────────────────
_POSITIVE_KW = [
    "급등", "신고가", "돌파", "수주", "계약", "성장", "흑자", "상승", "상향", "호실적",
    "강세", "최대", "최고", "확대", "증가", "개선", "호조", "기대", "수혜", "모멘텀",
    "surge", "beat", "strong", "growth", "upgrade", "record", "rally", "gain",
    "bullish", "outperform", "profit", "boost", "breakthrough", "positive",
]
_NEGATIVE_KW = [
    "급락", "하락", "적자", "손실", "감소", "위기", "우려", "규제", "제재", "취소",
    "약세", "하향", "부진", "악화", "충격", "리스크", "경고", "주의", "타격",
    "decline", "miss", "loss", "cut", "downgrade", "concern", "risk", "weak",
    "fall", "drop", "bearish", "underperform", "warning", "sanction", "ban",
]

# yfinance stock_id → ticker 매핑
_YFINANCE_TICKER = {
    "KR_005930": "005930.KS", "KR_000660": "000660.KS",
    "KR_010120": "010120.KS", "KR_267260": "267260.KS",
    "KR_012450": "012450.KS", "US_NVDA": "NVDA",
    "US_AMD": "AMD",          "TW_TSM": "TSM",
    "NL_ASML": "ASML",        "US_MSFT": "MSFT",
    "US_GOOGL": "GOOGL",      "US_TSLA": "TSLA",
}

# ── Mock 뉴스 풀 ─────────────────────────────────────────────────────────────
_NEWS_POOL: list[dict] = [
    {"stock_ids": ["US_NVDA", "KR_000660"], "themes": ["AI", "HBM"],
     "headline": "NVIDIA, Blackwell B300 출하 가속…SK하이닉스 HBM4 수요 동반 급증 전망",
     "sentiment": 0.85, "relevance": 0.95, "source": "Reuters"},
    {"stock_ids": ["US_NVDA"], "themes": ["AI", "반도체"],
     "headline": "NVIDIA 분기 매출 가이던스, 시장 기대치 상회…데이터센터 수요 견고",
     "sentiment": 0.90, "relevance": 0.98, "source": "Bloomberg"},
    {"stock_ids": ["KR_000660", "KR_005930"], "themes": ["HBM", "반도체"],
     "headline": "HBM3E 공급 부족 지속…SK하이닉스·삼성전자 생산 캐파 확대 경쟁",
     "sentiment": 0.70, "relevance": 0.92, "source": "매일경제"},
    {"stock_ids": ["KR_005930"], "themes": ["반도체", "HBM"],
     "headline": "삼성전자 HBM3E 엔비디아 퀄 테스트 통과 기대감…3분기 공급 가능성",
     "sentiment": 0.65, "relevance": 0.88, "source": "한국경제"},
    {"stock_ids": ["TW_TSM"], "themes": ["반도체", "파운드리"],
     "headline": "TSMC 2nm 수율 빠른 회복세…애플·NVIDIA 선주문 경쟁 치열",
     "sentiment": 0.80, "relevance": 0.93, "source": "Nikkei"},
    {"stock_ids": ["NL_ASML"], "themes": ["반도체 장비"],
     "headline": "ASML, High-NA EUV 2대 추가 출하 확인…2025년 공급 정상화 기대",
     "sentiment": 0.75, "relevance": 0.90, "source": "FT"},
    {"stock_ids": ["US_AMD"], "themes": ["AI", "반도체"],
     "headline": "AMD MI350 벤치마크, 전작 대비 40% 성능 향상…NVIDIA 점유율 위협",
     "sentiment": 0.72, "relevance": 0.87, "source": "The Verge"},
    {"stock_ids": ["KR_010120", "KR_267260"], "themes": ["전력 인프라"],
     "headline": "미국 전력망 노후화 교체 수요 급증…한국 변압기·전력기기 기업 수혜",
     "sentiment": 0.78, "relevance": 0.91, "source": "한국경제"},
    {"stock_ids": ["KR_267260"], "themes": ["전력 인프라"],
     "headline": "HD현대일렉트릭, 미국 초고압 변압기 3조 원 규모 수주 계약 추진",
     "sentiment": 0.82, "relevance": 0.94, "source": "매일경제"},
    {"stock_ids": ["KR_010120"], "themes": ["전력 인프라", "데이터센터"],
     "headline": "LS ELECTRIC, 데이터센터 전력 솔루션 사업 미국 확대…수주 잔고 사상 최대",
     "sentiment": 0.80, "relevance": 0.93, "source": "머니투데이"},
    {"stock_ids": ["US_MSFT"], "themes": ["AI", "클라우드"],
     "headline": "Microsoft Azure AI 성장률 35% 기록…Copilot 기업 구독 빠른 확산",
     "sentiment": 0.83, "relevance": 0.90, "source": "Bloomberg"},
    {"stock_ids": ["US_GOOGL"], "themes": ["AI", "클라우드"],
     "headline": "Alphabet GCP 성장세 회복 조짐…Gemini 2.0 기업 도입 확대",
     "sentiment": 0.70, "relevance": 0.85, "source": "CNBC"},
    {"stock_ids": ["KR_012450"], "themes": ["방산"],
     "headline": "한화에어로스페이스, 폴란드 K9 자주포 추가 계약 4조 원 규모 확정",
     "sentiment": 0.85, "relevance": 0.95, "source": "조선비즈"},
    {"stock_ids": ["US_NVDA", "US_AMD", "TW_TSM"], "themes": ["반도체"],
     "headline": "미 상무부, 대중 AI 반도체 추가 규제 검토…H20 포함 가능성 제기",
     "sentiment": -0.70, "relevance": 0.92, "source": "WSJ"},
    {"stock_ids": ["US_TSLA"], "themes": ["전기차"],
     "headline": "Tesla 중국 분기 판매량 전년 대비 12% 감소…BYD와 격차 확대",
     "sentiment": -0.65, "relevance": 0.90, "source": "Reuters"},
    {"stock_ids": ["KR_005930"], "themes": ["반도체"],
     "headline": "삼성전자 파운드리 수율 문제 지속…TSMC와 기술 격차 우려 재부각",
     "sentiment": -0.55, "relevance": 0.87, "source": "중앙일보"},
    {"stock_ids": [], "themes": ["AI", "반도체"],
     "headline": "Fed, 인플레이션 재가속 경고…성장주 밸류에이션 압박 가능성",
     "sentiment": -0.45, "relevance": 0.75, "source": "Bloomberg"},
    {"stock_ids": [], "themes": ["전력 인프라"],
     "headline": "IEA, 2025년 글로벌 전력 수요 증가율 역대 최고 전망…AI 데이터센터 주도",
     "sentiment": 0.70, "relevance": 0.80, "source": "IEA"},
]


def _keyword_sentiment(text: str) -> float:
    """키워드 기반 간이 감성 점수 (-1.0 ~ +1.0)"""
    t = text.lower()
    pos = sum(1 for k in _POSITIVE_KW if k.lower() in t)
    neg = sum(1 for k in _NEGATIVE_KW if k.lower() in t)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 2)


class NewsCollector:
    def __init__(self) -> None:
        self.use_mock = os.getenv("USE_MOCK_DATA", "true").lower() == "true"

    def collect(
        self,
        stock_ids: list[str] | None = None,
        max_per_stock: int = 5,
    ) -> dict[str, list[dict]]:
        targets = list(stock_ids) if stock_ids else list(_YFINANCE_TICKER.keys())
        if self.use_mock:
            logger.info("NewsCollector: Mock 모드")
            return self._collect_mock(targets, max_per_stock)
        logger.info("NewsCollector: 실제 데이터 모드 (yfinance)")
        return self._collect_real(targets, max_per_stock)

    # ── 실제 데이터 ──────────────────────────────────────────────────────────

    def _collect_real(
        self, stock_ids: list[str], max_per_stock: int
    ) -> dict[str, list[dict]]:
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance 미설치 → Mock 폴백")
            return self._collect_mock(stock_ids, max_per_stock)

        result: dict[str, list[dict]] = {}

        for sid in stock_ids:
            sym = _YFINANCE_TICKER.get(sid)
            if not sym:
                result[sid] = []
                continue
            try:
                news_raw = yf.Ticker(sym).news or []
                items = []
                for n in news_raw[:max_per_stock]:
                    headline = n.get("title") or n.get("headline", "")
                    if not headline:
                        continue
                    sentiment = _keyword_sentiment(headline)
                    relevance = 0.85
                    pub_ts = n.get("providerPublishTime") or n.get("published", 0)
                    if pub_ts:
                        published_at = datetime.fromtimestamp(pub_ts).isoformat()
                    else:
                        published_at = datetime.now().isoformat()
                    items.append({
                        "headline":     headline,
                        "sentiment":    sentiment,
                        "relevance":    relevance,
                        "source":       n.get("publisher", "Yahoo Finance"),
                        "published_at": published_at,
                        "link":         n.get("link", ""),
                        "themes":       [],
                        "_mock":        False,
                    })
                result[sid] = items or self._mock_for_stock(sid, max_per_stock)
            except Exception as e:
                logger.warning(f"뉴스 수집 실패 ({sid}): {e} → Mock 폴백")
                result[sid] = self._mock_for_stock(sid, max_per_stock)

        return result

    def collect_theme_news(self, theme_ids: list[str]) -> dict[str, list[dict]]:
        """테마별 뉴스 수집 (테마 리포트용) — Mock 전용"""
        result: dict[str, list[dict]] = {}
        now = datetime.now()
        for theme_id in theme_ids:
            result[theme_id] = []
            for news in _NEWS_POOL:
                if theme_id in news["themes"]:
                    pub_offset = random.randint(0, 480)
                    result[theme_id].append({
                        "headline":     news["headline"],
                        "sentiment":    news["sentiment"],
                        "relevance":    news["relevance"],
                        "source":       news["source"],
                        "published_at": (now - timedelta(minutes=pub_offset)).isoformat(),
                        "themes":       news["themes"],
                        "_mock":        True,
                    })
        return result

    # ── Mock 데이터 ──────────────────────────────────────────────────────────

    def _collect_mock(
        self, stock_ids: list[str], max_per_stock: int
    ) -> dict[str, list[dict]]:
        targets_set = set(stock_ids)
        result: dict[str, list[dict]] = {}
        now = datetime.now()

        for news in _NEWS_POOL:
            relevant = news["stock_ids"]
            affected = (
                [s for s in relevant if s in targets_set]
                if relevant
                else list(targets_set)
            )
            pub_offset   = random.randint(0, 480)
            published_at = (now - timedelta(minutes=pub_offset)).isoformat()
            for sid in affected:
                result.setdefault(sid, [])
                if len(result[sid]) < max_per_stock:
                    result[sid].append({
                        "headline":     news["headline"],
                        "sentiment":    news["sentiment"],
                        "relevance":    news["relevance"],
                        "source":       news["source"],
                        "published_at": published_at,
                        "themes":       news["themes"],
                        "_mock":        True,
                    })
        return result

    def _mock_for_stock(self, sid: str, max_per_stock: int) -> list[dict]:
        """단일 종목 Mock 뉴스 반환"""
        return self._collect_mock([sid], max_per_stock).get(sid, [])
