"""
DART 공시 연동 테스트

배경: disclosure_collector.py는 이미 완성돼 있었으나 main.py/report_builder.py
      어디에도 연결되지 않아 리포트에 "공시 연동: 🔶 미연동"으로만 표시되던 상태였음.
      DataValidator.validate()와 report_builder의 프롬프트 블록에 연결.
"""
from __future__ import annotations

from app.utils.data_validator import DataValidator
from app.reports.report_builder import _format_disclosure_block


_STOCKS = [{"id": "KR_005930"}]


def _price_data() -> dict:
    return {"KR_005930": {"change_pct": 0.0, "_mock": False}}


def _macro_data() -> dict:
    return {
        "_mock": False, "us_market": {}, "kr_market": {"KOSPI": {"value": 2650, "change_pct": 0.4}},
        "currencies": {}, "rates": {}, "sentiment": {},
    }


def test_disclosure_connected_flag_reflected_in_quality():
    validator = DataValidator()
    quality = validator.validate(_price_data(), {}, _macro_data(), _STOCKS, disclosure_connected=True)
    assert quality["disclosures"]["connected"] is True

    section = validator.format_report_section(quality)
    assert "연동됨" in section
    # 연동됐는데도 "미연동"이라고 잘못 표시되던 회귀 버그 재발 방지
    assert "🔶 미연동" not in section


def test_disclosure_not_connected_shows_correct_label():
    validator = DataValidator()
    quality = validator.validate(_price_data(), {}, _macro_data(), _STOCKS, disclosure_connected=False)
    assert quality["disclosures"]["connected"] is False

    section = validator.format_report_section(quality)
    assert "🔶 미연동" in section
    assert "✅ 연동됨" not in section


def test_format_disclosure_block_lists_items_per_stock():
    disclosure_data = {
        "KR_005930": [
            {"corp_name": "삼성전자", "title": "주요사항보고서(자기주식취득결정)", "rcept_dt": "20260805"},
            {"corp_name": "삼성전자", "title": "분기보고서", "rcept_dt": "20260801"},
        ],
    }
    block = _format_disclosure_block(disclosure_data)
    assert "삼성전자" in block
    assert "자기주식취득결정" in block


def test_format_disclosure_block_caps_at_three_per_stock():
    items = [
        {"corp_name": "삼성전자", "title": f"공시{i}", "rcept_dt": "20260805"} for i in range(5)
    ]
    block = _format_disclosure_block({"KR_005930": items})
    assert block.count("공시") == 3


def test_format_disclosure_block_handles_empty_or_none():
    assert "공시 데이터 없음" in _format_disclosure_block(None)
    assert "공시 데이터 없음" in _format_disclosure_block({})
    assert "공시 데이터 없음" in _format_disclosure_block({"KR_005930": []})
