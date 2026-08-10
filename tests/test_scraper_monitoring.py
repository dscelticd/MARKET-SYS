"""
네이버 수급 스크래핑 실패율 모니터링 테스트

배경: 우선순위 로드맵 3순위 — 네이버 금융 수급 스크래핑은 비공식 소스라 페이지
      구조가 바뀌면 조용히 Mock으로 폴백되는데, 실패가 며칠째 이어져도 사용자가
      알 방법이 없었음. 실패율이 임계값(70%) 이상이면 텔레그램으로 경고한다.
"""
from __future__ import annotations

from unittest.mock import patch

from app.main import check_kr_investor_flow_failure_rate
from app.utils.telegram_notifier import TelegramNotifier


def _price(sid: str, mock_flow: bool | None) -> dict:
    d = {"stock_id": sid}
    if mock_flow is not None:
        d["investor_flow"] = {"_mock": mock_flow}
    return d


def test_failure_rate_counts_mock_entries():
    price_data = {
        "KR_005930": _price("KR_005930", False),
        "KR_000660": _price("KR_000660", True),
        "KR_069500": _price("KR_069500", True),
        "US_NVDA": _price("US_NVDA", None),  # 해외 종목은 애초에 investor_flow 없음 → 제외
    }
    result = check_kr_investor_flow_failure_rate(price_data)
    assert result == (2, 3)  # 3개 KR 종목 중 2개 실패


def test_failure_rate_returns_none_when_no_kr_stocks():
    price_data = {"US_NVDA": _price("US_NVDA", None)}
    assert check_kr_investor_flow_failure_rate(price_data) is None


def test_failure_rate_returns_none_when_investor_flow_missing():
    """investor_flow 키 자체가 없는 경우(예: 아직 수집 전) None 처리"""
    price_data = {"KR_005930": {"stock_id": "KR_005930"}}
    assert check_kr_investor_flow_failure_rate(price_data) is None


def test_all_success_counts_zero_failures():
    price_data = {
        "KR_005930": _price("KR_005930", False),
        "KR_000660": _price("KR_000660", False),
    }
    assert check_kr_investor_flow_failure_rate(price_data) == (0, 2)


def _make_notifier() -> TelegramNotifier:
    n = TelegramNotifier()
    n.token = "test-token"
    n.chat_id = "12345"
    return n


def test_notify_scraper_failure_sends_when_configured():
    notifier = _make_notifier()
    with patch.object(notifier, "_send", return_value=True) as mock_send:
        ok = notifier.notify_scraper_failure("네이버 금융(외국인/기관 수급)", 5, 6, "morning")
        assert ok is True
        mock_send.assert_called_once()
        sent_text = mock_send.call_args[0][0]
        assert "5/6" in sent_text


def test_notify_scraper_failure_noop_when_not_configured():
    notifier = TelegramNotifier()
    notifier.token = ""
    notifier.chat_id = ""
    assert notifier.notify_scraper_failure("소스", 5, 6, "morning") is False
