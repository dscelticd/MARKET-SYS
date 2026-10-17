"""
KIS(한국투자증권) Open API — 국내 종목 투자자별(개인/외국인/기관) 순매수 수집

네이버 금융 스크래핑(비공식, price_collector.py의 _fetch_investor_flow)의 공식 대체
소스. 개인 순매수도 잔차 추정이 아닌 실측값을 직접 제공해 네이버 방식보다 정확하다.

사전 준비: 한국투자증권 계좌 개설(비대면 가능) → 홈페이지/앱에서 오픈API 서비스 신청
→ 앱키(App Key)/앱시크릿(App Secret) 발급. 모의투자 앱키도 실계좌 소유가 필요하다.
market_flow는 시세 조회만 하므로 모의투자 앱키(KIS_ENV=demo, 기본값)로도 동작하지만,
모의투자 서버의 종가는 마감 후 한동안 흔들리는 것이 실측됐다(2026-09-18 삼성전자
15:51 261,000원 → 16:28 260,000원). 확정 종가가 중요하므로 실전 앱키(KIS_ENV=real)를
권장한다 — 조회 전용 TR(FHKST01010400/01010900, FHPTJ04040000)은 두 환경이 같다.

API 참고: https://github.com/koreainvestment/open-trading-api
  - 토큰 발급: POST {base}/oauth2/tokenP (유효 24시간 — 파일 캐싱으로 재발급 최소화)
  - 투자자 매매동향: GET {base}/uapi/domestic-stock/v1/quotations/inquire-investor
    (tr_id=FHKST01010900, 응답 필드 prsn_ntby_qty/frgn_ntby_qty/orgn_ntby_qty)
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from app.utils.market_calendar import now_kst

logger = logging.getLogger(__name__)

_BASE_URLS = {
    "real": "https://openapi.koreainvestment.com:9443",
    "demo": "https://openapivts.koreainvestment.com:29443",
}
_TR_ID_INVESTOR = "FHKST01010900"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TOKEN_CACHE_FILE = _PROJECT_ROOT / "data" / "cache" / "kis_token.json"
_TOKEN_EXPIRY_BUFFER_SEC = 300  # 만료 5분 전부터는 미리 재발급 (경계값에서 요청 실패 방지)


def _summarize_daily_flows(daily: list[dict]) -> dict:
    """일별 개인/외국인/기관 순매수(주식수) 리스트(최신순) → 3/5/10/20일 누적 요약.
    KIS는 개인 순매수를 실측값으로 직접 제공 — price_collector.py의 네이버 기반
    _summarize_investor_flow()와 달리 잔차 추정이 아니므로 "_est" 접미사를 붙이지 않는다.
    """
    result: dict = {"_mock": False, "_source": "kis"}
    for days in (3, 5, 10, 20):
        window = daily[:days]
        if not window:
            continue
        result[f"institution_net_{days}d"] = sum(d["institution_net"] for d in window)
        result[f"foreign_net_{days}d"] = sum(d["foreign_net"] for d in window)
        result[f"individual_net_{days}d"] = sum(d["individual_net"] for d in window)
    return result


_TR_ID_MARKET_INDEX = "FHPTJ04040000"
_TR_ID_DAILY_PRICE = "FHKST01010400"
# 지수 코드 — FID_INPUT_ISCD가 실제 지수, FID_INPUT_ISCD_1은 시장 구분
_INDEX_CODES = {
    "KOSPI":  ("0001", "KSP"),
    "KOSDAQ": ("1001", "KSQ"),
}


def _is_completed_session(row: dict) -> bool:
    """장 마감이 끝난 실제 거래일 행인지 판별.

    장 시작 전에는 당일 행이 미리 만들어지는데 시가=고가=저가=종가이고 등락률이
    0.00이다(실측: 2026-09-01 08:23 조회 시 9/1 행이 전부 6820.02 = 8/31 종가).
    이 행을 그대로 쓰면 "오늘 지수 0.00%"라는 허위 데이터가 리포트에 들어간다.
    지수의 고가와 저가가 같은 날은 사실상 없으므로 이를 판별 기준으로 삼는다.
    """
    try:
        high = float(row.get("bstp_nmix_hgpr", 0))
        low = float(row.get("bstp_nmix_lwpr", 0))
        price = float(row.get("bstp_nmix_prpr", 0))
    except (TypeError, ValueError):
        return False
    return price > 0 and high != low


class KISCollector:
    def __init__(self) -> None:
        self.app_key = os.getenv("KIS_APP_KEY", "")
        self.app_secret = os.getenv("KIS_APP_SECRET", "")
        self.env = (os.getenv("KIS_ENV") or "demo").strip().lower()
        if self.env not in _BASE_URLS:
            # "prod", "실전" 등으로 잘못 넣으면 예전에는 조용히 모의투자 서버로
            # 붙었다 — 실전 앱키로 모의 서버에 접속하면 토큰 발급부터 실패하고,
            # 원인이 KIS_ENV 오타라는 사실은 어디에도 드러나지 않는다.
            logger.error(
                "[KIS_CONFIG] KIS_ENV=%r 은 알 수 없는 값 — 'real'(실전) 또는 'demo'(모의)만 "
                "허용됩니다. 모의투자 서버로 접속합니다.", self.env,
            )
            self.env = "demo"
        self.base_url = _BASE_URLS[self.env]

    def is_configured(self) -> bool:
        return bool(self.app_key and self.app_secret)

    # ── 토큰 발급/캐싱 ───────────────────────────────────────────────────────

    def _read_cached_token(self) -> str | None:
        if not _TOKEN_CACHE_FILE.exists():
            return None
        try:
            data = json.loads(_TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
            if data.get("app_key") != self.app_key:
                return None  # 앱키가 바뀌었으면 캐시 무효
            if data.get("env", "demo") != self.env:
                return None  # 모의↔실전 전환 — 다른 서버가 발급한 토큰은 쓸 수 없다
            if data.get("expires_at", 0) > time.time() + _TOKEN_EXPIRY_BUFFER_SEC:
                return data["access_token"]
        except Exception:
            pass
        return None

    def _write_cached_token(self, token: str, expires_in: int) -> None:
        _TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_CACHE_FILE.write_text(
            json.dumps({
                "app_key": self.app_key,
                "env": self.env,
                "access_token": token,
                "expires_at": time.time() + expires_in,
            }),
            encoding="utf-8",
        )

    def get_token(self) -> str | None:
        """접근토큰 발급 — 24시간 유효, 파일 캐싱으로 재사용(짧은 간격 재발급 시
        KIS가 동일 토큰을 반환하지만 불필요한 호출 자체를 줄이기 위해 캐싱).
        """
        if not self.is_configured():
            return None
        cached = self._read_cached_token()
        if cached:
            return cached
        try:
            resp = requests.post(
                f"{self.base_url}/oauth2/tokenP",
                data=json.dumps({
                    "grant_type": "client_credentials",
                    "appkey": self.app_key,
                    "appsecret": self.app_secret,
                }),
                headers={"content-type": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data["access_token"]
            self._write_cached_token(token, int(data.get("expires_in", 86400)))
            return token
        except Exception as e:
            logger.warning("KIS 토큰 발급 실패: %s", e)
            return None

    # ── 투자자별 순매수 조회 ─────────────────────────────────────────────────

    def fetch_investor_flow(self, ticker: str) -> dict:
        """KR 종목 하나의 일별 개인/외국인/기관 순매수(주식수)를 조회해 3/5/10/20일
        누적 요약을 반환. 실패 시 예외를 던져 호출부에서 네이버/Mock으로 폴백하게 함.
        """
        token = self.get_token()
        if not token:
            raise RuntimeError("KIS 토큰 발급 실패 — KIS_APP_KEY/KIS_APP_SECRET 확인 필요")

        resp = requests.get(
            f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-investor",
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": _TR_ID_INVESTOR,
            },
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"KIS API 오류: {data.get('msg1', '알 수 없는 오류')}")

        rows = data.get("output", [])
        daily = []
        for r in rows:
            try:
                daily.append({
                    "institution_net": int(r["orgn_ntby_qty"]),
                    "foreign_net": int(r["frgn_ntby_qty"]),
                    "individual_net": int(r["prsn_ntby_qty"]),
                })
            except (KeyError, ValueError, TypeError):
                continue

        if len(daily) < 3:
            raise ValueError(f"KIS 투자자 데이터 부족 (rows={len(daily)})")
        return _summarize_daily_flows(daily)

    # ── 국내 지수 (KOSPI/KOSDAQ) ─────────────────────────────────────────────

    def fetch_market_index(self, name: str, target_date: str | None = None) -> dict:
        """KOSPI/KOSDAQ의 최근 완료된 거래일 지수를 반환.

        yfinance의 ^KS11·^KQ11 피드가 거래일을 통째로 누락하는 사고가 반복돼 도입했다.
        실측(2026-09-01): yfinance ^KS11이 8/27에 멈춰 8/28·8/31 두 거래일을 빠뜨린
        채 "현재 6912.37 (+1.53%)"로 응답했으나, 실제 8/31 종가는 6820.02(-1.34% 누적)였다.
        같은 시점 삼성전자 등 개별 종목은 8/31까지 정상이라 지수만 어긋났고,
        그 결과 리포트가 "미국 하락 + 한국 상승 디커플링"이라는 없는 서사를 만들었다.

        target_date("YYYY-MM-DD")를 주면 **그 거래일 행만** 반환한다. 이것이 없으면
        장중 값이 통과한다 — _is_completed_session()은 장전 자리표시자(시가=고가=저가)
        만 걸러낼 뿐, 개장 3분 뒤의 행은 고가≠저가라 "완료"로 통과하기 때문이다.
        실측(2026-09-02 09:03 실행): 대상 세션이 9/1인데 KOSPI가 9/2 장중값
        6630.53(-3.00%)으로 수집돼, "9월 1일 종가 기준"이라 선언한 리포트에 들어갔다.

        실패 시 예외를 던져 호출부가 yfinance로 폴백하게 한다.
        반환: {"value", "change_pct", "data_date"}
        """
        if name not in _INDEX_CODES:
            raise ValueError(f"지원하지 않는 지수: {name}")
        token = self.get_token()
        if not token:
            raise RuntimeError("KIS 토큰 발급 실패")

        iscd, market = _INDEX_CODES[name]
        today = now_kst().strftime("%Y%m%d")
        resp = requests.get(
            f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market",
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": _TR_ID_MARKET_INDEX,
            },
            params={
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": iscd,
                "FID_INPUT_DATE_1": today,
                "FID_INPUT_ISCD_1": market,
                "FID_INPUT_DATE_2": today,
                "FID_INPUT_ISCD_2": market,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"KIS 지수 조회 오류: {data.get('msg1', '알 수 없는 오류')}")

        for row in data.get("output") or []:
            if not _is_completed_session(row):
                continue  # 장 시작 전 자리표시자 행 건너뜀
            raw_date = str(row.get("stck_bsop_date", ""))
            if len(raw_date) != 8:
                continue
            bar_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            if target_date and bar_date != target_date:
                continue   # 대상 거래일이 아닌 행(장중 당일 포함)은 쓰지 않는다
            return {
                "value": round(float(row["bstp_nmix_prpr"]), 2),
                "change_pct": round(float(row["bstp_nmix_prdy_ctrt"]), 2),
                "data_date": bar_date,
            }
        raise ValueError(
            f"{name}: {target_date or '완료된'} 거래일 데이터를 찾지 못함"
        )

    def _daily_price_rows(self, ticker: str, retries: int = 3) -> list[dict]:
        """inquire-daily-price 원본 행(최신순, 최대 30거래일). 장중이면 첫 행은 미확정."""
        token = self.get_token()
        if not token:
            raise RuntimeError("KIS 토큰 발급 실패")

        # KIS 데모 서버는 타임아웃이 간헐적으로 발생한다(실측: 연속 호출 3회 중
        # 2회 ReadTimeout, 3회째 성공). 관심종목 7개를 매번 조회해야 하므로
        # 짧은 재시도 없이는 결국 yfinance 폴백으로 도망치는 빈도가 높아져
        # KIS를 쓰는 의미가 줄어든다.
        last_exc: Exception | None = None
        data: dict | None = None
        for attempt in range(retries + 1):
            try:
                resp = requests.get(
                    f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-price",
                    headers={
                        "content-type": "application/json",
                        "authorization": f"Bearer {token}",
                        "appkey": self.app_key,
                        "appsecret": self.app_secret,
                        "tr_id": _TR_ID_DAILY_PRICE,
                    },
                    params={
                        "FID_COND_MRKT_DIV_CODE": "J",
                        "FID_INPUT_ISCD": ticker,
                        "FID_PERIOD_DIV_CODE": "D",
                        "FID_ORG_ADJ_PRC": "0",
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("rt_cd") != "0":
                    raise RuntimeError(f"KIS 종목시세 조회 오류: {data.get('msg1', '알 수 없는 오류')}")
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(1.5)
        if last_exc is not None or data is None:
            raise last_exc or RuntimeError("KIS 종목시세 조회 실패")
        return data.get("output") or []

    def fetch_stock_price(
        self, ticker: str, target_date: str | None = None, retries: int = 3
    ) -> dict:
        """국내 개별 종목의 종가를 KIS 공식 API(체결 데이터)로 직접 조회한다.

        yfinance의 국내 종목 종가 발표가 수 시간~다음날까지 늦어지는 사례가 반복
        관측됐다. 실측(2026-09-14~18, 5거래일 연속): 저녁 결산이 한국장 마감
        **9시간 뒤**(00:38 실행)에도 yfinance에 당일 종가가 없어 관심종목 7개
        전부가 "데이터 미도착"으로 리포트에서 빠졌다. 아침 브리핑도 미국장 마감
        4시간 뒤(09:13 실행)에 NVDA·QQQ 같은 초대형주조차 전일 데이터가 없었다
        (13:30 재조회 시점엔 이미 올라와 있었다 — 코드 결함이 아니라 발표 지연이다).

        지수에 KIS를 쓴 것과 같은 이유로 개별 종목에도 KIS를 쓴다. 증권사 자체
        체결 데이터라 제3자 집계 지연이 없다. prev_close는 추정하지 않고 API가
        함께 주는 직전 거래일 행에서 그대로 가져온다.

        target_date("YYYY-MM-DD")를 주면 그 거래일 행만 채택한다 — 오늘 자리의
        행은 장중이면 미확정 값이라 반드시 걸러야 한다(지수 조회에서 겪은 것과
        같은 함정).

        실패 시 예외를 던져 호출부가 yfinance로 폴백하게 한다.
        반환: {"value", "prev_close", "change_pct", "volume", "data_date"}
        """
        rows = self._daily_price_rows(ticker, retries)
        for i, row in enumerate(rows):
            raw_date = str(row.get("stck_bsop_date", ""))
            if len(raw_date) != 8:
                continue
            bar_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            if target_date and bar_date != target_date:
                continue   # 대상 거래일이 아닌 행(장중 당일 포함)은 쓰지 않는다
            try:
                close = float(row["stck_clpr"])
            except (KeyError, ValueError, TypeError):
                continue
            if close <= 0:
                continue   # 자리표시자/미확정 행

            prev_close = None
            if i + 1 < len(rows):
                try:
                    pc = float(rows[i + 1]["stck_clpr"])
                    prev_close = pc if pc > 0 else None
                except (KeyError, ValueError, TypeError):
                    prev_close = None

            # 시가·고가·저가도 함께 반환한다. 이게 없으면 호출부(price_collector)가
            # Open=High=Low=Close로 뭉갠 납작한 봉을 만들게 된다 — 캔들 패턴
            # 판정(_classify_candle)이 KIS를 쓴 날마다 매번 도지(십자형)로
            # 잘못 나오는 결과였다. KIS를 개별 종목의 사실상 매번 쓰는
            # 소스로 바꾼 뒤부터는 "가끔의 결함"이 아니라 "상시 결함"이 된다.
            def _num(key: str) -> float | None:
                try:
                    v = float(row.get(key, 0) or 0)
                    return v if v > 0 else None
                except (TypeError, ValueError):
                    return None

            return {
                "value": close,
                "open": _num("stck_oprc"),
                "high": _num("stck_hgpr"),
                "low":  _num("stck_lwpr"),
                "prev_close": prev_close,
                "change_pct": round(float(row.get("prdy_ctrt", 0) or 0), 2),
                "volume": int(float(row.get("acml_vol", 0) or 0)),
                "data_date": bar_date,
            }
        raise ValueError(f"{ticker}: {target_date or '완료된'} 거래일 데이터를 찾지 못함")

    def fetch_daily_bars(
        self, ticker: str, through_date: str, retries: int = 3
    ) -> list[dict]:
        """국내 종목의 최근 일봉(최대 30거래일)을 확정치로 반환 — 오래된 것부터.

        through_date("YYYY-MM-DD") 이후의 행은 버린다. 그 행은 진행 중인 세션이라
        종가가 아니다(계약 C2). 시가·고가·저가·종가 중 하나라도 0 이하인 행도
        버린다 — 개장 전 자리표시자 행이다.

        왜 대상일 하루가 아니라 30일인가: yfinance는 국내 종목의 최근 종가를
        장 마감 동시호가 전 체결가로 잡아 두고 나중에 고친다. 실측(2026-10-17,
        10/16 종가): 삼성전자 KIS 217,000 vs yfinance 215,500, SK하이닉스
        1,265,000 vs 1,272,000, LG전자·LS ELECTRIC도 어긋났다(그 이전 29일과
        고가·저가는 일치). 대상일 한 봉만 KIS로 바꾸면 그 앞 봉은 고쳐지기 전
        값일 수 있고, 차트는 통째로 yfinance라 본문 숫자와 마지막 봉이 달랐다.

        수정주가 기준(FID_ORG_ADJ_PRC=0)은 액면분할 등만 반영하고 배당은 반영하지
        않는다 — 국내 HTS·포털 차트와 같은 기준이다. 이어 붙이는 yfinance 쪽도
        auto_adjust=False(배당 미반영)로 받아야 이음매에 단차가 생기지 않는다.
        """
        bars: list[dict] = []
        for row in self._daily_price_rows(ticker, retries):
            raw_date = str(row.get("stck_bsop_date", ""))
            if len(raw_date) != 8:
                continue
            bar_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            if bar_date > through_date:
                continue
            try:
                o, h, l, c = (float(row[k]) for k in ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_clpr"))
            except (KeyError, TypeError, ValueError):
                continue
            if min(o, h, l, c) <= 0:
                continue
            bars.append({
                "date": bar_date, "open": o, "high": h, "low": l, "close": c,
                "volume": int(float(row.get("acml_vol", 0) or 0)),
                "change_pct": round(float(row.get("prdy_ctrt", 0) or 0), 2),
            })
        bars.sort(key=lambda b: b["date"])
        if not bars:
            raise ValueError(f"{ticker}: {through_date}까지의 확정 일봉이 없음")
        return bars
