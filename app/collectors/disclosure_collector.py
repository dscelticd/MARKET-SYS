"""
Disclosure Collector — 금융감독원 DART 공시 수집 (스텁)

사용 방법:
  1. https://opendart.fss.or.kr 에서 API 키 발급
  2. .env 에 DART_API_KEY=발급받은키 설정
  3. _collect_real() 구현 시 이 파일에 추가

현재 상태: DART_API_KEY 없을 경우 빈 결과 반환
"""
from __future__ import annotations

import json as _json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from app.utils.market_calendar import now_kst

logger = logging.getLogger(__name__)

# 한국 종목 고유번호(corp_code) 매핑 — DART 전자공시 기준
# https://opendart.fss.or.kr/api/company.json?corp_code= 로 조회 가능
#
# 2026-08-10 전수 재검증: DART corpCode.xml 전체 목록(opendart.fss.or.kr/api/corpCode.xml)을
# 내려받아 stock_code 기준으로 대조한 결과 7개 중 3개가 잘못된 값으로 확인됨 —
#   - KR_010120(LS ELECTRIC)의 기존 값 00258801은 실제로는 카카오(035720)의 코드였음
#     (실제 API 호출 결과 카카오 공시가 LS ELECTRIC 자리에 노출되는 것으로 확인)
#   - KR_015760(한국전력) 기존 값 00104747, KR_138080(오이솔루션) 기존 값 01043688은
#     DART에 존재하지 않는 코드라 조용히 0건만 반환되고 있었음
# KODEX 200(069500)은 ETF라 개별 기업 공시 대상이 아니어서(stock_code로 매칭되는
# corp_code 없음) 매핑에서 제외 — 무리하게 자산운용사 코드를 넣으면 KODEX 200과
# 무관한 회사 공시가 섞여 나오는 동일한 문제가 재발함.
_CORP_CODE: dict[str, str] = {
    "KR_005930": "00126380",   # 삼성전자
    "KR_000660": "00164779",   # SK하이닉스
    "KR_010120": "00105855",   # LS ELECTRIC (엘에스일렉트릭)
    "KR_015760": "00159193",   # 한국전력공사
    "KR_066570": "00401731",   # LG전자
    "KR_138080": "00571483",   # 오이솔루션
}

_DART_BASE = "https://opendart.fss.or.kr/api"


class DisclosureCollector:
    def __init__(self) -> None:
        self.api_key = os.getenv("DART_API_KEY", "")

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def collect(self, stock_ids: list[str] | None = None) -> dict[str, list[dict]]:
        """공시 수집 — DART_API_KEY 없으면 빈 결과 반환"""
        kr_ids = [s for s in (stock_ids or list(_CORP_CODE.keys())) if s.startswith("KR_")]
        if not self.is_configured():
            logger.debug("DART_API_KEY 미설정 — 공시 수집 건너뜀")
            return {sid: [] for sid in kr_ids}
        return self._collect_real(kr_ids)

    def _collect_real(self, stock_ids: list[str]) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        # 최근 7일 공시 수집
        end_dt   = now_kst()
        start_dt = end_dt - timedelta(days=7)
        bgn_de   = start_dt.strftime("%Y%m%d")
        end_de   = end_dt.strftime("%Y%m%d")

        for sid in stock_ids:
            corp_code = _CORP_CODE.get(sid)
            if not corp_code:
                result[sid] = []
                continue
            try:
                params = urllib.parse.urlencode({
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bgn_de":    bgn_de,
                    "end_de":    end_de,
                    "last_reprt_at": "Y",
                })
                url = f"{_DART_BASE}/list.json?{params}"
                with urllib.request.urlopen(url, timeout=8) as r:
                    data = _json.loads(r.read())

                items: list[dict] = []
                for d in (data.get("list") or [])[:5]:
                    items.append({
                        "title":        d.get("report_nm", ""),
                        "corp_name":    d.get("corp_name", ""),
                        "rcept_dt":     d.get("rcept_dt", ""),
                        "rcept_no":     d.get("rcept_no", ""),
                        "link":         f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={d.get('rcept_no','')}",
                        "disclosure_type": d.get("pblntf_ty", ""),
                    })
                result[sid] = items
                logger.info("DART 공시 수집 (%s): %d건", sid, len(items))
            except Exception as e:
                logger.warning("DART 공시 수집 실패 (%s): %s", sid, e)
                result[sid] = []

        return result
