"""
KIS(한국투자증권) Open API — 국내 종목 투자자별(개인/외국인/기관) 순매수 수집

네이버 금융 스크래핑(비공식, price_collector.py의 _fetch_investor_flow)의 공식 대체
소스. 개인 순매수도 잔차 추정이 아닌 실측값을 직접 제공해 네이버 방식보다 정확하다.

사전 준비: 한국투자증권 계좌 개설(비대면 가능) → 홈페이지/앱에서 오픈API 서비스 신청
→ 앱키(App Key)/앱시크릿(App Secret) 발급. 모의투자 앱키도 실계좌 소유가 필요하지만
market_flow는 시세 조회만 하므로 모의투자 앱키로 충분(KIS_ENV=demo, 기본값).

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
from pathlib import Path

import requests

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


class KISCollector:
    def __init__(self) -> None:
        self.app_key = os.getenv("KIS_APP_KEY", "")
        self.app_secret = os.getenv("KIS_APP_SECRET", "")
        self.env = os.getenv("KIS_ENV", "demo")
        self.base_url = _BASE_URLS.get(self.env, _BASE_URLS["demo"])

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
