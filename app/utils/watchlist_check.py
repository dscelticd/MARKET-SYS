"""
관심종목 설정 점검 — config/watchlist.json과 수집기 심볼 매핑의 어긋남을 잡는다.

관심종목은 두 곳에 산다:
  - config/watchlist.json          — 대시보드 '📋 종목 관리' 탭이 편집하는 목록
  - price_collector.YFINANCE_MAP   — 실제 시세를 받아올 심볼 (코드에 하드코딩)

대시보드에서 종목을 추가해도 YFINANCE_MAP은 바뀌지 않는다. 빠뜨리면 경고 로그
한 줄만 남기고 그 종목이 매 리포트에서 "데이터 미도착"으로 빠진다 — 이번 주에
잡았던 결측 배너와 겉보기가 똑같아 원인을 찾기 어렵다.

시장 판정도 함께 본다. 대상 거래일은 종목 ID 접두사로 정해지는데(KR_ → 한국장,
그 외 → 미국장), 미국 외 거래소(도쿄 .T, 홍콩 .HK 등) 종목을 넣으면 미국 거래일
기준으로 수집돼 날짜가 어긋난다. TSMC(TW_TSM)는 NYSE ADR이라 문제없다.
"""
from __future__ import annotations

from app.collectors.price_collector import YFINANCE_MAP

_KR_SUFFIXES = (".KS", ".KQ")


def validate_watchlist(stocks: list[dict], symbol_map: dict | None = None) -> list[str]:
    """문제 목록(사람이 읽을 문장). 비어 있으면 정상."""
    symbol_map = YFINANCE_MAP if symbol_map is None else symbol_map
    problems: list[str] = []
    for s in stocks:
        sid = str(s.get("id", ""))
        label = f"{s.get('name', sid)}({sid})"
        is_kr_id = sid.startswith("KR")

        country = str(s.get("country", ""))
        if country and (country == "KR") != is_kr_id:
            problems.append(
                f"{label}: 국가({country})와 ID 접두사가 맞지 않음 — "
                "대상 거래일은 ID 접두사로 정해지므로 엉뚱한 시장 기준으로 수집됨"
            )

        mapping = symbol_map.get(sid)
        if not mapping:
            problems.append(
                f"{label}: 심볼 매핑 없음 — 매 리포트에서 결측 처리됨. "
                "app/collectors/price_collector.py의 YFINANCE_MAP에 추가 필요"
            )
            continue

        sym = str(mapping[0])
        if is_kr_id and not sym.endswith(_KR_SUFFIXES):
            problems.append(
                f"{label}: 국내 종목인데 심볼 '{sym}'에 .KS/.KQ 접미사가 없음 "
                "(코스피 .KS, 코스닥 .KQ)"
            )
        if not is_kr_id and "." in sym:
            problems.append(
                f"{label}: '{sym}'은 미국 외 거래소 심볼로 보임 — 현재는 한국·미국 장만 "
                "구분하므로 미국 거래일 기준으로 수집돼 날짜가 어긋남"
            )

        ticker = str(s.get("ticker", "")).upper()
        bare = sym.upper()
        for suffix in _KR_SUFFIXES:
            bare = bare.removesuffix(suffix)
        if ticker and ticker != bare:
            problems.append(
                f"{label}: watchlist 티커 '{s.get('ticker')}'와 수집 심볼 '{sym}'이 다름"
            )
    return problems
