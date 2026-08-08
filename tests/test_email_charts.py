"""
이메일 캔들차트 인라인 첨부(cid: 참조) 테스트 — 실제 SMTP 연결 없이 MIME 구조만 검증
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.delivery.email_sender import EmailSender, _build_charts_html


def test_build_charts_html_includes_cid_references():
    chart_images = [
        {"stock_id": "KR_005930", "name": "삼성전자", "daily": b"fake-png-daily", "weekly": b"fake-png-weekly"},
    ]
    html = _build_charts_html(chart_images)
    assert "cid:chart_KR_005930_daily" in html
    assert "cid:chart_KR_005930_weekly" in html
    assert "삼성전자" in html


def test_build_charts_html_empty_when_no_charts():
    assert _build_charts_html(None) == ""
    assert _build_charts_html([]) == ""


def test_build_charts_html_skips_missing_image_kind():
    """daily만 있고 weekly가 None인 경우 weekly cid는 생성되지 않아야 함"""
    chart_images = [{"stock_id": "US_NVDA", "name": "NVIDIA", "daily": b"fake-png", "weekly": None}]
    html = _build_charts_html(chart_images)
    assert "cid:chart_US_NVDA_daily" in html
    assert "cid:chart_US_NVDA_weekly" not in html


def _make_sender() -> EmailSender:
    sender = EmailSender()
    sender.user = "test@example.com"
    sender.password = "app-password"
    sender.from_addr = "test@example.com"
    sender.to_addr = "recipient@example.com"
    return sender


def test_send_attaches_chart_images_as_mime_parts():
    sender = _make_sender()
    chart_images = [
        {"stock_id": "KR_005930", "name": "삼성전자", "daily": b"fake-png-bytes", "weekly": b"fake-png-bytes-2"},
    ]

    with patch("app.delivery.email_sender.smtplib.SMTP") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        ok = sender.send("제목", "본문", chart_images=chart_images)

        assert ok is True
        # HTML 파트는 한글 포함으로 base64 인코딩되어 cid: 문자열이 원문에 그대로
        # 나타나지 않음 — img 태그의 cid 참조 자체는 _build_charts_html 테스트에서
        # 별도 검증하고, 여기서는 이미지가 실제로 첨부되었는지(Content-ID 헤더)만 확인
        sent_msg_str = mock_server.sendmail.call_args[0][2]
        assert "Content-ID: <chart_KR_005930_daily>" in sent_msg_str
        assert "Content-ID: <chart_KR_005930_weekly>" in sent_msg_str
        assert sent_msg_str.count("Content-Type: image/png") == 2


def test_send_works_without_chart_images():
    """chart_images가 없어도(None) 이메일 발송 자체는 정상 동작해야 함"""
    sender = _make_sender()
    with patch("app.delivery.email_sender.smtplib.SMTP") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        ok = sender.send("제목", "본문")

        assert ok is True
        mock_server.sendmail.assert_called_once()
