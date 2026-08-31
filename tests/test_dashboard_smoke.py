"""
대시보드 스모크 테스트 — 공유 함수 시그니처 호환성

배경: dashboard.py는 2,616줄로 앱 코드의 26%를 차지하는 최대 파일인데 테스트에서
      한 번도 참조되지 않았다. 문제는 이 파일이 파이프라인과 **같은 엔진 함수**를
      독립적으로 호출한다는 점이다 — SignalScorer.score, RatingAnalyzer.analyze_batch,
      config_loader의 저장 함수들.

      실제 사고: signal_scorer.score()에서 죽은 theme_config 파라미터를 제거했을 때
      main.py만 고치면 되는 줄 알았으나 dashboard.py도 같은 시그니처로 호출하고 있었다.
      잔여 참조를 수동으로 확인해서야 발견했고, 놓쳤다면 대시보드의 "등급 즉시 새로고침"이
      런타임에 깨졌을 것이다(테스트는 전부 통과한 채로).

      전체 UI 테스트가 목적이 아니다. 목적은 **엔진 시그니처가 바뀌면 여기서 깨지게
      만드는 것**이다.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("streamlit", reason="대시보드는 streamlit 의존")


def test_dashboard_module_imports_cleanly():
    """모듈 레벨 오류(임포트 실패·이름 오류)를 잡는다."""
    import app.dashboard as dash
    assert hasattr(dash, "_render_macro_panel")


# ── 엔진 시그니처 호환성 ─────────────────────────────────────────────────────

def test_scorer_signature_matches_dashboard_call():
    """대시보드는 score()를 위치인자 4개로 호출한다:
    scorer.score(stock, price, news, macro). 파라미터가 늘거나 순서가 바뀌면
    여기서 먼저 깨져야 한다."""
    from app.engine.signal_scorer import SignalScorer
    params = list(inspect.signature(SignalScorer.score).parameters)
    assert params[0] == "self"
    assert params[1:5] == ["stock_info", "price_data", "news_data", "macro_data"]
    # 5번째 이후는 기본값이 있어야 위치인자 4개 호출이 유효하다
    sig = inspect.signature(SignalScorer.score)
    for name in params[5:]:
        assert sig.parameters[name].default is not inspect.Parameter.empty, (
            f"{name}에 기본값이 없으면 대시보드의 4-인자 호출이 깨진다"
        )


def test_scorer_accepts_dashboard_style_positional_call():
    """시그니처 검사만으로는 부족하니 실제로 그 형태로 호출해본다."""
    from app.engine.signal_scorer import SignalScorer
    stock = {"id": "KR_005930", "name": "삼성전자", "sector": "반도체",
             "themes": [], "country": "KR"}
    result = SignalScorer().score(stock, {"change_pct": 1.0}, [], {"sentiment": {}})
    assert "total_score" in result and "components" in result


def test_rating_analyzer_batch_signature():
    from app.engine.rating_analyzer import RatingAnalyzer
    params = list(inspect.signature(RatingAnalyzer.analyze_batch).parameters)
    assert params[:3] == ["self", "score_results", "watchlist"]


def test_scorer_output_feeds_analyzer_without_adaptation():
    """대시보드는 score() 결과를 그대로 analyze_batch에 넘긴다."""
    from app.engine.rating_analyzer import RatingAnalyzer
    from app.engine.signal_scorer import SignalScorer
    stocks = [{"id": "KR_005930", "name": "삼성전자", "ticker": "005930",
               "sector": "반도체", "themes": [], "country": "KR"}]
    scores = [SignalScorer().score(stocks[0], {"change_pct": 1.0}, [], {"sentiment": {}})]
    ratings = RatingAnalyzer().analyze_batch(scores, stocks)
    assert ratings and hasattr(ratings[0], "to_dict")
    assert "check_required" in ratings[0].to_dict()


# ── 설정 저장 함수 시그니처 ──────────────────────────────────────────────────

@pytest.mark.parametrize("func_name,expected", [
    ("save_report_times", ["morning_time", "evening_time"]),
    ("save_watchlist", ["stocks"]),
    ("save_themes", ["themes"]),
    ("save_display_order", ["order"]),
])
def test_config_save_signatures_match_dashboard_calls(func_name, expected):
    """대시보드의 편집 화면이 호출하는 저장 함수들 — 인자가 바뀌면 저장이 깨진다."""
    import app.utils.config_loader as cl
    fn = getattr(cl, func_name)
    assert list(inspect.signature(fn).parameters) == expected


# ── 대시보드가 읽는 데이터 계약 ──────────────────────────────────────────────

def test_dashboard_reads_fields_the_pipeline_actually_writes():
    """대시보드는 파이프라인이 저장한 JSON을 읽는다. main.py가 쓰는 키와
    대시보드가 기대하는 키가 어긋나면 화면이 조용히 빈다."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    main_src = (root / "app" / "main.py").read_text(encoding="utf-8")
    dash_src = (root / "app" / "dashboard.py").read_text(encoding="utf-8")
    # 대시보드가 data.get()으로 읽는 최상위 키가 main.py의 덤프에 존재하는지
    for key in ["ratings", "macro", "data_quality", "event_calendar", "data_freshness"]:
        assert f'"{key}"' in main_src, f"main.py가 '{key}'를 저장하지 않는다"
        assert key in dash_src, f"대시보드가 '{key}'를 읽지 않는다"


def test_removed_market_flow_field_is_not_referenced_by_dashboard():
    """난수로 생성되던 시장 전체 외국인 순매수 지표는 제거됐다.
    대시보드에 참조가 남아 있으면 항상 N/A가 표시된다."""
    from pathlib import Path
    dash_src = (Path(__file__).resolve().parents[1] / "app" / "dashboard.py").read_text(encoding="utf-8")
    assert "foreign_net_buy_bn" not in dash_src
