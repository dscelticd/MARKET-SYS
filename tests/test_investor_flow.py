"""
수급(외국인/기관/개인 순매매) 데이터 파싱·집계 로직 테스트

배경: 관심 종목 테마 점검 중 사용자 요청으로 실제 수급 데이터 소스를 조사한 결과,
      pykrx(공식 라이브러리)는 최근 KRX 정책 변경으로 로그인 세션(KRX_ID/KRX_PW)이
      없으면 요청 자체가 차단됨을 확인. 대안으로 로그인 없이 동작하는 네이버 금융
      (finance.naver.com/item/frgn.naver) 페이지를 BeautifulSoup으로 파싱하는 방식을
      채택함. 이 테스트는 네트워크 호출 없이 파싱·집계 로직만 검증한다.
"""
from __future__ import annotations

from app.collectors.price_collector import (
    _parse_frgn_table,
    _summarize_investor_flow,
    _mock_investor_flow,
)
from app.reports.report_builder import _format_investor_flow_block


def _sample_row_html(date: str, institution: int, foreign: int) -> str:
    """frgn.naver 페이지의 실제 td 구조(9열)를 모사한 한 행"""
    return f"""
    <tr>
        <td class="date">{date}</td>
        <td class="num">231,000</td>
        <td class="num">상승 500</td>
        <td class="num">+0.22%</td>
        <td class="num">20,424,708</td>
        <td class="num">{institution:,}</td>
        <td class="num">{foreign:,}</td>
        <td class="num">2,724,390,000</td>
        <td class="num">46.60%</td>
    </tr>
    """


def _sample_page_html(rows: list[tuple[str, int, int]]) -> str:
    body_rows = "".join(_sample_row_html(d, i, f) for d, i, f in rows)
    # 실제 페이지처럼 다른 type2 테이블(매도상위 등)이 먼저 나오도록 배치해
    # width=680 속성으로 정확한 테이블을 골라내는지 검증
    return f"""
    <html><body>
        <table class="type2"><tr><td>다른 매도상위 테이블 — 혼동 유발용</td></tr></table>
        <table width="680" cellspacing="0" class="type2">
            <tbody>
            <tr><th>날짜</th><th>종가</th><th>전일비</th><th>등락률</th>
                <th>거래량</th><th>기관순매매량</th><th>외국인순매매량</th>
                <th>외국인보유주수</th><th>외국인보유율</th></tr>
            {body_rows}
            </tbody>
        </table>
    </body></html>
    """


def test_parse_frgn_table_extracts_valid_date_rows():
    html = _sample_page_html([
        ("2026.08.07", 36604, -1737367),
        ("2026.08.06", -1220335, -3134136),
        ("2026.08.05", -2078706, 2298577),
    ])
    rows = _parse_frgn_table(html)
    assert len(rows) == 3
    assert rows[0] == {"institution_net": 36604, "foreign_net": -1737367}
    assert rows[2] == {"institution_net": -2078706, "foreign_net": 2298577}


def test_parse_frgn_table_ignores_other_type2_tables():
    """페이지 내 다른 type2 테이블(매도상위 등)의 행을 잘못 파싱하지 않아야 함"""
    html = _sample_page_html([("2026.08.07", 100, 200)])
    rows = _parse_frgn_table(html)
    assert len(rows) == 1  # "다른 매도상위" 텍스트 행이 섞여 들어오지 않음


def test_parse_frgn_table_returns_empty_when_table_missing():
    rows = _parse_frgn_table("<html><body>no table here</body></html>")
    assert rows == []


def test_summarize_investor_flow_computes_cumulative_windows():
    daily = [
        {"institution_net": 100, "foreign_net": 200},
        {"institution_net": -50, "foreign_net": 300},
        {"institution_net": 10, "foreign_net": -20},
    ]
    summary = _summarize_investor_flow(daily)
    assert summary["_mock"] is False
    # 3일 누적 = 전체 3개 행 합산
    assert summary["institution_net_3d"] == 60
    assert summary["foreign_net_3d"] == 480
    # 개인(추정) = -(기관+외국인)
    assert summary["individual_net_3d_est"] == -(60 + 480)
    # 데이터가 3개뿐이므로 5/10/20일 키는 3일치로 동일하게 채워짐(윈도우가 리스트 길이로 clamp)
    assert summary["institution_net_5d"] == 60


def test_summarize_investor_flow_skips_windows_with_no_data():
    summary = _summarize_investor_flow([])
    assert summary == {"_mock": False}


def test_mock_investor_flow_has_consistent_individual_estimate():
    flow = _mock_investor_flow()
    assert flow["_mock"] is True
    for days in (3, 5, 10, 20):
        inst = flow[f"institution_net_{days}d"]
        frgn = flow[f"foreign_net_{days}d"]
        indiv = flow[f"individual_net_{days}d_est"]
        assert indiv == -(inst + frgn)


def test_format_investor_flow_block_includes_estimate_disclaimer_data():
    price_data = {
        "KR_005930": {
            "name": "삼성전자",
            "investor_flow": {
                "_mock": False,
                "foreign_net_5d": -5000, "institution_net_5d": -1000, "individual_net_5d_est": 6000,
                "foreign_net_20d": -8000, "institution_net_20d": -2000, "individual_net_20d_est": 10000,
            },
        },
        "US_NVDA": {"name": "NVIDIA", "investor_flow": {}},
    }
    block = _format_investor_flow_block(price_data)
    assert "삼성전자" in block
    assert "NVIDIA" not in block  # 해외 종목은 수급 데이터가 없어 블록에서 제외


def test_format_investor_flow_block_handles_no_data():
    block = _format_investor_flow_block({"US_NVDA": {"name": "NVIDIA", "investor_flow": {}}})
    assert "수급 데이터 없음" in block
