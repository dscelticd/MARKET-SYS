"""
사용자 관전 포인트(watchlist.json의 memo 필드) 리포트 반영 테스트

배경: 구조 점검 중 발견 — watchlist.json의 종목별 memo(사용자가 직접 남긴 관심
      포인트, 예: "Starlink 가입자 성장 모니터링")가 대시보드 편집 화면에만 쓰이고
      Claude 리포트 프롬프트에는 전혀 전달되지 않고 있었음.
"""
from __future__ import annotations

from app.reports.report_builder import _format_memo_block


def test_memo_block_includes_stocks_with_memo():
    stocks = [
        {"name": "SpaceX", "memo": "Starlink 가입자 성장 및 Falcon 9 발사 수주 모니터링"},
        {"name": "삼성전자", "memo": "HBM3E 공급 확대 모니터링"},
    ]
    block = _format_memo_block(stocks)
    assert "SpaceX" in block
    assert "Starlink" in block
    assert "삼성전자" in block


def test_memo_block_skips_stocks_without_memo():
    stocks = [
        {"name": "A사", "memo": "관전 포인트 있음"},
        {"name": "B사", "memo": ""},
        {"name": "C사"},  # memo 키 자체가 없는 경우
    ]
    block = _format_memo_block(stocks)
    assert "A사" in block
    assert "B사" not in block
    assert "C사" not in block


def test_memo_block_handles_empty_or_none():
    assert "등록된 관전 포인트 없음" in _format_memo_block(None)
    assert "등록된 관전 포인트 없음" in _format_memo_block([])


def test_memo_block_all_stocks_without_memo_shows_placeholder():
    stocks = [{"name": "A사"}, {"name": "B사", "memo": ""}]
    assert "등록된 관전 포인트 없음" in _format_memo_block(stocks)
