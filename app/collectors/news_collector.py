"""
News Collector — 종목/테마 관련 뉴스 수집

실제 데이터 우선순위 (USE_MOCK_DATA=false):
  1. 한국 종목 (KR_*)  → 네이버 뉴스 API (NAVER_CLIENT_ID 설정 시)
  2. 미국·해외 종목     → yfinance 뉴스
  3. 수집 실패 시       → Mock 데이터 폴백 (_mock=True 표시)

USE_MOCK_DATA=true → 전체 Mock 데이터 사용 (개발/테스트용)
"""
from __future__ import annotations

import json as _json
import logging
import os
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from html import unescape
from urllib.parse import quote as _url_quote
from app.utils.market_calendar import now_kst

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

# ── 파생상품 이슈 키워드 ─────────────────────────────────────────────────────
# 레버리지/인버스/ETN 등 파생상품 특유의 이슈(청산, 롤오버, 괴리율 등)는
# 기초자산이나 현물 ETF의 직접 악재가 아니므로 별도로 분류한다.
DERIVATIVE_KEYWORDS = [
    "레버리지", "인버스", "ETN", "선물", "롤오버", "괴리율",
    "청산", "상장폐지", "파생", "2X", "3X", "곱버스",
]


# ── 거시 이벤트 유형 분류 ────────────────────────────────────────────────────
# 배경: 기존에는 뉴스를 감성 점수(-1~+1) 한 축으로만 압축해, "무슨 종류의 사건인가"가
# 완전히 소실됐다. 실측 사례 — "Dow Jones Futures Fall, Oil Prices Jump As U.S.
# Strikes Iran"이 단지 'Fall'이라는 단어 때문에 감성 -1.0인 일반 악재로만 기록되고,
# "전쟁 발발 → 유가·방산·위험선호에 파급"이라는 성격은 사라졌다.
#
# 여기서는 감성과 **별개 축**으로 사건 유형만 태깅한다. 점수 산식은 건드리지 않는다 —
# 이벤트별 가중치를 검증 없이 정하면 근거 없는 숫자가 되고, 기존 등급 적중률 이력과
# 비교 불가능해지기 때문. 우선 리포트에 드러내고, 유용성이 확인되면 그때 논의한다.
#
# 순서가 곧 우선순위 — 한 헤드라인이 여러 유형에 걸리면 **더 구체적인 쪽**을 택한다.
# 관세/무역을 지정학/전쟁보다 먼저 두는 이유: "trade war"는 군사 충돌이 아니라
# 통상 이슈인데, 지정학 쪽의 단일어 "war"가 먼저 걸려 오분류됐다.
# 반대로 "War in Ukraine disrupts trade"는 관세 키워드에 걸리지 않고 지정학으로
# 정확히 떨어진다("trade war"는 구(phrase) 단위 매칭이므로).
MACRO_EVENT_PATTERNS: list[tuple[str, list[str]]] = [
    ("관세/무역", [
        "관세", "무역분쟁", "무역전쟁", "수출규제", "수입규제", "무역협정", "쿼터",
        "tariff", "tariffs", "trade war", "trade deal", "export control",
        "export curb", "import duty", "quota", "embargo",
    ]),
    ("지정학/전쟁", [
        "전쟁", "공습", "미사일", "침공", "교전", "휴전", "분쟁", "테러", "군사",
        # 영문은 단어 경계로 매칭한다 — 부분 문자열 매칭 시 "war"가 "Hardware"·"Award"
        # 안에서 걸리는 오탐이 실제로 발생했다(2026-08-31 실측 2건).
        # 노동 파업·옵션 행사가와 겹치는 단수형 "strike"는 제외하고, 군사 용례가
        # 뚜렷한 형태만 남긴다.
        "war", "wars", "strikes", "airstrike", "air strike", "missile",
        "invasion", "ceasefire", "conflict", "military", "troops",
    ]),
    ("제재/수출통제", [
        "제재", "블랙리스트", "금수", "수출통제",
        "sanction", "sanctions", "blacklist", "entity list", "ban on",
    ]),
    ("통화정책/금리", [
        "금리", "기준금리", "연준", "한국은행", "금통위", "인상", "인하", "긴축", "완화",
        "물가", "인플레", "테이퍼링",
        "fed", "fomc", "rate hike", "rate cut", "interest rate", "inflation",
        "cpi", "ppi", "tapering", "hawkish", "dovish", "central bank",
    ]),
    ("정치/선거/규제", [
        "선거", "대선", "총선", "탄핵", "정부", "국회", "법안", "규제안", "반독점",
        "셧다운", "예산안",
        "election", "senate", "congress", "shutdown", "antitrust", "regulation",
        "lawsuit", "probe", "impeach", "policy",
    ]),
    ("재해/공급망", [
        "지진", "태풍", "홍수", "화재", "가뭄", "정전", "파업", "공급망", "물류대란",
        "감산", "생산차질",
        "earthquake", "typhoon", "hurricane", "flood", "wildfire", "blackout",
        "strike action", "supply chain", "shortage", "outage", "disruption",
    ]),
    ("실적/가이던스", [
        "실적", "어닝", "가이던스", "잠정실적", "컨센서스", "영업이익", "매출",
        "earnings", "guidance", "revenue", "outlook", "forecast", "quarterly results",
    ]),
]


def _keyword_matches(keyword: str, lowered_headline: str) -> bool:
    """키워드 매칭. 영문(ASCII)은 단어 경계를 요구하고, 한글은 부분 문자열로 매칭한다.

    한글에 단어 경계를 쓰면 조사가 붙은 형태("관세를", "전쟁이")를 놓치므로 구분한다.
    반대로 영문에 부분 문자열을 쓰면 "war"가 "Hardware"·"Award" 안에서 걸린다.
    """
    kw = keyword.lower()
    if kw.isascii():
        return re.search(rf"\b{re.escape(kw)}\b", lowered_headline) is not None
    return kw in lowered_headline


def detect_macro_event(headline: str) -> str | None:
    """헤드라인에서 거시 이벤트 유형을 판별. 해당 없으면 None.
    대소문자 무시 — 영문 헤드라인이 그대로 들어오기 때문."""
    lowered = headline.lower()
    for event_type, keywords in MACRO_EVENT_PATTERNS:
        if any(_keyword_matches(kw, lowered) for kw in keywords):
            return event_type
    return None


def classify_news_item(headline: str) -> dict:
    """레버리지/인버스/ETN 등 파생상품 이슈는 기초자산 직접 악재로 분류하지 않음.
    추가로 거시 이벤트 유형(전쟁·관세·금리·정치·재해 등)을 별도 축으로 태깅한다."""
    macro_event = detect_macro_event(headline)

    if any(kw in headline for kw in DERIVATIVE_KEYWORDS):
        return {
            "category":              "파생상품 이슈",
            "impact_to_underlying":  "낮음",
            "exclude_from_direct_negative_news": True,
            "macro_event":           macro_event,
        }
    return {
        "category":              "일반",
        "impact_to_underlying":  "보통",
        "exclude_from_direct_negative_news": False,
        "macro_event":           macro_event,
    }

# ── yfinance 티커 매핑 ────────────────────────────────────────────────────────
_YFINANCE_TICKER: dict[str, str] = {
    # 한국
    "KR_005930": "005930.KS", "KR_000660": "000660.KS",
    "KR_069500": "069500.KS", "KR_010120": "010120.KS",
    "KR_015760": "015760.KS", "KR_066570": "066570.KS",
    "KR_138080": "138080.KQ",
    # 미국
    "US_NVDA":   "NVDA",   "US_QQQ":  "QQQ",
    "US_VOO":    "VOO",    "US_QTUM": "QTUM",
    "US_VST":    "VST",    "US_SCHD": "SCHD",
    "US_SNDK":   "SNDK",   "US_COHR": "COHR",
    "US_CIEN":   "CIEN",   "US_SPCX": "SPCX",
    # 대만
    "TW_TSM":    "TSM",
}

# ── 네이버 뉴스 검색 쿼리 (한국 종목) ─────────────────────────────────────────
_NAVER_QUERY: dict[str, str] = {
    "KR_005930": "삼성전자 주식",
    "KR_000660": "SK하이닉스 주식",
    "KR_069500": "KODEX200 ETF",
    "KR_010120": "LS ELECTRIC 주가",
    "KR_015760": "한국전력 주가",
    "KR_066570": "LG전자 주가",
    "KR_138080": "오이솔루션 주가",
}

# ── Mock 뉴스 풀 (개발/테스트용 — 실제 기사 아님) ───────────────────────────
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
    {"stock_ids": ["US_SNDK"], "themes": ["반도체", "낸드플래시"],
     "headline": "NAND 가격 회복세 진입…SanDisk, 분기 흑자 전환 기대감 상승",
     "sentiment": 0.75, "relevance": 0.90, "source": "FT"},
    {"stock_ids": ["US_COHR", "US_CIEN"], "themes": ["광통신", "AI 인프라"],
     "headline": "800G 광트랜시버 수요 급증…Coherent·Ciena 수주 잔고 역대 최고",
     "sentiment": 0.82, "relevance": 0.92, "source": "The Verge"},
    {"stock_ids": ["KR_010120", "KR_015760"], "themes": ["전력 인프라"],
     "headline": "미국 전력망 노후화 교체 수요 급증…한국 전력기기·한국전력 수혜",
     "sentiment": 0.78, "relevance": 0.91, "source": "한국경제"},
    {"stock_ids": ["US_VST"], "themes": ["전력", "원자력"],
     "headline": "Vistra Energy, 원전 재가동 계획 확정…AI 데이터센터 장기 전력 계약 체결",
     "sentiment": 0.82, "relevance": 0.94, "source": "Reuters"},
    {"stock_ids": ["KR_010120"], "themes": ["전력 인프라", "데이터센터"],
     "headline": "LS ELECTRIC, 데이터센터 전력 솔루션 사업 미국 확대…수주 잔고 사상 최대",
     "sentiment": 0.80, "relevance": 0.93, "source": "머니투데이"},
    {"stock_ids": ["US_QQQ", "US_VOO"], "themes": ["미국주식 ETF", "AI"],
     "headline": "빅테크 AI 실적 서프라이즈…나스닥·S&P500 연고점 경신",
     "sentiment": 0.83, "relevance": 0.90, "source": "Bloomberg"},
    {"stock_ids": ["US_SCHD"], "themes": ["배당", "ETF"],
     "headline": "금리 인하 기대감 재부각…배당성장 ETF SCHD 자금 유입 급증",
     "sentiment": 0.70, "relevance": 0.85, "source": "Morningstar"},
    {"stock_ids": ["KR_066570"], "themes": ["가전", "전기차부품"],
     "headline": "LG전자 전장 사업부 흑자 전환 확인…미국·유럽 완성차 수주 확대",
     "sentiment": 0.72, "relevance": 0.88, "source": "매일경제"},
    {"stock_ids": ["US_NVDA", "TW_TSM"], "themes": ["반도체"],
     "headline": "미 상무부, 대중 AI 반도체 추가 규제 검토…H20 포함 가능성 제기",
     "sentiment": -0.70, "relevance": 0.92, "source": "WSJ"},
    {"stock_ids": ["KR_015760"], "themes": ["전력 인프라", "공기업"],
     "headline": "한국전력, 전기요금 동결 장기화 우려…부채 증가 리스크 재부각",
     "sentiment": -0.55, "relevance": 0.87, "source": "한국경제"},
    {"stock_ids": ["KR_005930"], "themes": ["반도체"],
     "headline": "삼성전자 파운드리 수율 문제 지속…TSMC와 기술 격차 우려 재부각",
     "sentiment": -0.55, "relevance": 0.87, "source": "중앙일보"},
    {"stock_ids": [], "themes": ["AI", "반도체"],
     "headline": "Fed, 인플레이션 재가속 경고…성장주 밸류에이션 압박 가능성",
     "sentiment": -0.45, "relevance": 0.75, "source": "Bloomberg"},
    {"stock_ids": [], "themes": ["전력 인프라"],
     "headline": "IEA, 2025년 글로벌 전력 수요 증가율 역대 최고 전망…AI 데이터센터 주도",
     "sentiment": 0.70, "relevance": 0.80, "source": "IEA"},
    # ── 신규 종목 Mock 뉴스 ──────────────────────────────────────────────────
    {"stock_ids": ["US_VST"], "themes": ["전력", "원자력", "AI 인프라"],
     "headline": "Vistra Energy, 원전 재가동 계획 발표…AI 데이터센터 전력 수요 수혜 기대",
     "sentiment": 0.82, "relevance": 0.92, "source": "Bloomberg"},
    {"stock_ids": ["US_SNDK"], "themes": ["반도체", "낸드플래시", "스토리지"],
     "headline": "SanDisk, NAND 가격 반등세 확인…AI 스토리지 수요 증가로 흑자 전환 기대",
     "sentiment": 0.75, "relevance": 0.88, "source": "Reuters"},
    {"stock_ids": ["US_COHR", "US_CIEN", "KR_138080"], "themes": ["광통신", "데이터센터", "AI 인프라"],
     "headline": "AI 데이터센터 광통신 수요 폭증…Coherent·Ciena·오이솔루션 수주 잔고 급증",
     "sentiment": 0.88, "relevance": 0.95, "source": "Nikkei"},
    {"stock_ids": ["US_COHR"], "themes": ["광통신", "AI 인프라"],
     "headline": "Coherent, 800G 광트랜시버 출하 가속…하이퍼스케일러 전 물량 수주 완료",
     "sentiment": 0.85, "relevance": 0.93, "source": "Bloomberg"},
    {"stock_ids": ["US_CIEN"], "themes": ["광통신", "네트워킹"],
     "headline": "Ciena, AI 클러스터 백본망 확장 수혜…분기 수주 사상 최대치 경신",
     "sentiment": 0.80, "relevance": 0.90, "source": "WSJ"},
    {"stock_ids": ["KR_138080"], "themes": ["광통신", "데이터센터"],
     "headline": "오이솔루션, 북미 하이퍼스케일러향 광트랜시버 공급 계약 확대…수출 급증",
     "sentiment": 0.83, "relevance": 0.91, "source": "전자신문"},
    {"stock_ids": ["KR_015760"], "themes": ["전력 인프라", "공기업"],
     "headline": "한국전력, 전기요금 현실화 추진…재무구조 개선 기대감 확대",
     "sentiment": 0.60, "relevance": 0.85, "source": "한국경제"},
    {"stock_ids": ["KR_015760"], "themes": ["전력 인프라"],
     "headline": "한국전력, 누적 부채 급증…요금 인상 지연 시 재무위기 재부각 우려",
     "sentiment": -0.55, "relevance": 0.87, "source": "조선비즈"},
    {"stock_ids": ["KR_066570"], "themes": ["가전", "전기차부품"],
     "headline": "LG전자, 전장 사업부 흑자 전환 확인…테슬라 전기차 부품 공급 확대",
     "sentiment": 0.72, "relevance": 0.88, "source": "매일경제"},
    {"stock_ids": ["US_QQQ", "US_VOO"], "themes": ["미국주식 ETF", "나스닥", "S&P500"],
     "headline": "나스닥·S&P500 연고점 경신…AI 랠리 지속에 성장주 ETF 자금 유입 확대",
     "sentiment": 0.75, "relevance": 0.82, "source": "CNBC"},
    {"stock_ids": ["US_SCHD"], "themes": ["배당", "분산투자"],
     "headline": "SCHD 배당성장 ETF, 금리 인하 기대감에 자금 유입 급증…배당주 재부각",
     "sentiment": 0.70, "relevance": 0.80, "source": "Morningstar"},
    {"stock_ids": ["US_QTUM"], "themes": ["퀀텀컴퓨팅", "AI"],
     "headline": "양자컴퓨팅 상용화 가시화…구글 Willow 칩 발표에 QTUM ETF 급등",
     "sentiment": 0.80, "relevance": 0.88, "source": "The Verge"},
    {"stock_ids": ["US_SPCX"], "themes": ["우주항공", "위성인터넷", "Starlink"],
     "headline": "SpaceX Starlink, 글로벌 가입자 1억 명 돌파…위성 인터넷 시장 주도권 확고",
     "sentiment": 0.90, "relevance": 0.95, "source": "Reuters"},
    {"stock_ids": ["US_SPCX"], "themes": ["우주항공", "재사용 로켓", "국방/정부계약"],
     "headline": "SpaceX, NASA·DoD 발사 계약 연속 수주…Falcon 9 재사용 발사 누적 300회 돌파",
     "sentiment": 0.85, "relevance": 0.92, "source": "Bloomberg"},
    {"stock_ids": ["US_SPCX"], "themes": ["우주항공", "Starlink", "AI 인프라"],
     "headline": "Starlink Direct-to-Cell 서비스 확대…위성-지상 통합 AI 네트워크 인프라 부각",
     "sentiment": 0.75, "relevance": 0.88, "source": "Wall Street Journal"},
    # ── 파생상품 이슈 — 기초자산/현물 ETF의 직접 악재로 분류되면 안 되는 사례 ──
    {"stock_ids": ["KR_000660"], "themes": ["반도체"],
     "headline": "SK하이닉스 레버리지 ETN, 변동성 확대에 25% 폭락…괴리율 경고",
     "sentiment": -0.75, "relevance": 0.70, "source": "이데일리"},
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


def _clean_html(text: str) -> str:
    """HTML 태그·엔티티 제거"""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _parse_naver_pubdate(pub_date_str: str) -> str:
    """RFC 2822 날짜 → ISO 포맷 (예: 'Fri, 29 May 2026 10:23:45 +0900')"""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub_date_str)
        return dt.isoformat()
    except Exception:
        return now_kst().isoformat()


class NewsCollector:
    def __init__(self) -> None:
        self.use_mock     = os.getenv("USE_MOCK_DATA", "true").lower() == "true"
        self.naver_id     = os.getenv("NAVER_CLIENT_ID", "").strip()
        self.naver_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()

    def _naver_available(self) -> bool:
        return bool(self.naver_id and self.naver_secret)

    # ── 공개 API ─────────────────────────────────────────────────────────────

    def collect(
        self,
        stock_ids: list[str] | None = None,
        max_per_stock: int = 5,
    ) -> dict[str, list[dict]]:
        targets = list(stock_ids) if stock_ids else list(_YFINANCE_TICKER.keys())
        if self.use_mock:
            logger.info("NewsCollector: Mock 모드")
            return self._collect_mock(targets, max_per_stock)
        logger.info("NewsCollector: 실제 데이터 모드")
        return self._collect_real_smart(targets, max_per_stock)


    # ── 실제 데이터: 소스 자동 선택 ──────────────────────────────────────────

    def _collect_real_smart(
        self, stock_ids: list[str], max_per_stock: int
    ) -> dict[str, list[dict]]:
        """
        KR_* → 네이버 뉴스 API (설정 시) 우선, 미설정 시 Mock 폴백
        US/TW/NL → yfinance, 실패 시 Mock 폴백
        """
        result: dict[str, list[dict]] = {}
        kr_stocks  = [s for s in stock_ids if s.startswith("KR_")]
        int_stocks = [s for s in stock_ids if not s.startswith("KR_")]

        # ── 한국 종목: 네이버 API ──
        if kr_stocks:
            if self._naver_available():
                for sid in kr_stocks:
                    items = self._collect_naver_single(sid, max_per_stock)
                    result[sid] = items if items else self._mock_for_stock(sid, max_per_stock)
            else:
                logger.info("NAVER_CLIENT_ID 미설정 → 한국 종목 Mock 폴백")
                for sid in kr_stocks:
                    result[sid] = self._mock_for_stock(sid, max_per_stock)

        # ── 해외 종목: yfinance ──
        if int_stocks:
            yf_result = self._collect_yfinance(int_stocks, max_per_stock)
            result.update(yf_result)

        return result

    # ── 네이버 뉴스 API ───────────────────────────────────────────────────────

    def _collect_naver_single(self, stock_id: str, max_items: int) -> list[dict]:
        """네이버 검색 API로 단일 종목 뉴스 수집"""
        query = _NAVER_QUERY.get(stock_id, "")
        if not query:
            return []

        url = (
            "https://openapi.naver.com/v1/search/news.json"
            f"?query={_url_quote(query)}&display={max_items}&sort=date"
        )
        req = urllib.request.Request(url)
        req.add_header("X-Naver-Client-Id",     self.naver_id)
        req.add_header("X-Naver-Client-Secret",  self.naver_secret)

        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.warning(f"네이버 API HTTP 오류 ({stock_id}): {e.code} {e.reason}")
            return []
        except Exception as e:
            logger.warning(f"네이버 API 실패 ({stock_id}): {e}")
            return []

        items: list[dict] = []
        for raw in data.get("items", [])[:max_items]:
            title = _clean_html(raw.get("title", ""))
            desc  = _clean_html(raw.get("description", ""))
            link  = raw.get("link", "").strip()
            if not title:
                continue
            sentiment = _keyword_sentiment(title + " " + desc)
            items.append({
                "headline":     title,
                "sentiment":    sentiment,
                "relevance":    0.85,
                "source":       "네이버 뉴스",
                "published_at": _parse_naver_pubdate(raw.get("pubDate", "")),
                "link":         link,
                "themes":       [],
                "_mock":        False,
                **classify_news_item(title),
            })

        logger.info(f"네이버 뉴스 수집 ({stock_id}): {len(items)}건")
        return items

    # ── yfinance 뉴스 ─────────────────────────────────────────────────────────

    def _collect_yfinance(
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
                items: list[dict] = []
                for n in news_raw[:max_per_stock]:
                    # yfinance v0.2+ 구조: content.title / 구버전: title
                    content  = n.get("content", {}) if isinstance(n.get("content"), dict) else {}
                    headline = (
                        content.get("title")
                        or n.get("title")
                        or n.get("headline", "")
                    )
                    if not headline:
                        continue
                    # 링크: canonicalUrl > clickThroughUrl > link
                    link = (
                        (content.get("canonicalUrl") or {}).get("url", "")
                        or (content.get("clickThroughUrl") or {}).get("url", "")
                        or n.get("link", "")
                    )
                    pub_ts = (
                        content.get("pubDate")
                        or n.get("providerPublishTime")
                        or n.get("published", 0)
                    )
                    if isinstance(pub_ts, str):
                        published_at = pub_ts
                    elif pub_ts:
                        published_at = datetime.fromtimestamp(int(pub_ts)).isoformat()
                    else:
                        published_at = now_kst().isoformat()

                    publisher = (
                        (content.get("provider") or {}).get("displayName", "")
                        or n.get("publisher", "Yahoo Finance")
                    )
                    items.append({
                        "headline":     headline,
                        "sentiment":    _keyword_sentiment(headline),
                        "relevance":    0.85,
                        "source":       publisher,
                        "published_at": published_at,
                        "link":         link,
                        "themes":       [],
                        "_mock":        False,
                        **classify_news_item(headline),
                    })
                result[sid] = items if items else self._mock_for_stock(sid, max_per_stock)
            except Exception as e:
                logger.warning(f"yfinance 뉴스 실패 ({sid}): {e} → Mock 폴백")
                result[sid] = self._mock_for_stock(sid, max_per_stock)

        return result

    # ── Mock 데이터 ───────────────────────────────────────────────────────────

    def _collect_mock(
        self, stock_ids: list[str], max_per_stock: int
    ) -> dict[str, list[dict]]:
        targets_set = set(stock_ids)
        result: dict[str, list[dict]] = {}
        now = now_kst()

        for news in _NEWS_POOL:
            relevant = news["stock_ids"]
            affected = (
                [s for s in relevant if s in targets_set]
                if relevant
                else list(targets_set)
            )
            pub_offset   = random.randint(0, 480)
            published_at = (now - timedelta(minutes=pub_offset)).isoformat()
            _hl = news["headline"][:80]
            _search_link = (
                f"https://news.google.com/search?q={_url_quote(_hl)}&hl=ko&gl=KR"
            )
            for sid in affected:
                result.setdefault(sid, [])
                if len(result[sid]) < max_per_stock:
                    result[sid].append({
                        "headline":     news["headline"],
                        "sentiment":    news["sentiment"],
                        "relevance":    news["relevance"],
                        "source":       news["source"],
                        "published_at": published_at,
                        "link":         _search_link,
                        "themes":       news["themes"],
                        "_mock":        True,
                        **classify_news_item(news["headline"]),
                    })
        return result

    def _mock_for_stock(self, sid: str, max_per_stock: int) -> list[dict]:
        """단일 종목 Mock 뉴스 반환"""
        return self._collect_mock([sid], max_per_stock).get(sid, [])
